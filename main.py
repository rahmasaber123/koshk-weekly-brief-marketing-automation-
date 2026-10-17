"""Runs the weekly brief end to end. Every step is isolated: one failure is reported, the rest still runs.

Exit code 0 if a brief (HTML or PDF) was produced, 1 otherwise.
Flags: USE_FIXTURES=1|sample (no API calls for collectors), SIMULATE_FAIL=ads|trends|llm.
"""
from __future__ import annotations

import json
import logging
import os
import sys
import traceback
from datetime import datetime
from typing import Any, Callable

from agents.ads_analyst import analyse_ads
from agents.base import LLMUnavailable
from agents.schemas import SourceResult
from agents.strategist import plan_content
from agents.trends_analyst import analyse_trends
from charts import make_charts
from collectors.ads import collect_ads
from collectors.trends import collect_trends
from config import OUTPUT_DIR, ROOT, fixtures_mode, load_config, now_cairo, simulate_fail
from cost import CostTracker
from deliver import send_email
from render import render_html, render_pdf, week_label
from validate import (code_ads_facts, code_trend_findings, insufficient_terms, validate_ads, validate_findings,
                      validate_ideas)

log = logging.getLogger("main")


def step(name: str, errors: list[dict], fn: Callable[[], Any], default: Any = None) -> Any:
    try:
        return fn()
    except Exception as e:
        errors.append({"step": name, "error": f"{type(e).__name__}: {str(e)[:300]}"})
        log.error(f"[{name}] FAILED: {type(e).__name__}: {e}")
        log.debug(traceback.format_exc())
        return default


def _source_log(r: SourceResult) -> dict:
    d = r.model_dump(mode="json", exclude={"data"})
    if isinstance(r.data, dict):
        d["counts"] = {"ads": len(r.data.get("ads", []))} if "ads" in r.data else \
            {"terms": len(r.data.get("terms", [])), "days": len(r.data.get("dates", []))}
    return d


def _period(r: SourceResult) -> str:
    dates = (r.data or {}).get("dates") or []
    if not dates:
        return r.period or ""
    a, b = (datetime.strptime(d, "%Y-%m-%d") for d in (dates[0], dates[-1]))
    return f"{a.day} {a:%b} – {b.day} {b:%b %Y}"  # no %-d: it crashes on Windows


def _sources_line(ads_r: SourceResult, tr_r: SourceResult) -> str:
    manual = lambda r: " (manual entry)" if r.source == "manual_csv" else ""
    return (f"Sources: Meta Ad Library{manual(ads_r)}, Egypt, active ads, collected {ads_r.collected_at.day} {ads_r.collected_at:%b %Y} · "
            f"Google Trends{manual(tr_r)}, Egypt, web search, {_period(tr_r)}")


def run_ads_agent(ads_r: SourceResult, tracker: CostTracker, drops: list, ai_errors: list) -> tuple[list, str | None]:
    raw = ads_r.data.get("ads", [])
    if not raw:
        return [], ads_r.reason
    try:
        analysis = analyse_ads(ads_r, tracker)
        items = validate_ads(analysis, raw, drops, load_config()["ads"]["max_in_brief"])
        note = analysis.note if len(items) < 3 else None
        return items, note or (None if items else "no relevant active ads found")
    except Exception as e:
        ai_errors.append(str(e))
        return code_ads_facts(raw), "AI step unavailable — showing the most recent ad per brand, facts only."


def run_trends_agent(tr_r: SourceResult, tracker: CostTracker, drops: list, ai_errors: list) -> list:
    if tr_r.status == "gap":
        return []
    try:
        return validate_findings(analyse_trends(tr_r, tracker), tr_r.data, drops)
    except Exception as e:
        ai_errors.append(str(e))
        return code_trend_findings(tr_r.data)


def run_strategist(ads: list, findings: list, gaps: list[str], tracker: CostTracker, drops: list,
                   ai_errors: list) -> tuple[bool, str | None, list, str | None]:
    """-> (available, reason_if_unavailable, ideas, note)"""
    usable = [f for f in findings if not f.get("greyed")]
    if not ads and not usable:
        return False, "No validated ads or findings this week, so there is no evidence to base ideas on.", [], None
    try:
        plan = plan_content(ads, usable, gaps, tracker)
        ids = {a["id"] for a in ads} | {f["finding_id"] for f in usable}
        ideas = validate_ideas(plan, ids, drops)
        return True, None, ideas, plan.note
    except Exception as e:
        ai_errors.append(str(e))
        return False, f"AI step unavailable ({e}). Facts above are unaffected.", [], None


