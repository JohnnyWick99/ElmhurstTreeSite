#!/usr/bin/env python3
"""
SecondScrape.py

Usage:
    python SecondScrape.py                    # reads & writes trees.csv

Dependencies:
    pip install requests beautifulsoup4
"""

import csv
import os
import re
import sys
import time
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

# --- Config ---------------------------------------------------------------

DEFAULT_CSV = "trees.csv"
DEFAULT_IMAGE_DIR = "images"

REQUEST_TIMEOUT = 20
DELAY_BETWEEN_REQUESTS = 1.0
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0 Safari/537.36"
    )
}

# Maps the new CSV column name -> the label text to search for on the page.
DETAIL_FIELDS = {
    "scientific_name":         "Scientific name",
    "additional_taxonomy":     "Additional taxonomy",
    "height_class":            "Height class",
    "diameter_breast_height":  "Diameter at breast height",
    "age_class":               "Age class",
    "canopy_radius":           "Canopy radius",
    "condition":               "Condition",
}

NEW_FIELDS = list(DETAIL_FIELDS.keys()) + ["image_path", "details_error"]


# --- Helpers --------------------------------------------------------------

def _clean(text: str) -> str:
    if text is None:
        return ""
    return re.sub(r"\s+", " ", text).strip()


def extract_td_value(soup: BeautifulSoup, label: str) -> str:
    """
    Find a <td> whose text matches `label` (optionally followed by ':') and
    return the clean text of the next <td> sibling.

    Handles values wrapped in nested tags like <i><a>...</a></i>.
    """
    pattern = re.compile(r"^\s*" + re.escape(label) + r"\s*:?\s*$", re.I)

    # label_td = a <td> whose *direct text* matches the label
    label_td = soup.find("td", string=pattern)
    if not label_td:
        # Sometimes the <td> has whitespace/child tags; fall back to scanning
        for td in soup.find_all("td"):
            if pattern.match(td.get_text(" ", strip=True)):
                label_td = td
                break
    if not label_td:
        return ""

    value_td = label_td.find_next_sibling("td")
    if not value_td:
        return ""

    return _clean(value_td.get_text(" ", strip=True))


def find_tree_image_url(soup: BeautifulSoup, base_url: str) -> str:
    """
    Find the first tree photo on the page and return an absolute URL.

    The arborscope template uses an <img> with classes
    "groupphoto framed img-responsive center-block", e.g.:
        <img src="/inventories/144/images/062320_083902179_Moore1_lg.jpeg?io=..."
             class="groupphoto framed img-responsive center-block" ...>

    Returns "" when no such image is on the page.
    """
    img = soup.find("img", class_="groupphoto")
    if not img or not img.get("src"):
        return ""
    return urljoin(base_url, img["src"].strip())


def download_tree_image(
    image_url: str,
    tree_id: str,
    image_dir: Path,
    session: requests.Session,
) -> str:
    """
    Download `image_url` to `image_dir/<tree_id><ext>` and return the
    saved path (relative to image_dir's parent if possible, else absolute).

    Returns "" if `image_url` is empty. Raises requests.RequestException
    on network errors so the caller can record them.
    """
    if not image_url:
        return ""

    # Pull a sensible extension out of the URL path (ignore the query string).
    path = urlparse(image_url).path
    ext = os.path.splitext(path)[1].lower() or ".jpg"

    safe_id = re.sub(r"[^A-Za-z0-9_-]", "_", str(tree_id)) or "unknown"
    image_dir.mkdir(parents=True, exist_ok=True)
    out_path = image_dir / f"{safe_id}{ext}"

    # If we've already downloaded this tree's image, don't re-fetch.
    if out_path.exists() and out_path.stat().st_size > 0:
        return str(out_path)

    resp = session.get(image_url, headers=HEADERS, timeout=REQUEST_TIMEOUT, stream=True)
    resp.raise_for_status()
    with out_path.open("wb") as f:
        for chunk in resp.iter_content(chunk_size=64 * 1024):
            if chunk:
                f.write(chunk)

    return str(out_path)


def scrape_details(
    url: str,
    session: requests.Session,
    tree_id: str,
    image_dir: Path,
) -> dict:
    """Fetch one detail URL and return {new_field: value, ...}."""
    out = {f: "" for f in NEW_FIELDS}

    if not url or not url.strip():
        out["details_error"] = "no more_details_url"
        return out

    try:
        resp = session.get(url, headers=HEADERS, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
    except requests.RequestException as e:
        out["details_error"] = f"request failed: {e}"
        return out

    try:
        soup = BeautifulSoup(resp.text, "html.parser")
        for col, label in DETAIL_FIELDS.items():
            out[col] = extract_td_value(soup, label)

        # Find and download the tree photo, if there is one.
        image_url = find_tree_image_url(soup, url)
        if image_url:
            try:
                out["image_path"] = download_tree_image(
                    image_url, tree_id, image_dir, session
                )
            except requests.RequestException as e:
                out["details_error"] = f"image download failed: {e}"
    except Exception as e:  # noqa: BLE001
        out["details_error"] = f"parse failed: {e}"

    return out


# --- Main -----------------------------------------------------------------

def main(in_path: Path, out_path: Path, image_dir: Path) -> None:
    if not in_path.exists():
        sys.exit(f"Input CSV not found: {in_path}")

    with in_path.open("r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        original_fields = reader.fieldnames or []
        rows = list(reader)

    if not rows:
        sys.exit(f"No rows in {in_path}")

    # Preserve original column order, then append only the NEW_FIELDS that
    # aren't already there (idempotent if the script is re-run).
    final_fields = list(original_fields) + [f for f in NEW_FIELDS if f not in original_fields]

    # Write to a temp file in the same directory, then replace. This way a
    # crash mid-run won't destroy the original CSV.
    tmp_path = out_path.with_suffix(out_path.suffix + ".tmp")

    session = requests.Session()
    print(f"Processing {len(rows)} row(s) from {in_path} -> {out_path}")
    print(f"Saving tree images to {image_dir}")

    with tmp_path.open("w", encoding="utf-8", newline="") as out:
        writer = csv.DictWriter(out, fieldnames=final_fields)
        writer.writeheader()

        for i, row in enumerate(rows, start=1):
            url = (row.get("more_details_url") or "").strip()
            tree_id = row.get("tree_id") or "?"
            print(f"  [{i}/{len(rows)}] tree_id={tree_id} -> {url or '(none)'}")

            details = scrape_details(url, session, tree_id, image_dir)
            if details.get("image_path"):
                print(f"      image -> {details['image_path']}")
            merged = {**{f: "" for f in final_fields}, **row, **details}
            writer.writerow(merged)
            out.flush()

            if DELAY_BETWEEN_REQUESTS and i < len(rows) and url:
                time.sleep(DELAY_BETWEEN_REQUESTS)

    os.replace(tmp_path, out_path)
    print(f"Done. Updated {out_path}")


if __name__ == "__main__":
    args = sys.argv[1:]
    in_p    = Path(args[0]) if len(args) >= 1 else Path(DEFAULT_CSV)
    out_p   = Path(args[1]) if len(args) >= 2 else in_p   # in-place by default
    img_dir = Path(args[2]) if len(args) >= 3 else Path(DEFAULT_IMAGE_DIR)
    main(in_p, out_p, img_dir)