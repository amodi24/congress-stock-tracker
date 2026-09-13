# Senate Trade Ledger

A static site tracking stock trades disclosed by U.S. Senators under the STOCK Act, sourced directly from the Senate's official [eFD system](https://efdsearch.senate.gov/search/).

## How it works

- `scripts/scrape_senate_ptrs.py` queries the Senate eFD search system for Periodic Transaction Reports, parses each filing's transaction table, and writes the results to `data/trades.json` (with bookkeeping in `data/state.json`).
- `.github/workflows/update.yml` runs that script automatically every day at 9:00 AM US/Eastern and commits any new data, which GitHub Pages then redeploys automatically.
- `index.html` / `styles.css` / `app.js` is a plain static page (no build step, no framework) that reads `data/trades.json` client-side and renders a searchable, sortable, filterable table.

## Running locally

```
py -3 -m venv .venv
./.venv/Scripts/pip install -r scripts/requirements.txt
./.venv/Scripts/python scripts/scrape_senate_ptrs.py --force   # --force bypasses the 9am-ET schedule check
./.venv/Scripts/python -m http.server 8000
```

Then open `http://127.0.0.1:8000/`.

## Scope

- Senate only — House disclosures are largely non-machine-readable PDFs and are out of scope for now.
- Amounts are reported as ranges (not exact figures) per STOCK Act requirements; `amount_min`/`amount_max` in the data are parsed from those ranges for sorting.
- This is an independent, non-commercial, informational project — not affiliated with the U.S. Senate.
