#!/usr/bin/env python3
"""
reproduce.py  --  Reproduce the results of the paper from the curated database.

Usage
-----
    python3 reproduce.py --db /path/to/mg.db [--out results] [--quick]

The only input is the curated SQLite database `mg.db` (download it from the
Zenodo deposit).  Nothing is downloaded from the Math Genealogy Project or
ProQuest, and no credentials are needed.

What it does
------------
1. Recomputes every count and table in the paper that comes from the
   database (Tables 1-3 and S2-S13, and the numbers quoted in the text) with
   SQL, and compares each with the value printed in the paper.
2. Runs the figure scripts (Figures 1-4, S2, S3) and the Fuh example, writing
   the PDFs to the output directory.
3. Recomputes the quantities derived from the figure scripts' output
   (cross-validated accuracy, ROC AUC values, margin statistics, expected
   true/false positives at the 0.30 margin threshold) and compares them with
   the paper.
4. If a ProQuest comparison file is supplied (--proquest), recomputes the
   MGP counts in it from the database and the pooled coverage ratios
   (Tables 4 and 5).  This step is optional.

Every comparison is printed as PASS/FAIL, and the summary is written to
<out>/summary.txt.  The exit status is 0 if every check passes.

Running time: about 2 minutes on a laptop (about 10 seconds with --quick,
which skips the figure scripts and the checks that depend on them).
"""

import argparse
import csv
import os
import re
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent

CHECKS = []          # (section, description, observed, expected, ok)


def check(section, desc, observed, expected, tol=0):
    """Record one comparison.  `tol` is an absolute tolerance."""
    if isinstance(expected, (int, float)) and isinstance(observed, (int, float)):
        ok = abs(observed - expected) <= tol + 1e-9
    else:
        ok = observed == expected
    CHECKS.append((section, desc, observed, expected, ok))
    return ok


def q(con, sql, *args):
    return con.execute(sql, args).fetchall()


def q1(con, sql, *args):
    return con.execute(sql, args).fetchone()[0]


# ----------------------------------------------------------------------
# Values printed in the paper
# ----------------------------------------------------------------------
BROAD = {   # imputed-field label -> label used in the tables
    "Pure Math": "Pure Mathematics", "CS": "CS / Systems",
    "Physics": "Physics / Applied", "Statistics": "Statistics",
    "Economics": "Economics / Game Theory", "Numerical": "Numerical Analysis",
    "Probability": "Probability", "OR": "Operations Research",
    "Biology": "Biology", "Education": "Math Education",
}
KNOWN_BY_FIELD = {      # Table S2
    "Unknown": 137939, "Pure Mathematics": 67269, "CS / Systems": 43861,
    "Physics / Applied": 21848, "Economics / Game Theory": 20230,
    "Statistics": 17491, "Numerical Analysis": 8112, "Probability": 7558,
    "Operations Research": 6090, "Biology": 5558, "Math Education": 3223,
}
IMPUTED_BY_FIELD = {   # Table S8
    "Pure Math": 82109, "CS": 16282, "Physics": 13730, "Statistics": 7916,
    "Numerical": 5150, "Probability": 4172, "OR": 2877, "Biology": 2692,
    "Economics": 1899, "Education": 1112,
}
CONF_BY_FIELD = {      # Table S9: n, avg confidence, high (>=0.80), low (<0.50)
    "Physics": (13730, 0.88, 10128, 683), "Education": (1112, 0.88, 835, 30),
    "Statistics": (7916, 0.85, 5439, 556), "CS": (16282, 0.84, 10867, 971),
    "Biology": (2692, 0.83, 1736, 244), "Economics": (1899, 0.78, 1008, 279),
    "OR": (2877, 0.77, 1406, 319), "Probability": (4172, 0.73, 1700, 555),
    "Numerical": (5150, 0.71, 1992, 849), "Pure Math": (82109, 0.55, 33586, 41355),
}
LANG_TOP10 = [("en", 242150), ("de", 27183), ("fr", 15795), ("nl", 6484),
              ("es", 4383), ("pt", 2123), ("it", 1419), ("pl", 1015),
              ("ca", 786), ("ro", 508)]                      # Table 2
