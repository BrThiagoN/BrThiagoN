#!/usr/bin/env python3
"""Generate self-contained GitHub profile SVGs using only the standard library."""

from __future__ import annotations

import argparse
import base64
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
LEVELS = ("#1d2732", "#0e4429", "#006d32", "#26a641", "#39d353")
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


class Canvas:
    """Small SVG writer; data never becomes unescaped SVG markup."""

    def __init__(self, width: int, height: int, config: dict):
        self.width, self.height = width, height
        name = " ".join(config["name"])
        description = f'{name}, estudante de Engenharia de Software na FIAP e estagiário de Produto e Desenvolvimento. Backend, cloud e IA aplicada. Estudando: {config["learning"]}. Prática: {config["practice"]}. Métricas e calendário com datas de snapshot do GitHub. Stack declarada, sem níveis de habilidade.'
        self.parts = [
            f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}" role="img" aria-labelledby="title description">',
            f'<title id="title">{escape(name)} · engineering dashboard</title>',
            f'<desc id="description">{escape(description)}</desc>',
            '<style>text{font-family:ui-monospace,SFMono-Regular,Menlo,Monaco,Consolas,"Liberation Mono","Courier New",monospace;fill:#c9d1d9} .strong{fill:#f0f6fc;font-weight:700} .muted{fill:#9da7b3} .accent{fill:#3fb950} .label{fill:#9da7b3;letter-spacing:1.2px}</style>',
        ]
        self.rect(0.5, 0.5, width - 1, height - 1, fill="#0d1117", radius=16, stroke="#30363d")

    def rect(self, x, y, width, height, fill="#10161e", radius=8, stroke="none"):
        self.parts.append(f'<rect x="{x:g}" y="{y:g}" width="{width:g}" height="{height:g}" rx="{radius:g}" fill="{fill}" stroke="{stroke}"/>')

    def text(self, x, y, value, size=16, style="", anchor="start"):
        self.parts.append(f'<text x="{x:g}" y="{y:g}" font-size="{size:g}" class="{style}" text-anchor="{anchor}">{escape(str(value))}</text>')

    def line(self, x1, y1, x2, y2):
        self.parts.append(f'<path d="M{x1:g} {y1:g}H{x2:g}" stroke="#26303b"/>' if y1 == y2 else f'<path d="M{x1:g} {y1:g}L{x2:g} {y2:g}" stroke="#26303b"/>')

    def panel(self, name, x, y, width, height, title, number, mobile=False):
        self.parts.append(f'<g data-panel="{name}" data-bounds="{x} {y} {width} {height}">')
        self.rect(x, y, width, height, stroke="#26303b")
        self.text(x + 24, y + 32, title, 20 if mobile else 16, "accent")
        self.text(x + width - 24, y + 32, number, 12, "muted", "end")
        self.line(x, y + 48, x + width, y + 48)

    def end_panel(self):
        self.parts.append("</g>")

    def avatar(self, x, y, size, content: bytes | None):
        self.rect(x - 2, y - 2, size + 4, size + 4, fill="#26303b", radius=(size + 4) / 2)
        if content:
            encoded = base64.b64encode(content).decode("ascii")
            self.parts.append(f'<defs><clipPath id="avatar-clip"><circle cx="{x + size / 2:g}" cy="{y + size / 2:g}" r="{size / 2:g}"/></clipPath></defs>')
            self.parts.append(f'<image x="{x}" y="{y}" width="{size}" height="{size}" href="data:image/jpeg;base64,{encoded}" clip-path="url(#avatar-clip)"/>')
        else:
            self.text(x + size / 2, y + size / 2 + 10, "TN", 28, "strong", "middle")

    def finish(self) -> str:
        self.parts.append("</svg>")
        return "\n".join(self.parts) + "\n"


def number(value: int | None) -> str:
    return "—" if value is None else f"{value:,}".replace(",", ".")


def snapshot_label(data: dict) -> str:
    dates = sorted({data[key]["fetched_on"] for key in ("profile", "repositories") if data.get(key)})
    return "snapshot / " + (" · ".join(dates) if dates else "indisponível")


