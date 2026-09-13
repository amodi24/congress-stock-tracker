# Congress & Insider Trade Ledger

A static site tracking stock trades disclosed by U.S. Senators and Representatives under the STOCK Act ([Senate eFD system](https://efdsearch.senate.gov/search/), [House Clerk's disclosure site](https://disclosures-clerk.house.gov/FinancialDisclosure)), alongside stock trades by corporate insiders (officers, directors, 10%+ owners) at the companies senators have traded ([SEC EDGAR](https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany) Form 4 filings).

## How it works

- `scripts/scrape_senate_ptrs.py` queries the Senate eFD search system for Periodic Transaction Reports, parses each filing's transaction table, and writes to `data/trades.json` (bookkeeping in `data/state.json`).
- `scripts/scrape_house_ptrs.py` queries the House Clerk's member-search form for PTR filings and downloads each one's PDF. Most current House PTRs are digitally generated with real extractable text, which is parsed with a set of regex patterns tuned against real filings (see "House PDF parsing" below); a minority are older scanned documents with no extractable text and are skipped. Writes to `data/house_trades.json` (bookkeeping in `data/house_state.json`).
- `scripts/scrape_insider_trades.py` reads the tickers out of `data/trades.json` (Senate only), resolves each to a CIK via SEC's `company_tickers.json`, and pulls each company's Form 4 (insider) filings from `data.sec.gov/submissions/CIK##########.json` + the underlying filing XML. Writes to `data/insider_trades.json` (bookkeeping in `data/insider_state.json`).
- `.github/workflows/update.yml` runs all three scripts automatically every day at 7:00 PM US/Eastern (after market close) and commits any new data, which GitHub Pages then redeploys automatically.
- `index.html` / `styles.css` / `app.js` is a plain static page (no build step, no framework) that reads all three JSON files client-side, normalizes them into one shape, and renders a single searchable/sortable/filterable table with a Source filter (Senators / Representatives / Insiders / All).

## Running locally

The House script's OCR fallback needs the [Tesseract](https://github.com/tesseract-ocr/tesseract) binary installed separately (it's not pip-installable): `winget install UB-Mannheim.TesseractOCR` on Windows, `sudo apt-get install tesseract-ocr` on Ubuntu/Debian (this is also how the GitHub Actions runner gets it). The script looks for `tesseract` on `PATH` first, falling back to the default Windows install location.

```
py -3 -m venv .venv
./.venv/Scripts/pip install -r scripts/requirements.txt
./.venv/Scripts/python scripts/scrape_senate_ptrs.py --force      # --force bypasses the 7pm-ET schedule check
./.venv/Scripts/python scripts/scrape_house_ptrs.py --force
./.venv/Scripts/python scripts/scrape_insider_trades.py --force   # run after the Senate script; it reads trades.json
./.venv/Scripts/python -m http.server 8000
```

Then open `http://127.0.0.1:8000/`.

## House PDF parsing

House PTRs don't have a clean structured format like the Senate's HTML tables or SEC's XML. Each filing is a PDF; `scrape_house_ptrs.py` extracts text with `pdfplumber` and matches each transaction line with a regex tuned against real filings, handling:
- Asset names and ticker/type-code brackets that wrap onto a different line than the transaction data
- Large dollar amounts (e.g. `$1,000,001 - $5,000,000`) whose second half wraps onto the next line, sometimes trailing after a ticker bracket
- Page-break artifacts where the table header re-prints mid-filing
- Owner codes (`SP`/`JT`/`DC` prefixes, blank = Self)
- A handful of filings with an internal data-entry error (e.g. a transaction "date" that's literally in the future, later than the filing itself) — skipped rather than shown as an impossible date

This is inherently more fragile than the Senate/SEC sources; if a future filing uses a slightly different layout the regex hasn't seen, it may under-parse that one filing rather than crash the whole run.

### Scanned filings (best-effort OCR)

~1 in 8 filings (based on the initial backfill) have no extractable text at all — they're scanned images of the older paper PTR form, not digitally generated. For these, `scrape_house_ptrs.py` falls back to OCR (`pytesseract` + Tesseract):
- Renders each page (skipping page 1, which is always this form's certification cover sheet, never data) and crops to just the "Full Asset Name" column, avoiding the checkbox/date columns entirely
- OCR can tell you *what a word says*, but not reliably *which grid cell it's in* — mapping an "X" mark to the right Purchase/Sale/Amount column would need real table-structure detection (gridline finding + coordinate mapping), which is out of scope here. So these entries carry **no transaction date, type, or amount** — just the company name and a link to the original PDF (labeled "Scanned — unverified" in the UI)
- Deliberately does not try to merge company names that wrap across two lines in the scan — an occasional split entry (e.g. "Fund Class P" on its own row) is a safer failure mode than incorrectly merging two unrelated companies into one
- Capped at 20 pages per filing; a few filings run 50+ pages of brokerage attachments and are only partially covered

This is best-effort, not authoritative — always follow the linked PDF to confirm details for anything that matters.

## Enabling the visit counter (optional)

The "Site visits" stat tile uses [GoatCounter](https://www.goatcounter.com/) (free, privacy-friendly, no cookies). It's off by default — the tile stays hidden until it's configured. To turn it on:

1. Sign up at goatcounter.com and pick a site code (e.g. `congress-stock-tracker`, giving you `https://congress-stock-tracker.goatcounter.com`).
2. In that site's Settings, enable **"Allow adding visitor counts on your website"** (off by default) so the public JSON count endpoint responds.
3. Replace `YOUR-GOATCOUNTER-CODE` in both `index.html` (the `data-goatcounter` script tag) and `app.js` (`GOATCOUNTER_CODE`) with your real site code, then commit and push.

If the public JSON endpoint ever turns out not to allow cross-origin `fetch()` in practice, swap `loadVisitCount()` in `app.js` for GoatCounter's own `<img>`/`<iframe>` embed snippet instead (see their [visitor counter docs](https://www.goatcounter.com/help/visitor-counter)) — that route sidesteps CORS entirely, at the cost of not matching the page's own typography.

## Scope

- **Insiders are scoped to companies senators have traded**, not the full market and not (yet) companies only Representatives have traded. A full-market Form 4 feed is ~900 filings/day (~110k+ over 6 months) and would need a materially different architecture; this keeps the site fast and lets you compare official vs. insider activity in the same stocks.
- **Insider transactions are non-derivative only** (direct common-stock buys/sells/awards) — option/derivative activity (the `derivativeTable` in Form 4) isn't parsed.
- Senate and House amounts are disclosed as ranges, not exact figures, per STOCK Act rules. Insider amounts are exact (`shares × price`) straight from the Form 4 XML.
- SEC EDGAR and the House Clerk's site both see a descriptive `User-Agent` with a real contact on every request.
- This is an independent, non-commercial, informational project — not affiliated with the U.S. Senate, the U.S. House, or the SEC.
