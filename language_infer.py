"""
language_infer.py  –  Infer DEGREES.LANGUAGE from thesis title + country.

Outputs a LANGUAGE_DETECT table:
    DEGREE_ID    INTEGER PRIMARY KEY  – DEGREE_ID (FK) of the DEGREES row
    DEGMGID      INTEGER              – person ID (DEGREES.DEGMGID)
    LANGUAGE     CHAR(30)   – primary detected language (BCP-47 code, or 'und')
    CONFIDENCE   REAL       – 0.0–1.0
    METHOD       CHAR(30)   – how we arrived at the answer
    IS_BILINGUAL INTEGER    – 1 if title appears to contain two languages
    LANG2        CHAR(30)   – second language when IS_BILINGUAL = 1

Usage:
    python3 language_infer.py [--db ~/Data/MG/mg.db] [--limit N]
"""

import re
import sqlite3
import argparse
import sys
from pathlib import Path

from langdetect import detect_langs, LangDetectException
from langdetect import DetectorFactory
DetectorFactory.seed = 42           # reproducible results

# ---------------------------------------------------------------------------
# Country → primary language(s) prior
# Tuples: (language_code, weight).  Weights are relative, not probabilities.
# ---------------------------------------------------------------------------
COUNTRY_LANG: dict = {
    "United States":          [("en", 1.0)],
    "United Kingdom":         [("en", 1.0)],
    "Canada":                 [("en", 0.75), ("fr", 0.25)],
    "Australia":              [("en", 1.0)],
    "New Zealand":            [("en", 1.0)],
    "Ireland":                [("en", 1.0)],
    "South Africa":           [("en", 0.80), ("af", 0.10)],
    "India":                  [("en", 0.90)],
    "Singapore":              [("en", 0.90)],
    "Israel":                 [("he", 0.50), ("en", 0.50)],
    "Germany":                [("de", 0.95), ("en", 0.05)],
    "Austria":                [("de", 0.95)],
    "Switzerland":            [("de", 0.65), ("fr", 0.25), ("it", 0.10)],
    "Netherlands":            [("nl", 0.90), ("en", 0.10)],
    "Belgium":                [("fr", 0.50), ("nl", 0.45), ("de", 0.05)],
    "France":                 [("fr", 0.98)],
    "Luxembourg":             [("fr", 0.60), ("de", 0.30)],
    "Spain":                  [("es", 0.95)],
    "Mexico":                 [("es", 1.0)],
    "Argentina":              [("es", 1.0)],
    "Chile":                  [("es", 1.0)],
    "Colombia":               [("es", 1.0)],
    "Venezuela":              [("es", 1.0)],
    "Peru":                   [("es", 1.0)],
    "Ecuador":                [("es", 1.0)],
    "Bolivia":                [("es", 1.0)],
    "Uruguay":                [("es", 1.0)],
    "Cuba":                   [("es", 1.0)],
    "Brazil":                 [("pt", 1.0)],
    "Portugal":               [("pt", 1.0)],
    "Italy":                  [("it", 0.95)],
    "Russia":                 [("ru", 0.98)],
    "Ukraine":                [("uk", 0.80), ("ru", 0.20)],
    "Belarus":                [("be", 0.50), ("ru", 0.50)],
    "Poland":                 [("pl", 0.98)],
    "Czech Republic":         [("cs", 0.98)],
    "Czechoslovakia":         [("cs", 0.60), ("sk", 0.40)],
    "Slovakia":               [("sk", 0.98)],
    "Hungary":                [("hu", 0.98)],
    "Romania":                [("ro", 0.98)],
    "Bulgaria":               [("bg", 0.98)],
    "Croatia":                [("hr", 0.98)],
    "Serbia":                 [("sr", 0.98)],
    "Yugoslavia":             [("sr", 0.50), ("hr", 0.30), ("sl", 0.20)],
    "Slovenia":               [("sl", 0.98)],
    "Greece":                 [("el", 0.98)],
    "Turkey":                 [("tr", 0.98)],
    "Sweden":                 [("sv", 0.98)],
    "Norway":                 [("no", 0.98)],
    "Denmark":                [("da", 0.98)],
    "Finland":                [("fi", 0.85), ("sv", 0.10)],
    "Iceland":                [("is", 0.98)],
    "Japan":                  [("ja", 0.70), ("en", 0.30)],
    "China":                  [("zh-cn", 0.80), ("en", 0.20)],
    "Taiwan":                 [("zh-tw", 0.80), ("en", 0.20)],
    "South Korea":            [("ko", 0.80), ("en", 0.20)],
    "Iran":                   [("fa", 0.90), ("en", 0.10)],
    "Iraq":                   [("ar", 0.95)],
    "Saudi Arabia":           [("ar", 1.0)],
    "Egypt":                  [("ar", 0.90), ("en", 0.10)],
    "Jordan":                 [("ar", 0.95)],
    "Lebanon":                [("ar", 0.80), ("fr", 0.15)],
    "Algeria":                [("fr", 0.70), ("ar", 0.30)],
    "Morocco":                [("fr", 0.60), ("ar", 0.40)],
    "Tunisia":                [("fr", 0.60), ("ar", 0.40)],
    "Pakistan":               [("en", 0.60), ("ur", 0.40)],
    "Lithuania":              [("lt", 0.98)],
    "Latvia":                 [("lv", 0.98)],
    "Estonia":                [("et", 0.98)],
    "Georgia":                [("ka", 0.98)],
    "Armenia":                [("hy", 0.98)],
    "Azerbaijan":             [("az", 0.98)],
    "Kazakhstan":             [("kk", 0.50), ("ru", 0.50)],
    "Uzbekistan":             [("uz", 0.50), ("ru", 0.50)],
    "Vietnam":                [("vi", 0.90), ("en", 0.10)],
    "Indonesia":              [("id", 0.90), ("en", 0.10)],
    "Malaysia":               [("ms", 0.70), ("en", 0.30)],
    "Thailand":               [("th", 0.80), ("en", 0.20)],
    "Albania":                [("sq", 0.98)],
}