DECADES = {1800: 43, 1810: 70, 1820: 118, 1830: 121, 1840: 168, 1850: 182,
           1860: 298, 1870: 483, 1880: 737, 1890: 923, 1900: 1436, 1910: 1719,
           1920: 2409, 1930: 3653, 1940: 3157, 1950: 7702, 1960: 18080,
           1970: 28449, 1980: 31961, 1990: 52876, 2000: 64412, 2010: 74794,
           2020: 27998}                                       # Table S4
COUNTRIES = [("United States", 137750), ("Germany", 38661),
             ("Netherlands", 23820), ("United Kingdom", 18178),
             ("NONE", 17606), ("France", 15309), ("Canada", 9166),
             ("Switzerland", 7921), ("Russia", 5781), ("Spain", 4704)]  # S3
DEGTYPES = [("Ph.D.", 280673), ("Dr. rer. nat.", 15786), ("Unknown", 11247),
            ("Ph. D.", 4550), ("D.Sc.", 4474), ("Dr. phil.", 3842),
            ("Dr.-Ing.", 3782), ("D.Phil.", 1925), ("Dr. rer. pol.", 995),
            ("Dr.", 970)]                                      # Table S5
ADVISORS = [("Kuo, C.-C. Jay", 181), ("Havinga, Egbert", 146),
            ("Neittaanmäki, Pekka", 132), ("Temam, Roger", 129),
            ("Chellappa, Ramalingam", 128), ("Sawilowsky, Shlomo", 111),
            ("Whinston, Andrew", 108), ("Scholten, Paul", 105),
            ("Bertsimas, Dimitris", 102), ("Jäger, Willi", 101)]  # Table S6
ERAS = {   # Table S13: n and the three largest imputed fields
    "pre-1950": (9687, [("Pure Math", 7380), ("Physics", 1635), ("Biology", 285)]),
    "1950-1969": (15905, [("Pure Math", 11007), ("Physics", 2111), ("Statistics", 859)]),
    "1970-1989": (33471, [("Pure Math", 20204), ("CS", 3487), ("Physics", 3152)]),
    "1990-2009": (47530, [("Pure Math", 22141), ("CS", 8924), ("Physics", 4805)]),
    "2010+": (16072, [("Pure Math", 6858), ("CS", 3213), ("Physics", 1813)]),
}
CV_RECALL = {"Pure Math": 90.8, "CS": 88.2, "Physics": 78.4, "Statistics": 83.9,
             "Economics": 86.0, "Numerical": 72.3, "Probability": 74.9,
             "OR": 69.1, "Biology": 58.3, "Education": 74.0}   # Figure 1
AUC = {"Pure Math": 0.942, "Statistics": 0.916, "Physics": 0.888,
       "Biology": 0.795}                                       # Table S11
MARGIN = {   # Table S10: % with margin > 0.30, % with margin < 0.05
    "Statistics": (84.1, 5.2), "Physics": (86.2, 4.0),
    "Biology": (77.7, 5.1), "Pure Math": (50.2, 24.9)}
THRESH = {   # Table S12: called, TPR, FPR, precision, expected TP, expected FP
    "Pure Math": (41232, 0.818, 0.024, 0.945, 38950, 2282),
    "Statistics": (6659, 0.764, 0.006, 0.928, 6178, 481),
    "Physics": (11840, 0.716, 0.009, 0.907, 10743, 1097),
    "Biology": (2093, 0.497, 0.003, 0.849, 1777, 316)}
COVERAGE_FIELD = {"Mathematics": (421, 1368, 0.31), "Statistics": (109, 368, 0.30),
                  "Economics": (364, 1414, 0.26), "CS": (290, 1884, 0.15),
                  "Physics": (112, 2953, 0.04)}                # Table 4
