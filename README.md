# Koshk Weekly Brief

Every Monday at 09:00 Cairo this produces a one-page brief for Koshk, a fictional Egyptian snack brand. The brief covers:
- what Chipsy, Doritos and Lion are running in the Meta Ad Library (Egypt, active ads)
- what Egypt is searching on Google Trends (Arabic terms)
- three content ideas, each linked to a specific ad or finding

The brief is emailed, saved to `output/` (HTML + PDF) and kept as a GitHub Actions artifact.

**Code collects and writes the facts. AI only picks and interprets. Every AI output is checked against the collected data.**

```
GitHub Actions (Mon 06:00 UTC) or "Run workflow"
 → collectors/ads.py      Apify actor apify/facebook-ads-scraper → official competitor pages only → images saved
 → collectors/trends.py   SerpApi Google Trends (EG): 5 Arabic terms, 90 days + rising queries
                           → code computes week-over-week changes and writes candidate facts (C1…Cn)
 → agents/                Ads Analyst · Trends Analyst (picks candidates) · Content Strategist   (PydanticAI, gpt-4o)
 → validate.py            drops unknown IDs, invented numbers, ideas naming competitors; asks the model to fix, then drops
 → charts.py → render.py  one-page HTML + PDF (Arabic fonts bundled)
 → deliver.py             Gmail SMTP; output/run_log.json + cost_history.csv
```

## Setup

1. **Accounts.** You need an [Apify](https://apify.com) token (free plan), a [SerpApi](https://serpapi.com) key (free plan), an OpenAI API key (paid, about $0.02 per run) and a Gmail [App Password](https://myaccount.google.com/apppasswords).
2. **Local run** (Python 3.12/3.13):
   ```bash
   python -m venv .venv && source .venv/bin/activate      # Windows: .venv\Scripts\Activate.ps1
   pip install -r requirements.txt                         # Linux: apt install libpango-1.0-0 libpangoft2-1.0-0
   cp .env.example .env                                    # add keys; never commit .env
   python main.py
   ```
   On Windows, PDF output also needs Pango (MSYS2: `pacman -S mingw-w64-ucrt-x86_64-pango`, then set `WEASYPRINT_DLL_DIRECTORIES=C:\msys64\ucrt64\bin`). Without it, the HTML brief is still produced.
3. **GitHub.** Under *Settings → Secrets and variables → Actions*, add `APIFY_TOKEN`, `SERPAPI_KEY`, `OPENAI_API_KEY`, `SMTP_USER`, `SMTP_PASSWORD` and `BRIEF_RECIPIENTS`. Under *Settings → Actions → General*, select *Read and write permissions*. Then go to *Actions → Koshk weekly brief → Run workflow*.

**Failure demo:** in *Run workflow*, set `simulate_fail` to `ads`, `trends` or `llm` (or set `SIMULATE_FAIL` locally). The brief still renders and reports the gap.

## Configuration (`config.yaml`)
- **Competitors.** Each competitor has Ad Library search URLs (Egypt, active) plus the official page names to keep. Chipsy and Doritos advertise via **PepsiCo Egypt**; Lion via its **LionEG** page.
- **Trends.** The search terms, which terms appear on the chart, and the low-data thresholds.
- **LLM.** The model and temperature. **Pricing:** per-provider prices used by the cost log.

## What stays manual
Choosing competitors, page names and search terms; checking prices; reviewing ideas before posting (audience and timing are suggestions); optionally filling `manual_inputs/*.csv` if a source is down for a long time (see `manual_inputs/README.md`).
