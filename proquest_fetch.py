"""Fetch ProQuest subject facet counts for a search results page.

Usage:
    python proquest_fetch.py "https://www.proquest.com/pqdtglobal/results/XXXPQ/1?accountid=14518"

The script:
  1. Reads cookies from ~/Projects/MG/proquest_cookies.txt
     (paste the Cookie header value from Safari Web Inspector into that file)
  2. Loads the results page to get the CSRF token and search ID
  3. Expands the Subject facet to get the "More" button URL
  4. Fetches the full subject list
  5. Prints subject counts as: Subject<TAB>Count

Workflow to get proquest_cookies.txt:
  - Open Safari Web Inspector (Develop → Show Web Inspector)
  - Go to any ProQuest page while logged in
  - Network tab → click any request to proquest.com
  - Copy the Cookie header value → paste into ~/Projects/MG/proquest_cookies.txt

Field groupings used in proquest_compare.py:
  Economics   : economics + economic theory
  Mathematics : mathematics (+ operations research)
  Statistics  : statistics
  CS          : computer science (+ artificial intelligence)
  Engineering : *engineering + materials science + metallurgy
  Physics     : *physics (condensed matter, plasma, particle, nuclear,
                biophysics, astrophysics, optics, atomic physics)
"""

import html
import os
import re
import sys
import json
import requests
from bs4 import BeautifulSoup


COOKIE_FILE = os.path.expanduser("~/Projects/MG/proquest_cookies.txt")

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


def get_session():
    session = requests.Session()
    if not os.path.exists(COOKIE_FILE):
        print(f"ERROR: cookie file not found: {COOKIE_FILE}")
        print("Paste your ProQuest Cookie header value into that file and retry.")
        sys.exit(1)
    raw = open(COOKIE_FILE).read().strip()
    for part in raw.split(";"):
        part = part.strip()
        if "=" in part:
            k, _, v = part.partition("=")
            session.cookies.set(k.strip(), v.strip(), domain="www.proquest.com")
    print(f"Loaded cookies from {COOKIE_FILE}")
    return session


def fetch_subject_facet(results_url: str):
    session = get_session()

    # --- Step 1: load results page to get CSRF token ---
    clean_url = results_url.split("#")[0]
    print(f"Loading results page...")
    resp = session.get(clean_url, headers={**BASE_HEADERS, "Accept": "text/html,application/xhtml+xml,*/*"})
    resp.raise_for_status()
    page_html = resp.text

    csrf = None
    m = re.search(r'[?&]_csrf=([a-f0-9\-]+)', page_html)
    if m:
        csrf = m.group(1)
    if not csrf:
        print("ERROR: could not find CSRF token in page.")
        return
    print(f"CSRF: {csrf}")

    # Extract search ID from URL (e.g. 8B1CB16BDAC54599PQ)
    m = re.search(r'/results/([A-Z0-9]+PQ)', clean_url)
    if not m:
        print("ERROR: could not parse search ID from URL.")
        return
    search_id = m.group(1)
    print(f"Search ID: {search_id}")

    # --- Step 2: expand Subject facet ---
    expand_url = (
        f"https://www.proquest.com/"
        f"results.multiselectfilterwithextrabuttons_0:expand"
        f"/mainSubject/mainSubject/Subject"
        f"?_csrf={csrf}&site=pqdtglobal&t:ac={search_id}/1"
    )
    print("Expanding Subject facet...")
    r2 = session.post(
        expand_url,
        data={"t:zoneid": "mainSubject-zone"},
        headers={**AJAX_HEADERS, "Referer": clean_url},
    )
    r2.raise_for_status()

    expand_json = r2.json()
    expand_html = expand_json["_tapestry"]["content"][0][1]

    # Find the "More" button URL
    m = re.search(r'href="([^"]*choosemoreoverlay[^"]*selectmore[^"]*)"', expand_html)
    if not m:
        print("ERROR: could not find More button URL in expanded facet.")
        print("Preview:", expand_html[:500])
        return
    more_url = html.unescape(m.group(1))
    print(f"More URL found.")

    # --- Step 3: fetch full subject list ---
    print("Fetching full subject list...")
    r3 = session.post(
        more_url,
        data={"t:zoneid": "selectMoreZone_Subject"},
        headers={**AJAX_HEADERS, "Referer": clean_url},
    )
    r3.raise_for_status()

    more_json = r3.json()
    # expand uses _tapestry.content[0][1]; the more overlay uses top-level content
    if "content" in more_json:
        more_html = more_json["content"]
    else:
        more_html = more_json["_tapestry"]["content"][0][1]

    # --- Step 4: parse subject rows ---
    # Each row: <tr class="filter_more_row">
    #   <td class="lrgCol">subject name</td>
    #   <td class="countCol">count</td>
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

    if subjects:
        print(f"\nSubject\tCount")
        for subj, cnt in sorted(subjects, key=lambda x: -x[1]):
            print(f"{subj}\t{cnt}")
        print(f"\n({len(subjects)} subjects total)")
    else:
        print("Could not parse subjects. Raw HTML fragment (first 2000 chars):")
        print(more_html[:2000])


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python proquest_fetch.py <results_url>")
        sys.exit(1)
    fetch_subject_facet(sys.argv[1])
