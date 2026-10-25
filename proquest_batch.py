"""Batch ProQuest subject facet fetcher + MG database comparison.

For each university × year combination, fetches ProQuest subject counts,
queries the MG database for counts, and writes a combined CSV.

Usage:
    python proquest_batch.py              # run all universities × years
    python proquest_batch.py --test       # test with one university/year

Raw ProQuest responses are saved to data/proquest/<university>_<year>.tsv
Final combined output: data/proquest_comparison.csv

Cookies: paste your ProQuest Cookie header into ~/Projects/MG/proquest_cookies.txt
"""

import csv
import html as htmllib
import os
import re
import sqlite3
import sys
import time
import random

import requests
from bs4 import BeautifulSoup

# --- Configuration ---

COOKIE_FILE = os.path.expanduser("~/Projects/MG/proquest_cookies.txt")
DB_PATH     = os.path.expanduser("~/Data/MG/mg.db")
RAW_DIR     = os.path.join(os.path.dirname(__file__), "data", "proquest")
CSV_OUT     = os.path.join(os.path.dirname(__file__), "data", "proquest_comparison.csv")
ACCOUNT_ID  = "14518"

# 20 sampled US universities (random.seed(2024), ≥500 MG records)
UNIVERSITIES = [
    "University of California, Berkeley",
    "University of Wisconsin-Madison",
    "Princeton University",
    "The University of Chicago",
    "University of Maryland College Park",
    "University of Texas at Austin",
    "Carnegie Mellon University",
    "Northwestern University",
    "The Pennsylvania State University",
    "Iowa State University",
    "Rutgers University, New Brunswick",
    "The Johns Hopkins University",
    "University of Rochester",
    "University of Colorado at Boulder",
    "University of California, Davis",
    "Rensselaer Polytechnic Institute",
    "University of Massachusetts Amherst",
    "University of Utah",
    "University of Connecticut",
    "University of Tennessee",  # ProQuest name (MG has "University of Tennessee - Knoxville")
]

YEARS = [1960, 1970, 1980, 1990, 2000, 2010]

# When ProQuest name differs from MG canonical name, map here
PQ_TO_MG_NAME = {
    "University of Tennessee": "University of Tennessee - Knoxville",
}

# MSC field groups (same as proquest_compare.py)
FIELD_MSC = {
    "Economics":   [91],
    "Statistics":  [62],
    "CS":          [68],
    "Engineering": [70, 74, 76, 78, 80, 93, 94],
    "Physics":     [81, 82, 83, 85, 86],
}
FIELD_ORDER = ["Economics", "Mathematics", "Statistics", "CS", "Engineering", "Physics", "Unknown"]

# ProQuest field groupings (subject terms → field)
PQ_FIELD_MAP = {
    "Economics":   {"economics", "economic theory"},
    "Mathematics": {"mathematics", "operations research"},
    "Statistics":  {"statistics"},
    "CS":          {"computer science", "artificial intelligence"},
    "Engineering": {
        "electrical engineering", "mechanical engineering", "chemical engineering",
        "civil engineering", "aerospace engineering", "biomedical engineering",
        "industrial engineering", "materials science", "petroleum engineering",
        "nuclear engineering", "environmental engineering", "metallurgy",
        "aerospace materials", "systems engineering",
    },
    "Physics": {
        "condensed matter physics", "plasma physics", "particle physics",
        "nuclear physics", "biophysics", "astrophysics", "optics",
        "atomic physics", "solid state physics", "geophysics",
    },
}

BASE_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/605.1.15 (KHTML, like Gecko) "
        "Version/26.3.1 Safari/605.1.15"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}
AJAX_HEADERS = {
    **BASE_HEADERS,
    "Accept": "text/javascript, text/html, application/xml, text/xml, */*",
    "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
    "X-Requested-With": "XMLHttpRequest",
    "X-Prototype-Version": "1.7.3",
}


# --- Session ---

def get_session():
    session = requests.Session()
    if not os.path.exists(COOKIE_FILE):
        print(f"ERROR: cookie file not found: {COOKIE_FILE}")
        sys.exit(1)
    raw = open(COOKIE_FILE).read().strip()
    for part in raw.split(";"):
        part = part.strip()
        if "=" in part:
            k, _, v = part.partition("=")
            session.cookies.set(k.strip(), v.strip(), domain="www.proquest.com")
    return session


# --- ProQuest search ---

