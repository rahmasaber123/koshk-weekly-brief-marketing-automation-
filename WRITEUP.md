# Koshk Weekly Brief: write-up

**How it works.** A GitHub Actions schedule runs every Monday at 09:00 Cairo. It can also be started by hand. Python collectors gather the facts:
- **Ads:** active ads in Egypt from the Meta Ad Library, via Apify. Only ads from the brands' official pages are kept: PepsiCo Egypt for Chipsy/Doritos, and LionEG. Images are saved at collection time.
- **Trends:** Google Trends for Egypt via SerpApi: competitor terms only (Chipsy and Doritos in Arabic and Latin spelling, Lion as «شيبس ليون») over 90 days, plus rising related queries that mention a competitor; everything else is filtered out. Terms with too little data are left out of the brief.

Code then computes week-over-week changes and **writes every trend fact**. Three AI agents (PydanticAI, gpt-4o, temperature 0.2) only choose and interpret: which ads matter, which facts matter, and three content ideas that must cite an ad (A#) or finding (F#). A validator drops anything not backed by the data and logs it. The brief separates **facts** from **"So what:"** interpretation, shows source links, collection date, geo (EG) and each finding's date window, and notes that Trends scores are relative interest, not volumes.

**Manual:** choosing competitors, official pages and search terms; reviewing ideas before posting; optional manual CSVs if a source is down for weeks.

**Monthly cost (actual, from `run_log.json`, run of 18 Oct 2026).** Per run:
- Apify: ≈$0.12 for 20 ads, within the free plan's monthly credit
- SerpApi: 3 searches, $0 on the free plan (≈250 searches/month; 244 left)
- OpenAI gpt-4o: ≈6k tokens ≈ $0.02 (paid; no free tier)

That's ≈$0.14 per run, or **≈$0.60/month** at 4.33 runs. GitHub Actions uses about 2 minutes per run (free).

**What could break → how it's handled**
- **Meta Ad Library:** Meta's official API only covers EU commercial ads, so a scraper is used. If the scraper breaks, Meta changes its page or credit runs out: retry ×2 → manual CSV → the ads section is marked as a gap with an Ad Library link, and the rest of the brief continues.
- **SerpApi quota or outage:** retry → manual CSV → gap. Low-data terms are greyed out ("not enough search data").
- **LLM down or returns invalid output:** code facts still render, and ideas are marked unavailable. If the AI cites invented IDs or numbers, or names a competitor in a post idea, the model is asked to fix it and then the item is dropped. Every drop is logged.
- **Email fails:** the brief is still saved as an artifact.
- **Schedule:** GitHub cron can start late, and is paused after 60 days of repo inactivity. The weekly cost commit keeps it active. Ubuntu is pinned to 24.04.
- **Data reality:** Doritos had no active Egypt ads during collection. The brief says so instead of padding. Isolated zero days in Trends are Google sampling gaps. Lion is searched as «شيبس ليون» because «ليون» alone mostly means Lyon. Ad results vary between runs, so a competitor that comes back empty gets one extra Apify run.

**Reference video:** Watched. Adopted the core idea: an automated pipeline that turns research into a ready-to-read brief. Rejected tools that need paid plans or don't cover Egypt, and letting AI do the research end to end: here code collects and checks every fact and AI only interprets, because accuracy carries the most weight.

**Time: ~5 hours in total. ≈1.5 h on collectors (Apify ads, SerpApi trends) and config; ≈1.5 h on the AI agents and validation; ≈1 h on the one-page brief, chart and email; ≈1 h on accuracy fixes found in the first real runs (unofficial advertisers in keyword results, noisy rising queries) and GitHub Actions setup.
