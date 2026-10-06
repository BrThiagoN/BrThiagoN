#!/usr/bin/env python3
"""Render review PNGs, including a bounded GitHub README simulation.

Optional local tool: needs system PyGObject/GdkPixbuf and pycairo.
Never imported by the generator or used in the GitHub Actions workflow.
"""

from __future__ import annotations

from html import escape
from pathlib import Path
import re
import xml.etree.ElementTree as ET

import cairo
import gi

gi.require_version('GdkPixbuf', '2.0')
from gi.repository import GdkPixbuf

ROOT = Path(__file__).resolve().parents[1]
SVG_NS = 'http://www.w3.org/2000/svg'
ET.register_namespace('', SVG_NS)


def measure(value: str, size: int, bold=False, mono=False) -> float:
    context = cairo.Context(cairo.ImageSurface(cairo.FORMAT_ARGB32, 1, 1))
    context.select_font_face('Liberation Mono' if mono else 'Noto Sans', cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_BOLD if bold else cairo.FONT_WEIGHT_NORMAL)
    context.set_font_size(size)
    return context.text_extents(value).x_advance


def wrap(value: str, size: int, width: float) -> list[str]:
    lines, current = [], ''
    for word in value.split():
        candidate = current + (' ' if current else '') + word
        if current and measure(candidate, size) > width:
            lines.append(current)
            current = word
        else:
            current = candidate
    if current:
        lines.append(current)
    return lines


def text(x, y, value, size=16, color='#e6edf3', bold=False, mono=False):
    family = 'Liberation Mono,monospace' if mono else 'Noto Sans,Arial,sans-serif'
    return f'<text x="{x:g}" y="{y:g}" font-family="{family}" font-size="{size}" fill="{color}" font-weight="{700 if bold else 400}">{escape(value)}</text>'


def projects_from_readme() -> list[tuple[str, str, str]]:
    lines = (ROOT / 'README.md').read_text().splitlines()
    projects = []
    for index, line in enumerate(lines):
        match = re.match(r'- \*\*\[(.+?)\]\([^)]+\)\*\* · (.+)', line)
        if match:
            projects.append((match[1], match[2].replace('`', '').rstrip('\\').strip(), lines[index + 1].strip()))
    return projects


def readme_preview(width: int, mobile=False) -> str:
    padding = 16 if mobile else 24
    available = width - padding * 2
    dashboard = ET.parse(ROOT / 'assets' / ('dashboard-mobile.svg' if mobile else 'dashboard.svg')).getroot()
    dashboard.set('id', 'preview-dashboard')
    style = dashboard.find(f'{{{SVG_NS}}}style')
    # An actual README loads the SVG as an image, isolating its CSS from Markdown.
    # Scope the nested SVG styles to reproduce that isolation in this simulation.
    style.text = re.sub(r'(^|})([^{}]+)\{', lambda match: match[1] + '#preview-dashboard ' + match[2] + '{', style.text)
    intrinsic_width, intrinsic_height = map(float, (dashboard.attrib['width'], dashboard.attrib['height']))
    dashboard.set('x', str(padding))
    dashboard.set('y', '64')
    dashboard.set('width', str(available))
    dashboard.set('height', str(intrinsic_height * available / intrinsic_width))
    dashboard.attrib.pop('role', None)
    fragments = [text(padding, 34, 'BrThiagoN / README.md', 12, mono=True), ET.tostring(dashboard, encoding='unicode')]
    y = 64 + intrinsic_height * available / intrinsic_width + 44
    fragments.append(text(padding, y, '$ ls ~/featured-projects', 18, bold=True, mono=True))
    y += 38
    left = padding + 20
    prose_size = 14 if mobile else 16
    for name, stack, description in projects_from_readme():
        fragments.append(text(padding, y, '•', prose_size, color='#9198a1'))
        fragments.append(text(left, y, name, prose_size, color='#4493f8', bold=True))
        name_width = measure(name, prose_size, bold=True)
        stack_width = measure(' · ' + stack, 13, mono=True)
        if name_width + stack_width > available - 20:
            y += 23
            fragments.append(text(left, y, stack, 13, color='#c9d1d9', mono=True))
        else:
            fragments.append(text(left + name_width + 8, y, '· ' + stack, 13, color='#c9d1d9', mono=True))
        for line in wrap(description, prose_size, available - 20):
            y += 23
            fragments.append(text(left, y, line, prose_size))
        y += 34
    y += 10
    fragments.append(text(padding, y, '$ contact', 18, bold=True, mono=True))
    y += 32
    contact = 'LinkedIn · thiagotgn6@gmail.com · GitHub'
    for line in wrap(contact, prose_size, available):
        fragments.append(text(padding, y, line, prose_size, color='#4493f8'))
        y += 23
    height = round(y + 16)
    root = f'<svg xmlns="{SVG_NS}" width="{width}" height="{height}" viewBox="0 0 {width} {height}">'
    background = f'<rect x="0.5" y="0.5" width="{width - 1}" height="{height - 1}" rx="6" fill="#0d1117" stroke="#3d444d"/>'
    return root + background + ''.join(fragments) + '</svg>'


def write_png(content: bytes, path: Path, width: int | None = None):
    loader = GdkPixbuf.PixbufLoader.new_with_type('svg')
    loader.write(content)
    loader.close()
    image = loader.get_pixbuf()
    if width and image.get_width() != width:
        height = round(image.get_height() * width / image.get_width())
        image = image.scale_simple(width, height, GdkPixbuf.InterpType.BILINEAR)
    image.savev(str(path), 'png', [], [])
    print(f'{path.relative_to(ROOT)}: {image.get_width()} × {image.get_height()}')


def main():
    output = ROOT / 'assets/previews'
    output.mkdir(exist_ok=True)
    for variant, width in (('desktop', 846), ('mobile', 336)):
        source = ROOT / 'assets' / ('dashboard-mobile.svg' if variant == 'mobile' else 'dashboard.svg')
        write_png(source.read_bytes(), output / f'dashboard-{variant}.png', width)
    for variant, width in (('desktop', 896), ('mobile', 390)):
        document = readme_preview(width, mobile=variant == 'mobile')
        ET.fromstring(document)
        write_png(document.encode('utf-8'), output / f'readme-{variant}.png')


if __name__ == '__main__':
    main()
