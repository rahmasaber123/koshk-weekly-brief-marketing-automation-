"""Checks every agent output against the raw collected data. Anything that doesn't match is dropped and logged.

Also holds the code-only fallbacks used when the LLM is down, so the facts sections still render.
"""
from __future__ import annotations

import logging
import re
from typing import Any

from agents.schemas import AdsAnalysis, ContentPlan, TrendsAnalysis
from config import load_config

log = logging.getLogger(__name__)
NUM_RE = re.compile(r"\d+(?:\.\d+)?")
TOL = 0.051  # tolerance when comparing copied floats (79.7 vs 79.70)


def _drop(drops: list[dict], kind: str, ident: str, reason: str) -> None:
    drops.append({"type": kind, "id": ident, "reason": reason})
    log.warning(f"[validate] dropped {kind} {ident}: {reason}")


def _nums_in(text: str) -> list[float]:
    text = re.sub(r"0\s*[–-]\s*100", " ", text or "")  # the scale note "0–100" is not a data claim
    text = re.sub(r"\b\d+[\s-]*(?:days?|weeks?|months?)\b", " ", text, flags=re.I)  # "last 7 days" is a window
    text = re.sub(r"\b[AF]\d+\b", " ", text)           # evidence IDs like F1 / A3
    return [float(n) for n in NUM_RE.findall(text)]


def _num_ok(x: float, allowed: set[float]) -> bool:
    return any(abs(abs(x) - abs(a)) <= TOL for a in allowed)


# ---------------- ads ----------------

def validate_ads(analysis: AdsAnalysis, raw_ads: list[dict], drops: list[dict], max_n: int = 3) -> list[dict]:
    by_id = {a["id"]: a for a in raw_ads}
    out: list[dict] = []
    for p in analysis.ads:
        raw = by_id.get(p.ad_id)
        if raw is None:
            _drop(drops, "ad", p.ad_id, "ad_id not in collected data")
            continue
        if any(o["id"] == p.ad_id for o in out):
            _drop(drops, "ad", p.ad_id, "duplicate ad_id")
            continue
        allowed = set(_nums_in(raw.get("ad_text", "")) + _nums_in(raw.get("start_date", "")))
        bad = [n for n in _nums_in(p.message_fact + " " + p.interpretation) if not _num_ok(n, allowed)]
        if bad:
            _drop(drops, "ad", p.ad_id, f"numbers not in the ad's text: {bad}")
            continue
        out.append({**raw, "relevance_reason": p.relevance_reason, "message_fact": p.message_fact,
                    "interpretation": p.interpretation})
    return out[:max_n]


# ---------------- trends ----------------

def _allowed_for_term(term: str, data: dict) -> tuple[set[float], set[str]]:
    nums: set[float] = set()
    strs: set[str] = set()

    def walk(v: Any) -> None:
        if isinstance(v, bool) or v is None:
            return
        if isinstance(v, (int, float)):
            nums.add(float(v))
        elif isinstance(v, str):
            strs.add(v.strip())
            nums.update(_nums_in(v))         # e.g. "+250%" -> 250, dates -> their parts
        elif isinstance(v, dict):
            for x in v.values():
                walk(x)
        elif isinstance(v, list):
            for x in v:
                walk(x)

    # Only what the agent was shown for this term (not the raw daily series — too permissive a match set).
    walk((data.get("summary") or {}).get(term))
    walk((data.get("related_rising") or {}).get(term))
    return nums, strs


def _value_ok(v: float | str, nums: set[float], strs: set[str]) -> bool:
    if isinstance(v, (int, float)):
        return _num_ok(float(v), nums)
    s = str(v).strip()
    if s in strs:
        return True
    found = _nums_in(s)                      # "+9%", "9 %", "-4" -> 9 / 4
    return bool(found) and all(_num_ok(n, nums) for n in found)


def validate_findings(analysis: TrendsAnalysis, trends_data: dict, drops: list[dict],
                      max_n: int = 3) -> list[dict]:
    """Drops findings with unknown terms or any value/number not in the raw data. Renumbers F1..Fn."""
    summary = trends_data.get("summary") or {}
    related = trends_data.get("related_rising") or {}
    out: list[dict] = []
    for f in analysis.findings:
        fid = f.finding_id
        if f.term not in summary and f.term not in related:
            _drop(drops, "finding", fid, f"term '{f.term}' not in collected data")
            continue
        if f.insufficient_data:
            if (summary.get(f.term) or {}).get("status") == "ok":
                _drop(drops, "finding", fid, f"marked insufficient but '{f.term}' has data")
                continue
            out.append({**f.model_dump(), "values": [], "greyed": True})
            continue
        nums, strs = _allowed_for_term(f.term, trends_data)
        bad_vals = [v for v in f.values if not _value_ok(v, nums, strs)]
        bad_text = [n for n in _nums_in(f.fact + " " + f.interpretation) if not _num_ok(n, nums)]
        if not f.values:
            _drop(drops, "finding", fid, "no values copied from data")
        elif bad_vals or bad_text:
            _drop(drops, "finding", fid, f"values not in raw data: {bad_vals + bad_text}")
        elif any(o["term"] == f.term and o["fact"] == f.fact for o in out):
            _drop(drops, "finding", fid, "duplicate finding")
        else:
            out.append({**f.model_dump(), "greyed": False})
    out = out[:max_n]
    for i, f in enumerate(out, 1):
        f["finding_id"] = f"F{i}"
    return out