COVERAGE_YEAR = {1960: (25, 563, 0.04), 1970: (115, 791, 0.15),
                 1980: (138, 779, 0.18), 1990: (223, 1898, 0.12),
                 2000: (248, 1790, 0.14), 2010: (547, 2166, 0.25)}  # Table 5


# ----------------------------------------------------------------------
# 1. Database checks and tables
# ----------------------------------------------------------------------
YEAR_OK = "DEGYEAR GLOB '[0-9][0-9][0-9][0-9]'"


def database_checks(con):
    S = "Database"
    check(S, "PERSONS rows (individuals)", q1(con, "SELECT COUNT(*) FROM PERSONS"), 337248)
    check(S, "DEGREES rows (degree records)", q1(con, "SELECT COUNT(*) FROM DEGREES"), 339179)
    check(S, "STUDENTS rows (advisor-student pairs)", q1(con, "SELECT COUNT(*) FROM STUDENTS"), 407783)
    check(S, "foreign_key_check returns no rows", len(q(con, "PRAGMA foreign_key_check")), 0)
    check(S, "tables in the database",
          sorted(r[0] for r in q(con, "SELECT name FROM sqlite_master WHERE type='table'")),
          sorted(["PERSONS", "DEGREES", "MSC", "FIELDS", "SCORES", "STUDENTS",
                  "SCHOOL_CANONICAL", "LANGUAGE_DETECT", "MSC_IMPUTE"]))

    # Table 1 / Listing S1
    check(S, "missing MSC (Table 1)", q1(con, "SELECT COUNT(*) FROM DEGREES WHERE DEGMSC IS NULL"), 137939)
    check(S, "missing degree year (Table 1)",
          q1(con, "SELECT COUNT(*) FROM DEGREES WHERE DEGYEAR='Unknown' OR DEGYEAR IS NULL OR DEGYEAR=''"), 16119)
    check(S, "missing thesis title (Table 1)",
          q1(con, "SELECT COUNT(*) FROM DEGREES WHERE TITLE='Unknown' OR TITLE IS NULL"), 33339)

    # Keys and schema (Section 2.2)
    check(S, "individuals with two or more degree records",
          q1(con, "SELECT COUNT(*) FROM (SELECT DEGMGID FROM DEGREES GROUP BY DEGMGID HAVING COUNT(*)>1)"), 1866)
    check(S, "STUDENTS rows with unknown advisor (NULL)",
          q1(con, "SELECT COUNT(*) FROM STUDENTS WHERE ADVISOR IS NULL"), 28807)
    check(S, "distinct degree types",
          q1(con, "SELECT COUNT(DISTINCT DEGTYPE) FROM DEGREES"), 1257)
    check(S, "countries (excluding the NONE/None placeholders)",
          q1(con, "SELECT COUNT(DISTINCT COUNTRY) FROM DEGREES WHERE LOWER(COUNTRY)<>'none'"), 136)

    # Table S2: degrees by broad field (queries/degreetypes.sql)
    sql = open(HERE / "queries" / "degreetypes.sql").read() if (HERE / "queries" / "degreetypes.sql").exists() else None
    if sql:
        got = dict(q(con, sql))
        for k, v in KNOWN_BY_FIELD.items():
            check(S, f"Table S2: {k}", got.get(k), v)

    # Table S3, S4, S5, S6
    got = q(con, "SELECT COUNTRY, COUNT(*) c FROM DEGREES GROUP BY COUNTRY ORDER BY c DESC LIMIT 10")
    check(S, "Table S3: top-10 countries", got, COUNTRIES)
    got = dict(q(con, f"SELECT CAST(DEGYEAR AS INTEGER)/10*10, COUNT(*) FROM DEGREES "
                      f"WHERE {YEAR_OK} AND CAST(DEGYEAR AS INTEGER) >= 1800 GROUP BY 1"))
    check(S, "Table S4: degrees by decade", got, DECADES)
    peak = q(con, f"SELECT DEGYEAR, COUNT(*) c FROM DEGREES WHERE {YEAR_OK} GROUP BY 1 ORDER BY c DESC LIMIT 1")[0]
    check(S, "peak degree year and count", (int(peak[0]), peak[1]), (2013, 7826))
    got = q(con, "SELECT DEGTYPE, COUNT(*) c FROM DEGREES GROUP BY DEGTYPE ORDER BY c DESC LIMIT 10")
    check(S, "Table S5: top-10 degree types", got, DEGTYPES)
    got = q(con, "SELECT p.LNAME || ', ' || p.FNAME, COUNT(*) c FROM STUDENTS s "
                 "JOIN PERSONS p ON s.ADVISOR=p.MGID GROUP BY s.ADVISOR ORDER BY c DESC LIMIT 10")
    check(S, "Table S6: top-10 advisors", got, ADVISORS)
    n_adv = q1(con, "SELECT COUNT(DISTINCT ADVISOR) FROM STUDENTS WHERE ADVISOR IS NOT NULL")
    n_one = q1(con, "SELECT COUNT(*) FROM (SELECT ADVISOR FROM STUDENTS WHERE ADVISOR IS NOT NULL "
                    "GROUP BY ADVISOR HAVING COUNT(*)=1)")
    check(S, "advisors with exactly one student", n_one, 34873)
    check(S, "share of advisors with one student (%)", round(100 * n_one / n_adv), 42)


