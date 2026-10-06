#!/usr/bin/env python3
"""Generate self-contained GitHub profile SVGs using only the standard library."""

from __future__ import annotations

import argparse
from datetime import date, datetime, timedelta
from html import escape
from html.parser import HTMLParser
import json
import os
from pathlib import Path
import re
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlparse
from urllib.request import Request, urlopen
import xml.etree.ElementTree as ET
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
API = "https://api.github.com"
TIMEOUT = 15
MAX_RESPONSE = 4 * 1024 * 1024
MONTHS = ("Jan", "Fev", "Mar", "Abr", "Mai", "Jun", "Jul", "Ago", "Set", "Out", "Nov", "Dez")
LEVELS = ("#1e1b26", "#482437", "#7b304c", "#bf4b76", "#ef8fb4")
THEMES = {
    "dark": {
        "foreground": "#f3ece8", "muted": "#a5a1ad", "accent": "#f05c73",
        "pink": "#ef8fb4", "rule": "#3d3342", "seal": "#803044",
        "levels": LEVELS,
    },
    "light": {
        "foreground": "#24292f", "muted": "#59636e", "accent": "#b91f45",
        "pink": "#96335f", "rule": "#d1d9e0", "seal": "#dba7b6",
        "levels": ("#ebe8ee", "#efc9d8", "#d990af", "#b9507e", "#832044"),
    },
}
WAVE_DURATION_MS = 420
WAVE_SPAN_MS = 1350
WAVE_START_MS = 120
# Keep the full seven-day stagger below one weekly step, including 54-week grids.
WAVE_ROW_STAGGER_MS = 4
CONTRIBUTION_LEVELS = {
    "NONE": 0,
    "FIRST_QUARTILE": 1,
    "SECOND_QUARTILE": 2,
    "THIRD_QUARTILE": 3,
    "FOURTH_QUARTILE": 4,
}
CONTRIBUTIONS_QUERY = """
query ProfileCalendar($login: String!, $from: DateTime!, $to: DateTime!) {
  user(login: $login) {
    contributionsCollection(from: $from, to: $to) {
      contributionCalendar {
        totalContributions
        weeks {
          contributionDays { date contributionCount contributionLevel }
        }
      }
    }
  }
}
"""


class DataError(RuntimeError):
    """A service or snapshot cannot supply trustworthy data."""


def today() -> date:
    return datetime.now(ZoneInfo("America/Sao_Paulo")).date()


def contribution_period(end: date) -> tuple[date, date]:
    """An inclusive rolling year; handle February 29 without losing a day."""
    try:
        start = end.replace(year=end.year - 1) + timedelta(days=1)
    except ValueError:
        start = date(end.year - 1, 3, 1)
    return start, end


def integer(value: object) -> int:
    if type(value) is not int or value < 0:
        raise DataError("Resposta incompleta: esperado um número inteiro não negativo.")
    return value


def request_bytes(url: str, token: str | None = None, payload: dict | None = None) -> bytes:
    """Never send credentials to avatar or public-calendar hosts, or log responses."""
    headers = {"User-Agent": "BrThiagoN-profile-dashboard", "Accept-Language": "en-US"}
    if urlparse(url).hostname == "api.github.com":
        headers.update({"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"})
        if token:
            headers["Authorization"] = f"Bearer {token}"
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    if data is not None:
        headers["Content-Type"] = "application/json"
    request = Request(url, headers=headers, data=data)
    for attempt in range(2):
        try:
            with urlopen(request, timeout=TIMEOUT) as response:
                content = response.read(MAX_RESPONSE + 1)
            if len(content) > MAX_RESPONSE:
                raise DataError("Resposta do GitHub excede o limite de tamanho.")
            return content
        except HTTPError as error:
            if attempt == 0 and (error.code == 429 or error.code >= 500):
                time.sleep(1)
                continue
            raise DataError(f"GitHub retornou HTTP {error.code}.") from None
        except (URLError, TimeoutError, OSError):
            if attempt == 0:
                time.sleep(1)
                continue
            raise DataError("GitHub indisponível ou timeout; tente novamente mais tarde.") from None
    raise DataError("Não foi possível consultar o GitHub.")


