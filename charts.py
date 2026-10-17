"""Line chart of relative interest (0–100) for the Arabic terms, in two themes: dark (screen) and light (print/PDF).

Arabic shaping: matplotlib >= 3.11 shapes Arabic natively (connected letters, right-to-left). Running
arabic-reshaper + python-bidi on top of that scrambles the text, so they are only applied on older versions.
"""
from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.dates as mdates  # noqa: E402
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib import font_manager  # noqa: E402

from collectors.trends import as_number  # noqa: E402
from config import FONTS_DIR, IMAGES_DIR, load_config  # noqa: E402

log = logging.getLogger(__name__)
_MPL = tuple(int(x) for x in matplotlib.__version__.split(".")[:2])
NATIVE_ARABIC = _MPL >= (3, 11)

THEMES = {
    "dark": {"bg": "#161616", "fg": "#f5f5f2", "muted": "#a3a39c", "grid": "#262626", "accent": "#ffd100",
             "others": ["#f5f5f2", "#a3a39c", "#6b6b66", "#4a4a46"]},
    "light": {"bg": "#ffffff", "fg": "#111111", "muted": "#5c5c57", "grid": "#e4e4e0", "accent": "#e6b800",
              "others": ["#111111", "#7a7a74", "#a3a39c", "#c8c8c2"]},
}


def _register_fonts() -> str:
    for f in FONTS_DIR.glob("*.ttf"):
        font_manager.fontManager.addfont(str(f))
    return "IBM Plex Sans Arabic"


def ar(text: str) -> str:
    if NATIVE_ARABIC:
        return text
    import arabic_reshaper
    from bidi.algorithm import get_display
    return get_display(arabic_reshaper.reshape(text))


def _draw(dates: list[str], series: dict[str, list[str]], main_term: str, greyed: set[str],
          theme: dict, family: str, out: Path) -> None:
    x = [datetime.strptime(d, "%Y-%m-%d") for d in dates]
    fig, axis = plt.subplots(figsize=(4.6, 2.05), dpi=220)
    fig.patch.set_facecolor(theme["bg"])
    axis.set_facecolor(theme["bg"])
    others = iter(theme["others"])
    ordered = [t for t in series if t != main_term] + ([main_term] if main_term in series else [])
    for term in ordered:
        y = [as_number(v) for v in series[term]]          # "<1" -> None (gap), never a number
        y = [float("nan") if v is None else v for v in y]
        is_main = term == main_term
        color = theme["accent"] if is_main else next(others, theme["muted"])
        label = ("low data: " if term in greyed else "") + ar(term)
        axis.plot(x, y, color=color, linewidth=2.0 if is_main else 1.0,
                  alpha=0.35 if term in greyed else 1.0, linestyle=":" if term in greyed else "-",
                  label=label, zorder=3 if is_main else 2)
    axis.set_ylim(0, 105)
    axis.set_yticks([0, 50, 100])
    axis.xaxis.set_major_locator(mdates.MonthLocator())
    axis.xaxis.set_major_formatter(mdates.DateFormatter("%b"))
    for side in ("top", "right", "left"):
        axis.spines[side].set_visible(False)
    axis.spines["bottom"].set_color(theme["grid"])
    axis.grid(axis="y", color=theme["grid"], linewidth=0.6)
    axis.tick_params(colors=theme["muted"], labelsize=6.5, length=0)
    for lbl in axis.get_xticklabels() + axis.get_yticklabels():
        lbl.set_fontfamily("IBM Plex Mono")
    leg = axis.legend(loc="upper left", bbox_to_anchor=(0, 1.2), ncol=len(series), frameon=False,
                      prop={"family": family, "size": 7}, handlelength=1.2, columnspacing=1.0)
    for txt in leg.get_texts():
        txt.set_color(theme["fg"])
    fig.tight_layout(pad=0.3)
    fig.savefig(out, facecolor=theme["bg"])
    plt.close(fig)


def make_charts(trends_data: dict) -> dict[str, str] | None:
    """Returns {'dark': path, 'light': path} relative to repo root, or None if there is nothing to plot."""
    dates, series = trends_data.get("dates"), trends_data.get("series")
    if not dates or not series:
        return None
    chart_terms = load_config()["trends"].get("chart_terms")
    if chart_terms:
        series = {t: v for t, v in series.items() if t in chart_terms} or series
    family = _register_fonts()
    greyed = {t for t, s in (trends_data.get("summary") or {}).items() if s.get("status") != "ok"}
    IMAGES_DIR.mkdir(parents=True, exist_ok=True)
    paths = {}
    for name, theme in THEMES.items():
        out = IMAGES_DIR / f"chart_{name}.png"
        _draw(dates, series, trends_data.get("main_term", ""), greyed, theme, family, out)
        paths[name] = f"output/images/{out.name}"
    log.info(f"[chart] OK, {len(series)} terms (native Arabic shaping: {NATIVE_ARABIC})")
    return paths