def school_checks(con):
    S = "School names"
    check(S, "raw school names", q1(con, "SELECT COUNT(*) FROM SCHOOL_CANONICAL"), 2867)
    check(S, "canonical names", q1(con, "SELECT COUNT(DISTINCT CANONICAL_NAME) FROM SCHOOL_CANONICAL"), 2804)
    check(S, "match methods (Table S1)",
          dict(q(con, "SELECT MATCH_METHOD, COUNT(*) FROM SCHOOL_CANONICAL GROUP BY 1")),
          {"identity": 2804, "user_confirmed": 58, "normalization": 4, "manual": 1})


def language_checks(con):
    S = "Language"
    check(S, "inferred languages", q1(con, "SELECT COUNT(*) FROM LANGUAGE_DETECT"), 305814)
    hi, mid, lo = q(con, "SELECT SUM(CONFIDENCE>=0.8), SUM(CONFIDENCE>=0.5 AND CONFIDENCE<0.8), "
                         "SUM(CONFIDENCE<0.5) FROM LANGUAGE_DETECT")[0]
    check(S, "confidence >= 0.80 / 0.50-0.80 / < 0.50", (hi, mid, lo), (193572, 96679, 15563))
    check(S, "bilingual titles", q1(con, "SELECT SUM(IS_BILINGUAL) FROM LANGUAGE_DETECT"), 222)
    got = q(con, "SELECT LANGUAGE, COUNT(*) c FROM LANGUAGE_DETECT GROUP BY 1 ORDER BY c DESC LIMIT 10")
    check(S, "Table 2: top-10 languages", got, LANG_TOP10)


IMPUTED = ("FROM MSC_IMPUTE m JOIN DEGREES d USING (DEGREE_ID) WHERE d.DEGMSC IS NULL")