class GitHubClient:
    def __init__(self, token: str | None):
        self.token = token

    def request(self, path: str, payload: dict | None = None) -> object:
        try:
            return json.loads(request_bytes(API + path, self.token, payload))
        except (ValueError, UnicodeError):
            raise DataError("GitHub retornou JSON inválido.") from None


def fetch_profile(client: GitHubClient, login: str) -> dict:
    profile = client.request(f"/users/{login}")
    if not isinstance(profile, dict) or str(profile.get("login", "")).lower() != login.lower():
        raise DataError("Perfil não encontrado na API.")
    return {
        "source": "GitHub REST API",
        "fetched_on": today().isoformat(),
        **{key: integer(profile.get(key)) for key in ("public_repos", "followers", "following")},
        "avatar_url": profile.get("avatar_url", ""),
    }


def fetch_repositories(client: GitHubClient, login: str) -> dict:
    """Paginate completely: never display a partial sum of received stars."""
    repositories = []
    page = 1
    while True:
        batch = client.request(f"/users/{login}/repos?type=owner&sort=full_name&per_page=100&page={page}")
        if not isinstance(batch, list):
            raise DataError("Lista de repositórios inválida.")
        for repo in batch:
            if not isinstance(repo, dict) or type(repo.get("fork")) is not bool or not repo.get("name"):
                raise DataError("Metadados de repositório incompletos.")
            repositories.append({"name": repo["name"], "fork": repo["fork"], "stars": integer(repo.get("stargazers_count"))})
        if len(batch) < 100:
            break
        page += 1
    repositories.sort(key=lambda repo: repo["name"].lower())
    return {
        "source": "GitHub REST API · public owned repositories, excluding forks",
        "fetched_on": today().isoformat(),
        "received_stars": sum(repo["stars"] for repo in repositories if not repo["fork"]),
        "repositories": repositories,
    }


def validate_calendar(days: list[dict], start: date, end: date, total: int | None = None) -> list[dict]:
    """Missing cells are unavailable data, never fabricated zero contributions."""
    expected = {(start + timedelta(days=i)).isoformat() for i in range((end - start).days + 1)}
    filtered = []
    for day in days:
        if day.get("date") in expected:
            count, level = integer(day.get("count")), integer(day.get("level"))
            if level > 4 or (count == 0) != (level == 0):
                raise DataError("Calendário com intensidade inválida.")
            filtered.append({"date": day["date"], "count": count, "level": level})
    if len(filtered) != len(expected) or {day["date"] for day in filtered} != expected:
        raise DataError("Calendário incompleto; nenhum dia ausente será preenchido com zero.")
    filtered.sort(key=lambda day: day["date"])
    if total is not None and sum(day["count"] for day in filtered) != integer(total):
        raise DataError("Total de contribuições não corresponde aos dias retornados.")
    return filtered


def calendar_snapshot(days: list[dict], start: date, end: date, source: str) -> dict:
    return {
        "source": source, "fetched_on": today().isoformat(),
        "from": start.isoformat(), "to": end.isoformat(),
        "total": sum(day["count"] for day in days), "days": days,
    }


def fetch_contributions(client: GitHubClient, login: str, end: date) -> dict:
    start, end = contribution_period(end)
    result = client.request("/graphql", {
        "query": CONTRIBUTIONS_QUERY,
        "variables": {"login": login, "from": f"{start}T00:00:00Z", "to": f"{end}T23:59:59Z"},
    })
    if not isinstance(result, dict) or result.get("errors"):
        raise DataError("Consulta GraphQL de contribuições indisponível para este token.")
    try:
        calendar = result["data"]["user"]["contributionsCollection"]["contributionCalendar"]
        days = [
            {"date": day["date"], "count": day["contributionCount"], "level": CONTRIBUTION_LEVELS[day["contributionLevel"]]}
            for week in calendar["weeks"] for day in week["contributionDays"]
        ]
        days = validate_calendar(days, start, end, calendar["totalContributions"])
    except (KeyError, TypeError):
        raise DataError("Resposta GraphQL de contribuições incompleta.") from None
    return calendar_snapshot(days, start, end, "GitHub GraphQL API · token-visible contributions")