# langdetect uses 'zh-cn'/'zh-tw'; normalise to 'zh'
LANG_NORM = {"zh-cn": "zh", "zh-tw": "zh"}

def normalise(code):
    return LANG_NORM.get(code, code)

# Language pairs that langdetect regularly confuses.
# When the detected language is X and the country prior favours Y,
# and (X, Y) is a confusable pair, defer to the country prior.
CONFUSABLE = {
    frozenset({"nl", "af"}),       # Dutch / Afrikaans
    frozenset({"da", "no"}),       # Danish / Norwegian
    frozenset({"da", "sv"}),       # Danish / Swedish
    frozenset({"no", "sv"}),       # Norwegian / Swedish
    frozenset({"cs", "sk"}),       # Czech / Slovak
    frozenset({"hr", "sr"}),       # Croatian / Serbian
    frozenset({"id", "ms"}),       # Indonesian / Malay
    frozenset({"uk", "ru"}),       # Ukrainian / Russian
    frozenset({"be", "ru"}),       # Belarusian / Russian
    frozenset({"ca", "es"}),       # Catalan / Spanish
    frozenset({"gl", "pt"}),       # Galician / Portuguese
    frozenset({"pt", "es"}),       # Portuguese / Spanish
}

# ---------------------------------------------------------------------------
# Bilingual separator patterns
# ---------------------------------------------------------------------------
# We require a fairly long string on each side to avoid triggering on math.
_BILINGUAL_SEP = re.compile(
    r'(?<=\w)\s*/\s*(?=\w)'          # word / word
    r'|(?<=\w)\s+=\s+(?=[A-ZÁÉÀÜÄÖÑ])',  # word = UPPERCASE (not math)
    re.IGNORECASE,
)

# Remove LaTeX/HTML/math noise before detection
_NOISE = re.compile(
    r'\\[a-zA-Z]+\{[^}]*\}'   # \cmd{...}
    r'|\$[^$]+\$'              # $inline math$
    r'|&quot;'                 # HTML entity
    r'|\{[^}]+\}'              # {braces}
    r'|\[[^\]]+\]'             # [brackets]
    r'|<[^>]+>'                # <tags>
    r'|\([^)]{1,40}\)'         # (short parenthetical) — math, abbrev
)

def clean_title(title):
    t = _NOISE.sub(' ', title)
    t = re.sub(r'\s+', ' ', t).strip()
    return t

def split_bilingual(title):
    """Return (part1, part2) if title looks bilingual, else None."""
    parts = _BILINGUAL_SEP.split(title, maxsplit=1)
    if len(parts) < 2:
        return None
    a, b = parts[0].strip(), parts[1].strip()
    # Both parts must be non-trivial (>=4 words) to count as bilingual
    if len(a.split()) >= 4 and len(b.split()) >= 4:
        return a, b
    return None

