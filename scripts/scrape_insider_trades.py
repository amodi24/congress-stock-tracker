"""Pulls SEC Form 4 insider-trading filings for the companies senators have
traded (derived from data/trades.json) and merges new transactions into
data/insider_trades.json.

Scope: only the non-derivative table (direct common-stock buys/sells/awards),
not option/derivative activity, and only companies that already appear in
the Senate dataset -- not the full market. See README for why.

Safe to run daily forever: recomputes the ticker set from trades.json each
run (so it stays in sync as senators trade new names), but only fetches
filings whose accession number isn't already in data/insider_state.json.
"""

import json
import sys
import time
import xml.etree.ElementTree as ET
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

SEC_USER_AGENT = "congress-stock-tracker (personal, non-commercial) amodi24@hotmail.com"
TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik:010d}.json"
ARCHIVES_BASE = "https://www.sec.gov/Archives/edgar/data"
REQUEST_DELAY_SECONDS = 0.2
BACKFILL_DAYS = 183

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
SENATE_TRADES_PATH = DATA_DIR / "trades.json"
INSIDER_TRADES_PATH = DATA_DIR / "insider_trades.json"
STATE_PATH = DATA_DIR / "insider_state.json"

TRANSACTION_CODE_LABELS = {
    "P": "Open Market Purchase",
    "S": "Open Market Sale",
    "A": "Grant / Award",
    "D": "Disposition to Issuer",
    "F": "Tax Withholding",
    "M": "Option Exercise",
    "G": "Gift",
    "C": "Conversion",
    "X": "Option Exercise",
    "J": "Other",
}


def new_session() -> requests.Session:
    session = requests.Session()
    session.headers.update({"User-Agent": SEC_USER_AGENT})
    return session


def load_json(path: Path, default):
    if path.exists():
        with path.open(encoding="utf-8") as fh:
            return json.load(fh)
    return default


