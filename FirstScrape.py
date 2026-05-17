#!/usr/bin/env python3
"""
FirstScrape.py

Usage:
    python FirstScrape.py urls.txt output.csv

    # or just run it with defaults (urls.txt -> trees.csv in the same folder):
    python FirstScrape.py

Dependencies:
    pip install requests beautifulsoup4
"""

import csv
import json
import re
import sys
import time
from pathlib import Path
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

# --- Config ---------------------------------------------------------------

DEFAULT_INPUT = "urls.txt"
DEFAULT_OUTPUT = "trees.csv"
DEFAULT_LOCATIONS = "List of Trees ID-Type-Location.txt"

REQUEST_TIMEOUT = 20          # seconds per request
DELAY_BETWEEN_REQUESTS = 1.0  # polite delay
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0 Safari/537.36"
    )
}

CSV_FIELDS = [
    "url",
    "tree_id",
    "tree_name",
    "latitude",
    "longitude",
    "dedication_type",
    "dedication_year",
    "dedication_honoree",
    "dedication_desc",
    "more_details_url",
    "error",
]


# --- Tree-location lookup -------------------------------------------------

def load_tree_locations(path: Path) -> dict:
    """
    Load a {tree_id: (latitude, longitude)} mapping from the
    "List of Trees ID-Type-Location.txt" file.

    The file is JSON of the form:
        {"trees": [
            ["1", "86149", "41.894508362", "-87.945060730", "...", ""],
            ...
        ]}

    Per the data spec:
      - element 0 is the tree ID
      - element 2 is the latitude
      - element 3 is the longitude
    """
    if not path.exists():
        print(f"Warning: locations file not found: {path}")
        return {}

    try:
        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)
    except json.JSONDecodeError as e:
        print(f"Warning: could not parse {path} as JSON: {e}")
        return {}

    lookup = {}
    for entry in data.get("trees", []):
        if not isinstance(entry, list) or len(entry) < 4:
            continue
        tree_id = str(entry[0]).strip()
        latitude = str(entry[2]).strip()
        longitude = str(entry[3]).strip()
        if tree_id:
            lookup[tree_id] = (latitude, longitude)

    return lookup

# --- Extraction helpers ---------------------------------------------------

def _clean(text: str) -> str:
    """Collapse whitespace and strip."""
    if text is None:
        return ""
    return re.sub(r"\s+", " ", text).strip()


def extract_tree_id_number(soup: BeautifulSoup) -> str:
    """
    Pull the numeric Tree ID out of text like "Tree ID: 1".
    """
    label = soup.find(string=re.compile(r"Tree\s*ID\s*:", re.I))
    if not label:
        return ""
    m = re.search(r"Tree\s*ID\s*:\s*(\S+)", str(label), re.I)
    return _clean(m.group(1)) if m else ""


def extract_tree_name(soup: BeautifulSoup) -> str:
    """
    Find the <strong> element that sits under the "Tree ID: N" label.

    On arborscope pages the markup looks like:
        <h3><a ...>
            <span>Tree ID: 1</span><br />
            <strong>Chestnut Oak</strong>
        </a></h3>

    Strategy:
      1) Find text "Tree ID" and return the next <strong> in document order.
      2) Fall back: the first <strong> inside an <h3>.
      3) Last resort: the first <strong> on the page.
    """
    label = soup.find(string=re.compile(r"Tree\s*ID", re.I))
    if label:
        nxt = label.find_next("strong")
        if nxt and _clean(nxt.get_text()):
            return _clean(nxt.get_text())

    h3 = soup.find("h3")
    if h3:
        strong = h3.find("strong")
        if strong and _clean(strong.get_text()):
            return _clean(strong.get_text())

    first_strong = soup.find("strong")
    return _clean(first_strong.get_text()) if first_strong else ""


def extract_labeled_value(soup: BeautifulSoup, label: str) -> str:
    """
    Extract the value that appears after a "Label:" string in the page.

    Handles common patterns like:
        <b>Dedication Type:</b> Memorial
        Dedication Type: Memorial<br>
        <strong>Dedication Type:</strong><span>Memorial</span>
    """
    pattern = re.compile(r"\b" + re.escape(label) + r"\s*:?\s*", re.I)

    # Find the text node containing the label
    node = soup.find(string=pattern)
    if not node:
        return ""

    # Case A: the label and value live in the same text node, e.g.
    # "Dedication Type: Memorial"
    full_text = str(node)
    m = pattern.search(full_text)
    if m:
        tail = full_text[m.end():].strip()
        if tail:
            # Stop at the next "Dedication <word>:" label if several share one node
            tail = re.split(r"\s*Dedication\s+\w+\s*:", tail, maxsplit=1)[0]
            cleaned = _clean(tail)
            if cleaned:
                return cleaned

    # Case B: the value is in a sibling/next element
    parent = node.parent
    if parent:
        # gather text from the parent, strip the label, return the remainder
        parent_text = _clean(parent.get_text(" ", strip=True))
        stripped = pattern.sub("", parent_text, count=1).strip()
        if stripped:
            stripped = re.split(r"\s*Dedication\s+\w+\s*:", stripped, maxsplit=1)[0]
            return _clean(stripped)

    # Case C: walk forward through siblings until we hit non-empty text
    for sib in node.next_elements:
        if sib is node:
            continue
        text = _clean(getattr(sib, "get_text", lambda *a, **k: str(sib))())
        if text and not pattern.match(text):
            return text

    return ""