# ---------------------------------------------------------------------------
# Language detection
# ---------------------------------------------------------------------------
MIN_WORDS = 4   # titles shorter than this get penalised confidence

def detect(text):
    """Return sorted list of (lang, prob) from langdetect, normalised."""
    try:
        results = detect_langs(text)
        return [(normalise(r.lang), round(r.prob, 4)) for r in results]
    except LangDetectException:
        return []

def country_prior(country):
    entries = COUNTRY_LANG.get(country, [])
    if not entries:
        return {}
    total = sum(w for _, w in entries)
    return {normalise(lang): w / total for lang, w in entries}

def infer_language(title, country):
    """
    Returns a dict with keys:
        language, confidence, method, is_bilingual, lang2
    """
    result = dict(language="und", confidence=0.0, method="none",
                  is_bilingual=0, lang2=None)

    title_clean = clean_title(title)
    n_words = len(title_clean.split())
    prior = country_prior(country)

    # --- 1. Very short titles: rely on country prior ---
    if n_words < MIN_WORDS:
        if prior:
            best = max(prior, key=prior.get)
            p = prior[best]
            conf = p * 0.7  # discount: text didn't help
            return dict(language=best,
                        confidence=round(conf, 4),
                        method="country_prior",
                        is_bilingual=0, lang2=None)
        det_short = detect(title_clean)
        if det_short:
            return dict(language=det_short[0][0],
                        confidence=round(det_short[0][1] * 0.5, 4),
                        method="text",
                        is_bilingual=0, lang2=None)
        return result

    # --- 2. Check for bilingual structure (on cleaned title) ---
    bi = split_bilingual(title_clean)
    if bi:
        part1, part2 = bi
        # Re-clean each part (removes any residual math noise in the split)
        part1 = clean_title(part1)
        part2 = clean_title(part2)
        # Both parts must still have enough words after cleaning
        if len(part1.split()) >= 4 and len(part2.split()) >= 4:
            det1 = detect(part1)
            det2 = detect(part2)
            lang1 = det1[0][0] if det1 else "und"
            lang2 = det2[0][0] if det2 else "und"
            conf1 = det1[0][1] if det1 else 0.0
            conf2 = det2[0][1] if det2 else 0.0
        else:
            lang1 = lang2 = "und"
            conf1 = conf2 = 0.0
        # Both sides need reasonable confidence AND must differ in language
        if (lang1 != lang2 and lang1 != "und" and lang2 != "und"
                and conf1 >= 0.50 and conf2 >= 0.50):
            # Pick the non-English one as primary language
            if lang1 == "en":
                primary, secondary = lang2, lang1
                conf = conf2
            else:
                primary, secondary = lang1, lang2
                conf = conf1
            return dict(language=primary,
                        confidence=round(min(conf, 0.95), 4),
                        method="bilingual_sep",
                        is_bilingual=1,
                        lang2=secondary)

    # --- 3. Text-based detection ---
    det = detect(title_clean)

    if not det:
        if prior:
            best = max(prior, key=prior.get)
            return dict(language=best,
                        confidence=round(prior[best] * 0.5, 4),
                        method="country_prior",
                        is_bilingual=0, lang2=None)
        return result

    # --- 4. Blend text detection + country prior ---
    # Longer/clearer titles: trust text more; shorter: trust prior more
    if n_words >= 8:
        text_weight, prior_weight = 0.75, 0.25
    elif n_words >= MIN_WORDS:
        text_weight, prior_weight = 0.55, 0.45
    else:
        text_weight, prior_weight = 0.30, 0.70

    candidates = {}
    for lang, prob in det:
        candidates[lang] = candidates.get(lang, 0) + prob * text_weight
    for lang, w in prior.items():
        candidates[lang] = candidates.get(lang, 0) + w * prior_weight

    best_lang = max(candidates, key=candidates.get)
    conf = min(candidates[best_lang], 1.0)

    # If text detection and country prior disagree but languages are
    # confusable (e.g. Dutch/Afrikaans), trust the country prior
    text_lang = det[0][0]
    if (prior and best_lang == text_lang and text_lang not in prior):
        prior_top = max(prior, key=prior.get) if prior else None
        if prior_top and frozenset({text_lang, prior_top}) in CONFUSABLE:
            best_lang = prior_top
            conf = prior[prior_top] * prior_weight + conf * 0.1

    # Slight penalty for medium-length titles
    if n_words < 6:
        conf *= 0.85

    method = "text"
    if prior and best_lang in prior:
        method = "text+country"
    elif prior and det[0][0] != best_lang:
        method = "country_override"

    return dict(language=best_lang,
                confidence=round(conf, 4),
                method=method,
                is_bilingual=0, lang2=None)

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default="~/Data/MG/mg.db")
    parser.add_argument("--limit", type=int, default=0,
                        help="Process only first N rows (0 = all)")
    parser.add_argument("--batch", type=int, default=5000)
    args = parser.parse_args()

    db_path = Path(args.db).expanduser()
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode=WAL")

    # Create or replace the output table
    conn.executescript("""
        DROP TABLE IF EXISTS LANGUAGE_DETECT;
        CREATE TABLE LANGUAGE_DETECT (
            DEGREE_ID    INTEGER PRIMARY KEY REFERENCES DEGREES(DEGREE_ID),
            DEGMGID      INTEGER,
            LANGUAGE     CHAR(30),
            CONFIDENCE   REAL,
            METHOD       CHAR(30),
            IS_BILINGUAL INTEGER DEFAULT 0,
            LANG2        CHAR(30)
        );
    """)
    conn.commit()

    query = """
        SELECT DEGREE_ID, DEGMGID, TITLE, COUNTRY
        FROM   DEGREES
        WHERE  TITLE IS NOT NULL AND LENGTH(TITLE) > 3
    """
    if args.limit:
        query += f" LIMIT {args.limit}"

    total_q = "SELECT COUNT(*) FROM DEGREES WHERE TITLE IS NOT NULL AND LENGTH(TITLE) > 3"
    total = conn.execute(total_q).fetchone()[0]
    if args.limit:
        total = min(total, args.limit)

    rows = conn.execute(query).fetchall()
    print(f"Processing {len(rows):,} titles ...", flush=True)

    batch = []
    for i, (rowid, mgid, title, country) in enumerate(rows, 1):
        res = infer_language(title, country or "")
        batch.append((
            rowid,
            mgid,
            res["language"],
            res["confidence"],
            res["method"],
            res["is_bilingual"],
            res["lang2"],
        ))
        if len(batch) >= args.batch:
            conn.executemany(
                "INSERT INTO LANGUAGE_DETECT VALUES (?,?,?,?,?,?,?)", batch
            )
            conn.commit()
            batch.clear()
            pct = i * 100 // total
            print(f"  {i:>7,} / {total:,}  ({pct}%)", flush=True)

    if batch:
        conn.executemany(
            "INSERT INTO LANGUAGE_DETECT VALUES (?,?,?,?,?,?,?)", batch
        )

        conn.commit()

    print(f"\nDone.  {len(rows):,} rows written to LANGUAGE_DETECT.")

    # Quick summary
    print("\nTop languages:")
    for lang, n in conn.execute("""
        SELECT LANGUAGE, COUNT(*) AS n
        FROM   LANGUAGE_DETECT
        GROUP  BY LANGUAGE
        ORDER  BY n DESC
        LIMIT  25
    """).fetchall():
        print(f"  {lang:<12}  {n:>9,}")

    bilingual = conn.execute(
        "SELECT COUNT(*) FROM LANGUAGE_DETECT WHERE IS_BILINGUAL=1"
    ).fetchone()[0]
    print(f"\nBilingual titles detected: {bilingual:,}")

    hi = conn.execute(
        "SELECT COUNT(*) FROM LANGUAGE_DETECT WHERE CONFIDENCE >= 0.8"
    ).fetchone()[0]
    med = conn.execute(
        "SELECT COUNT(*) FROM LANGUAGE_DETECT WHERE CONFIDENCE >= 0.5 AND CONFIDENCE < 0.8"
    ).fetchone()[0]
    lo = conn.execute(
        "SELECT COUNT(*) FROM LANGUAGE_DETECT WHERE CONFIDENCE < 0.5"
    ).fetchone()[0]
    print(f"\nConfidence bands:")
    print(f"  high  (>=0.80): {hi:>9,}")
    print(f"  med   (0.5-0.8): {med:>9,}")
    print(f"  low   (<0.50):  {lo:>9,}")

    conn.close()

if __name__ == "__main__":
    main()
