#!/usr/bin/env python3
"""Render review PNGs, including a bounded GitHub README simulation.

Optional local tool: needs system PyGObject/GdkPixbuf.
Generated PNGs go to assets/previews/, which is ignored by Git.
Never imported by the generator or used in the GitHub Actions workflow.
"""

from __future__ import annotations

from html import escape
from html.parser import HTMLParser
from pathlib import Path
import re
import xml.etree.ElementTree as ET

import gi
from generate_dashboard import svg_styles

gi.require_version('GdkPixbuf', '2.0')
from gi.repository import GdkPixbuf

ROOT = Path(__file__).resolve().parents[1]
SVG_NS = 'http://www.w3.org/2000/svg'
ET.register_namespace('', SVG_NS)


def text(x, y, value, size=16, color='#e6edf3', bold=False, mono=False):
    family = 'Liberation Mono,monospace' if mono else 'Noto Sans,Arial,sans-serif'
    return f'<text x="{x:g}" y="{y:g}" font-family="{family}" font-size="{size}" fill="{color}" font-weight="{700 if bold else 400}">{escape(value)}</text>'


class ReadmeImages(HTMLParser):
    """Read the real README's image order, responsive sources and block breaks."""

    def __init__(self, mobile=False):
        super().__init__()
        self.mobile, self.block, self.source, self.images = mobile, 0, None, []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == 'div':
            self.block += 1
        elif tag == 'picture':
            self.source = None
        elif tag == 'source' and self.mobile:
            self.source = attrs['srcset']
        elif tag == 'img':
            self.images.append((self.source or attrs['src'], attrs.get('width') == '100%', self.block))
            self.source = None


def static_dashboard(mobile=False, theme='dark') -> ET.Element:
    return static_svg('assets/dashboard-mobile.svg' if mobile else 'assets/dashboard.svg', theme)


def static_svg(source: str, theme='dark') -> ET.Element:
    dashboard = ET.parse(ROOT / source).getroot()
    # PNG is a frozen review frame: fix the requested theme and show the full grid.
    dashboard.find(f'{{{SVG_NS}}}style').text = svg_styles(theme=theme, animate=False)
    return dashboard


def readme_preview(width: int, mobile=False, theme='dark') -> str:
    colors = {
        'dark': {'background':'#0d1117', 'border':'#3d444d', 'text':'#e6edf3'},
        'light': {'background':'#ffffff', 'border':'#d1d9e0', 'text':'#24292f'},
    }[theme]
    padding = 16 if mobile else 24
    available = width - padding * 2
    parser = ReadmeImages(mobile)
    parser.feed((ROOT / 'README.md').read_text())
    fragments = [text(padding, 34, 'BrThiagoN / README.md', 12, color=colors['text'], mono=True)]
    left, top, line_height, previous_block = padding, 64, 0, None
    for index, (source, full_width, block) in enumerate(parser.images):
        document = static_svg(source, theme)
        image_id = f'preview-image-{index}'
        document.set('id', image_id)
        style = document.find(f'{{{SVG_NS}}}style')
        # External images isolate CSS. Scope every nested SVG independently.
        style.text = re.sub(r'(^|})([^{}]+)\{', lambda match: match[1] + ','.join(f'#{image_id} ' + selector.strip() for selector in match[2].split(',')) + '{', style.text)
        intrinsic_width, intrinsic_height = map(float, (document.attrib['width'], document.attrib['height']))
        image_width = available if full_width else min(available, intrinsic_width)
        image_height = intrinsic_height * image_width / intrinsic_width
        if (previous_block is not None and block != previous_block) or left + image_width > width - padding:
            top += line_height + 4
            left, line_height = padding, 0
        document.set('x', str(left))
        document.set('y', str(top))
        document.set('width', str(image_width))
        document.set('height', str(image_height))
        fragments.append(ET.tostring(document, encoding='unicode'))
        line_height = max(line_height, image_height)
        left += image_width + 4
        previous_block = block
    height = round(top + line_height + 24)
    root = f'<svg xmlns="{SVG_NS}" width="{width}" height="{height}" viewBox="0 0 {width} {height}">'
    background = f'<rect x="0.5" y="0.5" width="{width - 1}" height="{height - 1}" rx="6" fill="{colors["background"]}" stroke="{colors["border"]}"/>'
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
    for theme in ('dark', 'light'):
        suffix = '-light' if theme == 'light' else ''
        for variant, width in (('desktop', 846), ('mobile', 336)):
            document = ET.tostring(static_dashboard(mobile=variant == 'mobile', theme=theme))
            write_png(document, output / f'dashboard-{variant}{suffix}.png', width)
        for variant, width in (('desktop', 896), ('mobile', 390)):
            document = readme_preview(width, mobile=variant == 'mobile', theme=theme)
            ET.fromstring(document)
            write_png(document.encode('utf-8'), output / f'readme-{variant}{suffix}.png')


if __name__ == '__main__':
    main()
