"""
school_canonicalize.py
----------------------
Identifies variant school name spellings in the DEGREES table,
produces a SCHOOL_CANONICAL mapping table in the database, and
writes a review CSV for uncertain matches.

Pipeline
--------
1. Load all distinct (SCHOOL, COUNTRY, count) from DEGREES.
2. Normalize each name to a comparison key:
     - unicode NFC, strip leading/trailing whitespace
     - collapse multiple spaces
     - strip "The " prefix / ", The" suffix
     - lowercase
3. Within each COUNTRY group, compare every pair of names using
   fuzz.ratio (character-level edit distance, via rapidfuzz).
   Pairs with score >= HIGH_THRESH (92) are auto-accepted.
   Pairs with score in [LOW_THRESH, HIGH_THRESH) (75–92) go to review CSV.
   Two guards prevent false positives:
     a. US state blocking: if both names mention a US state and those
        states differ, the pair is blocked (different institution).
     b. Distinctiveness guard: if names differ by exactly one token each
        and those tokens are dissimilar, send to review rather than merge.
4. Advisor co-occurrence: if the same advisor has students at two schools
   whose names are similar (>= ADVISOR_THRESH = 70), that is strong
   evidence they are the same institution. Such pairs are auto-accepted
   at the lower ADVISOR_THRESH, both within and across country groups.
   The distinctiveness guard still applies, but not state blocking
   (cross-country pairs may not have country strings).
5. A small curated cross-language alias table handles major pairs
   whose names share no common tokens.
6. Build SCHOOL_CANONICAL table: every raw name maps to a canonical
   name (the variant with the highest degree count in its cluster),
   the match method, and a confidence score.
7. Write uncertain pairs to school_review.csv for manual review.

Usage
-----
    python3 school_canonicalize.py

Outputs
-------
    ~/Data/MG/mg.db          — SCHOOL_CANONICAL table created/replaced
    ~/Projects/MG/school_review.csv  — pairs needing human review
"""

import sqlite3
import unicodedata
import re
import csv
import os
from collections import defaultdict
from rapidfuzz import fuzz

# ── Config ────────────────────────────────────────────────────────────────────
DB_PATH        = os.path.expanduser("~/Data/MG/mg.db")
REVIEW_CSV     = os.path.join(os.path.dirname(__file__), "school_review.csv")
HIGH_THRESH    = 92   # fuzz.ratio threshold for auto-accept (no advisor evidence)
LOW_THRESH     = 75   # fuzz.ratio threshold for sending to review
DIST_THRESH    = 85   # minimum similarity between differing tokens for auto-accept
               #       (guards against "Univ. of Essex" ≡ "Univ. of Sussex" errors)
ADVISOR_THRESH = 70   # fuzz.ratio threshold for auto-accept when the same advisor
               #       has students at both schools (strong co-occurrence evidence)
MIN_COUNT      = 2    # ignore singletons in blocking (still mapped, just not compared)

# Pairs that score high on both metrics but are genuinely different institutions.
# Stored as frozensets so order doesn't matter.
KNOWN_DISTINCT = {
    frozenset(["Northeastern University",            "Northwestern University"]),
    frozenset(["Henan Normal University",            "Hunan Normal University"]),
    frozenset(["Henan University",                   "Hunan University"]),
    frozenset(["Tomsk State University",             "Omsk State University"]),
    frozenset(["Tomsk Polytechnic University",       "Omsk Technical University"]),
    frozenset(["Universidade Federal do Rio de Janeiro",
               "Universidade Federal Rural do Rio de Janeiro"]),
    frozenset(["Indian Institute of Science Education and Research, Bhopal",
               "Indian Institute of Science Education and Research, Pune"]),
    frozenset(["Indian Institute of Science Education and Research, Kolkata",
               "Indian Institute of Science Education and Research, Pune"]),
    frozenset(["Indian Institute of Technology, Kanpur",
               "Indian Institute of Technology, Mumbai"]),
    frozenset(["Indian Institute of Technology, Delhi",
               "Indian Institute of Technology, Mumbai"]),
    frozenset(["Indian Institute of Technology, Bombay",
               "Indian Institute of Technology, Madras"]),
    frozenset(["Université de Lille I",              "Université de Lille III"]),
    frozenset(["State University of New York at Albany",
               "State University of New York at Binghamton"]),
    frozenset(["State University of New York at Albany",
               "State University of New York at Buffalo"]),
    frozenset(["State University of New York at Binghamton",
               "State University of New York at Buffalo"]),
    frozenset(["State University of New York at Stony Brook",
               "State University of New York at Buffalo"]),
    frozenset(["Southwest University",   "Southeast University"]),
    frozenset(["University of Arkansas", "University of Kansas"]),
    frozenset(["Moscow State Institute of Electronics and Mathematics",
               "Moscow Institute of Electronics and Mathematics"]),
}