def main() -> int:
    run_time = now_cairo()
    cfg = load_config()
    tracker, errors, drops, ai_errors = CostTracker(), [], [], []
    log.info(f"[run] start {run_time:%Y-%m-%d %H:%M} Cairo · fixtures={fixtures_mode()} · simulate_fail={simulate_fail()}")
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    # 1. collect facts (collectors never raise, step() is a second safety net)
    gap = lambda name: SourceResult(status="gap", reason=f"{name} collector crashed", collected_at=now_cairo(),
                                    data={"ads": [], "search_url": cfg["ads"]["fallback_search_url"]})
    ads_r = step("collect_ads", errors, lambda: collect_ads(tracker), default=None) or gap("ads")
    tr_r = step("collect_trends", errors, lambda: collect_trends(tracker), default=None) or gap("trends")
    gaps = [f"{n}: {r.reason}" for n, r in (("ads", ads_r), ("trends", tr_r)) if r.status != "ok" and r.reason]

    # 2. agents add judgement; validate.py checks everything against the raw data
    ads, ads_note = step("ads_agent", errors, lambda: run_ads_agent(ads_r, tracker, drops, ai_errors),
                         default=([], "ads step failed"))
    findings = step("trends_agent", errors, lambda: run_trends_agent(tr_r, tracker, drops, ai_errors), default=[])
    insufficient = insufficient_terms(tr_r.data) if tr_r.status != "gap" else []
    ideas_available, ideas_reason, ideas, ideas_note = step(
        "strategist", errors, lambda: run_strategist(ads, findings, gaps, tracker, drops, ai_errors),
        default=(False, "strategist step failed", [], None))
    ai_status = "gap" if (ai_errors and not ideas_available) else ("partial" if ai_errors else "ok")

    # 3. chart + cost + render
    charts = step("chart", errors, lambda: make_charts(tr_r.data)) if tr_r.status != "gap" else None
    step("serpapi_quota", errors, tracker.fetch_serpapi_quota)
    state = {"run_time": run_time, "ads_result": ads_r, "trends_result": tr_r, "ads": ads, "ads_note": ads_note,
             "findings": findings, "insufficient": insufficient, "ideas_available": ideas_available,
             "ideas_reason": ideas_reason, "ideas": ideas, "ideas_note": ideas_note, "ai_status": ai_status,
             "charts": charts, "sources_line": _sources_line(ads_r, tr_r), "cost_line": tracker.summary_line()}
    html_path = step("render_html", errors, lambda: render_html(state))
    pdf = step("render_pdf", errors, lambda: render_pdf(state))
    pdf_path, pdf_pages = pdf if pdf else (None, None)
    step("cost_history", errors, lambda: tracker.append_history(run_time.strftime("%Y-%m-%d")))

    # 4. deliver (failure never fails the run)
    subject = f"{cfg['email']['subject_prefix']} · {week_label(run_time)}"
    email = step("email", errors, lambda: send_email(html_path, pdf_path, subject),
                 default={"status": "failed", "reason": "email step crashed"})

    produced = bool(html_path or pdf_path)
    run_log = {
        "run_started": run_time.isoformat(), "run_finished": now_cairo().isoformat(),
        "flags": {"USE_FIXTURES": os.getenv("USE_FIXTURES"), "SIMULATE_FAIL": os.getenv("SIMULATE_FAIL")},
        "sources": {"ads": _source_log(ads_r), "trends": _source_log(tr_r)},
        "agents": {"status": ai_status, "errors": ai_errors, "ads_kept": [a["id"] for a in ads],
                   "findings_kept": [f["finding_id"] for f in findings], "ideas_kept": len(ideas)},
        "validation_drops": drops,
        "outputs": {"html": str(html_path.relative_to(ROOT)) if html_path else None,
                    "pdf": str(pdf_path.relative_to(ROOT)) if pdf_path else None, "pdf_pages": pdf_pages},
        "email": email, "cost": tracker.breakdown(), "errors": errors, "brief_produced": produced,
    }
    (OUTPUT_DIR / "run_log.json").write_text(json.dumps(run_log, ensure_ascii=False, indent=1, default=str),
                                             encoding="utf-8")
    log.info(f"[run] done · ads {ads_r.status} · trends {tr_r.status} · AI {ai_status} · "
             f"{len(drops)} drops · email {email.get('status')} · {tracker.summary_line()}")
    return 0 if produced else 1


if __name__ == "__main__":
    sys.exit(main())
