"""Pulls Senate Periodic Transaction Reports from the official eFD system
(efdsearch.senate.gov) and merges any new transactions into data/trades.json.

Safe to run daily forever: it re-queries a rolling ~6 month window on every
run, but only fetches and parses filings whose UUID isn't already recorded
in data/state.json, so repeat runs are cheap and idempotent.
"""

import json
import re
import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup

BASE = "https://efdsearch.senate.gov"
SEARCH_HOME = f"{BASE}/search/home/"
SEARCH_DATA = f"{BASE}/search/report/data/"
PTR_REPORT_TYPE = "11"
PAGE_SIZE = 100
REQUEST_DELAY_SECONDS = 0.5
BACKFILL_DAYS = 183  # ~6 months

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
TRADES_PATH = DATA_DIR / "trades.json"
STATE_PATH = DATA_DIR / "state.json"

UUID_RE = re.compile(r"/search/view/ptr/([0-9a-f-]+)/")


def new_session() -> requests.Session:
    session = requests.Session()
    session.headers.update({"User-Agent": "congress-stock-tracker/1.0 (personal, non-commercial)"})
    return session


def accept_agreement(session: requests.Session) -> None:
    resp = session.get(SEARCH_HOME)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")
    token_input = soup.find("input", {"name": "csrfmiddlewaretoken"})
    if not token_input:
        raise RuntimeError("Could not find CSRF token on eFD search home page")
    csrf_token = token_input["value"]
    resp = session.post(
        SEARCH_HOME,
        data={"csrfmiddlewaretoken": csrf_token, "prohibition_agreement": "1"},
        headers={"Referer": SEARCH_HOME},
    )
    resp.raise_for_status()


def fetch_ptr_rows(session: requests.Session, start_date: date) -> list[dict]:
    csrf_token = session.cookies.get("csrftoken")
    if not csrf_token:
        raise RuntimeError("No csrftoken cookie after accepting agreement")

    headers = {
        "X-CSRFToken": csrf_token,
        "X-Requested-With": "XMLHttpRequest",
        "Referer": f"{BASE}/search/",
    }
    base_payload = {
        "report_types": f"[{PTR_REPORT_TYPE}]",
        "filer_types": "[]",
        "submitted_start_date": start_date.strftime("%m/%d/%Y 00:00:00"),
        "submitted_end_date": "",
        "candidate_state": "",
        "senator_state": "",
        "office_id": "",
        "first_name": "",
        "last_name": "",
    }

    rows: list[dict] = []
    start = 0
    while True:
        payload = dict(base_payload, draw=1, start=start, length=PAGE_SIZE)
        resp = session.post(SEARCH_DATA, data=payload, headers=headers)
        resp.raise_for_status()
        body = resp.json()
        page_rows = body.get("data", [])
        rows.extend(page_rows)
        total = body.get("recordsTotal", 0)
        start += PAGE_SIZE
        if start >= total or not page_rows:
            break
        time.sleep(REQUEST_DELAY_SECONDS)

    filings = []
    for first, last, _office, link_html, filed_date in rows:
        match = UUID_RE.search(link_html)
        if not match:
            continue
        filings.append(
            {
                "filing_id": match.group(1),
                "senator_first": first.strip(),
                "senator_last": last.strip(),
                "filing_date": filed_date.strip(),
            }
        )
    return filings


def mdy_to_iso(date_str: str) -> str | None:
    try:
        return datetime.strptime(date_str.strip(), "%m/%d/%Y").strftime("%Y-%m-%d")
    except ValueError:
        return None


def parse_amount(amount_text: str) -> tuple[int | None, int | None]:
    numbers = [int(n.replace(",", "")) for n in re.findall(r"\$?([\d,]+)", amount_text)]
    if len(numbers) >= 2:
        return numbers[0], numbers[1]
    if len(numbers) == 1:
        if "over" in amount_text.lower():
            return numbers[0], None
        return numbers[0], numbers[0]
    return None, None


def fetch_transactions(session: requests.Session, filing: dict) -> list[dict]:
    url = f"{BASE}/search/view/ptr/{filing['filing_id']}/"
    resp = session.get(url)
    if resp.status_code == 404:
        return []
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")

    table = soup.find("table", class_="table-striped")
    if not table or not table.find("tbody"):
        return []

    transactions = []
    for tr in table.find("tbody").find_all("tr"):
        cells = [td.get_text(strip=True, separator=" ") for td in tr.find_all("td")]
        if len(cells) != 9:
            continue
        _seq, tx_date, owner, ticker, asset_name, asset_type, tx_type, amount_text, comment = cells
        amount_min, amount_max = parse_amount(amount_text)
        transactions.append(
            {
                "filing_id": filing["filing_id"],
                "filing_date": filing["filing_date"],
                "filing_date_iso": mdy_to_iso(filing["filing_date"]),
                "senator_first": filing["senator_first"],
                "senator_last": filing["senator_last"],
                "transaction_date": tx_date,
                "transaction_date_iso": mdy_to_iso(tx_date),
                "owner": owner,
                "ticker": ticker if ticker and ticker != "--" else None,
                "asset_name": asset_name,
                "asset_type": asset_type,
                "transaction_type": tx_type,
                "amount_range": amount_text,
                "amount_min": amount_min,
                "amount_max": amount_max,
                "comment": comment if comment and comment != "--" else None,
            }
        )
    return transactions


def load_json(path: Path, default):
    if path.exists():
        with path.open(encoding="utf-8") as fh:
            return json.load(fh)
    return default


def save_json(path: Path, data) -> None:
    with path.open("w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2, ensure_ascii=False)
        fh.write("\n")


def is_scheduled_hour_ok() -> bool:
    """When run from GitHub Actions' two DST-covering cron triggers, only the
    trigger that actually lands on 7pm US/Eastern should do a real run."""
    if "--force" in sys.argv:
        return True
    eastern_hour = datetime.now(ZoneInfo("America/New_York")).hour
    return eastern_hour == 19


def main() -> None:
    if not is_scheduled_hour_ok():
        print("Not the scheduled 7pm US/Eastern hour yet on this trigger; skipping run.")
        return

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    trades = load_json(TRADES_PATH, [])
    state = load_json(STATE_PATH, {"processed_filing_ids": []})
    processed = set(state.get("processed_filing_ids", []))

    session = new_session()
    accept_agreement(session)

    start_date = date.today() - timedelta(days=BACKFILL_DAYS)
    filings = fetch_ptr_rows(session, start_date)
    new_filings = [f for f in filings if f["filing_id"] not in processed]
    print(f"Found {len(filings)} filings in window, {len(new_filings)} new.")

    for filing in new_filings:
        transactions = fetch_transactions(session, filing)
        trades.extend(transactions)
        processed.add(filing["filing_id"])
        time.sleep(REQUEST_DELAY_SECONDS)

    trades.sort(key=lambda t: (t["transaction_date_iso"] or "", t["senator_last"]), reverse=True)
    save_json(TRADES_PATH, trades)
    save_json(
        STATE_PATH,
        {
            "processed_filing_ids": sorted(processed),
            "last_run_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "trade_count": len(trades),
        },
    )
    print(f"Wrote {len(trades)} total transactions, {len(new_filings)} filings processed this run.")


if __name__ == "__main__":
    main()