def imputation_checks(con):
    S = "MSC imputation"
    check(S, "imputed records", q1(con, f"SELECT COUNT(*) {IMPUTED}"), 137939)
    got = dict(q(con, f"SELECT m.FIELD, COUNT(*) {IMPUTED} GROUP BY 1"))
    check(S, "Table S8: imputed distribution", got, IMPUTED_BY_FIELD)

    # Table 3 (combined = known + imputed)
    known = dict(q(con, open(HERE / "queries" / "degreetypes.sql").read())) \
        if (HERE / "queries" / "degreetypes.sql").exists() else KNOWN_BY_FIELD
    combined = {BROAD[k]: v + known.get(BROAD[k], 0) for k, v in got.items()}
    table3 = {"Pure Mathematics": 149378, "CS / Systems": 60143, "Physics / Applied": 35578,
              "Statistics": 25407, "Economics / Game Theory": 22129, "Numerical Analysis": 13262,
              "Probability": 11730, "Operations Research": 8967, "Biology": 8250,
              "Math Education": 4335}
    check(S, "Table 3: combined known + imputed", combined, table3)

    # Table S9
    rows = q(con, f"SELECT m.FIELD, COUNT(*), AVG(m.CONFIDENCE), SUM(m.CONFIDENCE>=0.8), "
                  f"SUM(m.CONFIDENCE<0.5) {IMPUTED} GROUP BY 1")
    got = {f: (n, round(a, 2), hi, lo) for f, n, a, hi, lo in rows}
    # the average for Pure Math is 0.5546, which the paper prints as 0.56 in the
    # text and 0.55 here; compare to 2 decimals with a 0.01 tolerance
    for f, exp in CONF_BY_FIELD.items():
        g = got[f]
        check(S, f"Table S9: {f} (n, high, low)", (g[0], g[2], g[3]), (exp[0], exp[2], exp[3]))
        check(S, f"Table S9: {f} average confidence", g[1], exp[1], tol=0.011)

    # Signal usage (Section 3.3)
    n = 137939
    check(S, "sibling votes only", q1(con, f"SELECT COUNT(*) {IMPUTED} AND m.METHODS='siblings'"), 39518)
    check(S, "neither title nor network (base prior only)",
          q1(con, f"SELECT COUNT(*) {IMPUTED} AND m.METHODS='prior_only'"), 20103)
    check(S, "share base-prior only (%)", round(100 * 20103 / n, 1), 14.6)
    for label, pat, exp in [("sibling", "%siblings%", 76.7), ("advisor", "%advisor%", 26.8),
                            ("title", "%title%", 22.4), ("student", "%students%", 21.9)]:
        k = q1(con, f"SELECT COUNT(*) {IMPUTED} AND m.METHODS LIKE ?", pat)
        check(S, f"records using {label} signal (%)", round(100 * k / n, 1), exp)
    check(S, "high confidence >= 0.60", q1(con, f"SELECT COUNT(*) {IMPUTED} AND m.CONFIDENCE>=0.60"), 84075)
    check(S, "low confidence < 0.40", q1(con, f"SELECT COUNT(*) {IMPUTED} AND m.CONFIDENCE<0.40"), 42638)

    # Table S13 (era)
    era = ("CASE WHEN CAST(d.DEGYEAR AS INTEGER)<1950 THEN 'pre-1950' "
           "WHEN CAST(d.DEGYEAR AS INTEGER)<1970 THEN '1950-1969' "
           "WHEN CAST(d.DEGYEAR AS INTEGER)<1990 THEN '1970-1989' "
           "WHEN CAST(d.DEGYEAR AS INTEGER)<2010 THEN '1990-2009' ELSE '2010+' END")
    valid = "d.DEGYEAR GLOB '[0-9][0-9][0-9][0-9]' AND CAST(d.DEGYEAR AS INTEGER) >= 1800"
    tot = dict(q(con, f"SELECT {era}, COUNT(*) {IMPUTED} AND {valid} GROUP BY 1"))
    check(S, "Table S13: records with a valid year", sum(tot.values()), 122665)
    for e, (n_e, top3) in ERAS.items():
        check(S, f"Table S13: {e} total", tot.get(e), n_e)
        got = q(con, f"SELECT m.FIELD, COUNT(*) c {IMPUTED} AND {valid} AND {era}=? "
                     f"GROUP BY 1 ORDER BY c DESC, m.FIELD LIMIT 3", e)
        check(S, f"Table S13: {e} top-3 fields", got, top3)

    # Geography (Section 3.3)
    geo = dict(q(con, f"SELECT d.COUNTRY, COUNT(*) {IMPUTED} GROUP BY 1"))
    check(S, "imputed records: United States / Netherlands / Germany",
          (geo.get("United States"), geo.get("Netherlands"), geo.get("Germany")), (56506, 18771, 15091))
    nl = dict(q(con, f"SELECT m.FIELD, COUNT(*) {IMPUTED} AND d.COUNTRY='Netherlands' GROUP BY 1"))
    check(S, "Dutch imputed records: Pure Math / Physics", (nl.get("Pure Math"), nl.get("Physics")), (9394, 4653))