# Manually curated cross-language aliases: (raw_a, raw_b, canonical)
# canonical = whichever form is preferred (usually the local official name)
MANUAL_ALIASES = [
    ("University of Munich",
     "Ludwig-Maximilians-Universität München",
     "Ludwig-Maximilians-Universität München"),
    ("University of Cologne",
     "Universität zu Köln",
     "Universität zu Köln"),
    ("University of Vienna",
     "Universität Wien",
     "Universität Wien"),
    ("University of Zurich",
     "Universität Zürich",
     "Universität Zürich"),
    ("University of Basel",
     "Universität Basel",
     "Universität Basel"),
    ("University of Bern",
     "Universität Bern",
     "Universität Bern"),
    ("University of Bonn",
     "Rheinische Friedrich-Wilhelms-Universität Bonn",
     "Rheinische Friedrich-Wilhelms-Universität Bonn"),
    ("Free University of Berlin",
     "Freie Universität Berlin",
     "Freie Universität Berlin"),
    ("University of Hamburg",
     "Universität Hamburg",
     "Universität Hamburg"),
    ("University of Frankfurt",
     "Goethe-Universität Frankfurt am Main",
     "Goethe-Universität Frankfurt am Main"),
    ("Technical University of Munich",
     "Technische Universität München",
     "Technische Universität München"),
    ("University of Paris",
     "Université de Paris",
     "Université de Paris"),
    ("University of Rome",
     "Sapienza Università di Roma",
     "Sapienza Università di Roma"),
]

# ── US state blocking ─────────────────────────────────────────────────────────
# Multi-word states first so "new york" is matched before bare "york", and
# "west virginia" before "virginia".  Word-boundary regex ensures "kansas"
# doesn't match inside "arkansas", "virginia" doesn't match inside "west
# virginia" (because multi-word patterns are tried first), etc.
_US_STATES_ORDERED = [
    'new hampshire', 'new jersey', 'new mexico', 'new york',
    'north carolina', 'north dakota', 'rhode island', 'south carolina',
    'south dakota', 'west virginia',
    'alabama', 'alaska', 'arizona', 'arkansas', 'california', 'colorado',
    'connecticut', 'delaware', 'florida', 'georgia', 'hawaii', 'idaho',
    'illinois', 'indiana', 'iowa', 'kansas', 'kentucky', 'louisiana',
    'maine', 'maryland', 'massachusetts', 'michigan', 'minnesota',
    'mississippi', 'missouri', 'montana', 'nebraska', 'nevada',
    'ohio', 'oklahoma', 'oregon', 'pennsylvania', 'tennessee', 'texas',
    'utah', 'vermont', 'virginia', 'washington', 'wisconsin', 'wyoming',
]
_STATE_PATTERNS = [
    (state, re.compile(r'\b' + re.escape(state) + r'\b'))
    for state in _US_STATES_ORDERED
]


# ── Directional word blocking ─────────────────────────────────────────────────
# "East X" and "West X" (or Northeast/Southeast etc.) are always distinct
# institutions.  Words are grouped so "north" and "northern" are treated as
# the same direction and won't block each other.
_DIRECTION_GROUP = {
    'east': 'east',         'eastern': 'east',
    'west': 'west',         'western': 'west',
    'north': 'north',       'northern': 'north',
    'south': 'south',       'southern': 'south',
    'northeast': 'ne',      'northeastern': 'ne',
    'northwest': 'nw',      'northwestern': 'nw',
    'southeast': 'se',      'southeastern': 'se',
    'southwest': 'sw',      'southwestern': 'sw',
    'central': 'central',
}


