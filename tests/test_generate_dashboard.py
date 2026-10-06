"""Offline safety tests. Synthetic API fixtures never become committed metrics."""

from contextlib import redirect_stderr, redirect_stdout
from copy import deepcopy
from datetime import date, timedelta
from html.parser import HTMLParser
import io
import json
from pathlib import Path
import re
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch
from urllib.error import HTTPError, URLError
import xml.etree.ElementTree as ET

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import generate_dashboard as dashboard

NS = {"svg": "http://www.w3.org/2000/svg"}


class DashboardTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.config = json.loads((dashboard.ROOT / "scripts/profile.json").read_text())
        cls.snapshot = dashboard.load_snapshot(dashboard.ROOT / "assets/profile-data.json", cls.config["login"])
        cls.avatar = (dashboard.ROOT / "assets/avatar.jpg").read_bytes()

    def test_missing_token_is_actionable_and_does_not_write(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict("os.environ", {}, clear=True):
            output = io.StringIO()
            with redirect_stderr(output):
                self.assertEqual(dashboard.main(["--output-dir", directory]), 2)
            self.assertIn("GITHUB_TOKEN ausente", output.getvalue())
            self.assertIn("--offline", output.getvalue())
            self.assertEqual(list(Path(directory).iterdir()), [])

    def test_offline_check_never_uses_network(self):
        with patch.object(dashboard, "request_bytes", side_effect=AssertionError("Network forbidden")), redirect_stdout(io.StringIO()):
            self.assertEqual(dashboard.main(["--offline", "--check"]), 0)

    def test_write_unchanged_preserves_file_mtime(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "asset.svg"
            self.assertTrue(dashboard.write_if_changed(path, b"snapshot"))
            timestamp = path.stat().st_mtime_ns
            self.assertFalse(dashboard.write_if_changed(path, b"snapshot"))
            self.assertEqual(timestamp, path.stat().st_mtime_ns)

    def test_failed_apis_preserve_independent_dated_snapshots(self):
        client = Mock()
        client.request.side_effect = dashboard.DataError("GitHub retornou HTTP 503.")
        with redirect_stderr(io.StringIO()) as output:
            result = dashboard.refresh_snapshot(client, self.config, deepcopy(self.snapshot), date(2026, 10, 6))
        self.assertEqual(result, self.snapshot)
        self.assertIn("snapshot anterior preservado", output.getvalue())

    def test_one_failed_metric_does_not_block_other_updates(self):
        fresh_profile = {**self.snapshot["profile"], "followers": 123}
        with patch.object(dashboard, "fetch_profile", return_value=fresh_profile), patch.object(dashboard, "fetch_repositories", side_effect=dashboard.DataError("HTTP 500")), patch.object(dashboard, "fetch_contributions", return_value=self.snapshot["contributions"]), redirect_stderr(io.StringIO()):
            result = dashboard.refresh_snapshot(Mock(), self.config, self.snapshot, date(2026, 10, 6))
        self.assertEqual(result["profile"]["followers"], 123)
        self.assertEqual(result["repositories"], self.snapshot["repositories"])

    def test_missing_data_renders_unavailable_instead_of_zero(self):
        for mobile in (False, True):
            root = ET.fromstring(dashboard.render_svg(self.config, {}, None, mobile))
            texts = [node.text for node in root.findall(".//svg:text", NS)]
            self.assertEqual(texts.count("—"), 4)
            self.assertIn("Calendário indisponível.", texts)
            self.assertFalse(any(node.attrib.get("fill") in dashboard.LEVELS for node in root.findall(".//svg:rect", NS)))

    def test_leap_year_period(self):
        start, end = dashboard.contribution_period(date(2024, 2, 29))
        self.assertEqual(start, date(2023, 3, 1))
        self.assertEqual((end - start).days + 1, 366)
        self.assertEqual(dashboard.contribution_period(date(2026, 10, 6))[0], date(2025, 10, 7))

    def test_missing_and_duplicate_calendar_days_are_rejected(self):
        start = end = date(2026, 10, 6)
        day = {"date": "2026-10-06", "count": 0, "level": 0}
        for days in ([], [day, day]):
            with self.assertRaises(dashboard.DataError):
                dashboard.validate_calendar(days, start, end)

    def test_calendar_total_and_intensity_must_match_counts(self):
        start = end = date(2026, 10, 6)
        with self.assertRaises(dashboard.DataError):
            dashboard.validate_calendar([{"date": "2026-10-06", "count": 2, "level": 1}], start, end, 3)
        for level in (0, 5):
            with self.assertRaises(dashboard.DataError):
                dashboard.validate_calendar([{"date": "2026-10-06", "count": 2, "level": level}], start, end)

    def test_graphql_maps_valid_daily_data_and_scope(self):
        start, end = dashboard.contribution_period(date(2026, 10, 6))
        days = [{"date": (start + timedelta(days=i)).isoformat(), "contributionCount": 0, "contributionLevel": "NONE"} for i in range((end - start).days + 1)]
        days[10].update(contributionCount=3, contributionLevel="SECOND_QUARTILE")
        client = Mock()
        client.request.return_value = {"data": {"user": {"contributionsCollection": {"contributionCalendar": {"totalContributions": 3, "weeks": [{"contributionDays": days}]}}}}}
        result = dashboard.fetch_contributions(client, self.config["login"], end)
        self.assertEqual(result["total"], 3)
        self.assertEqual(result["days"][10]["level"], 2)
        self.assertIn("token-visible", result["source"])
        self.assertEqual(client.request.call_args.args[1]["variables"]["login"], self.config["login"])

    def test_graphql_errors_and_null_user_do_not_echo_api_payload(self):
        client = Mock()
        for response in ({"errors": [{"message": "private payload"}]}, {"data": {"user": None}}):
            client.request.return_value = response
            with self.assertRaises(dashboard.DataError) as error:
                dashboard.fetch_contributions(client, self.config["login"], date(2026, 10, 6))
            self.assertNotIn("private payload", str(error.exception))

    def test_repository_pagination_excludes_fork_stars(self):
        client = Mock()
        client.request.side_effect = [
            [{"name": f"repo-{i}", "fork": False, "stargazers_count": 1} for i in range(100)],
            [{"name": "fork", "fork": True, "stargazers_count": 999}],
        ]
        result = dashboard.fetch_repositories(client, self.config["login"])
        self.assertEqual(result["received_stars"], 100)
        self.assertEqual(len(result["repositories"]), 101)
        self.assertIn("page=2", client.request.call_args.args[0])

    def test_failed_repository_page_does_not_return_partial_total(self):
        client = Mock()
        client.request.side_effect = [[{"name": f"repo-{i}", "fork": False, "stargazers_count": 1} for i in range(100)], dashboard.DataError("HTTP 503")]
        with self.assertRaises(dashboard.DataError):
            dashboard.fetch_repositories(client, self.config["login"])

    def test_incomplete_profile_metrics_are_rejected(self):
        client = Mock()
        client.request.return_value = {"login": self.config["login"], "followers": 6}
        with self.assertRaises(dashboard.DataError):
            dashboard.fetch_profile(client, self.config["login"])
        for value in (None, -1, True, "17"):
            with self.assertRaises(dashboard.DataError):
                dashboard.integer(value)

    def test_public_parser_reads_counts_not_just_color_levels(self):
        parser = dashboard.PublicCalendarParser()
        parser.feed('<td id="a" data-date="2026-10-05" data-level="4"></td><tool-tip for="a">1,234 contributions on October 5th.</tool-tip><td id="b" data-date="2026-10-06" data-level="0"></td><tool-tip for="b">No contributions on October 6th.</tool-tip>')
        self.assertEqual([day["count"] for day in parser.days()], [1234, 0])
        parser = dashboard.PublicCalendarParser()
        parser.feed('<td id="a" data-date="2026-10-05" data-level="4"></td>')
        self.assertEqual(parser.days(), [])

    def test_http_401_is_safe_and_does_not_retry(self):
        error = HTTPError(dashboard.API, 401, "secret-containing service message", {}, None)
        with patch.object(dashboard, "urlopen", side_effect=error) as request:
            with self.assertRaises(dashboard.DataError) as caught:
                dashboard.request_bytes(dashboard.API + "/users/BrThiagoN", "synthetic-test-credential")
        self.assertEqual(request.call_count, 1)
        self.assertEqual(str(caught.exception), "GitHub retornou HTTP 401.")

    def test_http_500_retries_once_then_fails(self):
        error = HTTPError(dashboard.API, 500, "server error", {}, None)
        with patch.object(dashboard, "urlopen", side_effect=error) as request, patch.object(dashboard.time, "sleep"):
            with self.assertRaises(dashboard.DataError):
                dashboard.request_bytes(dashboard.API + "/users/BrThiagoN")
        self.assertEqual(request.call_count, 2)

    def test_timeout_is_actionable(self):
        with patch.object(dashboard, "urlopen", side_effect=URLError("timeout")), patch.object(dashboard.time, "sleep"):
            with self.assertRaisesRegex(dashboard.DataError, "timeout"):
                dashboard.request_bytes(dashboard.API + "/users/BrThiagoN")

    def test_credentials_are_not_sent_to_avatar_host(self):
        response = Mock()
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        response.read.return_value = b"avatar"
        with patch.object(dashboard, "urlopen", return_value=response) as request:
            dashboard.request_bytes("https://avatars.githubusercontent.com/u/219904326", "synthetic-test-credential")
        self.assertFalse(request.call_args.args[0].has_header("Authorization"))
        with patch.object(dashboard, "request_bytes") as request:
            with self.assertRaises(dashboard.DataError):
                dashboard.refresh_avatar({"avatar_url": "https://untrusted.example/avatar"}, dashboard.ROOT / "assets")
            request.assert_not_called()

    def test_svg_is_self_contained_valid_and_accessible(self):
        for mobile in (False, True):
            output = dashboard.render_svg(self.config, self.snapshot, self.avatar, mobile)
            root = ET.fromstring(output)
            self.assertIn("viewBox", root.attrib)
            self.assertEqual(root.attrib["role"], "img")
            self.assertIsNotNone(root.find("svg:title", NS))
            self.assertIsNotNone(root.find("svg:desc", NS))
            ids = [node.attrib['id'] for node in root.iter() if 'id' in node.attrib]
            self.assertEqual(len(ids), len(set(ids)))
            for node in root.iter():
                self.assertNotIn(node.tag.split("}")[-1], ("script", "foreignObject", "animate", "iframe"))
                if "href" in node.attrib:
                    target = node.attrib['href']
                    self.assertTrue(target.startswith('data:image/jpeg;base64,') or (target.startswith('#') and target[1:] in ids))
            self.assertIn('"Courier New",monospace', output)

    def test_svg_text_escapes_untrusted_content(self):
        config = deepcopy(self.config)
        config["name"][0] = '<script>&"'
        root = ET.fromstring(dashboard.render_svg(config, self.snapshot, None))
        self.assertEqual(root.findall(".//svg:script", NS), [])
        self.assertTrue(any('<script>&"' in ''.join(node.itertext()) for node in root.findall(".//svg:text", NS)))

    def test_adaptive_svg_has_no_background_and_dark_fallback(self):
        for mobile in (False, True):
            root = ET.fromstring(dashboard.render_svg(self.config, self.snapshot, None, mobile))
            self.assertEqual(root.findall('svg:rect', NS), [])
            styles = root.find('svg:style', NS).text
            base, light = styles.split('@media (prefers-color-scheme: light)', 1)
            for key in ('foreground', 'muted', 'accent', 'pink'):
                self.assertIn(dashboard.THEMES['dark'][key], base)
                self.assertIn(dashboard.THEMES['light'][key], light)
            self.assertNotIn('background', styles)

    def test_palette_text_contrast_in_github_light_dark_and_dimmed(self):
        def luminance(color):
            channels = [int(color[i:i + 2], 16) / 255 for i in (1, 3, 5)]
            linear = [value / 12.92 if value <= .04045 else ((value + .055) / 1.055) ** 2.4 for value in channels]
            return sum(weight * value for weight, value in zip((.2126, .7152, .0722), linear))

        for theme, backgrounds in (('light', ('#ffffff',)), ('dark', ('#0d1117', '#22272e'))):
            for background in backgrounds:
                for key in ('foreground', 'muted', 'accent', 'pink'):
                    low, high = sorted((luminance(background), luminance(dashboard.THEMES[theme][key])))
                    self.assertGreaterEqual((high + .05) / (low + .05), 4.5, (theme, background, key))

    def test_wave_reveals_columns_in_order_without_changing_real_days(self):
        calendars = [self.snapshot['contributions']]
        # Partial first weeks must start first on every weekday, also in leap years.
        for offset in range(7):
            start, end = dashboard.contribution_period(date(2028, 12, 31) + timedelta(days=offset))
            days = [{'date':(start + timedelta(days=i)).isoformat(), 'count':0, 'level':0} for i in range((end - start).days + 1)]
            calendars.append(dashboard.calendar_snapshot(days, start, end, 'test fixture'))
        for calendar in calendars:
            for mobile in (False, True):
                data = {**self.snapshot, 'contributions':calendar}
                root = ET.fromstring(dashboard.render_svg(self.config, data, None, mobile))
                cells = [node for node in root.findall('.//svg:rect', NS) if 'contribution-cell' in node.attrib.get('class', '')]
                self.assertEqual(len(cells), len(calendar['days']))
                columns = {}
                for cell in cells:
                    delay = int(re.fullmatch(r'animation-delay:(\d+)ms', cell.attrib['style'])[1])
                    # Mobile bands are checked independently in their visual order.
                    band = 1 if mobile and float(cell.attrib['y']) >= 524 else 0
                    columns.setdefault(band, {}).setdefault(float(cell.attrib['x']), []).append(delay)
                    self.assertLessEqual(delay + dashboard.WAVE_DURATION_MS, 7500)
                    self.assertIn('contribution-cell', cell.attrib['class'])
                for band in columns.values():
                    column_delays = [min(band[x]) for x in sorted(band)]
                    self.assertEqual(column_delays, sorted(column_delays))
                    self.assertGreaterEqual(column_delays[-1] - column_delays[0], 5900)
                    ordered = [band[x] for x in sorted(band)]
                    for earlier, later in zip(ordered, ordered[1:]):
                        self.assertLess(max(earlier), min(later))
                    completion = max(delay for delays in ordered for delay in delays) + dashboard.WAVE_DURATION_MS
                    self.assertGreaterEqual(completion, 7000)
                styles = root.find('svg:style', NS).text
                self.assertIn('@media (prefers-reduced-motion: no-preference)', styles)
                self.assertIn('to{opacity:1;transform:translateY(0)}', styles)
                self.assertNotIn('infinite', styles)
                self.assertNotIn('animation', dashboard.svg_styles(animate=False))

    def test_panel_text_bounds_in_both_layouts(self):
        # Conservative monospace advance (0.62 em) also covers Courier fallback.
        for mobile in (False, True):
            root = ET.fromstring(dashboard.render_svg(self.config, self.snapshot, self.avatar, mobile))
            for group in root.findall("svg:g", NS):
                x, y, width, height = map(float, group.attrib["data-bounds"].split())
                for node in group.findall(".//svg:text", NS):
                    left, baseline, size = float(node.attrib["x"]), float(node.attrib["y"]), float(node.attrib["font-size"])
                    content = ''.join(node.itertext())
                    advance = len(content) * size * 0.62
                    if node.attrib.get("class") == "label":
                        advance += max(0, len(content) - 1) * 1.2
                    if node.attrib.get("text-anchor") == "end":
                        left -= advance
                    self.assertGreaterEqual(left, x + 8, content)
                    self.assertLessEqual(left + advance, x + width - 8, content)
                    self.assertGreaterEqual(baseline - size, y + 8, content)
                    self.assertLessEqual(baseline + size * 0.25, y + height - 8, content)

    def test_header_writing_preserves_text_and_finishes_in_reading_order(self):
        for mobile in (False, True):
            root = ET.fromstring(dashboard.render_svg(self.config, self.snapshot, None, mobile))
            profile = next(group for group in root.findall('svg:g', NS) if group.attrib.get('data-panel') == 'profile')
            expected = (
                self.config['name'] if mobile else [' '.join(self.config['name'])]
            ) + ['Engenharia de Software @ FIAP'] + (
                self.config['role'] if mobile else [' '.join(self.config['role'])]
            ) + [self.config['building'], ' / '.join(self.config['focus'])]
            lines = profile.findall('svg:text', NS)
            self.assertEqual([''.join(line.itertext()) for line in lines], expected)
            previous_end = 0
            for line in lines:
                self.assertIn('handwriting-line', line.attrib['class'])
                self.assertEqual(line.attrib['{http://www.w3.org/XML/1998/namespace}space'], 'preserve')
                letters = line.findall('svg:tspan', NS)
                timings = [re.fullmatch(r'animation-delay:(\d+)ms;animation-duration:(\d+)ms', letter.attrib['style']) for letter in letters]
                self.assertTrue(all(timings))
                delays = [int(timing[1]) for timing in timings]
                self.assertTrue(all(earlier < later for earlier, later in zip(delays, delays[1:])))
                self.assertGreater(delays[0], previous_end)
                previous_end = delays[-1] + int(timings[-1][2])
                self.assertTrue(all(letter.attrib['class'] == 'handwriting-letter' for letter in letters))
            self.assertLess(previous_end, 7500)
            styles = root.find('svg:style', NS).text
            self.assertIn('@keyframes handwriting-ink', styles)
            motion = styles.split('@media (prefers-reduced-motion: no-preference){', 1)[1].split('}', 2)[1]
            self.assertIn('animation-name:handwriting-ink', motion)
            self.assertIn('.handwriting-letter{stroke-opacity:0;', styles)
            self.assertNotIn('infinite', styles)
            self.assertNotIn('handwriting', dashboard.svg_styles(animate=False))

    def test_all_commands_decode_with_correct_fallback_and_decorative_frames(self):
        for mobile in (False, True):
            root = ET.fromstring(dashboard.render_svg(self.config, self.snapshot, None, mobile))
            commands = root.findall('.//svg:g[@data-command]', NS)
            suffix = '-mobile' if mobile else ''
            outputs = dashboard.render_footer_assets(self.config)
            for key in ('projects-heading', 'contact-heading'):
                footer = ET.fromstring(outputs[f'footer/{key}{suffix}.svg'])
                commands.extend(footer.findall('.//svg:g[@data-command]', NS))
            self.assertEqual([node.attrib['data-command'] for node in commands], ['$ github --stats', '$ cat stack.json', '$ ls ~/featured-projects', '$ contact'])
            for command in commands:
                frames = command.findall('svg:g[@aria-hidden="true"]', NS)
                value = command.attrib['data-command']
                self.assertEqual(len(frames), 3)
                self.assertEqual([frame.find('svg:text', NS).text for frame in frames], list(dashboard.command_frames(value)))
                self.assertTrue(all(frame.attrib['opacity'] == '0' for frame in frames))
                final = command.find('svg:g[@class="decode-final"]/svg:text', NS)
                self.assertEqual(final.text, value)
                self.assertNotIn('opacity', final.attrib)
                resolved = []
                for frame in frames:
                    text = frame.find('svg:text', NS)
                    self.assertEqual(len(text.text), len(value))
                    resolved.append({index for index, character in enumerate(text.text) if character == value[index]})
                    self.assertTrue(all(text.text[index] == character for index, character in enumerate(value) if not character.isalnum()))
                    for key in ('x', 'y', 'font-size', 'text-anchor'):
                        self.assertEqual(text.attrib[key], final.attrib[key])
                self.assertLess(resolved[0], resolved[1])
                self.assertLess(resolved[1], resolved[2])
            styles = root.find('svg:style', NS).text
            motion = styles.split('@media (prefers-reduced-motion: no-preference){', 1)[1].split('@keyframes', 1)[0]
            self.assertIn('.decode-final{animation:decode-resolved', motion)
            self.assertIn('.decode-frame-2{animation-name:decode-stage-2}', motion)
            self.assertLessEqual(dashboard.DECODE_START_MS + dashboard.DECODE_DURATION_MS, 1200)
            self.assertNotIn('decode-', dashboard.svg_styles(animate=False))

    def test_horizontal_dividers_draw_left_to_right_with_static_fallback(self):
        documents = [dashboard.render_svg(self.config, self.snapshot, None, mobile) for mobile in (False, True)]
        documents += list(dashboard.render_footer_assets(self.config).values())
        count = 0
        for content in documents:
            root = ET.fromstring(content)
            for line in root.findall('.//svg:path', NS):
                if 'rule' not in line.attrib.get('class', '').split():
                    continue
                x1, y1, x2, y2 = map(float, re.fullmatch(r'M([\d.]+) ([\d.]+)L([\d.]+) ([\d.]+)', line.attrib['d']).groups())
                self.assertNotIn('stroke-dashoffset', line.attrib)
                self.assertNotIn('stroke-dasharray', line.attrib)
                if y1 == y2:
                    self.assertGreater(x2, x1)
                    self.assertIn('divider', line.attrib['class'])
                    self.assertEqual(line.attrib['pathLength'], '1')
                    count += 1
                else:
                    self.assertEqual(x1, x2)
                    self.assertNotIn('divider', line.attrib['class'])
        self.assertEqual(count, 13)  # Main desktop/mobile (2/3), eight project rows.
        motion = dashboard.svg_styles().split('@media (prefers-reduced-motion: no-preference){', 1)[1].split('@keyframes', 1)[0]
        self.assertIn('.divider{stroke-dasharray:1;animation:divider-draw', motion)
        self.assertIn('from{stroke-dashoffset:1}to{stroke-dashoffset:0}', dashboard.svg_styles())
        self.assertNotIn('divider-draw', dashboard.svg_styles(animate=False))

    def test_stack_scanner_preserves_real_text_and_complete_fallback(self):
        for mobile in (False, True):
            root = ET.fromstring(dashboard.render_svg(self.config, self.snapshot, None, mobile))
            stack = root.find('svg:g[@data-panel="stack"]', NS)
            values = stack.findall('svg:g[@class="stack-values"]', NS)
            clips = stack.findall('svg:defs/svg:clipPath', NS)
            scans = stack.findall('svg:path[@class="stack-scan"]', NS)
            self.assertEqual(len(values), 5 if mobile else 4)
            self.assertEqual(len(values), len(clips))
            self.assertEqual(len(values), len(scans))
            all_text = ' / '.join(value.find('svg:text', NS).text for value in values)
            for key in ('languages', 'backend', 'data', 'tools'):
                for technology in self.config[key]:
                    self.assertIn(technology, all_text)
            for index, (value, clip, scan) in enumerate(zip(values, clips, scans)):
                rect = clip.find('svg:rect', NS)
                text = value.find('svg:text', NS)
                self.assertEqual(value.attrib['clip-path'], f'url(#{clip.attrib["id"]})')
                self.assertEqual(clip.attrib['clipPathUnits'], 'userSpaceOnUse')
                self.assertNotIn('opacity', value.attrib)
                self.assertNotIn('transform', rect.attrib)
                self.assertEqual(rect.attrib['x'], text.attrib['x'])
                self.assertGreater(float(rect.attrib['width']), len(text.text) * float(text.attrib['font-size']) * .62)
                delay = dashboard.STACK_START_MS + index * dashboard.STACK_ROW_STEP_MS
                self.assertEqual(rect.attrib['style'], f'transform-origin:{rect.attrib["x"]}px {rect.attrib["y"]}px;animation-delay:{delay}ms')
                self.assertIn(f'animation-delay:{delay}ms', scan.attrib['style'])
                self.assertEqual(scan.attrib['opacity'], '0')
                self.assertEqual(scan.attrib['aria-hidden'], 'true')
                self.assertLess(delay + dashboard.STACK_SCAN_DURATION_MS, 3000)
            motion = root.find('svg:style', NS).text.split('@media (prefers-reduced-motion: no-preference){', 1)[1].split('@keyframes', 1)[0]
            self.assertIn('.stack-aperture{transform-box:view-box;', motion)
            self.assertRegex(motion, r'\.stack-scan\{animation:stack-register \d+ms steps\(18,end\) forwards\}')
            self.assertIn('steps(18,end)', motion)
            self.assertNotIn('stack-reveal', dashboard.svg_styles(animate=False))

    def test_seal_fragments_partition_original_artwork_and_keep_complete_fallback(self):
        original = ET.parse(dashboard.ROOT / 'assets/development-mark.svg').getroot()
        for mobile in (False, True):
            root = ET.fromstring(dashboard.render_svg(self.config, self.snapshot, None, mobile))
            ink = root.find('.//svg:g[@id="development-ink"]', NS)
            self.assertEqual([node.attrib['d'] for node in ink.findall('svg:path', NS)], [node.attrib['d'] for node in original.findall('svg:path', NS)])
            clips = [clip for clip in root.findall('.//svg:clipPath', NS) if clip.attrib['id'].startswith('seal-tile-')]
            self.assertEqual(len(clips), 8)
            rectangles = [tuple(float(node.attrib[key]) for key in ('x', 'y', 'width', 'height')) for clip in clips for node in clip.findall('svg:rect', NS)]
            self.assertEqual(set(rectangles), {(x, y, 60, 36) for x in (0, 60) for y in (0, 36, 72, 108)})
            pieces = root.find('.//svg:g[@class="seal-pieces"]', NS)
            self.assertEqual(pieces.attrib['opacity'], '0')
            self.assertEqual(pieces.attrib['aria-hidden'], 'true')
            tiles = [int(re.fullmatch(r'url\(#seal-tile-(\d)\)', node.attrib['clip-path'])[1]) for node in pieces.findall('svg:g', NS)]
            self.assertEqual(sorted(tiles), list(range(8)))
            self.assertNotEqual(tiles, sorted(tiles))
            delays = [int(re.fullmatch(r'animation-delay:(\d+)ms', node.attrib['style'])[1]) for node in pieces.findall('.//svg:use', NS)]
            self.assertEqual(delays, sorted(set(delays)))
            self.assertEqual(delays[-1] + dashboard.SEAL_FRAGMENT_DURATION_MS, dashboard.SEAL_TOTAL_MS)
            final = root.find('.//svg:use[@class="seal-final"]', NS)
            self.assertEqual(final.attrib['href'], '#development-ink')
            self.assertNotIn('opacity', final.attrib)
            self.assertLessEqual(dashboard.SEAL_TOTAL_MS, 1200)
            styles = root.find('svg:style', NS).text
            motion = styles.split('@media (prefers-reduced-motion: no-preference){', 1)[1].split('@keyframes', 1)[0]
            self.assertIn('.seal-pieces{animation:seal-pieces', motion)
            self.assertIn('.seal-fragment{animation:seal-fragment', motion)
            self.assertNotIn('seal-fragment', dashboard.svg_styles(animate=False))

    def test_leap_calendar_cells_and_legend_do_not_overlap(self):
        data = deepcopy(self.snapshot)
        start, end = dashboard.contribution_period(date(2028, 12, 31))
        days = [{"date": (start + timedelta(days=i)).isoformat(), "count": 0, "level": 0} for i in range((end - start).days + 1)]
        data["contributions"] = dashboard.calendar_snapshot(days, start, end, "test fixture")
        self.assertEqual(len(dashboard.calendar_weeks(data["contributions"])), 54)
        for mobile in (False, True):
            root = ET.fromstring(dashboard.render_svg(self.config, data, None, mobile))
            group = next(g for g in root.findall("svg:g", NS) if g.attrib.get("data-panel") == "contributions")
            x, y, width, height = map(float, group.attrib["data-bounds"].split())
            cells = group.findall("svg:g/svg:rect", NS)
            legend_top = min(float(node.attrib['y']) for node in group.findall('svg:rect', NS))
            self.assertEqual(len(cells), len(days))
            for node in cells:
                self.assertLessEqual(float(node.attrib["x"]) + float(node.attrib["width"]), x + width - 16)
                self.assertLessEqual(float(node.attrib["y"]) + float(node.attrib["height"]), legend_top - 8)

    def test_readme_paths_contacts_and_all_featured_links(self):
        readme = (dashboard.ROOT / "README.md").read_text()
        for name in ("dashboard.svg", "dashboard-mobile.svg"):
            self.assertIn(f'./assets/{name}', readme)
            self.assertTrue((dashboard.ROOT / "assets" / name).is_file())
        self.assertIn(self.config["contact"]["linkedin"], readme)
        self.assertIn("mailto:" + self.config["contact"]["email"], readme)
        for repo in ("serah-googleExtension", "Limite-de-desempenho-de-API", "music-store-cli", "vinharia-agnelo-arduino"):
            self.assertIn(f"https://github.com/{self.config['login']}/{repo}", readme)

    def test_footer_keeps_every_project_readable_and_links_clickable(self):
        outputs = dashboard.render_footer_assets(self.config)
        for path, content in outputs.items():
            self.assertEqual((dashboard.ROOT / 'assets' / path).read_text(), content)
            root = ET.fromstring(content)
            self.assertEqual(root.findall('svg:rect', NS), [])
            if Path(path).stem.removesuffix('-mobile') in ('linkedin', 'email', 'github'):
                self.assertNotIn('@keyframes', content)
            for group in root.findall('svg:g', NS):
                x, y, width, height = map(float, group.attrib['data-bounds'].split())
                for node in group.findall('.//svg:text', NS):
                    left, baseline, size = float(node.attrib['x']), float(node.attrib['y']), float(node.attrib['font-size'])
                    advance = len(node.text or '') * size * .62
                    if node.attrib.get('text-anchor') == 'end':
                        left -= advance
                    self.assertGreaterEqual(left, x + 8, (path, node.text))
                    self.assertLessEqual(left + advance, x + width - 8, (path, node.text))
                    self.assertGreaterEqual(baseline - size, y + 8, (path, node.text))
                    self.assertLessEqual(baseline + size * .25, y + height - 8, (path, node.text))
        for project in self.config['projects']:
            for suffix in ('', '-mobile'):
                root = ET.fromstring(outputs[f'footer/{project["slug"]}{suffix}.svg'])
                texts = [node.text for node in root.findall('.//svg:text', NS)]
                nodes = root.findall('.//svg:text', NS)
                self.assertEqual(nodes[0].attrib.get('text-decoration'), 'underline')
                self.assertTrue(all('text-decoration' not in node.attrib for node in nodes[1:]))
                self.assertEqual(texts[0], project['name'])
                self.assertEqual(texts[1], ' / '.join(project['stack']))
                self.assertEqual(' '.join(texts[2:]), project['description'])
        for contact in ('linkedin', 'email', 'github'):
            for suffix in ('', '-mobile'):
                root = ET.fromstring(outputs[f'footer/{contact}{suffix}.svg'])
                self.assertEqual(root.find('.//svg:text', NS).attrib.get('text-decoration'), 'underline')

        class Links(HTMLParser):
            def __init__(self):
                super().__init__()
                self.target, self.links = None, {}

            def handle_starttag(self, tag, attrs):
                attrs = dict(attrs)
                if tag == 'a':
                    self.target = attrs['href']
                elif tag == 'img' and self.target:
                    self.links[self.target] = attrs['src']
                if tag in ('img', 'source'):
                    path = attrs.get('src') or attrs['srcset']
                    self.assert_path(path)

            def assert_path(self, path):
                if not (dashboard.ROOT / path).is_file():
                    raise AssertionError(f'Asset ausente no README: {path}')

            def handle_endtag(self, tag):
                if tag == 'a':
                    self.target = None

        links = Links()
        links.feed((dashboard.ROOT / 'README.md').read_text())
        for project in self.config['projects']:
            self.assertEqual(links.links[project['url']], f'./assets/footer/{project["slug"]}.svg')
        self.assertIn(self.config['contact']['linkedin'], links.links)
        self.assertIn('mailto:' + self.config['contact']['email'], links.links)


if __name__ == "__main__":
    unittest.main()