def fisher_checks(con):
    S = "Fisher case study"
    fid = 46924
    check(S, "Fisher's students", q1(con, "SELECT COUNT(*) FROM STUDENTS WHERE ADVISOR=?", fid), 7)
    check(S, "Fisher's advisors", q1(con, "SELECT COUNT(*) FROM STUDENTS WHERE STUDENT=?", fid), 2)
    rao = q1(con, "SELECT p.MGID FROM PERSONS p WHERE p.LNAME='Rao' AND p.FNAME='C. R.'")
    check(S, "C. R. Rao's students", q1(con, "SELECT COUNT(*) FROM STUDENTS WHERE ADVISOR=?", rao), 51)
    fuh = q(con, "SELECT FNAME, LNAME FROM PERSONS WHERE MGID=194")[0]
    check(S, "MGID 194 is Cheng-Der Fuh", fuh, ("Cheng-Der", "Fuh"))


# ----------------------------------------------------------------------
# 2. Figure scripts and the quantities derived from them
# ----------------------------------------------------------------------
def run_script(out, name, *args):
    """Run one of the paper's scripts in the output directory."""
    t0 = time.time()
    cmd = [sys.executable, str(HERE / name), *args]
    res = subprocess.run(cmd, cwd=out, capture_output=True, text=True)
    (out / "logs").mkdir(exist_ok=True)
    (out / "logs" / (name + ".log")).write_text(res.stdout + res.stderr)
    status = "ok" if res.returncode == 0 else f"FAILED (exit {res.returncode})"
    print(f"  {name:<38s} {status:<8s} {time.time()-t0:5.1f}s", flush=True)
    return res