def load_advanced_form(session) -> tuple[str, list[tuple[str, str]]]:
    """Load ProQuest advanced search page, return (form_action, all_fields)."""
    url = f"https://www.proquest.com/pqdtglobal/advanced?accountid={ACCOUNT_ID}"
    headers = {**BASE_HEADERS, "Accept": "text/html,application/xhtml+xml,*/*"}
    r = session.get(url, headers=headers)
    r.raise_for_status()
    if "sessionexpired" in r.url or "signin" in r.url.lower():
        print("ERROR: session expired — refresh proquest_cookies.txt")
        sys.exit(1)
    soup = BeautifulSoup(r.text, "html.parser")
    form = next(
        f for f in soup.find_all("form")
        if "searchform" in (f.get("action") or "")
    )
    fields = []
    for inp in form.find_all(["input", "textarea", "select"]):
        name = inp.get("name")
        if not name:
            continue
        if inp.name == "select":
            opt = inp.find("option", selected=True)
            val = opt["value"] if opt else ""
        else:
            val = inp.get("value", "")
        fields.append((name, val))
    return form["action"], fields


def search_proquest(session, university: str, year: int,
                    form_action: str, form_fields: list) -> str | None:
    """Submit a ProQuest search and return the results page URL."""
    override = {
        "schoolName": university,
        "textfield":   str(year),
        "textfield_0": str(year),
    }
    data = [(name, override.get(name, val)) for name, val in form_fields]
    headers = {
        **BASE_HEADERS,
        "Accept": "text/html,application/xhtml+xml,*/*",
        "Content-Type": "application/x-www-form-urlencoded",
        "Referer": f"https://www.proquest.com/pqdtglobal/advanced?accountid={ACCOUNT_ID}",
    }
    r = session.post(form_action, data=data, headers=headers, allow_redirects=True)
    if r.status_code != 200:
        print(f"  Search HTTP {r.status_code}")
        return None
    if "sessionexpired" in r.url or "signin" in r.url.lower():
        print("  ERROR: session expired — refresh proquest_cookies.txt")
        sys.exit(1)
    return r.url


def get_csrf(session, results_url: str) -> tuple[str | None, str]:
    """Load results page, return (csrf_token, page_html)."""
    headers = {**BASE_HEADERS, "Accept": "text/html,application/xhtml+xml,*/*"}
    r = session.get(results_url, headers=headers)
    r.raise_for_status()
    page_html = r.text
    m = re.search(r'[?&]_csrf=([a-f0-9\-]+)', page_html)
    csrf = m.group(1) if m else None
    return csrf, page_html


def _parse_expand_subjects(expand_html: str) -> list[tuple[str, int]]:
    """Parse subjects from the initial expand HTML (used when no More button)."""
    subjects = []
    for m in re.finditer(
        r'<a[^>]*class="filter_mainSubject"[^>]*>([^<]+)<span[^>]*>\s*\((\d+)\)',
        expand_html,
    ):
        name = m.group(1).strip()
        subjects.append((name, int(m.group(2))))
    return subjects


def _parse_more_subjects(more_html: str) -> list[tuple[str, int]]:
    """Parse subjects from the More overlay HTML."""
    soup = BeautifulSoup(more_html, "html.parser")
    subjects = []
    for row in soup.find_all("tr", class_="filter_more_row"):
        name_td = row.find("td", class_="lrgCol")
        count_td = row.find("td", class_="countCol")
        if name_td and count_td:
            name = name_td.get_text(strip=True)
            cnt_str = re.sub(r"[^0-9]", "", count_td.get_text())
            if name and cnt_str:
                subjects.append((name, int(cnt_str)))
    return subjects


def fetch_subjects(session, results_url: str, csrf: str, search_id: str) -> list[tuple[str, int]]:
    """Expand Subject facet and fetch full subject list. Returns [(subject, count)]."""
    referer = results_url

    # Step 1: expand
    expand_url = (
        f"https://www.proquest.com/"
        f"results.multiselectfilterwithextrabuttons_0:expand"
        f"/mainSubject/mainSubject/Subject"
        f"?_csrf={csrf}&site=pqdtglobal&t:ac={search_id}/1"
    )
    r2 = session.post(
        expand_url,
        data={"t:zoneid": "mainSubject-zone"},
        headers={**AJAX_HEADERS, "Referer": referer},
    )
    r2.raise_for_status()
    expand_html = r2.json()["_tapestry"]["content"][0][1]

    m = re.search(r'href="([^"]*choosemoreoverlay[^"]*selectmore[^"]*)"', expand_html)
    if not m:
        # No More button — all subjects fit in the initial expand list
        subjects = _parse_expand_subjects(expand_html)
        return subjects
    more_url = htmllib.unescape(m.group(1))

    # Step 2: fetch full list via More overlay
    r3 = session.post(
        more_url,
        data={"t:zoneid": "selectMoreZone_Subject"},
        headers={**AJAX_HEADERS, "Referer": referer},
    )
    r3.raise_for_status()
    more_html = r3.json()["content"]
    return _parse_more_subjects(more_html)


