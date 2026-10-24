"""Jinja2 -> one-page HTML (dark, for screen/email) and PDF via WeasyPrint (light, print styles).

If the PDF spills onto a second page, the type and image sizes are stepped down until it fits.
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import re

from jinja2 import Environment, FileSystemLoader, select_autoescape
from markupsafe import Markup, escape

from config import ASSETS_DIR, FONTS_DIR, OUTPUT_DIR, ROOT, TEMPLATES_DIR, load_config

log = logging.getLogger(__name__)
DENSITY = [(9.0, 38.0), (8.6, 32.0), (8.2, 27.0), (7.8, 23.0), (7.4, 19.0)]   # (base font pt, ad image height mm)


def week_label(d: datetime) -> str:
    d = d - timedelta(days=1)   # label the week the data covers: a Monday run reports the week that just ended
    mon = d - timedelta(days=d.weekday())
    sun = mon + timedelta(days=6)
    span = (f"{mon.day}–{sun.day} {sun:%b %Y}" if mon.month == sun.month
            else f"{mon.day} {mon:%b} – {sun.day} {sun:%b %Y}")
    return f"Week {d.isocalendar().week} · {span}"


def _src(rel_path: str | None, mode: str) -> str | None:
    """Repo-relative path -> src usable by the HTML file (in output/) or by WeasyPrint (file URI)."""
    if not rel_path:
        return None
    p = (ROOT / rel_path).resolve()
    if not p.exists():
        return None
    return p.as_uri() if mode == "pdf" else os.path.relpath(p, OUTPUT_DIR)


def _ad_items(state: dict, mode: str) -> list[dict]:
    return [{"id": a["id"], "brand": a.get("competitor") or a.get("page_name"), "start_date": a.get("start_date", ""),
             "fact": a.get("message_fact", ""), "interpretation": a.get("interpretation"),
             "link": a["ad_library_link"], "image": _src(a.get("image_path"), mode)} for a in state["ads"]]


def build_context(state: dict, mode: str, fs: float, img_h: float) -> dict[str, Any]:
    ads_r, tr_r = state["ads_result"], state["trends_result"]
    charts = state.get("charts") or {}
    logo = ASSETS_DIR / "logo.png"
    return {
        "mode": mode, "fs": fs, "img_h": img_h,
        "font_base": FONTS_DIR.resolve().as_uri() if mode == "pdf" else os.path.relpath(FONTS_DIR, OUTPUT_DIR),
        "logo": _src("assets/logo.png", mode) if logo.exists() else None,
        "synthetic": ads_r.synthetic or tr_r.synthetic,
        "brand": load_config()["brand"]["name"],
        "week_label": week_label(state["run_time"]),
        "collected_label": state["run_time"].strftime("%d %b %Y %H:%M"),
        "statuses": [{"name": "Ads", "status": ads_r.status}, {"name": "Trends", "status": tr_r.status},
                     {"name": "AI", "status": state["ai_status"]}],
        "ads": {"status": ads_r.status, "reason": (ads_r.reason or "").rstrip("."),
                "search_url": ads_r.data.get("search_url"), "items": _ad_items(state, mode),
                "note": state.get("ads_note")},
        "trends": {"status": tr_r.status, "reason": (tr_r.reason or "").rstrip("."), "url": tr_r.data.get("trends_url"),
                   "period": tr_r.period or "", "findings": state["findings"],
                   "insufficient": state["insufficient"],
                   "chart_dark": _src(charts.get("dark"), mode), "chart_light": _src(charts.get("light"), mode)},
        "ideas": {"available": state["ideas_available"], "reason": state.get("ideas_reason"),
                  "items": state["ideas"], "note": state.get("ideas_note")},
        "sources_line": state["sources_line"], "cost_line": state["cost_line"],
    }


AR_RUN = re.compile(r"[\u0600-\u06FF]+(?:[\s\u060C]+[\u0600-\u06FF]+)*")


def isolate_arabic(text: Any) -> Markup:
    """Escape, then wrap Arabic runs in <bdi> + left-to-right marks so mixed English/Arabic lines keep their
    word order and brackets (WeasyPrint and many email clients ignore <bdi> isolation on its own)."""
    if text is None:
        return Markup("")
    return Markup(AR_RUN.sub(lambda m: f'\u200e<bdi lang="ar">{m.group(0)}</bdi>\u200e', str(escape(text))))


def _env() -> Environment:
    env = Environment(loader=FileSystemLoader(TEMPLATES_DIR), autoescape=select_autoescape(["html"]))
    env.filters["ar"] = isolate_arabic
    return env


def render_html(state: dict) -> Path:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    html = _env().get_template("brief.html").render(**build_context(state, "html", *DENSITY[0]))
    out = OUTPUT_DIR / "brief.html"
    out.write_text(html, encoding="utf-8")
    log.info(f"[render] HTML saved: {out.relative_to(ROOT)}")
    return out


def render_pdf(state: dict) -> tuple[Path, int]:
    """Returns (path, pages). Steps density down until the brief fits on one A4 page."""
    from weasyprint import HTML  # imported here so HTML output still works if WeasyPrint's system libs are missing

    out = OUTPUT_DIR / "brief.pdf"
    pages = 0
    for level, (fs, img_h) in enumerate(DENSITY):
        html = _env().get_template("brief.html").render(**build_context(state, "pdf", fs, img_h))
        doc = HTML(string=html, base_url=str(ROOT)).render()
        pages = len(doc.pages)
        if pages == 1 or level == len(DENSITY) - 1:
            doc.write_pdf(out)
            break
    if pages > 1:
        log.warning(f"[render] PDF is {pages} pages even at the smallest density")
    log.info(f"[render] PDF saved: {out.relative_to(ROOT)} ({pages} page, density {fs}pt)")
    return out, pages