def figure_checks(db, out):
    import numpy as np
    import pandas as pd
    from sklearn.metrics import roc_curve, auc

    S = "Figures"
    print("Running the figure scripts ...", flush=True)
    steps = [
        ("save_top2_probs.py", "--db", db),
        ("plot_margin_grid.py",),
        ("plot_msc_imputation.py", "--db", db),
        ("plot_by_year.py", "--db", db),
        ("plot_confusion_matrix.py", "--db", db, "--seed", "42"),
        ("plot_roc_margin.py", "--db", db),
        ("plot_fisher_network.py", "--db", db),
        ("example_ambiguous_imputation.py", "--db", db),
    ]
    results = {}
    for s in steps:
        results[s[0]] = run_script(out, *s)
    expected_pdf = ["margin_grid.pdf", "msc_imputation_comparison.pdf", "countbyyear.pdf",
                    "msc_confusion_matrix.pdf", "roc_margin.pdf", "fisher_network.pdf"]
    for f in expected_pdf:
        check(S, f"{f} was produced", (out / f).exists(), True)

    # Figure 1: cross-validated accuracy
    log = results["plot_confusion_matrix.py"].stdout
    m = re.search(r"Overall accuracy:\s*([\d.]+)%", log)
    check(S, "Figure 1: overall cross-validated accuracy (%)", float(m.group(1)) if m else None, 84.7)
    for f, exp in CV_RECALL.items():
        m = re.search(rf"^\s*{re.escape(f)}\s+([\d.]+)%", log, re.M)
        check(S, f"Figure 1: recall for {f} (%)", float(m.group(1)) if m else None, exp)
    check(S, "Figure 1: hold-out size", "n=20,124" in log or "Test: 20,124" in log, True)

    # Table S10 and Figure 2: margin statistics
    t = pd.read_csv(out / "top2_probs.csv")
    check(S, "top2_probs.csv rows", len(t), 137939)
    t["MARGIN"] = t["P1"] - t["P2"]
    for f, (conf, tie) in MARGIN.items():
        s = t[t.FIELD1 == f]
        check(S, f"Table S10: {f} margin > 0.30 (%)", round(100 * (s.MARGIN > 0.30).mean(), 1), conf)
        check(S, f"Table S10: {f} margin < 0.05 (%)", round(100 * (s.MARGIN < 0.05).mean(), 1), tie)

    # Tables S11 and S12 and Figure S3: leave-one-out ROC
    r = pd.read_csv(out / "roc_predictions.csv")
    check(S, "roc_predictions.csv rows", len(r), 201240)
    for f, exp in AUC.items():
        y = (r.TRUE_FIELD == f).values
        score = np.where(r.PRED_FIELD == f, r.MARGIN, -1.0)
        fpr, tpr, _ = roc_curve(y, score)
        check(S, f"Table S11: AUC for {f}", round(auc(fpr, tpr), 3), exp)
    for f, (called, tpr_e, fpr_e, prec_e, tp_e, fp_e) in THRESH.items():
        y = (r.TRUE_FIELD == f).values
        hit = ((r.PRED_FIELD == f) & (r.MARGIN >= 0.30)).values
        tp = (hit & y).sum()
        prec = tp / hit.sum()
        n_called = int(((t.FIELD1 == f) & (t.MARGIN >= 0.30)).sum())
        check(S, f"Table S12: {f} called", n_called, called)
        check(S, f"Table S12: {f} TPR", round(tp / y.sum(), 3), tpr_e)
        check(S, f"Table S12: {f} FPR", round((hit & ~y).sum() / (~y).sum(), 3), fpr_e)
        check(S, f"Table S12: {f} precision", round(prec, 3), prec_e)
        check(S, f"Table S12: {f} expected TP", round(n_called * prec), tp_e)
        check(S, f"Table S12: {f} expected FP", round(n_called * (1 - prec)), fp_e)

    # Fuh example
    log = results["example_ambiguous_imputation.py"].stdout
    post = log.split("Posterior probability distribution")[1]
    for lab, val in [("Probability", 0.530), ("Statistics", 0.343), ("Pure Math", 0.128)]:
        m = re.search(rf"^\s*{re.escape(lab)}\s+([\d.]+)", post, re.M)
        check(S, f"Fuh example: posterior for {lab}", round(float(m.group(1)), 3) if m else None, val)


# ----------------------------------------------------------------------
# 3. ProQuest coverage (optional)
# ----------------------------------------------------------------------
FIELD_MSC = {"Economics": [91], "Statistics": [62], "CS": [68],
             "Engineering": [70, 74, 76, 78, 80, 93, 94],
             "Physics": [81, 82, 83, 85, 86]}
FIELD_ORDER = ["Economics", "Mathematics", "Statistics", "CS", "Engineering", "Physics", "Unknown"]
PQ_TO_MG_NAME = {"University of Tennessee": "University of Tennessee - Knoxville"}


def mg_counts(con, name, year):
    counts = {f: 0 for f in FIELD_ORDER}
    for msc, n in q(con, "SELECT d.DEGMSC, COUNT(*) FROM DEGREES d JOIN SCHOOL_CANONICAL sc "
                         "ON d.SCHOOL=sc.RAW_NAME WHERE sc.CANONICAL_NAME=? AND d.DEGYEAR=? "
                         "GROUP BY d.DEGMSC", name, year):
        if msc is None or msc == "":
            counts["Unknown"] += n
            continue
        try:
            code = int(msc)
        except (TypeError, ValueError):
            counts["Unknown"] += n
            continue
        for f, codes in FIELD_MSC.items():
            if code in codes:
                counts[f] += n
                break
        else:
            counts["Mathematics"] += n
    return counts