def subjects_to_fields(subjects: list[tuple[str, int]]) -> dict[str, int]:
    """Map ProQuest subjects to the 6 broad fields."""
    counts = {f: 0 for f in FIELD_ORDER}
    for subj, cnt in subjects:
        subj_lower = subj.lower().strip().rstrip("\u200e").strip()
        matched = False
        for field, terms in PQ_FIELD_MAP.items():
            if subj_lower in terms:
                counts[field] += cnt
                matched = True
                break
        # partial matches for engineering/physics not in the exact set
        if not matched:
            if "engineering" in subj_lower and "software" not in subj_lower:
                counts["Engineering"] += cnt
            elif "physics" in subj_lower:
                counts["Physics"] += cnt
    return counts


# --- MG database ---

def mg_counts(con, canonical_name: str, year: int) -> dict[str, int]:
    rows = con.execute("""
        SELECT d.DEGMSC, COUNT(*) as cnt
        FROM DEGREES d
        JOIN SCHOOL_CANONICAL sc ON d.SCHOOL = sc.RAW_NAME
        WHERE sc.CANONICAL_NAME = ? AND d.DEGYEAR = ?
        GROUP BY d.DEGMSC
    """, (canonical_name, year)).fetchall()

    raw = {r[0]: r[1] for r in rows}   # NULL = unknown MSC; 0 is a real code (general)
    counts = {f: 0 for f in FIELD_ORDER}
    math_count = 0
    for msc, n in raw.items():
        if msc is None or msc == "":
            counts["Unknown"] += n
            continue
        try:
            msc_int = int(msc)
        except (ValueError, TypeError):
            counts["Unknown"] += n
            continue
        matched = False
        for field, codes in FIELD_MSC.items():
            if msc_int in codes:
                counts[field] += n
                matched = True
                break
        if not matched:
            math_count += n
    counts["Mathematics"] = math_count
    return counts


# --- Main ---

def safe_filename(university: str, year: int) -> str:
    return re.sub(r"[^a-zA-Z0-9]+", "_", university).strip("_") + f"_{year}.tsv"


def run(test_mode=False):
    os.makedirs(RAW_DIR, exist_ok=True)
    session = get_session()
    con = sqlite3.connect(DB_PATH)

    universities = UNIVERSITIES[:1] if test_mode else UNIVERSITIES
    years = [1990] if test_mode else YEARS

    # Load the search form once; it contains session-specific hidden fields
    print("Loading search form...")
    form_action, form_fields = load_advanced_form(session)
    print("Ready.\n")

    csv_rows = []

    for university in universities:
        for year in years:
            label = f"{university} {year}"
            raw_path = os.path.join(RAW_DIR, safe_filename(university, year))

            # Skip if raw file already exists
            if os.path.exists(raw_path):
                print(f"[cached] {label}")
                subjects = []
                with open(raw_path) as f:
                    for line in f:
                        parts = line.strip().split("\t")
                        if len(parts) == 2 and parts[1].isdigit():
                            subjects.append((parts[0], int(parts[1])))
            else:
                print(f"[fetch]  {label}")
                results_url = search_proquest(session, university, year, form_action, form_fields)
                if not results_url:
                    print(f"  No results URL")
                    continue

                m = re.search(r'/(?:results|advanced)/([A-Z0-9]+PQ)', results_url)
                if not m:
                    print(f"  Could not extract search ID from: {results_url}")
                    continue
                search_id = m.group(1)
                # Normalize to /results/ URL for CSRF and subject fetching
                results_url = f"https://www.proquest.com/pqdtglobal/results/{search_id}/1?accountid={ACCOUNT_ID}"

                csrf, _ = get_csrf(session, results_url)
                if not csrf:
                    print(f"  No CSRF token found")
                    continue

                subjects = fetch_subjects(session, results_url, csrf, search_id)

                # Save raw
                with open(raw_path, "w") as f:
                    f.write("Subject\tCount\n")
                    for subj, cnt in sorted(subjects, key=lambda x: -x[1]):
                        f.write(f"{subj}\t{cnt}\n")
                print(f"  Saved {len(subjects)} subjects → {raw_path}")

                time.sleep(1)  # be polite

            pq_fields = subjects_to_fields(subjects)
            mg_name = PQ_TO_MG_NAME.get(university, university)
            mg_fields = mg_counts(con, mg_name, year)

            for field in FIELD_ORDER:
                csv_rows.append({
                    "University": university,
                    "Year": year,
                    "Field": field,
                    "ProQuest_Count": pq_fields.get(field, 0),
                    "MG_Count": mg_fields.get(field, 0),
                })

    con.close()

    # Write CSV
    with open(CSV_OUT, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["University", "Year", "Field", "ProQuest_Count", "MG_Count"])
        writer.writeheader()
        writer.writerows(csv_rows)

    print(f"\nWrote {len(csv_rows)} rows to {CSV_OUT}")


if __name__ == "__main__":
    test_mode = "--test" in sys.argv
    run(test_mode=test_mode)