class PublicCalendarParser(HTMLParser):
    """Optional local bootstrap from GitHub itself, with strict date/count coverage."""

    def __init__(self):
        super().__init__()
        self.cells: dict[str, dict] = {}
        self.tooltips: dict[str, str] = {}
        self.target: str | None = None

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if "data-date" in attributes and "data-level" in attributes and attributes.get("id"):
            try:
                self.cells[attributes["id"]] = {"date": attributes["data-date"], "level": int(attributes["data-level"])}
            except ValueError:
                raise DataError("Calendário público com intensidade inválida.") from None
        if tag == "tool-tip":
            self.target = attributes.get("for")
            if self.target:
                self.tooltips[self.target] = ""

    def handle_data(self, data):
        if self.target:
            self.tooltips[self.target] += data

    def handle_endtag(self, tag):
        if tag == "tool-tip":
            self.target = None

    def days(self) -> list[dict]:
        days = []
        for identifier, cell in self.cells.items():
            match = re.match(r"\s*(No|[\d,]+) contributions? on\b", self.tooltips.get(identifier, ""))
            if match:
                count = 0 if match[1] == "No" else int(match[1].replace(",", ""))
                days.append({**cell, "count": count})
        return days


def fetch_public_contributions(login: str, end: date) -> dict:
    start, end = contribution_period(end)
    days = []
    for year in range(start.year, end.year + 1):
        query = urlencode({"from": f"{year}-01-01", "to": f"{year}-12-31"})
        parser = PublicCalendarParser()
        parser.feed(request_bytes(f"https://github.com/users/{login}/contributions?{query}").decode("utf-8"))
        days.extend(parser.days())
    days = validate_calendar(days, start, end)
    return calendar_snapshot(days, start, end, "GitHub public contribution calendar · local bootstrap")


def refresh_avatar(profile: dict | None, assets: Path) -> None:
    if not profile:
        return
    parsed = urlparse(profile.get("avatar_url", ""))
    if parsed.scheme != "https" or parsed.hostname != "avatars.githubusercontent.com" or not re.fullmatch(r"/u/\d+", parsed.path):
        raise DataError("URL de avatar não pertence ao CDN de avatares do GitHub.")
    content = request_bytes(f"https://avatars.githubusercontent.com{parsed.path}?s=192&v=4")
    if not content.startswith(b"\xff\xd8\xff") or len(content) > 512 * 1024:
        raise DataError("Avatar não está no formato JPEG esperado; preservando o anterior.")
    write_if_changed(assets / "avatar.jpg", content)


def load_snapshot(path: Path, login: str) -> dict:
    if not path.exists():
        return {"schema_version": 1, "login": login}
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("schema_version") != 1 or data.get("login") != login:
        raise DataError("Snapshot pertence a outro perfil ou tem versão incompatível.")
    for key in ("profile", "repositories", "contributions"):
        component = data.get(key)
        if not component:
            continue
        date.fromisoformat(component["fetched_on"])
        if key == "profile":
            for metric in ("public_repos", "followers", "following"):
                integer(component[metric])
        elif key == "repositories":
            integer(component["received_stars"])
            expected = sum(integer(repo["stars"]) for repo in component["repositories"] if not repo["fork"])
            if expected != component["received_stars"]:
                raise DataError("Snapshot de stars inconsistente.")
        else:
            validate_calendar(component["days"], date.fromisoformat(component["from"]), date.fromisoformat(component["to"]), component["total"])
    return data


def refresh_snapshot(client: GitHubClient, config: dict, cached: dict, end: date, public: bool = False) -> dict:
    result = dict(cached)
    fetchers = {
        "profile": lambda: fetch_profile(client, config["login"]),
        "repositories": lambda: fetch_repositories(client, config["login"]),
        "contributions": lambda: fetch_public_contributions(config["login"], end) if public else fetch_contributions(client, config["login"], end),
    }
    for component, fetch in fetchers.items():
        try:
            result[component] = fetch()
        except (DataError, ValueError, KeyError, TypeError) as error:
            reason = str(error) if isinstance(error, DataError) else "Dados incompletos ou inválidos."
            fallback = "snapshot anterior preservado" if result.get(component) else "métrica omitida"
            print(f"Aviso [{component}]: {reason} {fallback}.", file=sys.stderr)
    return result


def write_if_changed(path: Path, content: bytes) -> bool:
    if path.exists() and path.read_bytes() == content:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_bytes(content)
    temporary.replace(path)
    return True