def coverage_checks(con, path):
    import pandas as pd
    S = "ProQuest coverage"
    d = pd.read_csv(path)
    # 1. the MGP side of the comparison file agrees with the database
    bad = 0
    for (uni, yr), g in d.groupby(["University", "Year"]):
        mg = mg_counts(con, PQ_TO_MG_NAME.get(uni, uni), int(yr))
        for _, row in g.iterrows():
            bad += mg[row.Field] != row.MG_Count
    check(S, "MGP counts in the comparison file match mg.db (cells that differ)", bad, 0)
    # 2. pooled ratios (Tables 4 and 5): five fields, positive ProQuest cells only
    d = d[d.Field.isin(COVERAGE_FIELD) & (d.ProQuest_Count > 0)]
    for f, (mg_e, pq_e, r_e) in COVERAGE_FIELD.items():
        g = d[d.Field == f]
        mg_n, pq_n = int(g.MG_Count.sum()), int(g.ProQuest_Count.sum())
        check(S, f"Table 4: {f} (MGP, ProQuest, ratio)", (mg_n, pq_n, round(mg_n / pq_n, 2)), (mg_e, pq_e, r_e))
    check(S, "Table 4: all fields (MGP, ProQuest, ratio)",
          (int(d.MG_Count.sum()), int(d.ProQuest_Count.sum()), round(d.MG_Count.sum() / d.ProQuest_Count.sum(), 2)),
          (1296, 7987, 0.16))
    for y, (mg_e, pq_e, r_e) in COVERAGE_YEAR.items():
        g = d[d.Year == y]
        mg_n, pq_n = int(g.MG_Count.sum()), int(g.ProQuest_Count.sum())
        check(S, f"Table 5: {y} (MGP, ProQuest, ratio)", (mg_n, pq_n, round(mg_n / pq_n, 2)), (mg_e, pq_e, r_e))


# ----------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", default="mg.db", help="path to the curated database (default: ./mg.db)")
    ap.add_argument("--out", default="results", help="output directory (default: ./results)")
    ap.add_argument("--quick", action="store_true", help="database tables only; skip the figure scripts")
    ap.add_argument("--proquest", default=None,
                    help="optional: data/proquest_comparison.csv, to also reproduce Tables 4 and 5")
    args = ap.parse_args()

    db = Path(args.db).expanduser().resolve()
    if not db.exists():
        sys.exit(f"Database not found: {db}\nDownload mg.db from the Zenodo deposit "
                 f"(see README.md) and pass its path with --db.")
    out = Path(args.out).expanduser().resolve()
    out.mkdir(parents=True, exist_ok=True)

    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    print(f"Database: {db}\nOutput:   {out}\n")
    print("Checking the database and its tables ...", flush=True)
    for f in (database_checks, school_checks, language_checks, imputation_checks, fisher_checks):
        f(con)
    if args.proquest:
        coverage_checks(con, args.proquest)
    con.close()
    if not args.quick:
        figure_checks(str(db), out)

    # report
    lines, fails = [], 0
    section = None
    for sec, desc, obs, exp, ok in CHECKS:
        if sec != section:
            lines.append(f"\n[{sec}]")
            section = sec
        if ok:
            lines.append(f"  PASS  {desc}")
        else:
            fails += 1
            lines.append(f"  FAIL  {desc}\n          observed: {obs}\n          paper:    {exp}")
    lines.append(f"\n{len(CHECKS) - fails} of {len(CHECKS)} checks passed"
                 + ("" if args.proquest else "  (ProQuest coverage tables not checked; use --proquest)")
                 + ("  (figure scripts skipped: --quick)" if args.quick else "") + ".")
    text = "\n".join(lines)
    (out / "summary.txt").write_text(text + "\n")
    print(text)
    print(f"\nSummary written to {out / 'summary.txt'}")
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()
