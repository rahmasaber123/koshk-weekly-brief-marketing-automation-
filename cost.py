"""Per-run cost tracking from ACTUAL usage. Missing fields are logged as 'unknown', never a crash.

Usage summary of past runs:  python cost.py
"""
from __future__ import annotations

import csv
import logging
import os
from typing import Any

import requests

from config import OUTPUT_DIR, load_config

log = logging.getLogger(__name__)
WEEKS_PER_MONTH = 4.33


class CostTracker:
    def __init__(self) -> None:
        self.pricing = load_config().get("pricing", {})
        self.apify: dict[str, Any] = {"usd": 0.0, "basis": "not run"}
        self.serpapi: dict[str, Any] = {"searches": 0, "searches_left": "n/a", "usd": 0.0}
        self.openai: dict[str, Any] = {"calls": 0, "input_tokens": 0, "output_tokens": 0,
                                       "usd": 0.0, "unknown_calls": 0}

    # ---- Apify ----
    def mark_apify_attempted(self) -> None:
        """Called before a real Apify call: if the run then fails, its charges are unknown, not zero."""
        self.apify = {"usd": None, "basis": "unknown (run failed before usage could be read)"}

    def set_apify(self, run_cost_fields: dict, items: int, from_fixture: bool = False) -> None:
        if from_fixture:
            self.apify = {"usd": 0.0, "basis": "fixture (no API call)", "items": items}
            return
        price = self.pricing.get("apify_ads_per_1000")
        estimate = round(items * price / 1000, 4) if isinstance(price, (int, float)) else None
        actual = run_cost_fields.get("usageTotalUsd")
        actual = float(actual) if isinstance(actual, (int, float)) else None
        # Apify docs don't state whether usageTotalUsd includes pay-per-result charges, so we log both
        # and report the higher one (never under-report).
        if actual is not None and estimate is not None:
            usd, basis = max(actual, estimate), ("actual (usageTotalUsd)" if actual >= estimate
                                                  else "estimated (items x price > usageTotalUsd)")
        elif actual is not None:
            usd, basis = actual, "actual (usageTotalUsd)"
        elif estimate is not None:
            usd, basis = estimate, "estimated (items x price)"
        else:
            usd, basis = None, "unknown"
        self.apify = {"usd": usd, "basis": basis, "items": items, "usageTotalUsd": actual,
                      "items_x_price_usd": estimate, "pricing_model": run_cost_fields.get("pricingModel")}

    # ---- SerpApi ----
    def add_serpapi_search(self) -> None:
        self.serpapi["searches"] += 1
        price = self.pricing.get("serpapi_per_search", 0.0) or 0.0
        self.serpapi["usd"] = round(self.serpapi["searches"] * price, 4)

    def fetch_serpapi_quota(self) -> None:
        """account.json does not count as a search."""
        key = os.getenv("SERPAPI_KEY")
        if not key or self.serpapi["searches"] == 0:
            return
        self.serpapi["searches_left"] = "unknown"
        try:
            r = requests.get("https://serpapi.com/account.json", params={"api_key": key}, timeout=20)
            if r.ok:
                d = r.json()
                self.serpapi["searches_left"] = d.get("total_searches_left", d.get("plan_searches_left", "unknown"))
        except (requests.RequestException, ValueError):
            pass  # stays "unknown"

    # ---- OpenAI ----
    def add_llm_usage(self, result: Any) -> None:
        """Accepts a PydanticAI run result. `usage` is a method in older versions, a property in newer."""
        self.openai["calls"] += 1
        try:
            u = result.usage
            u = u() if callable(u) else u
            inp = getattr(u, "input_tokens", None) or getattr(u, "request_tokens", None)
            out = getattr(u, "output_tokens", None) or getattr(u, "response_tokens", None)
            if inp is None or out is None:
                raise AttributeError("token fields missing")
        except Exception:
            self.openai["unknown_calls"] += 1
            return
        model_key = load_config()["llm"]["model"].replace(":", "_").replace("-", "_").replace(".", "_")
        p = self.pricing.get(model_key) or {}   # "openai:gpt-4o" -> pricing key "openai_gpt_4o"
        self.openai["input_tokens"] += int(inp)
        self.openai["output_tokens"] += int(out)
        if "input_per_1m" in p and "output_per_1m" in p:
            self.openai["usd"] = round(self.openai["input_tokens"] / 1e6 * p["input_per_1m"]
                                       + self.openai["output_tokens"] / 1e6 * p["output_per_1m"], 6)
        else:
            self.openai["unknown_calls"] += 1

    # ---- output ----
    def total(self) -> tuple[float, bool]:
        """(total_usd of known lines, complete?)"""
        parts = [self.apify["usd"], self.serpapi["usd"], self.openai["usd"]]
        known = sum(p for p in parts if isinstance(p, (int, float)))
        complete = all(isinstance(p, (int, float)) for p in parts) and self.openai["unknown_calls"] == 0
        return round(known, 4), complete

    def breakdown(self) -> dict:
        total, complete = self.total()
        return {"apify": self.apify, "serpapi": self.serpapi, "openai": self.openai,
                "total_usd": total, "complete": complete,
                "projected_monthly_usd": round(total * WEEKS_PER_MONTH, 4),
                "pricing_checked": "see config.yaml pricing block"}

    def summary_line(self) -> str:
        total, complete = self.total()
        ap = self.apify["usd"]
        ap_s = f"${ap:.3f}" if isinstance(ap, (int, float)) else "unknown"
        oa = self.openai
        oa_s = "unknown" if oa["unknown_calls"] and not oa["input_tokens"] else \
            f"${oa['usd']:.4f} ({(oa['input_tokens'] + oa['output_tokens']) / 1000:.1f}k tokens)"
        prefix = "" if complete else "≥"
        return (f"Run cost {prefix}${total:.3f} (Apify {ap_s} · SerpApi {self.serpapi['searches']} searches, "
                f"{self.serpapi['searches_left']} left, ${self.serpapi['usd']:.2f} · OpenAI {oa_s}) · "
                f"~{prefix}${total * WEEKS_PER_MONTH:.2f}/month (x4.33)")

    def append_history(self, run_date: str) -> None:
        path = OUTPUT_DIR / "cost_history.csv"
        new = not path.exists()
        total, complete = self.total()
        with open(path, "a", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            if new:
                w.writerow(["date", "total_usd", "complete"])
            w.writerow([run_date, f"{total:.4f}", complete])


def history_summary() -> str:
    path = OUTPUT_DIR / "cost_history.csv"
    if not path.exists():
        return "no output/cost_history.csv yet"
    with open(path, encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    vals = [float(r["total_usd"]) for r in rows if r.get("total_usd")]
    if not vals:
        return "cost_history.csv is empty"
    avg = sum(vals) / len(vals)
    return (f"{len(vals)} runs · avg ${avg:.4f}/run · min ${min(vals):.4f} · max ${max(vals):.4f} · "
            f"projected ${avg * WEEKS_PER_MONTH:.3f}/month")


if __name__ == "__main__":
    print(history_summary())
