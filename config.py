"""Shared config loader, paths and run flags. No secrets here — those come from env vars."""
from __future__ import annotations

import logging
import os
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import yaml
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent
REAL_FIXTURES_DIR = ROOT / "fixtures"
SAMPLE_FIXTURES_DIR = ROOT / "tests" / "sample_fixtures"
MANUAL_DIR = ROOT / "manual_inputs"
OUTPUT_DIR = ROOT / "output"
IMAGES_DIR = OUTPUT_DIR / "images"
TEMPLATES_DIR = ROOT / "templates"
ASSETS_DIR = ROOT / "assets"
FONTS_DIR = ASSETS_DIR / "fonts"

load_dotenv(ROOT / ".env")
os.environ.setdefault("PYDANTIC_AI_NO_BANNER", "1")

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S")
# Keep libraries quiet: HTTP debug lines can contain URLs with keys; WeasyPrint/fontTools are very verbose.
for _name in ("httpx", "urllib3", "openai", "httpcore", "fontTools", "weasyprint", "matplotlib"):
    logging.getLogger(_name).setLevel(logging.WARNING)


@lru_cache(maxsize=1)
def load_config() -> dict[str, Any]:
    with open(ROOT / "config.yaml", encoding="utf-8") as f:
        return yaml.safe_load(f)


def tz() -> ZoneInfo:
    return ZoneInfo(load_config()["timezone"])


def now_cairo() -> datetime:
    return datetime.now(tz())


def fixtures_mode() -> str | None:
    """USE_FIXTURES=1 -> real saved responses in fixtures/; USE_FIXTURES=sample -> synthetic test data."""
    v = (os.getenv("USE_FIXTURES") or "").strip().lower()
    if v in ("1", "true", "yes"):
        return "real"
    if v == "sample":
        return "sample"
    return None


def fixtures_dir() -> Path:
    return SAMPLE_FIXTURES_DIR if fixtures_mode() == "sample" else REAL_FIXTURES_DIR


def simulate_fail() -> str | None:
    """SIMULATE_FAIL=ads|trends|llm (demo of failure handling)."""
    v = (os.getenv("SIMULATE_FAIL") or "").strip().lower()
    return v or None