def save_json(path: Path, data) -> None:
    with path.open("w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2, ensure_ascii=False)
        fh.write("\n")


def tickers_from_senate_data() -> list[str]:
    trades = load_json(SENATE_TRADES_PATH, [])
    return sorted({t["ticker"] for t in trades if t.get("ticker")})


def build_ticker_to_cik(session: requests.Session) -> dict[str, int]:
    resp = session.get(TICKERS_URL)
    resp.raise_for_status()
    time.sleep(REQUEST_DELAY_SECONDS)
    return {row["ticker"].upper(): row["cik_str"] for row in resp.json().values()}


def fetch_recent_form4_filings(session: requests.Session, cik: int, start_date: date) -> list[dict]:
    url = SUBMISSIONS_URL.format(cik=cik)
    resp = session.get(url)
    time.sleep(REQUEST_DELAY_SECONDS)
    if resp.status_code == 404:
        return []
    resp.raise_for_status()
    recent = resp.json()["filings"]["recent"]

    filings = []
    for i, form in enumerate(recent["form"]):
        if form != "4":
            continue
        filing_date = recent["filingDate"][i]
        if filing_date < start_date.isoformat():
            continue
        primary_doc = recent["primaryDocument"][i]
        filings.append(
            {
                "accession": recent["accessionNumber"][i],
                "filing_date": filing_date,
                "xml_name": primary_doc.rsplit("/", 1)[-1],
            }
        )
    return filings


def xml_text(el, path: str) -> str | None:
    if el is None:
        return None
    node = el.find(path)
    return node.text.strip() if node is not None and node.text else None


def parse_form4(xml_bytes: bytes, filing_date: str, queried_ticker: str) -> list[dict]:
    root = ET.fromstring(xml_bytes)

    issuer = root.find("issuer")
    # Use the ticker we queried by (from the Senate dataset), not whatever
    # issuerTradingSymbol the filing itself reports -- older filings or a
    # different share class can report a different symbol for the same CIK,
    # which would otherwise break the ticker-based cross-reference with
    # Senate trades.
    ticker = queried_ticker
    issuer_name = xml_text(issuer, "issuerName") or ""

    owner = root.find("reportingOwner")
    owner_name = xml_text(owner, "reportingOwnerId/rptOwnerName") or ""
    rel = owner.find("reportingOwnerRelationship") if owner is not None else None
    officer_title = xml_text(rel, "officerTitle")

    def is_true(value: str | None) -> bool:
        return value is not None and value.strip().lower() in ("true", "1")

    roles = []
    if is_true(xml_text(rel, "isOfficer")):
        roles.append(officer_title if officer_title else "Officer")
    if is_true(xml_text(rel, "isDirector")):
        roles.append("Director")
    if is_true(xml_text(rel, "isTenPercentOwner")):
        roles.append("10% Owner")
    role = " / ".join(roles) if roles else "Insider"

    table = root.find("nonDerivativeTable")
    if table is None:
        return []

    transactions = []
    for tx in table.findall("nonDerivativeTransaction"):
        tx_date = xml_text(tx, "transactionDate/value")
        code = xml_text(tx, "transactionCoding/transactionCode")
        shares_text = xml_text(tx, "transactionAmounts/transactionShares/value")
        price_text = xml_text(tx, "transactionAmounts/transactionPricePerShare/value")
        acquired_disposed = xml_text(tx, "transactionAmounts/transactionAcquiredDisposedCode/value")

        shares = float(shares_text) if shares_text else None
        price = float(price_text) if price_text else None
        value_usd = round(shares * price, 2) if shares and price else None

        transactions.append(
            {
                "source": "insider",
                "ticker": ticker,
                "issuer_name": issuer_name,
                "reporting_owner": owner_name,
                "role": role,
                "transaction_date_iso": tx_date,
                "filing_date_iso": filing_date,
                "transaction_code": code,
                "transaction_code_label": TRANSACTION_CODE_LABELS.get(code, code or "Other"),
                "acquired_disposed": acquired_disposed,
                "shares": shares,
                "price_per_share": price,
                "value_usd": value_usd,
            }
        )
    return transactions


def is_scheduled_hour_ok() -> bool:
    if "--force" in sys.argv:
        return True
    eastern_hour = datetime.now(ZoneInfo("America/New_York")).hour
    return eastern_hour == 19


def main() -> None:
    if not is_scheduled_hour_ok():
        print("Not the scheduled 7pm US/Eastern hour yet on this trigger; skipping run.")
        return

    tickers = tickers_from_senate_data()
    if not tickers:
        print("No Senate tickers found yet; run scrape_senate_ptrs.py first.")
        return

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    trades = load_json(INSIDER_TRADES_PATH, [])
    state = load_json(STATE_PATH, {"processed_accessions": []})
    processed = set(state.get("processed_accessions", []))

    session = new_session()
    ticker_to_cik = build_ticker_to_cik(session)

    start_date = date.today() - timedelta(days=BACKFILL_DAYS)
    resolved = 0
    new_filing_count = 0

    def checkpoint():
        save_json(INSIDER_TRADES_PATH, trades)
        save_json(
            STATE_PATH,
            {
                "processed_accessions": sorted(processed),
                "last_run_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "trade_count": len(trades),
            },
        )

    for i, ticker in enumerate(tickers, start=1):
        cik = ticker_to_cik.get(ticker.upper())
        if not cik:
            continue
        resolved += 1

        filings = fetch_recent_form4_filings(session, cik, start_date)
        new_here = [f for f in filings if f["accession"] not in processed]
        print(
            f"[{i}/{len(tickers)}] {ticker}: {len(filings)} filings in window, "
            f"{len(new_here)} new (total filings processed so far: {new_filing_count})",
            flush=True,
        )
        for filing in new_here:
            accession_no_dash = filing["accession"].replace("-", "")
            url = f"{ARCHIVES_BASE}/{cik}/{accession_no_dash}/{filing['xml_name']}"
            resp = session.get(url)
            time.sleep(REQUEST_DELAY_SECONDS)
            processed.add(filing["accession"])
            if resp.status_code != 200:
                continue
            try:
                transactions = parse_form4(resp.content, filing["filing_date"], ticker)
            except ET.ParseError:
                continue
            trades.extend(transactions)
            new_filing_count += 1

        if i % 25 == 0:
            checkpoint()
            print(f"  ...checkpoint saved ({len(trades)} transactions so far)", flush=True)

    print(f"Resolved {resolved}/{len(tickers)} Senate tickers to a CIK.")
    print(f"Processed {new_filing_count} new Form 4 filings.")

    trades.sort(key=lambda t: (t["transaction_date_iso"] or "", t["reporting_owner"]), reverse=True)
    save_json(INSIDER_TRADES_PATH, trades)
    save_json(
        STATE_PATH,
        {
            "processed_accessions": sorted(processed),
            "last_run_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "trade_count": len(trades),
        },
    )
    print(f"Wrote {len(trades)} total insider transactions.")


if __name__ == "__main__":
    main()