def svg_styles(theme: str | None = None, animate: bool = True) -> str:
    """Adaptive image CSS; an explicit theme is only used by local PNG previews.

    SVG images inherit the embedding element's color scheme. GitHub sets that
    scheme from its site theme, even when it differs from the operating system.
    Unsupported media queries leave the dark palette and a complete calendar.
    """
    def palette_rules(palette: dict) -> str:
        rules = [
            f'text{{fill:{palette["foreground"]}}}',
            f'.muted{{fill:{palette["muted"]}}}',
            f'.accent,.accent-fill{{fill:{palette["accent"]}}}',
            f'.pink{{fill:{palette["pink"]}}}',
            f'.rule{{stroke:{palette["rule"]}}}',
            f'.mark-outline{{stroke:{palette["seal"]}}}',
        ]
        rules.extend(f'.level-{i}{{fill:{color}}}' for i, color in enumerate(palette['levels']))
        return '\n'.join(rules)

    rules = [
        'text{font-family:ui-monospace,SFMono-Regular,Menlo,Monaco,Consolas,"Liberation Mono","Courier New",monospace}',
        '.display{font-family:"Bahnschrift","DIN Condensed","Nimbus Sans Narrow","Arial Narrow","Liberation Sans Narrow",sans-serif;font-weight:700}',
        '.strong{font-weight:700}',
        palette_rules(THEMES[theme or 'dark']),
    ]
    if theme is None:
        rules.append('@media (prefers-color-scheme: light){\n' + palette_rules(THEMES['light']) + '\n}')
    if animate:
        rules.extend([
            '@media (prefers-reduced-motion: no-preference){\n'
            f'.contribution-cell{{animation:contribution-wave {WAVE_DURATION_MS}ms cubic-bezier(.2,.65,.3,1) both;transform-box:fill-box;transform-origin:center}}\n'
            '}',
            '@keyframes contribution-wave{from{opacity:0;transform:translateY(4px)}to{opacity:1;transform:translateY(0)}}',
        ])
    return '\n'.join(rules)


class Canvas:
    """SVG document sized for the README column, with escaped text and local paths."""

    def __init__(self, width: int, height: int, config: dict):
        self.width, self.height = width, height
        name = ' '.join(config['name'])
        description = f'{name}, estudante de Engenharia de Software na FIAP e estagiário de Produto e Desenvolvimento. Backend, cloud e IA aplicada. Estudando: {config["learning"]}. Prática: {config["practice"]}. Calendário e métricas reais do GitHub, com datas de snapshot. 開発 significa desenvolvimento.'
        self.parts = [
            f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}" role="img" aria-labelledby="title description">',
            f'<title id="title">{escape(name)} / engineering profile</title>',
            f'<desc id="description">{escape(description)}</desc>',
            '<style>\n' + svg_styles() + '\n</style>',
        ]

    def rect(self, x, y, width, height, fill='#171820', radius=0, stroke='none', style='', delay=None):
        attributes = f' class="{style}"' if style else ''
        if delay is not None:
            attributes += f' style="animation-delay:{delay}ms"'
        self.parts.append(f'<rect x="{x:g}" y="{y:g}" width="{width:g}" height="{height:g}" rx="{radius:g}" fill="{fill}" stroke="{stroke}"{attributes}/>')

    def text(self, x, y, value, size=16, style='', anchor='start'):
        fill = THEMES['dark'].get(style, THEMES['dark']['foreground'])
        self.parts.append(f'<text x="{x:g}" y="{y:g}" font-size="{size:g}" fill="{fill}" class="{style}" text-anchor="{anchor}">{escape(str(value))}</text>')

    def line(self, x1, y1, x2, y2):
        self.parts.append(f'<path d="M{x1:g} {y1:g}L{x2:g} {y2:g}" class="rule" stroke="{THEMES["dark"]["rule"]}" fill="none"/>')

    def group(self, name, x, y, width, height):
        self.parts.append(f'<g data-panel="{name}" data-bounds="{x} {y} {width} {height}">')

    def panel(self, name, x, y, width, height, title, mobile=False):
        self.group(name, x, y, width, height)
        self.text(x + (20 if mobile else 24), y + 28, title, 20 if mobile else 18, 'accent')

    def end_panel(self):
        self.parts.append('</g>')

    def development_mark(self, x, y, width):
        mark = ET.parse(ROOT / 'assets/development-mark.svg').getroot()
        scale = width / 120
        self.parts.append(f'<g aria-label="開発 / desenvolvimento" transform="translate({x} {y}) scale({scale:g})">')
        self.parts.append(f'<path d="M0 0H96L120 24V144H0Z" class="mark-outline" fill="none" stroke="{THEMES["dark"]["seal"]}" stroke-width="1.5"/>')
        for node in mark.findall('{http://www.w3.org/2000/svg}path'):
            outline = node.attrib['d']
            if not re.fullmatch(r'[MLCZ0-9.,\s-]+', outline):
                raise DataError('Selo vetorial contém comandos inesperados.')
            self.parts.append(f'<path d="{outline}" class="accent-fill" fill="{THEMES["dark"]["accent"]}"/>')
        self.parts.append('</g>')

    def finish(self) -> str:
        self.parts.append('</svg>')
        return '\n'.join(self.parts) + '\n'