def extract_more_details_url(soup: BeautifulSoup, base_url: str) -> str:
    """
    Find the URL behind the "View more details" control.

    On arborscope pages it's an <input type="button"> whose onclick calls
    window.open('/featureDetails.cfm?...', '_blank'). We also handle the
    plain-<a> case in case the markup varies between pages.
    """
    # 1) <input type="button" value="View more details" onclick="window.open(...)">
    btn = soup.find(
        "input",
        attrs={"value": re.compile(r"view\s*more\s*details", re.I)},
    )
    if btn and btn.get("onclick"):
        m = re.search(r"""window\.open\(\s*['"]([^'"]+)['"]""", btn["onclick"])
        if m:
            return urljoin(base_url, m.group(1))

    # 2) <button onclick="...">View more details</button>
    for b in soup.find_all(["button", "input"]):
        text = b.get("value") or b.get_text(" ", strip=True)
        if text and re.search(r"view\s*more\s*details", text, re.I):
            onclick = b.get("onclick", "")
            m = re.search(r"""window\.open\(\s*['"]([^'"]+)['"]""", onclick)
            if m:
                return urljoin(base_url, m.group(1))

    # 3) <a href="...">view more details</a>
    link = soup.find("a", string=re.compile(r"view\s*more\s*details", re.I))
    if not link:
        for a in soup.find_all("a"):
            if re.search(r"view\s*more\s*details", a.get_text(" ", strip=True), re.I):
                link = a
                break
    if link and link.get("href"):
        return urljoin(base_url, link["href"].strip())

    return ""


# --- Main loop ------------------------------------------------------------

def scrape_url(url: str, session: requests.Session, locations: dict) -> dict:
    """Fetch a single URL and return a row dict."""
    row = {field: "" for field in CSV_FIELDS}
    row["url"] = url

    try:
        resp = session.get(url, headers=HEADERS, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
    except requests.RequestException as e:
        row["error"] = f"request failed: {e}"
        return row

    try:
        soup = BeautifulSoup(resp.text, "html.parser")
        row["tree_id"]            = extract_tree_id_number(soup)
        row["tree_name"]          = extract_tree_name(soup)
        row["dedication_type"]    = extract_labeled_value(soup, "Dedication Type")
        row["dedication_year"]    = extract_labeled_value(soup, "Dedication Year")
        row["dedication_honoree"] = extract_labeled_value(soup, "Dedication Honoree")
        row["dedication_desc"]    = extract_labeled_value(soup, "Dedication Description")
        row["more_details_url"]   = extract_more_details_url(soup, url)

        # Look up latitude / longitude by tree ID
        lat, lon = locations.get(row["tree_id"], ("", ""))
        row["latitude"]  = lat
        row["longitude"] = lon
    except Exception as e:  # noqa: BLE001
        row["error"] = f"parse failed: {e}"

    return row


def main(input_path: Path, output_path: Path, locations_path: Path) -> None:
    if not input_path.exists():
        sys.exit(f"Input file not found: {input_path}")

    with input_path.open("r", encoding="utf-8") as f:
        urls = [line.strip() for line in f if line.strip() and not line.startswith("#")]

    if not urls:
        sys.exit(f"No URLs found in {input_path}")

    locations = load_tree_locations(locations_path)
    print(f"Loaded {len(locations)} tree location(s) from {locations_path}")

    print(f"Processing {len(urls)} URL(s) -> {output_path}")

    session = requests.Session()
    with output_path.open("w", newline="", encoding="utf-8") as out:
        writer = csv.DictWriter(out, fieldnames=CSV_FIELDS)
        writer.writeheader()

        for i, url in enumerate(urls, start=1):
            print(f"  [{i}/{len(urls)}] {url}")
            row = scrape_url(url, session, locations)
            writer.writerow(row)
            out.flush()  # write row-by-row so progress isn't lost on crash
            if DELAY_BETWEEN_REQUESTS and i < len(urls):
                time.sleep(DELAY_BETWEEN_REQUESTS)

    print(f"Done. Wrote {output_path}")


if __name__ == "__main__":
    args = sys.argv[1:]
    in_path  = Path(args[0]) if len(args) >= 1 else Path(DEFAULT_INPUT)
    out_path = Path(args[1]) if len(args) >= 2 else Path(DEFAULT_OUTPUT)
    loc_path = Path(args[2]) if len(args) >= 3 else Path(DEFAULT_LOCATIONS)
    main(in_path, out_path, loc_path)