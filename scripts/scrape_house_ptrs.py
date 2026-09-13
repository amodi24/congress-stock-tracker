"""Pulls House of Representatives Periodic Transaction Reports from the
Clerk's financial disclosure site and merges new transactions into
data/house_trades.json.

Unlike the Senate's electronic PTRs, House PTRs are filed as PDFs. Most
(the ones filed digitally, which is nearly all current filings) have
real extractable text that this script parses with a set of patterns
tuned against real filings. A minority of older/paper filings are
scanned images with no extractable text -- for those, best-effort OCR
(Tesseract) pulls out the asset names mentioned (cropped to just the
"Full Asset Name" column of the standard PTR form, to avoid the
checkbox/date columns, which OCR can't reliably map to the right grid
cell from text alone). These OCR entries carry no transaction date,
type, or amount -- just a company name and a link to the original PDF
-- and are flagged with ocr_best_effort so the UI can show them
differently from a fully-parsed transaction.

Safe to run daily forever: queries this year and last year every run
(cheap -- the site's own filter is year-granularity, not date-range),
but only downloads/parses PDFs whose filing ID isn't already in
data/house_state.json, and discards transactions outside the rolling
backfill window after parsing.
"""

import io
import json
import re
import shutil
import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pdfplumber
import pytesseract
import requests
from bs4 import BeautifulSoup

_WINDOWS_TESSERACT = r"C:\Program Files\Tesseract-OCR\tesseract.exe"
if shutil.which("tesseract"):
    pass  # already on PATH (e.g. apt-installed on the GitHub Actions runner)
elif Path(_WINDOWS_TESSERACT).exists():
    pytesseract.pytesseract.tesseract_cmd = _WINDOWS_TESSERACT

HOUSE_USER_AGENT = "congress-stock-tracker (personal, non-commercial) amodi24@hotmail.com"
BASE = "https://disclosures-clerk.house.gov"
SEARCH_PAGE = f"{BASE}/FinancialDisclosure/ViewSearch"
SEARCH_RESULTS = f"{BASE}/FinancialDisclosure/ViewMemberSearchResult"
REQUEST_DELAY_SECONDS = 0.3
BACKFILL_DAYS = 183
OCR_MAX_PAGES = 20  # cap per filing; some scanned filings run 50+ pages of attachments
OCR_RESOLUTION = 200
# Proportional crop of the rendered page, isolating the "Full Asset Name"
# column of the standard PTR form (measured against real sample filings).
OCR_CROP_LEFT = 0.045
OCR_CROP_RIGHT = 0.26
OCR_CROP_TOP = 0.22

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
HOUSE_TRADES_PATH = DATA_DIR / "house_trades.json"
STATE_PATH = DATA_DIR / "house_state.json"

OWNER_LABELS = {"SP": "Spouse", "JT": "Joint", "DC": "Dependent Child"}

TX_TYPE_LABELS = {
    "P": "Purchase",
    "S": "Sale (Full)",
    "S (partial)": "Sale (Partial)",
    "S (full)": "Sale (Full)",
    "Purchase": "Purchase",
    "Sale": "Sale (Full)",
    "Sale (Partial)": "Sale (Partial)",
    "Sale (Full)": "Sale (Full)",
    "Exchange": "Exchange",
}

TX_RE = re.compile(
    r"^(?P<pre>.*?)\s*(?P<type>Purchase|Sale \(Partial\)|Sale \(Full\)|Sale|Exchange"
    r"|P|S \(partial\)|S \(full\)|S)\s+"
    r"(?P<date1>\d{2}/\d{2}/\d{4})\s+\d{2}/\d{2}/\d{4}\s+"
    r"(?P<amount>\$[\d,]+(?:\s*-\s*\$?[\d,]+)?|Over\s+\$[\d,]+|\$[\d,]+\s*-\s*)\$?\s*\$?$"
)
TICKER_RE = re.compile(r"\(([A-Z][A-Z0-9.]{0,6})\)")
OWNER_RE = re.compile(r"^(SP|JT|DC)\s+")
AMOUNT_TAIL_RE = re.compile(r"\$[\d,]+\s*$")
HEADER_LINE_RE = re.compile(r"^(ID Owner Asset|Type Date Gains|\$200\?)")
SIGNED_RE = re.compile(r"Digitally Signed:.*?,\s*(\d{2}/\d{2}/\d{4})")

OCR_NOISE_RE = re.compile(r"^provide\b|not.*ticker|^[A-Za-z]{1,3}$", re.IGNORECASE)
OCR_PREFIX_RE = re.compile(r"^[\[\]{}|_\s]*[A-Za-z]{1,3}[_\s]+")
OCR_JUNK_CHARS_RE = re.compile(r"[\[\]{}|_]")


def new_session() -> requests.Session:
    session = requests.Session()
    session.headers.update({"User-Agent": HOUSE_USER_AGENT})
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


def get_csrf_token(session: requests.Session) -> str:
    resp = session.get(SEARCH_PAGE)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")
    token_input = soup.find("input", {"name": "__RequestVerificationToken"})
    if not token_input:
        raise RuntimeError("Could not find CSRF token on House disclosure search page")
    return token_input["value"]