def insufficient_terms(trends_data: dict) -> list[dict]:
    """Code-reported (not AI) list of terms with too little data, shown greyed in the brief."""
    return [{"term": t, "reason": s.get("reason", "")}
            for t, s in (trends_data.get("summary") or {}).items() if s.get("status") != "ok"]


# ---------------- ideas ----------------

# No hand-written word lists: the only words checked come from config.yaml (the competitors you track).
SELF_CLAIM_RE = re.compile(r"\bits own\b|koshk'?s own", re.I)   # AI assuming facts about fictional Koshk


def _competitor_words() -> list[str]:
    cfg = load_config()
    return [c["name"] for c in cfg["ads"]["competitors"]] + list(cfg["trends"].get("chart_terms", []))


def idea_problem(idea: Any) -> str | None:
    """Rule check for one content idea (used as a retry trigger for the agent and as the final gate)."""
    head = f"{idea.title} {idea.hook_ar}".lower()
    hit = next((w for w in _competitor_words() if re.search(rf"(?<!\w){re.escape(w.lower())}(?!\w)", head)), None)
    if hit:
        return f"idea '{idea.title}' names a competitor ('{hit}') in Koshk's own post"
    if m := SELF_CLAIM_RE.search(f"{idea.title} {idea.why_now}"):
        return f"idea '{idea.title}' assumes facts about Koshk itself ('{m.group(0)}')"
    return None


def plan_problem(plan: ContentPlan) -> str | None:
    problems = [p for p in (idea_problem(i) for i in plan.ideas) if p]
    if not problems:
        return None
    return ("Fix these ideas: " + "; ".join(problems) + ". Koshk's posts must not name competitors and must not "
            "assume anything about Koshk's own product. Drop an idea rather than break these rules.")


def ads_problem(analysis: AdsAnalysis) -> str | None:
    bad = [f"{p.ad_id}: '{p.interpretation}'" for p in analysis.ads if SELF_CLAIM_RE.search(p.interpretation)]
    if not bad:
        return None
    return ("These interpretations assume facts about Koshk's own product: " + "; ".join(bad) + ". Rewrite each "
            "as a content angle that differs from the competitor, without assuming anything about Koshk.")


def validate_ideas(plan: ContentPlan, valid_ids: set[str], drops: list[dict], max_n: int = 3) -> list[dict]:
    out: list[dict] = []
    for i, idea in enumerate(plan.ideas, 1):
        missing = [b for b in idea.based_on if b not in valid_ids]
        problem = idea_problem(idea)
        if problem:
            _drop(drops, "idea", f"idea{i}", problem)
        elif not idea.based_on:
            _drop(drops, "idea", f"idea{i}", "based_on is empty")
        elif missing:
            _drop(drops, "idea", f"idea{i}", f"based_on IDs not in validated data: {missing}")
        else:
            d = idea.model_dump()
            d["hook_ar"] = d["hook_ar"].strip().strip('"\'«»“”').strip()   # template adds «»
            out.append(d)
    return out[:max_n]


# ---------------- code-only fallbacks (LLM down) ----------------

def code_ads_facts(raw_ads: list[dict], max_n: int = 3) -> list[dict]:
    """Most recent ad per competitor whose page name contains the brand name. Facts only, no interpretation."""
    picked: list[dict] = []
    for a in sorted(raw_ads, key=lambda x: x.get("start_date", ""), reverse=True):
        if all(p["competitor"] != a["competitor"] for p in picked):   # collector already kept official pages only
            text = a.get("ad_text") or "(no ad text)"
            picked.append({**a, "message_fact": text[:140] + ("…" if len(text) > 140 else ""),
                           "interpretation": None, "relevance_reason": "code fallback (AI unavailable)"})
    return picked[:max_n]


def code_trend_findings(trends_data: dict, max_n: int = 3) -> list[dict]:
    """Largest week-over-week changes, then the top rising query. Templated facts, no interpretation."""
    out: list[dict] = []
    ok = [(t, s) for t, s in (trends_data.get("summary") or {}).items()
          if s.get("status") == "ok" and s.get("wow_change_pct") is not None]
    for t, s in sorted(ok, key=lambda x: abs(x[1]["wow_change_pct"]), reverse=True)[:2]:
        out.append({"term": t, "values": [s["last_7d_avg"], s["prev_7d_avg"], s["wow_change_pct"]],
                    "period": f"{s['last_7d']} vs {s['prev_7d']}", "greyed": False, "interpretation": None,
                    "fact": f"Relative interest in {t} averaged {s['last_7d_avg']} in the last 7 days vs "
                            f"{s['prev_7d_avg']} the week before ({s['wow_change_pct']:+d}%)."})
    for t, r in (trends_data.get("related_rising") or {}).items():
        if r.get("status") == "ok" and r["items"] and len(out) < max_n:
            q = r["items"][0]
            out.append({"term": t, "values": [q["value"]], "period": "Past 90 days", "greyed": False,
                        "interpretation": None,
                        "fact": f"Top rising query related to {t} is {q['query']} ({q['value']})."})
            break
    for i, f in enumerate(out[:max_n], 1):
        f["finding_id"] = f"F{i}"
    return out[:max_n]