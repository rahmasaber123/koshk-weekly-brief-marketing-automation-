"""Google Trends for Egypt via SerpApi (engine=google_trends).

Fallback chain: SerpApi (retry 2x) -> manual_inputs/trends.csv -> gap.
pytrends was deliberately left out (unofficial, archived, and routinely 429-blocked by Google).
Values like "<1" are kept as text and never treated as numbers.
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from statistics import mean
from typing import Any
from urllib.parse import quote

import requests

from agents.schemas import SourceResult
from collectors.common import (PermanentError, TransientError, check_response, load_fixture,
                               read_manual_csv, save_fixture, with_retry)
from config import MANUAL_DIR, fixtures_mode, load_config, now_cairo, simulate_fail

log = logging.getLogger(__name__)
SERPAPI_URL = "https://serpapi.com/search.json"
TS_FIXTURE = "trends_timeseries.json"


# ---------------- public ----------------

def collect_trends(tracker: Any = None) -> SourceResult:
    """Never raises. Returns status ok | partial | gap."""
    cfg = load_config()
    try:
        return _collect(cfg, tracker)
    except Exception as e:  # last-resort guard
        log.exception("[trends] unexpected error")
        return _gap(cfg, f"unexpected error: {type(e).__name__}: {str(e)[:200]}")


def trends_explore_url(terms: list[str], geo: str, date_range: str) -> str:
    """Reproducible Google Trends link for the same filters."""
    q = ",".join(quote(t) for t in terms)
    return f"https://trends.google.com/trends/explore?date={quote(date_range)}&geo={geo}&q={q}"


# ---------------- flow ----------------

def _collect(cfg: dict, tracker: Any) -> SourceResult:
    tcfg = cfg["trends"]
    terms: list[str] = tcfg["terms"][:5]
    mode = fixtures_mode()
    try:
        if simulate_fail() == "trends":
            raise PermanentError("SIMULATE_FAIL=trends (simulated SerpApi outage)")
        if mode:
            raw_ts = load_fixture(TS_FIXTURE)
            raw_related = _load_related_fixtures(tcfg["related_queries_for"])
            source = f"fixture ({mode})"
        else:
            raw_ts = _serpapi_timeseries(terms, cfg, tracker)
            save_fixture(TS_FIXTURE, raw_ts)
            raw_related = _serpapi_related_all(tcfg["related_queries_for"], cfg, tracker)
            source = "serpapi"
        dates, series = _parse_timeseries(raw_ts, terms)
        keep = tcfg["terms"] + [c["name"] for c in cfg["ads"]["competitors"]]   # derived from the data setup
        related = {t: _parse_related(r, tcfg["rising_per_term"], keep) for t, r in raw_related.items()}
        result = _build_result(cfg, terms, dates, series, related, source)
        result.synthetic = bool(raw_ts.get("_synthetic"))
        log.info(f"[trends] {result.status.upper()} via {source}, {len(terms)} terms, {len(dates)} days")
        return result
    except (PermanentError, TransientError, KeyError, ValueError, TypeError) as e:
        primary_error = f"{type(e).__name__}: {e}" if isinstance(e, (KeyError, TypeError)) else str(e)
        log.warning(f"[trends] primary source failed: {primary_error}")

    manual = _from_manual_csv(cfg, terms, primary_error)
    if manual:
        return manual
    return _gap(cfg, f"SerpApi failed ({primary_error}); no manual_inputs/trends.csv")


# ---------------- SerpApi ----------------

def _serpapi_get(params: dict, tracker: Any, label: str) -> dict:
    key = os.getenv("SERPAPI_KEY")
    if not key:
        raise PermanentError("SERPAPI_KEY not set")

    def call() -> dict:
        resp = requests.get(SERPAPI_URL, params={**params, "api_key": key}, timeout=60)
        check_response(resp, "SerpApi")
        data = resp.json()
        if data.get("error"):
            raise PermanentError(f"SerpApi: {str(data['error'])[:200]}")  # e.g. quota used up, bad key
        return data

    data = with_retry(call, label)
    if tracker is not None:
        tracker.add_serpapi_search()
    return data


def _base_params(cfg: dict) -> dict:
    return {"engine": "google_trends", "geo": cfg["geo"], "date": cfg["trends"]["date_range"],
            "hl": "en", "tz": "-180"}  # tz in minutes offset from UTC; Cairo = UTC+3


def _serpapi_timeseries(terms: list[str], cfg: dict, tracker: Any) -> dict:
    return _serpapi_get({**_base_params(cfg), "q": ",".join(terms), "data_type": "TIMESERIES"},
                        tracker, "trends:timeseries")


def _serpapi_related_all(terms: list[str], cfg: dict, tracker: Any) -> dict[str, dict]:
    """One call per term. A failed term is recorded as {'_error': ...} instead of failing everything."""
    out: dict[str, dict] = {}
    for i, term in enumerate(terms):
        try:
            raw = _serpapi_get({**_base_params(cfg), "q": term, "data_type": "RELATED_QUERIES"},
                               tracker, f"trends:related:{i}")
            save_fixture(f"trends_related_{i}.json", raw)
            out[term] = raw
        except (PermanentError, TransientError) as e:
            log.warning(f"[trends] related queries failed for term #{i}: {e}")
            out[term] = {"_error": str(e)}
    return out


def _load_related_fixtures(terms: list[str]) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for i, term in enumerate(terms):
        try:
            out[term] = load_fixture(f"trends_related_{i}.json")
        except PermanentError as e:
            out[term] = {"_error": str(e)}
    return out


# ---------------- parsing ----------------

def _ts_to_date(ts: str | int) -> str:
    return datetime.fromtimestamp(int(ts), tz=timezone.utc).strftime("%Y-%m-%d")


def _parse_timeseries(raw: dict, terms: list[str]) -> tuple[list[str], dict[str, list[str]]]:
    """-> (dates, {term: [raw value strings aligned with dates]}). Skips partial (incomplete) points."""
    timeline = raw["interest_over_time"]["timeline_data"]
    dates: list[str] = []
    series: dict[str, list[str]] = {t: [] for t in terms}
    for entry in timeline:
        if entry.get("partial_data"):
            continue
        dates.append(_ts_to_date(entry["timestamp"]))
        by_query = {v.get("query"): v for v in entry.get("values", [])}
        for idx, term in enumerate(terms):
            v = by_query.get(term) or (entry["values"][idx] if idx < len(entry.get("values", [])) else None)
            series[term].append(str(v.get("value", "")).strip() if v else "")
    if not dates:
        raise ValueError("SerpApi returned no complete time-series points")
    return dates, series


def _parse_related(raw: dict, limit: int, keep_words: list[str] | None = None) -> dict:
    """Rising queries, filtered to snack-related ones: rising queries for a word like شيبس often include
    unrelated spikes (football clubs, cars), so only queries containing a word from `keep_words` are kept."""
    if raw.get("_error"):
        return {"status": "gap", "reason": raw["_error"], "items": []}
    rising = (raw.get("related_queries") or {}).get("rising") or []
    words = [w.lower() for w in (keep_words or [])]
    kept, dropped = [], []
    for r in rising:
        q = r.get("query", "")
        (kept if not words or any(w in q.lower() for w in words) else dropped).append(
            {"query": q, "value": str(r.get("value", "")), "link": r.get("link", "")})
    if dropped:
        log.info(f"[trends] filtered out {len(dropped)} unrelated rising queries: {[d['query'] for d in dropped]}")
    if not kept:
        return {"status": "insufficient data", "items": [], "filtered_out": dropped,
                "reason": f"no {load_config()['brand']['category']}-related rising queries" if rising else "no rising queries returned"}
    return {"status": "ok", "reason": None, "items": kept[:limit], "filtered_out": dropped}


def as_number(v: str) -> int | None:
    """'34' -> 34. '<1', '' and anything non-numeric -> None (kept as text, never a number)."""
    v = (v or "").strip()
    return int(v) if v.isdigit() else None


def _term_summary(dates: list[str], values: list[str], min_points: int, min_avg: float = 5) -> dict:
    """Week-over-week from daily points: mean of the last 7 complete days vs the 7 before."""
    if len(dates) < 14:
        return {"status": "insufficient data", "reason": f"only {len(dates)} daily points"}
    last, prev = values[-7:], values[-14:-7]
    last_n = [as_number(v) for v in last if as_number(v) is not None]
    prev_n = [as_number(v) for v in prev if as_number(v) is not None]
    nonzero_last = sum(1 for n in last_n if n >= 1)
    nonzero_prev = sum(1 for n in prev_n if n >= 1)
    numeric_all = [(as_number(v), d) for v, d in zip(values, dates) if as_number(v) is not None]
    base = {"last_7d": f"{dates[-7]} → {dates[-1]}", "prev_7d": f"{dates[-14]} → {dates[-8]}",
            "text_values_last_14d": sorted({v for v in last + prev if as_number(v) is None and v})}
    if nonzero_last < min_points or nonzero_prev < min_points:
        return {**base, "status": "insufficient data",
                "reason": f"too few days with interest ≥1 (last 7d: {nonzero_last}, prev 7d: {nonzero_prev})"}
    a, b = round(mean(last_n), 1), round(mean(prev_n), 1)
    if max(a, b) < min_avg:
        return {**base, "status": "insufficient data",
                "reason": f"interest too low for a meaningful change (weekly averages {b} → {a})"}
    peak_val, peak_date = max(numeric_all, key=lambda x: x[0])
    return {**base, "status": "ok",
            "last_7d_avg": a, "prev_7d_avg": b,
            "wow_change_points": round(a - b, 1),
            "wow_change_pct": round((a - b) / b * 100) if b > 0 else None,
            "avg_90d": round(mean(n for n, _ in numeric_all), 1),
            "peak_value": peak_val, "peak_date": peak_date}


def _span(a: str, b: str) -> str:
    """'2026-10-10', '2026-10-16' -> '10–16 Oct'  /  '2026-07-19', '2026-10-16' -> '19 Jul – 16 Oct'."""
    x, y = datetime.strptime(a, "%Y-%m-%d"), datetime.strptime(b, "%Y-%m-%d")
    return f"{x.day}–{y.day} {y:%b}" if x.month == y.month else f"{x.day} {x:%b} – {y.day} {y:%b}"


def _candidates(summary: dict, related: dict, period_label: str) -> list[dict]:
    """Ground-truth trend facts, written by code from the numbers (the AI only picks and interprets).
    Order: biggest week-over-week moves first, then snack-related rising queries."""
    weekly = [(t, s) for t, s in summary.items() if s.get("status") == "ok" and s.get("wow_change_pct") is not None]
    weekly.sort(key=lambda x: abs(x[1]["wow_change_pct"]), reverse=True)
    out: list[dict] = []
    for t, s in weekly:
        pct = s["wow_change_pct"]
        out.append({"kind": "weekly_change", "term": t,
                    "values": [s["last_7d_avg"], s["prev_7d_avg"], pct],
                    "period": f"{_span(*s['last_7d'].split(' → '))} vs {_span(*s['prev_7d'].split(' → '))}",
                    "fact": f"Relative interest averaged {s['last_7d_avg']} in the last 7 days vs "
                            f"{s['prev_7d_avg']} the week before ({pct:+d}%)."})
    for t, r in related.items():
        for item in r.get("items", []) if r.get("status") == "ok" else []:
            out.append({"kind": "rising_query", "term": t, "query": item["query"], "values": [item["value"]],
                        "period": period_label,
                        "fact": f"Rising related search: «{item['query']}» ({item['value']})."})
    for i, c in enumerate(out, 1):
        c["id"] = f"C{i}"
    return out


def _build_result(cfg: dict, terms: list[str], dates: list[str], series: dict[str, list[str]],
                  related: dict[str, dict], source: str, extra_reason: str | None = None) -> SourceResult:
    tcfg = cfg["trends"]
    summary = {t: _term_summary(dates, series[t], tcfg["min_points_per_week"], tcfg.get("min_avg_for_change", 5))
               for t in terms}
    related_failed = [t for t, r in related.items() if r["status"] == "gap"]
    status = "partial" if (related_failed or extra_reason) else "ok"
    reasons = [r for r in [extra_reason] if r]
    if related_failed:
        reasons.append(f"rising queries unavailable for: {', '.join(related_failed)}")
    period = f"{dates[0]} → {dates[-1]} (daily, {tcfg['period_label']})"
    return SourceResult(
        status=status, reason="; ".join(reasons) or None, source=source, collected_at=now_cairo(),
        filters={"geo": cfg["geo"], "date": tcfg["date_range"], "terms": terms,
                 "category": "All categories", "property": "Web Search"},
        period=period,
        data={"terms": terms, "main_term": tcfg["main_term"], "dates": dates, "series": series,
              "summary": summary, "related_rising": related,
              "candidates": _candidates(summary, related, _span(dates[0], dates[-1])),
              "trends_url": trends_explore_url(terms, cfg["geo"], tcfg["date_range"]),
              "scale_note": "Relative interest (0–100), not search volumes"},
    )


# ---------------- fallbacks ----------------

def _from_manual_csv(cfg: dict, terms: list[str], primary_error: str) -> SourceResult | None:
    """manual_inputs/trends.csv columns: date (YYYY-MM-DD), term, value (as shown in Trends, '<1' allowed)."""
    rows = read_manual_csv(MANUAL_DIR / "trends.csv")
    if not rows:
        return None
    try:
        dates = sorted({r["date"] for r in rows})
        csv_terms = [t for t in terms if any(r["term"] == t for r in rows)] or sorted({r["term"] for r in rows})[:5]
        lookup = {(r["date"], r["term"]): r["value"] for r in rows}
        series = {t: [lookup.get((d, t), "") for d in dates] for t in csv_terms}
        related = {t: {"status": "gap", "reason": "not available from manual CSV", "items": []}
                   for t in cfg["trends"]["related_queries_for"] if t in csv_terms}
        res = _build_result(cfg, csv_terms, dates, series, related, "manual_csv",
                            extra_reason=f"SerpApi failed ({primary_error}); used manual_inputs/trends.csv")
        log.info(f"[trends] PARTIAL via manual CSV, {len(csv_terms)} terms")
        return res
    except (KeyError, ValueError) as e:
        log.warning(f"[trends] manual CSV unusable: {e}")
        return None


def _gap(cfg: dict, reason: str) -> SourceResult:
    tcfg = cfg["trends"]
    log.warning(f"[trends] GAP: {reason}")
    return SourceResult(
        status="gap", reason=reason, source=None, collected_at=now_cairo(),
        filters={"geo": cfg["geo"], "date": tcfg["date_range"], "terms": tcfg["terms"]},
        period=tcfg["period_label"],
        data={"trends_url": trends_explore_url(tcfg["terms"], cfg["geo"], tcfg["date_range"]),
              "terms": tcfg["terms"], "main_term": tcfg["main_term"]},
    )