def fetch_member_filings(session: requests.Session, token: str, year: int) -> list[dict]:
    resp = session.post(
        SEARCH_RESULTS,
        data={
            "__RequestVerificationToken": token,
            "LastName": "",
            "FilingYear": str(year),
            "State": "",
            "District": "",
        },
        headers={"Referer": SEARCH_PAGE},
    )
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")

    filings = []
    for row in soup.select("tr[role=row]"):
        cells = row.find_all("td")
        if len(cells) != 4:
            continue
        link = cells[0].find("a")
        if not link or not link.get("href", "").endswith(".pdf"):
            continue
        filing_type = cells[3].get_text(strip=True)
        if filing_type != "PTR Original":
            continue
        name_raw = link.get_text(strip=True).replace("Hon..", "").strip()
        if "," in name_raw:
            last, first = (p.strip() for p in name_raw.split(",", 1))
        else:
            last, first = name_raw, ""
        href = link["href"]
        filing_id = Path(href).stem
        filings.append(
            {
                "filing_id": filing_id,
                "pdf_url": f"{BASE}/{href}",
                "member_first": first,
                "member_last": last,
                "office": cells[1].get_text(strip=True),
            }
        )
    return filings


def lookahead_lines(lines: list[str], start_idx: int, count: int) -> list[str]:
    out = []
    j = start_idx + 1
    while j < len(lines) and len(out) < count:
        if not HEADER_LINE_RE.match(lines[j].strip()):
            out.append(lines[j])
        j += 1
    return out


def find_ticker(pre_text: str, lines: list[str], start_idx: int) -> str | None:
    matches = TICKER_RE.findall(pre_text)
    if matches:
        return matches[-1]
    for candidate in lookahead_lines(lines, start_idx, 3):
        m = TICKER_RE.search(candidate)
        if m:
            return m.group(1)
    return None


def complete_amount(amount: str, lines: list[str], start_idx: int) -> str:
    if amount.rstrip().endswith("-"):
        for candidate in lookahead_lines(lines, start_idx, 3):
            m = AMOUNT_TAIL_RE.search(candidate)
            if m:
                return amount.rstrip() + " " + m.group(0).strip()
    return amount


def parse_amount(amount_text: str) -> tuple[int | None, int | None]:
    numbers = [int(n.replace(",", "")) for n in re.findall(r"\$?([\d,]+)", amount_text)]
    if len(numbers) >= 2:
        return numbers[0], numbers[1]
    if len(numbers) == 1:
        if "over" in amount_text.lower():
            return numbers[0], None
        return numbers[0], numbers[0]
    return None, None


def mdy_to_iso(date_str: str) -> str | None:
    try:
        return datetime.strptime(date_str.strip(), "%m/%d/%Y").strftime("%Y-%m-%d")
    except ValueError:
        return None


def ocr_extract_asset_names(pdf) -> list[str]:
    """Best-effort: OCR just the 'Full Asset Name' column of each page
    (cropped to avoid the checkbox/date columns, which OCR can't reliably
    map to a grid cell) and return the distinct, cleaned lines found.
    Deliberately does NOT try to merge wrapped multi-line names -- an
    occasional split entry is a safer failure mode than incorrectly
    merging two unrelated companies together.
    """
    names: list[str] = []
    seen: set[str] = set()
    # Page 1 of this paper-form template is always a certification cover
    # sheet ("please see attached" etc), never real transaction rows.
    for page in pdf.pages[1 : 1 + OCR_MAX_PAGES]:
        image = page.to_image(resolution=OCR_RESOLUTION).original
        width, height = image.size
        cropped = image.crop(
            (int(width * OCR_CROP_LEFT), int(height * OCR_CROP_TOP), int(width * OCR_CROP_RIGHT), height)
        )
        text = pytesseract.image_to_string(cropped, config="--psm 4")
        for line in text.split("\n"):
            cleaned = OCR_PREFIX_RE.sub("", line.strip())
            cleaned = OCR_JUNK_CHARS_RE.sub("", cleaned).strip(" -.")
            if not cleaned or len(cleaned) < 4 or OCR_NOISE_RE.search(cleaned):
                continue
            key = cleaned.upper()
            if key not in seen:
                seen.add(key)
                names.append(cleaned)
    return names