def extract_direction(tok_set: set) -> str | None:
    """Return the canonical direction group for the first directional word found, or None."""
    for tok in tok_set:
        grp = _DIRECTION_GROUP.get(tok)
        if grp:
            return grp
    return None


def extract_us_state(norm_name: str) -> str | None:
    """Return the first US state name found in norm_name (already lowercased), or None."""
    for state, pat in _STATE_PATTERNS:
        if pat.search(norm_name):
            return state
    return None


# ── Helpers ───────────────────────────────────────────────────────────────────
_THE_PREFIX = re.compile(r'^the\s+', re.IGNORECASE)
_THE_SUFFIX = re.compile(r',\s*the\s*$', re.IGNORECASE)
_MULTI_SPC  = re.compile(r'\s{2,}')
_PUNCT      = re.compile(r'[^\w]', re.UNICODE)   # for token-level comparison

# Generic institutional and structural words stripped before scoring.
# Accents are removed first (see _strip_accents) so one ASCII form covers all
# language variants.  Words that distinguish co-located institutions of different
# types (e.g. 'technische', 'autonoma', 'freie') are intentionally NOT listed
# here — they are handled by the symmetric-difference scoring below.
_GENERIC_TOKENS = frozenset([
    # English
    'university', 'universities', 'institute', 'institution', 'institutions',
    'college', 'school', 'of', 'and', 'for', 'at', 'in',
    # Non-English equivalents of university
    'universitat', 'universidad', 'universidade', 'universita',
    'universite', 'universiteit', 'universitet', 'universitetet',
    'hochschule', 'fachhochschule',
    # Non-English equivalents of institute / college / school
    'institut', 'instituto', 'istituto', 'ecole',
    # Polytechnic variants (accents stripped: politècnica → politecnica)
    'politecnica', 'politecnico', 'polytechnique', 'politechnika',
    # Prepositions and articles — Romance languages
    'de', 'du', 'des', 'la', 'le', 'les', 'el', 'los', 'las',
    'di', 'da', 'degli', 'delle', 'della', 'del',
    'y', 'e', 'et',
    # German prepositions / articles
    'der', 'die', 'das', 'den', 'dem', 'von', 'fur', 'und', 'zu',
    # Dutch
    'van', 'voor',
    # Structural adjectives: truly generic across all institutions of a country,
    # do not distinguish co-located schools (safe to strip unconditionally).
    'national', 'federal', 'royal',
    'nacional', 'nationale', 'nazionale',   # national — ES/FR/IT
    'estadual', 'statale',                  # state    — PT-BR/IT
    'superior', 'superieur', 'superieure',  # higher   — EN/FR
])


def _strip_accents(s: str) -> str:
    """Decompose accented characters to their ASCII base form (NFKD → drop combining)."""
    return ''.join(
        c for c in unicodedata.normalize('NFKD', s)
        if not unicodedata.combining(c)
    )


def _tok_set(norm: str) -> set[str]:
    """Return the set of non-generic tokens from an already-normalized name."""
    return set(_strip_accents(norm).split()) - _GENERIC_TOKENS


