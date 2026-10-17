"""Helpers shared by collectors: retries, fixtures, manual CSVs."""
from __future__ import annotations

import csv
import json
import logging
import time
from pathlib import Path
from typing import Any, Callable, TypeVar

import requests

from config import fixtures_dir, load_config, REAL_FIXTURES_DIR

log = logging.getLogger(__name__)
T = TypeVar("T")


class TransientError(Exception):
    """Worth retrying (network error, timeout, HTTP 429/5xx)."""


class PermanentError(Exception):
    """Not worth retrying (bad key, no credit, bad input)."""


def check_response(resp: requests.Response, service: str) -> None:
    """Raise Transient/Permanent errors for non-2xx. Never includes the request URL (may carry keys)."""
    if resp.ok:
        return
    detail = resp.text[:200].replace("\n", " ")
    msg = f"{service} HTTP {resp.status_code}: {detail}"
    if resp.status_code == 429 or resp.status_code >= 500:
        raise TransientError(msg)
    raise PermanentError(msg)


def with_retry(fn: Callable[[], T], label: str) -> T:
    """Run fn; retry TransientError / network errors `attempts` times with exponential backoff."""
    cfg = load_config()["retry"]
    attempts, base = int(cfg["attempts"]), float(cfg["backoff_seconds"])
    for i in range(attempts + 1):
        try:
            return fn()
        except (TransientError, requests.ConnectionError, requests.Timeout) as e:
            if i == attempts:
                raise TransientError(f"{label}: gave up after {attempts + 1} tries ({type(e).__name__}: {str(e)[:150]})") from e
            wait = base * (2 ** i)
            log.info(f"[{label}] temporary error, retry {i + 1}/{attempts} in {wait:.0f}s: {str(e)[:120]}")
            time.sleep(wait)
    raise RuntimeError("unreachable")


def _scrub(obj: Any) -> Any:
    """Remove anything that looks like a credential before writing a fixture."""
    if isinstance(obj, dict):
        return {k: _scrub(v) for k, v in obj.items() if k.lower() not in {"api_key", "token", "apikey"}}
    if isinstance(obj, list):
        return [_scrub(v) for v in obj]
    return obj


def save_fixture(name: str, data: Any) -> None:
    """Save a raw response from a successful real call to fixtures/ (never in fixture mode)."""
    REAL_FIXTURES_DIR.mkdir(parents=True, exist_ok=True)
    path = REAL_FIXTURES_DIR / name
    path.write_text(json.dumps(_scrub(data), ensure_ascii=False, indent=1), encoding="utf-8")


def load_fixture(name: str) -> Any:
    path = fixtures_dir() / name
    if not path.exists():
        raise PermanentError(f"fixture not found: {path.relative_to(path.parents[1])}")
    return json.loads(path.read_text(encoding="utf-8"))


def read_manual_csv(path: Path) -> list[dict[str, str]] | None:
    if not path.exists():
        return None
    with open(path, encoding="utf-8-sig", newline="") as f:
        rows = [{k.strip(): (v or "").strip() for k, v in r.items() if k} for r in csv.DictReader(f)]
    return rows or None
