# Senate & Insider Trade Ledger

A static site tracking two things side by side: stock trades disclosed by U.S. Senators under the STOCK Act ([Senate eFD system](https://efdsearch.senate.gov/search/)), and stock trades by corporate insiders (officers, directors, 10%+ owners) at the companies those senators have traded ([SEC EDGAR](https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany) Form 4 filings).

## How it works

- `scripts/scrape_senate_ptrs.py` queries the Senate eFD search system for Periodic Transaction Reports, parses each filing's transaction table, and writes the results to `data/trades.json` (bookkeeping in `data/state.json`).
- `scripts/scrape_insider_trades.py` reads the tickers out of `data/trades.json`, resolves each to a CIK via SEC's `company_tickers.json`, and pulls each company's Form 4 (insider) filings from `data/submissions/CIK##########.json` + the underlying filing XML. Writes to `data/insider_trades.json` (bookkeeping in `data/insider_state.json`). Because the ticker list is recomputed from the Senate data every run, insider coverage automatically expands as senators trade new names.
- `.github/workflows/update.yml` runs both scripts automatically every day at 9:00 AM US/Eastern and commits any new data, which GitHub Pages then redeploys automatically.
- `index.html` / `styles.css` / `app.js` is a plain static page (no build step, no framework) that reads both JSON files client-side, normalizes them into one shape, and renders a single searchable/sortable/filterable table with a Source filter (Senators / Insiders / All).

## Running locally

```
py -3 -m venv .venv
./.venv/Scripts/pip install -r scripts/requirements.txt
./.venv/Scripts/python scripts/scrape_senate_ptrs.py --force      # --force bypasses the 9am-ET schedule check
./.venv/Scripts/python scripts/scrape_insider_trades.py --force   # run after the Senate script; it reads trades.json
./.venv/Scripts/python -m http.server 8000
```

Then open `http://127.0.0.1:8000/`.

## Enabling the visit counter (optional)

The "Site visits" stat tile uses [GoatCounter](https://www.goatcounter.com/) (free, privacy-friendly, no cookies). It's off by default — the tile stays hidden until it's configured. To turn it on:

1. Sign up at goatcounter.com and pick a site code (e.g. `congress-stock-tracker`, giving you `https://congress-stock-tracker.goatcounter.com`).
2. In that site's Settings, enable **"Allow adding visitor counts on your website"** (off by default) so the public JSON count endpoint responds.
3. Replace `YOUR-GOATCOUNTER-CODE` in both `index.html` (the `data-goatcounter` script tag) and `app.js` (`GOATCOUNTER_CODE`) with your real site code, then commit and push.

If the public JSON endpoint ever turns out not to allow cross-origin `fetch()` in practice, swap `loadVisitCount()` in `app.js` for GoatCounter's own `<img>`/`<iframe>` embed snippet instead (see their [visitor counter docs](https://www.goatcounter.com/help/visitor-counter)) — that route sidesteps CORS entirely, at the cost of not matching the page's own typography.

## Scope

- **Senate only** on the official side — House disclosures are largely non-machine-readable PDFs and are out of scope for now.
- **Insiders are scoped to companies senators have traded**, not the full market. A full-market Form 4 feed is ~900 filings/day (~110k+ over 6 months) and would need a materially different architecture; this keeps the site fast and lets you compare official vs. insider activity in the same stocks.
- **Insider transactions are non-derivative only** (direct common-stock buys/sells/awards) — option/derivative activity (the `derivativeTable` in Form 4) isn't parsed.
- Senate amounts are disclosed as ranges, not exact figures, per STOCK Act rules. Insider amounts are exact (`shares × price`) straight from the Form 4 XML.
- SEC EDGAR requires a descriptive `User-Agent` with a real contact on every request; the scraper sends one identifying this project.
- This is an independent, non-commercial, informational project — not affiliated with the U.S. Senate or the SEC.