def distinctive_score(norm_a: str, norm_b: str) -> float:
    """Similarity score focused on the tokens that actually distinguish the two names.

    Steps:
    1. Strip generic institutional words from both names (accents removed first).
    2. Remove tokens shared by both names — e.g. 'technische' when comparing two
       Technische Universitäts adds no discriminating signal and should not inflate
       the score.
    3. Compare only the remaining unique tokens character-by-character.
    4. Fall back to the full distinctive-token comparison when one or both sides
       have no unique tokens (one name is a subset of the other).

    Examples
    --------
    TU Chemnitz vs TU Ilmenau  → shared={technische} → "chemnitz" vs "ilmenau" → ~40 %
    TU Berlin   vs FU Berlin   → shared={berlin}     → "technische" vs "freie"  → ~20 %
    Nacional La Plata vs Nacional Litoral → shared={nacional} → "plata" vs "litoral" → ~40 %
    """
    toks_a = _tok_set(norm_a)
    toks_b = _tok_set(norm_b)
    shared  = toks_a & toks_b
    unique_a = toks_a - shared
    unique_b = toks_b - shared

    if unique_a and unique_b:
        str_a = ' '.join(sorted(unique_a))
        str_b = ' '.join(sorted(unique_b))
    else:
        # One name's tokens are a subset of the other's; compare full token sets.
        str_a = ' '.join(sorted(toks_a)) if toks_a else _strip_accents(norm_a)
        str_b = ' '.join(sorted(toks_b)) if toks_b else _strip_accents(norm_b)

    return fuzz.ratio(str_a, str_b)

def normalize(name: str) -> str:
    """Return lowercase, NFC-normalized, whitespace-collapsed comparison key."""
    s = unicodedata.normalize('NFC', name).strip()
    s = _THE_PREFIX.sub('', s)
    s = _THE_SUFFIX.sub('', s)
    s = _MULTI_SPC.sub(' ', s)
    return s.lower()

def canonical_for_cluster(names_counts: list[tuple[str, int]]) -> str:
    """Pick the variant with the highest degree count as canonical name."""
    return max(names_counts, key=lambda x: x[1])[0]

# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()

    # Load all distinct school names with country and degree count
    print("Loading school data...")
    rows = cur.execute("""
        SELECT SCHOOL, COUNTRY, COUNT(*) AS cnt
        FROM DEGREES
        WHERE SCHOOL IS NOT NULL AND SCHOOL <> '' AND SCHOOL <> 'NONE'
        GROUP BY SCHOOL, COUNTRY
    """).fetchall()

    # school_info: raw_name -> (country, count)
    school_info: dict[str, tuple[str, int]] = {}
    for school, country, cnt in rows:
        if school not in school_info or school_info[school][1] < cnt:
            school_info[school] = (country, cnt)

    # Group by country
    by_country: dict[str, list[str]] = defaultdict(list)
    for school, (country, cnt) in school_info.items():
        by_country[country].append(school)

    print(f"  {len(school_info):,} distinct school names across "
          f"{len(by_country):,} countries")

    # ── Step 1: exact normalization dedup ────────────────────────────────────
    # Map normalized key -> list of (raw_name, count)
    norm_groups: dict[str, list[tuple[str, int]]] = defaultdict(list)
    for school, (country, cnt) in school_info.items():
        norm_groups[normalize(school)].append((school, cnt))

    exact_matches: dict[str, str] = {}  # raw -> canonical
    for key, variants in norm_groups.items():
        canonical = canonical_for_cluster(variants)
        for raw, _ in variants:
            exact_matches[raw] = canonical

    exact_merged = sum(1 for k, variants in norm_groups.items()
                       if len(variants) > 1)
    print(f"  Normalization step: {exact_merged} groups with >1 variant")

    # ── Step 2: manual cross-language aliases ────────────────────────────────
    manual_map: dict[str, str] = {}
    for a, b, canonical in MANUAL_ALIASES:
        if a in school_info:
            manual_map[a] = canonical
        if b in school_info:
            manual_map[b] = canonical

    print(f"  Manual aliases applied: {len(manual_map)} raw names mapped")

    # ── Step 3: advisor co-occurrence ────────────────────────────────────────
    # If the same advisor has students at two schools with similar names, that
    # is strong evidence the names are variants of the same institution.
    # (An advisor who moves from Purdue to Duke won't trigger this because
    # "purdue university" and "duke university" score far below ADVISOR_THRESH.)
    print("Loading advisor co-occurrence data...")
    adv_rows = cur.execute("""
        SELECT s.ADVISOR, d.SCHOOL
        FROM STUDENTS s
        JOIN DEGREES d ON d.DEGMGID = s.STUDENT
        WHERE d.SCHOOL IS NOT NULL AND d.SCHOOL != '' AND d.SCHOOL != 'NONE'
        GROUP BY s.ADVISOR, d.SCHOOL
    """).fetchall()

    advisor_schools: dict[str, set[str]] = defaultdict(set)
    for advisor, school in adv_rows:
        if advisor and school in school_info:
            advisor_schools[advisor].add(school)

    # Pre-compute advisor_pair_counts: frozenset({school_a, school_b}) -> number
    # of distinct advisors who have students at both schools (same country only).
    # More advisors = stronger evidence that the names are variants of the same
    # institution.  An advisor who moved from Dalhousie (Canada) to Tulane (US)
    # is normal career mobility; cross-country pairs are excluded.
    advisor_pair_counts: dict[frozenset, int] = defaultdict(int)
    for schools in advisor_schools.values():
        schools_list = list(schools)
        for ii in range(len(schools_list)):
            for jj in range(ii + 1, len(schools_list)):
                country_i = school_info[schools_list[ii]][0]
                country_j = school_info[schools_list[jj]][0]
                if country_i != country_j:
                    continue
                ni = normalize(schools_list[ii])
                nj = normalize(schools_list[jj])
                if distinctive_score(ni, nj) >= ADVISOR_THRESH:
                    key = frozenset([schools_list[ii], schools_list[jj]])
                    advisor_pair_counts[key] += 1

    print(f"  Advisor co-occurrence: {len(advisor_pair_counts)} similar school pairs "
          f"(score >= {ADVISOR_THRESH}) found across {len(advisor_schools)} advisors")

    # ── Step 4: fuzzy matching within country + cross-country advisor pairs ───
    auto_pairs:   list[tuple[str, str, float]] = []  # (a, b, score)
    review_pairs: list[tuple[str, str, float]] = []

    def clean_toks(s):
        return {_PUNCT.sub('', t) for t in s.split() if _PUNCT.sub('', t)}

    def _effective_thresh(adv_count: int) -> int:
        """Lower the auto-accept threshold as advisor evidence accumulates."""
        if adv_count >= 5:
            return ADVISOR_THRESH - 10   # 60
        if adv_count >= 3:
            return ADVISOR_THRESH - 5    # 65
        if adv_count >= 1:
            return ADVISOR_THRESH        # 70
        return HIGH_THRESH               # 92

    def _classify_pair(raw_a, raw_b, norm_a, norm_b, score, adv_count):
        """Classify a candidate pair as auto-accept, review, or skip (None).

        Returns 'auto', 'review', or None (blocked).
        adv_count: number of distinct advisors with students at both schools.
        """
        pair_key = frozenset([raw_a, raw_b])

        # ── US state blocking ──────────────────────────────────────────────
        # If both names mention a US state and those states differ, the schools
        # are definitely different institutions.  Advisor evidence is irrelevant
        # here — all pairs are already same-country.
        state_a = extract_us_state(norm_a)
        state_b = extract_us_state(norm_b)
        if state_a and state_b and state_a != state_b:
            if score >= HIGH_THRESH:
                return 'review'
            return None  # blocked

        # ── "University of X" vs "X State University" + directional blocking ─
        # Compute distinctive token sets once; used for both checks below.
        toks_a = _tok_set(norm_a)
        toks_b = _tok_set(norm_b)

        # Directional: "East X" vs "West X", "Northeast X" vs "Southeast X"
        # north/northern share a group so they don't block each other.
        dir_a = extract_direction(toks_a)
        dir_b = extract_direction(toks_b)
        if dir_a and dir_b and dir_a != dir_b:
            if score >= HIGH_THRESH:
                return 'review'
            return None  # blocked

        # State asymmetry: if one name has 'state' and the other does not,
        # they cannot be the same institution regardless of name similarity.
        if ('state' in toks_a) != ('state' in toks_b):
            if score >= HIGH_THRESH:
                return 'review'   # surprisingly high — worth flagging
            return None  # blocked

        # ── Effective threshold (slides down with more advisor evidence) ───
        effective_thresh = _effective_thresh(adv_count)
        if score < effective_thresh:
            if score >= LOW_THRESH:
                return 'review'
            return None

        # score >= effective_thresh: check guards before auto-accepting
        if pair_key in KNOWN_DISTINCT:
            return 'review'

        tok_a = clean_toks(norm_a)
        tok_b = clean_toks(norm_b)
        extra_a = tok_a - tok_b
        extra_b = tok_b - tok_a
        if (len(extra_a) == 1 and len(extra_b) == 1
                and fuzz.ratio(next(iter(extra_a)),
                               next(iter(extra_b))) < DIST_THRESH):
            # Differ by exactly one dissimilar token → flag for review even
            # with advisor evidence (e.g. advisor had students at "Univ. of
            # Essex" and "Univ. of Sussex" — still different institutions).
            return 'review'

        return 'auto'

    # Within-country pairs
    for country, schools in by_country.items():
        candidates = [s for s in schools
                      if school_info[s][1] >= MIN_COUNT
                      and s not in manual_map]
        n = len(candidates)
        if n < 2:
            continue
        norms = [normalize(s) for s in candidates]
        for i in range(n):
            for j in range(i + 1, n):
                score = distinctive_score(norms[i], norms[j])
                pair_key = frozenset([candidates[i], candidates[j]])
                adv_count = advisor_pair_counts.get(pair_key, 0)
                if score < LOW_THRESH and adv_count == 0:
                    continue  # fast path: below all thresholds, no advisor evidence
                decision = _classify_pair(candidates[i], candidates[j],
                                          norms[i], norms[j], score, adv_count)
                if decision == 'auto':
                    auto_pairs.append((candidates[i], candidates[j], score))
                elif decision == 'review':
                    review_pairs.append((candidates[i], candidates[j], score))


    advisor_auto = sum(1 for a, b, _ in auto_pairs
                       if advisor_pair_counts.get(frozenset([a, b]), 0) > 0)
    print(f"  Fuzzy matching: {len(auto_pairs)} auto-accepted pairs "
          f"({advisor_auto} boosted by advisor evidence), "
          f"{len(review_pairs)} sent to review")

    # ── Step 5: build clusters from auto pairs using union-find ───────────────
    parent: dict[str, str] = {s: s for s in school_info}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(x, y):
        rx, ry = find(x), find(y)
        if rx == ry:
            return
        # Keep the one with higher count as root
        cx = school_info[rx][1]
        cy = school_info[ry][1]
        if cx >= cy:
            parent[ry] = rx
        else:
            parent[rx] = ry

    # Apply exact normalization unions
    for key, variants in norm_groups.items():
        for raw, _ in variants[1:]:
            union(variants[0][0], raw)

    # Apply auto fuzzy pairs
    for a, b, _ in auto_pairs:
        union(a, b)

    # Apply manual aliases
    for raw, canonical in manual_map.items():
        if canonical in school_info:
            union(raw, canonical)
        else:
            # canonical not in db; just point raw to itself for now
            pass

    # Determine final canonical for each school
    # canonical = highest-count member of each cluster
    clusters: dict[str, list[tuple[str, int]]] = defaultdict(list)
    for school in school_info:
        root = find(school)
        clusters[root].append((school, school_info[school][1]))

    raw_to_canonical: dict[str, tuple[str, float, str]] = {}
    # (raw -> (canonical, confidence, method))

    for root, members in clusters.items():
        canonical = canonical_for_cluster(members)
        for raw, cnt in members:
            if raw == canonical:
                method = 'identity'
                conf   = 1.0
            elif normalize(raw) == normalize(canonical):
                method = 'normalization'
                conf   = 0.98
            elif raw in manual_map:
                method = 'manual'
                conf   = 0.95
            else:
                score = distinctive_score(normalize(raw), normalize(canonical))
                if advisor_pair_counts.get(frozenset([raw, canonical]), 0) > 0:
                    method = 'advisor_fuzzy'
                else:
                    method = 'fuzzy'
                conf   = score / 100.0
            raw_to_canonical[raw] = (canonical, conf, method)

    # Also map all singletons (count < MIN_COUNT, not in any pair)
    for school in school_info:
        if school not in raw_to_canonical:
            raw_to_canonical[school] = (school, 1.0, 'identity')

    print(f"  Total mapped: {len(raw_to_canonical):,} raw names "
          f"-> {len(set(c for c,_,_ in raw_to_canonical.values())):,} canonical names")

    # ── Write SCHOOL_CANONICAL table ─────────────────────────────────────────
    cur.execute("DROP TABLE IF EXISTS SCHOOL_CANONICAL")
    cur.execute("""
        CREATE TABLE SCHOOL_CANONICAL (
            RAW_NAME       TEXT PRIMARY KEY,
            CANONICAL_NAME TEXT NOT NULL,
            CONFIDENCE     REAL,
            MATCH_METHOD   TEXT
        )
    """)
    cur.executemany(
        "INSERT INTO SCHOOL_CANONICAL VALUES (?,?,?,?)",
        [(raw, canon, conf, method)
         for raw, (canon, conf, method) in sorted(raw_to_canonical.items())]
    )
    con.commit()
    print(f"  SCHOOL_CANONICAL table written ({len(raw_to_canonical):,} rows)")

    # ── Write review CSV ─────────────────────────────────────────────────────
    # Guard: if the existing CSV already has manual decisions, refuse to
    # overwrite it.  The user must rename or delete it first.
    if os.path.exists(REVIEW_CSV):
        try:
            with open(REVIEW_CSV, newline='', encoding='utf-8') as _f:
                _existing = list(csv.DictReader(_f))
            _manual = [r for r in _existing
                       if r.get('decision', '').strip()
                       and r.get('decision', '').strip().lower() != 'merge']
            if _manual:
                print(f"\n  WARNING: {REVIEW_CSV} already contains "
                      f"{len(_manual)} manual decision(s).")
                print("  Rename or delete the file before re-running to avoid "
                      "overwriting your edits.")
                print("  Skipping CSV write — SCHOOL_CANONICAL table was still updated.")
                con.close()
                return
        except Exception:
            pass  # unreadable file → proceed with overwrite

    # Auto-accepted pairs come first (decision = "merge"), sorted by score desc.
    # Uncertain pairs follow (decision left blank for manual review).
    auto_pairs.sort(key=lambda x: -x[2])
    review_pairs.sort(key=lambda x: -x[2])
    all_pairs = [(a, b, score, 'merge') for a, b, score in auto_pairs] + \
                [(a, b, score, '')      for a, b, score in review_pairs]

    with open(REVIEW_CSV, 'w', newline='', encoding='utf-8') as f:
        w = csv.writer(f)
        w.writerow(['name_a', 'count_a', 'country_a',
                    'name_b', 'count_b', 'country_b',
                    'similarity', 'advisor_evidence', 'proposed_canonical', 'decision'])
        for a, b, score, decision in all_pairs:
            ca, cntA = school_info[a]
            cb, cntB = school_info[b]
            proposed = a if cntA >= cntB else b
            has_adv = advisor_pair_counts.get(frozenset([a, b]), 0)
            w.writerow([a, cntA, ca, b, cntB, cb,
                        f'{score:.1f}', has_adv, proposed, decision])
    print(f"  Review CSV written: {len(auto_pairs)} merged + "
          f"{len(review_pairs)} pending review -> {REVIEW_CSV}")

    # ── Summary stats ────────────────────────────────────────────────────────
    method_counts = defaultdict(int)
    for _, (_, _, method) in raw_to_canonical.items():
        method_counts[method] += 1

    print("\nMatch method breakdown:")
    for method, count in sorted(method_counts.items(), key=lambda x: -x[1]):
        print(f"  {method:20s}: {count:5,}")

    total_raw = len(raw_to_canonical)
    total_canon = len(set(c for c,_,_ in raw_to_canonical.values()))
    print(f"\nReduced {total_raw:,} raw names to {total_canon:,} canonical names "
          f"({total_raw - total_canon:,} duplicates resolved, "
          f"{100*(total_raw-total_canon)/total_raw:.1f}%)")

    con.close()

if __name__ == '__main__':
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=DB_PATH, help="path to mg.db (default: %(default)s)")
    DB_PATH = os.path.expanduser(ap.parse_args().db)
    main()