def number(value: int | None) -> str:
    return '—' if value is None else f'{value:,}'.replace(',', '.')


def snapshot_label(data: dict) -> str:
    dates = sorted({data[key]['fetched_on'] for key in ('profile', 'repositories') if data.get(key)})
    return 'snapshot / ' + (' · '.join(dates) if dates else 'indisponível')


def render_stats(svg: Canvas, data: dict, mobile=False):
    x, y, width, height = (0, 680, 440, 204) if mobile else (0, 456, 880, 128)
    svg.panel('stats', x, y, width, height, '$ github --stats', mobile)
    if mobile:
        svg.text(20, y + 54, snapshot_label(data), 12, 'muted')
    else:
        svg.text(width - 24, y + 28, snapshot_label(data), 12, 'muted', 'end')
    profile, repos = data.get('profile') or {}, data.get('repositories') or {}
    metrics = (
        ('Repos públicos', profile.get('public_repos')), ('Followers', profile.get('followers')),
        ('Following', profile.get('following')), ('Stars recebidas', repos.get('received_stars')),
    )
    for i, (label, value) in enumerate(metrics):
        if mobile:
            left = 20 + (i % 2) * 208
            top = y + 96 + (i // 2) * 66
        else:
            left, top = 24 + i * 208, y + 80
        svg.text(left, top, number(value), 32, 'strong')
        svg.text(left, top + 26, label, 17 if mobile else 14, 'muted')
        if not mobile and i:
            svg.line(left - 16, y + 54, left - 16, y + height - 16)
    if mobile:
        svg.line(20, y + 134, width - 20, y + 134)
    svg.end_panel()


def calendar_weeks(calendar: dict) -> list[list[dict]]:
    weeks: dict[date, list[dict]] = {}
    for day in calendar['days']:
        current = date.fromisoformat(day['date'])
        sunday = current - timedelta(days=(current.weekday() + 1) % 7)
        weeks.setdefault(sunday, []).append(day)
    return [weeks[key] for key in sorted(weeks)]


def draw_weeks(svg: Canvas, weeks: list[list[dict]], x: int, y: int, pitch: float, cell: float, label_size: int):
    for row, label in ((1, 'Seg'), (3, 'Qua'), (5, 'Sex')):
        svg.text(x - 12, y + row * pitch + cell, label, label_size, 'muted', 'end')
    previous_month, last_label = None, -100
    for column, week in enumerate(weeks):
        for day in week:
            current = date.fromisoformat(day['date'])
            if current.month != previous_month:
                if column - last_label >= 3 and column < len(weeks) - 2:
                    svg.text(x + column * pitch, y - 12, MONTHS[current.month - 1], label_size, 'muted')
                    last_label = column
                previous_month = current.month
            row = (current.weekday() + 1) % 7
            svg.parts.append(f'<g><title>{day["date"]}: {day["count"]} contribuições</title>')
            delay = round(WAVE_START_MS + column * WAVE_SPAN_MS / max(1, len(weeks) - 1)) + row * WAVE_ROW_STAGGER_MS
            svg.rect(x + column * pitch, y + row * pitch, cell, cell, fill=LEVELS[day['level']], radius=1,
                     style=f'contribution-cell level-{day["level"]}', delay=delay)
            svg.parts.append('</g>')


def render_contributions(svg: Canvas, data: dict, mobile=False):
    x, y, width, height = (0, 296, 440, 368) if mobile else (0, 216, 880, 224)
    svg.panel('contributions', x, y, width, height, 'Contribuições', mobile)
    calendar = data.get('contributions')
    if not calendar:
        svg.text(20 if mobile else 24, y + 80, 'Calendário indisponível.', 18, 'muted')
        svg.text(20 if mobile else 24, y + 108, 'Atualização via GitHub API.', 15, 'muted')
        svg.end_panel()
        return
    period = f'{calendar["from"]} / {calendar["to"]}'
    if mobile:
        svg.text(20, y + 54, period, 14, 'muted')
    else:
        svg.text(width - 24, y + 28, period, 13, 'muted', 'end')
    weeks = calendar_weeks(calendar)
    if mobile:
        split = (len(weeks) + 1) // 2
        draw_weeks(svg, weeks[:split], 60, 386, 13.3, 10, 14)
        draw_weeks(svg, weeks[split:], 60, 524, 13.3, 10, 14)
    else:
        pitch = min(15, (width - 84) / len(weeks))
        draw_weeks(svg, weeks, 60, 308, pitch, min(12, pitch - 3), 12)
    baseline = y + height - (18 if mobile else 12)
    svg.text(20 if mobile else 24, baseline, f'{number(calendar["total"])} contribuições', 18 if mobile else 16, 'strong')
    legend_x = width - (126 if mobile else 146)
    svg.text(legend_x - 10, baseline, 'menos', 11, 'muted', 'end')
    for level, color in enumerate(LEVELS):
        svg.rect(legend_x + level * 13, baseline - 10, 10, 10, fill=color, radius=1, style=f'level-{level}')
    svg.text(legend_x + 72, baseline, 'mais', 11, 'muted')
    svg.end_panel()


def render_stack(svg: Canvas, config: dict, mobile=False):
    x, y, width, height = (0, 900, 440, 284) if mobile else (0, 600, 880, 164)
    svg.panel('stack', x, y, width, height, '$ cat stack.json', mobile)
    if mobile:
        svg.text(20, y + 58, 'Languages', 15, 'muted')
        svg.text(20, y + 86, ' / '.join(config['languages'][:3]), 20)
        svg.text(20, y + 112, ' / '.join(config['languages'][3:]), 20)
        svg.text(20, y + 146, 'Backend + data', 15, 'muted')
        svg.text(20, y + 174, ' / '.join(config['backend']), 20)
        svg.text(20, y + 200, ' / '.join(config['data']), 20)
        svg.text(20, y + 234, 'Tools', 15, 'muted')
        svg.text(20, y + 264, ' / '.join(config['tools']), 18)
    else:
        rows = (
            ('languages', config['languages']),
            ('backend', config['backend'] + [config['data'][0]]),
            ('data', config['data'][1:]),
            ('tools', config['tools']),
        )
        for i, (label, values) in enumerate(rows):
            baseline = y + 66 + i * 26
            svg.text(24, baseline, label, 14, 'muted')
            svg.text(176, baseline, ' / '.join(values), 17)
    svg.end_panel()


def render_svg(config: dict, data: dict, avatar: bytes | None, mobile=False) -> str:
    # The GitHub page already provides the profile sidebar and the real avatar.
    svg = Canvas(440 if mobile else 880, 1224 if mobile else 808, config)
    svg.group('profile', 0, 0, svg.width, 280 if mobile else 204)
    svg.rect(0, 16, 3, 248 if mobile else 172, fill=THEMES['dark']['accent'], style='accent-fill')
    if mobile:
        svg.text(20, 60, config['name'][0], 46, 'display')
        svg.text(20, 106, config['name'][1], 46, 'display')
        svg.development_mark(344, 18, 76)
        svg.text(20, 148, 'Engenharia de Software @ FIAP', 18, 'muted')
        svg.text(20, 180, config['role'][0], 18)
        svg.text(20, 206, config['role'][1], 18)
        svg.text(20, 238, config['building'], 17)
        svg.text(20, 264, ' / '.join(config['focus']), 18, 'pink')
    else:
        svg.text(24, 68, ' '.join(config['name']), 54, 'display')
        svg.development_mark(744, 20, 104)
        svg.text(24, 102, 'Engenharia de Software @ FIAP', 17, 'muted')
        svg.text(24, 130, ' '.join(config['role']), 17)
        svg.text(24, 162, config['building'], 17)
        svg.text(24, 190, ' / '.join(config['focus']), 17, 'pink')
    svg.end_panel()
    svg.line(20 if mobile else 24, 280 if mobile else 204, svg.width - (20 if mobile else 24), 280 if mobile else 204)

    render_contributions(svg, data, mobile)
    render_stats(svg, data, mobile)
    svg.line(20 if mobile else 24, 884 if mobile else 584, svg.width - (20 if mobile else 24), 884 if mobile else 584)
    render_stack(svg, config, mobile)
    calendar = data.get('contributions')
    footer = 'GitHub / ' + (f'calendário consultado em {calendar["fetched_on"]}' if calendar else 'calendário indisponível')
    svg.text(20 if mobile else 24, 1208 if mobile else 792, footer, 12, 'muted')
    return svg.finish()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--offline", action="store_true", help="Reproduz os SVGs usando o snapshot versionado, sem rede.")
    modes.add_argument("--public", action="store_true", help="Bootstrap local sem token usando dados públicos do próprio GitHub.")
    parser.add_argument("--check", action="store_true", help="Com --offline, verifica se os SVGs estão atualizados, sem escrever arquivos.")
    parser.add_argument("--date", type=date.fromisoformat, help="Último dia do calendário, YYYY-MM-DD (padrão: hoje em São Paulo).")
    parser.add_argument("--output-dir", type=Path, default=ROOT / "assets", help="Diretório de assets e snapshot.")
    args = parser.parse_args(argv)
    if args.check and not args.offline:
        parser.error("--check exige --offline.")
    if args.date and args.offline:
        parser.error("--offline usa o período do snapshot; não combine com --date.")
    token = os.environ.get("GITHUB_TOKEN")
    if not args.offline and not args.public and not token:
        print("Erro: GITHUB_TOKEN ausente. Defina a variável para consultar a API, use --offline para reproduzir o snapshot ou --public para um bootstrap público local.", file=sys.stderr)
        return 2
    try:
        config = json.loads((ROOT / "scripts/profile.json").read_text(encoding="utf-8"))
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9-]{0,38}", config["login"]):
            raise DataError("Login do GitHub inválido.")
        assets = args.output_dir
        snapshot_path = assets / "profile-data.json"
        cached = load_snapshot(snapshot_path, config["login"])
        if args.offline and not snapshot_path.exists():
            raise DataError("Snapshot ausente. Execute primeiro com GITHUB_TOKEN ou --public.")
        if args.offline:
            data = cached
        else:
            data = refresh_snapshot(GitHubClient(None if args.public else token), config, cached, args.date or today(), args.public)
            try:
                refresh_avatar(data.get("profile"), assets)
            except DataError as error:
                print(f"Aviso [avatar]: {error}", file=sys.stderr)
        avatar_path = assets / "avatar.jpg"
        avatar = avatar_path.read_bytes() if avatar_path.exists() else None
        outputs = {"dashboard.svg": render_svg(config, data, avatar), "dashboard-mobile.svg": render_svg(config, data, avatar, mobile=True)}
        for content in outputs.values():
            ET.fromstring(content)
        if args.check:
            outdated = [name for name, content in outputs.items() if not (assets / name).exists() or (assets / name).read_text(encoding="utf-8") != content]
            if outdated:
                raise DataError("SVGs desatualizados: " + ", ".join(outdated))
            print("SVGs válidos e reproduzíveis a partir do snapshot.")
        else:
            write_if_changed(snapshot_path, (json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8"))
            changed = [name for name, content in outputs.items() if write_if_changed(assets / name, content.encode("utf-8"))]
            print("Atualizados: " + ", ".join(changed) if changed else "Dashboard sem alterações.")
        return 0
    except (DataError, ValueError, KeyError, TypeError, OSError, ET.ParseError) as error:
        reason = str(error) if isinstance(error, DataError) else "Arquivo local ou configuração inválida; confira os caminhos e o JSON."
        print(f"Erro: {reason}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
