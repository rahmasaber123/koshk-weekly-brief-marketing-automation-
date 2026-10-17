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

def validate_findings(analysis: TrendsAnalysis, trends_data: dict, drops: list[dict],
                      max_n: int = 3) -> list[dict]:
    """Findings are code-written candidate facts picked by ID; the agent only adds the interpretation.
    Drops unknown/duplicate IDs and interpretations that contain numbers. Renumbers F1..Fn."""
    by_id = {c["id"]: c for c in trends_data.get("candidates") or []}
    out: list[dict] = []
    for p in analysis.findings:
        c = by_id.get(p.candidate_id)
        if c is None:
            _drop(drops, "finding", p.candidate_id, "candidate_id not in collected data")
        elif any(o["candidate_id"] == c["id"] for o in out):
            _drop(drops, "finding", p.candidate_id, "duplicate candidate")
        elif _nums_in(p.interpretation):
            _drop(drops, "finding", p.candidate_id, f"interpretation adds numbers: {_nums_in(p.interpretation)}")
        else:
            out.append({**c, "candidate_id": c["id"], "interpretation": p.interpretation, "greyed": False})
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
    """Most recent ad per competitor. Facts only, no interpretation."""
    picked: list[dict] = []
    for a in sorted(raw_ads, key=lambda x: x.get("start_date", ""), reverse=True):
        if all(p["competitor"] != a["competitor"] for p in picked):   # collector already kept official pages only
            text = a.get("ad_text") or "(no ad text)"
            picked.append({**a, "message_fact": text[:140] + ("…" if len(text) > 140 else ""),
                           "interpretation": None, "relevance_reason": "code fallback (AI unavailable)"})
    return picked[:max_n]


def code_trend_findings(trends_data: dict, max_n: int = 3) -> list[dict]:
    """AI down: top 2 weekly changes + top rising query, straight from the code-written candidates."""
    cands = trends_data.get("candidates") or []
    weekly = [c for c in cands if c["kind"] == "weekly_change"][:2]
    rising = [c for c in cands if c["kind"] == "rising_query"][:1]
    out = [{**c, "candidate_id": c["id"], "interpretation": None, "greyed": False} for c in (weekly + rising)[:max_n]]
    for i, f in enumerate(out, 1):
        f["finding_id"] = f"F{i}"
    return out