def parse_pdf(pdf_bytes: bytes) -> tuple[str, str | None, list[dict]]:
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        text = "\n".join(page.extract_text() or "" for page in pdf.pages)

        if len(text.strip()) < 100:
            # Scanned/image PDF: no extractable text for the normal parser.
            asset_names = ocr_extract_asset_names(pdf)
            transactions = [
                {
                    "transaction_date_iso": None,
                    "owner": None,
                    "ticker": None,
                    "asset_name": name,
                    "transaction_type": None,
                    "amount_range": None,
                    "amount_min": None,
                    "amount_max": None,
                    "ocr_best_effort": True,
                }
                for name in asset_names
            ]
            return "ocr", None, transactions

    signed_match = SIGNED_RE.search(text)
    filing_date_iso = mdy_to_iso(signed_match.group(1)) if signed_match else None

    lines = text.split("\n")
    transactions = []
    for i, line in enumerate(lines):
        m = TX_RE.match(line.strip())
        if not m:
            continue
        pre = m.group("pre").strip()
        owner_match = OWNER_RE.match(pre)
        owner_code = owner_match.group(1) if owner_match else None
        asset_name = OWNER_RE.sub("", pre).strip()
        if not asset_name:
            continue

        amount_text = complete_amount(m.group("amount"), lines, i)
        amount_min, amount_max = parse_amount(amount_text)
        ticker = find_ticker(pre, lines, i)
        tx_type = TX_TYPE_LABELS.get(m.group("type"), m.group("type"))

        transactions.append(
            {
                "transaction_date_iso": mdy_to_iso(m.group("date1")),
                "owner": OWNER_LABELS.get(owner_code, "Self"),
                "ticker": ticker,
                "asset_name": asset_name,
                "transaction_type": tx_type,
                "amount_range": amount_text,
                "amount_min": amount_min,
                "amount_max": amount_max,
                "ocr_best_effort": False,
            }
        )
    return "digital", filing_date_iso, transactions


def is_scheduled_hour_ok() -> bool:
    if "--force" in sys.argv:
        return True
    eastern_hour = datetime.now(ZoneInfo("America/New_York")).hour
    return eastern_hour == 19


def main() -> None:
    if not is_scheduled_hour_ok():
        print("Not the scheduled 7pm US/Eastern hour yet on this trigger; skipping run.")
        return

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    trades = load_json(HOUSE_TRADES_PATH, [])
    state = load_json(STATE_PATH, {"processed_filing_ids": []})
    processed = set(state.get("processed_filing_ids", []))

    session = new_session()
    token = get_csrf_token(session)

    cutoff = (date.today() - timedelta(days=BACKFILL_DAYS)).isoformat()
    this_year = date.today().year

    all_filings = []
    for year in (this_year, this_year - 1):
        filings = fetch_member_filings(session, token, year)
        print(f"Year {year}: {len(filings)} PTR Original filings found.")
        all_filings.extend(filings)
        time.sleep(REQUEST_DELAY_SECONDS)

    # Filings previously marked unparseable (pre-OCR runs) are deliberately
    # NOT excluded here -- they're retried once under the OCR path below.
    new_filings = [f for f in all_filings if f["filing_id"] not in processed]
    print(f"{len(new_filings)} new filings to fetch and parse.")

    ocr_filings = 0
    ocr_assets_found = 0
    digital_filings = 0
    empty_filings = 0
    skipped_future_dated = 0
    kept_transactions = 0
    today_iso = date.today().isoformat()

    def checkpoint():
        save_json(HOUSE_TRADES_PATH, trades)
        save_json(
            STATE_PATH,
            {
                "processed_filing_ids": sorted(processed),
                "last_run_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "trade_count": len(trades),
            },
        )

    for i, filing in enumerate(new_filings, start=1):
        resp = session.get(filing["pdf_url"])
        time.sleep(REQUEST_DELAY_SECONDS)
        if resp.status_code != 200:
            continue

        mode, filing_date_iso, transactions = parse_pdf(resp.content)
        processed.add(filing["filing_id"])

        if mode == "ocr":
            ocr_filings += 1
            ocr_assets_found += len(transactions)
        elif transactions:
            digital_filings += 1
        else:
            empty_filings += 1

        for tx in transactions:
            tx_date = tx["transaction_date_iso"]
            if tx_date and tx_date < cutoff:
                continue
            if tx_date and tx_date > today_iso:
                # A handful of filings have an obvious filer typo (e.g. a
                # transaction "date" a year in the future, later than the
                # filing itself) -- skip rather than show an impossible date.
                skipped_future_dated += 1
                continue
            trades.append(
                {
                    "filing_id": filing["filing_id"],
                    "filing_date_iso": filing_date_iso,
                    "member_first": filing["member_first"],
                    "member_last": filing["member_last"],
                    "office": filing["office"],
                    "pdf_url": filing["pdf_url"],
                    **tx,
                }
            )
            kept_transactions += 1

        if i % 50 == 0:
            checkpoint()
            print(f"  ...checkpoint saved ({len(trades)} transactions so far)", flush=True)

    print(f"Parsed {digital_filings} filings digitally.")
    print(f"OCR'd {ocr_filings} scanned filings, finding {ocr_assets_found} best-effort asset mentions.")
    print(f"{empty_filings} filings yielded nothing (empty OCR or no matching transaction lines).")
    print(f"Skipped {skipped_future_dated} transactions with an impossible (filer-typo) future date.")
    print(f"Added {kept_transactions} transactions within the {BACKFILL_DAYS}-day window.")

    trades.sort(key=lambda t: (t["transaction_date_iso"] or "", t["member_last"]), reverse=True)
    checkpoint()
    print(f"Wrote {len(trades)} total House transactions.")


if __name__ == "__main__":
    main()