def render_stats(svg: Canvas, data: dict, x: int, y: int, width: int, mobile=False):
    svg.panel("stats", x, y, width, 232 if mobile else 204, "$ github --stats", "03", mobile)
    svg.text(x + 24, y + 72, snapshot_label(data), 15 if mobile else 10, "muted")
    profile, repos = data.get("profile") or {}, data.get("repositories") or {}
    metrics = (
        ("repos públicos", profile.get("public_repos")), ("followers", profile.get("followers")),
        ("following", profile.get("following")), ("stars recebidas", repos.get("received_stars")),
    )
    cell_width = (width - 64) / 2
    for i, (label, value) in enumerate(metrics):
        left, top = x + 24 + (i % 2) * (cell_width + 16), y + 88 + (i // 2) * (72 if mobile else 56)
        svg.rect(left, top - 8, cell_width, 56 if mobile else 48, fill="#161b22", radius=4)
        svg.text(left + 12, top + (18 if mobile else 15), number(value), 30 if mobile else 26, "strong" if i != 3 else "accent")
        svg.text(left + 12, top + (40 if mobile else 33), label, 18 if mobile else 11, "muted")
    svg.end_panel()


def calendar_weeks(calendar: dict) -> list[list[dict]]:
    weeks: dict[date, list[dict]] = {}
    for day in calendar["days"]:
        current = date.fromisoformat(day["date"])
        sunday = current - timedelta(days=(current.weekday() + 1) % 7)
        weeks.setdefault(sunday, []).append(day)
    return [weeks[key] for key in sorted(weeks)]


def draw_weeks(svg: Canvas, weeks: list[list[dict]], x: int, y: int, pitch: float, cell: float, label_size: int):
    for row, label in ((1, "Seg"), (3, "Qua"), (5, "Sex")):
        svg.text(x - 12, y + row * pitch + cell, label, label_size, "muted", "end")
    previous_month, last_label = None, -100
    for column, week in enumerate(weeks):
        for day in week:
            current = date.fromisoformat(day["date"])
            if current.month != previous_month:
                if column - last_label >= 3 and column < len(weeks) - 2:
                    svg.text(x + column * pitch, y - 12, MONTHS[current.month - 1], label_size, "muted")
                    last_label = column
                previous_month = current.month
            row = (current.weekday() + 1) % 7
            svg.parts.append(f'<g><title>{day["date"]}: {day["count"]} contribuições</title>')
            svg.rect(x + column * pitch, y + row * pitch, cell, cell, fill=LEVELS[day["level"]], radius=2)
            svg.parts.append("</g>")


def render_contributions(svg: Canvas, data: dict, mobile=False):
    x, y, width, height = (16, 312, 488, 432) if mobile else (288, 76, 808, 244)
    svg.panel("contributions", x, y, width, height, "$ ./contributions.sh", "01", mobile)
    calendar = data.get("contributions")
    if not calendar:
        svg.text(x + 24, y + 100, "Calendário indisponível.", 18 if mobile else 16, "muted")
        svg.text(x + 24, y + 128, "Atualização automática via GitHub API.", 14, "muted")
        svg.end_panel()
        return
    svg.text(x + 24, y + 72, f'{calendar["from"]} → {calendar["to"]}', 17 if mobile else 12, "muted")
    weeks = calendar_weeks(calendar)
    if mobile:
        split = (len(weeks) + 1) // 2
        draw_weeks(svg, weeks[:split], 72, 430, 15.5, 12, 16)
        draw_weeks(svg, weeks[split:], 72, 590, 15.5, 12, 16)
    else:
        pitch = min(14, (width - 76) / len(weeks))
        draw_weeks(svg, weeks, 340, 180, pitch, min(11, pitch - 3), 11)
    svg.text(x + 24, y + height - 22, f'{number(calendar["total"])} contribuições', 20 if mobile else 15, "strong")
    legend_x = x + width - (144 if mobile else 168)
    svg.text(legend_x - 10, y + height - 22, "menos", 11, "muted", "end")
    for level, color in enumerate(LEVELS):
        svg.rect(legend_x + level * 14, y + height - 33, 10, 10, fill=color, radius=2)
    svg.text(legend_x + 78, y + height - 22, "mais", 11, "muted")
    svg.end_panel()


def render_stack(svg: Canvas, config: dict, mobile=False):
    x, y, width, height = (16, 1228, 488, 264) if mobile else (288, 556, 808, 176)
    svg.panel("stack", x, y, width, height, "$ cat stack.json", "04", mobile)
    left, right = x + 24, x + (24 if mobile else 408)
    label_y = y + 72
    svg.text(left, label_y, "LANGUAGES", 16 if mobile else 11, "label")
    svg.text(left, label_y + 26, " · ".join(config["languages"][:3]), 20 if mobile else 16)
    svg.text(left, label_y + 52, " · ".join(config["languages"][3:]), 20 if mobile else 16)
    backend_y = label_y + 82 if mobile else label_y
    svg.text(right, backend_y, "BACKEND / DATA", 16 if mobile else 11, "label")
    svg.text(right, backend_y + 26, " · ".join(config["backend"]), 20 if mobile else 16)
    svg.text(right, backend_y + 52, " · ".join(config["data"]), 20 if mobile else 16)
    tools_y = y + height - 22
    svg.text(left, tools_y, "TOOLS", 16 if mobile else 11, "label")
    svg.text(left + (80 if mobile else 64), tools_y, " · ".join(config["tools"]), 18 if mobile else 14)
    svg.end_panel()


def render_svg(config: dict, data: dict, avatar: bytes | None, mobile=False) -> str:
    svg = Canvas(520 if mobile else 1120, 1536 if mobile else 776, config)
    svg.text(24 if mobile else 32, 34, "thiago@github:~$ ./profile", 18 if mobile else 17, "accent")
    if not mobile:
        svg.text(1088, 34, "ENGINEERING / PROFILE", 12, "muted", "end")
    svg.line(0, 56, svg.width, 56)

    if mobile:
        svg.parts.append('<g data-panel="profile" data-bounds="16 72 488 224">')
        svg.rect(16, 72, 488, 224, stroke="#26303b")
        svg.avatar(40, 100, 88, avatar)
        svg.text(152, 116, config["name"][0], 28, "strong")
        svg.text(152, 150, config["name"][1], 28, "strong")
        svg.text(152, 178, "@" + config["login"], 17, "muted")
        svg.text(40, 222, "Engenharia de Software @ FIAP", 20)
        svg.text(40, 250, "Backend · Cloud · IA aplicada", 20, "accent")
        svg.text(40, 278, config["location"], 16, "muted")
    else:
        svg.parts.append('<g data-panel="profile" data-bounds="24 76 248 656">')
        svg.rect(24, 76, 248, 656, stroke="#26303b")
        svg.text(48, 106, "00 / PROFILE", 11, "label")
        svg.avatar(48, 128, 112, avatar)
        svg.text(48, 284, config["name"][0], 28, "strong")
        svg.text(48, 316, config["name"][1], 28, "strong")
        svg.text(48, 346, "@" + config["login"], 15, "muted")
        svg.text(48, 390, config["education"][0], 14)
        svg.text(48, 414, config["education"][1], 14, "muted")
        svg.line(48, 438, 248, 438)
        svg.text(48, 470, config["focus"][0], 18, "accent")
        svg.text(48, 498, config["focus"][1], 18, "accent")
        svg.text(48, 534, config["location"], 14, "muted")
        svg.line(48, 558, 248, 558)
        svg.text(48, 586, "CONTACT / LINKS", 11, "label")
        svg.text(48, 616, "github / " + config["login"], 13)
        svg.text(48, 644, "linkedin / thiagonascimento08", 11)
        svg.text(48, 672, config["contact"]["email"], 13)
        svg.text(48, 700, "links no README ↓", 11, "muted")
    svg.end_panel()

    render_contributions(svg, data, mobile)
    x, y, width = (16, 760, 488) if mobile else (288, 336, 416)
    svg.panel("whoami", x, y, width, 204, "$ whoami", "02", mobile)
    svg.text(x + 24, y + 80, config["role"][0], 22 if mobile else 18, "strong")
    svg.text(x + 24, y + 106, config["role"][1], 22 if mobile else 18, "strong")
    svg.text(x + 24, y + 146, config["building"], 19 if mobile else 15)
    if mobile:
        svg.text(x + 24, y + 178, "Testes · Arquitetura · " + config["practice"], 17, "muted")
    else:
        svg.text(x + 24, y + 177, "testes · arquitetura / " + config["practice"].lower(), 13, "muted")
    svg.end_panel()

    render_stats(svg, data, 16 if mobile else 720, 980 if mobile else 336, 488 if mobile else 376, mobile)
    render_stack(svg, config, mobile)
    calendar = data.get("contributions")
    footer = "GitHub / " + (f'snapshot do calendário {calendar["fetched_on"]}' if calendar else "calendário indisponível")
    svg.text(24 if mobile else 32, 1520 if mobile else 759, footer, 12 if mobile else 11, "muted")
    if not mobile:
        svg.text(1088, 759, "backend · linux · applied ai", 11, "muted", "end")
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
