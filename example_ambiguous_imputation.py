"""
example_ambiguous_imputation.py
────────────────────────────────
Reproduces the Cheng-Der Fuh (MGID 194) near-tie example used in MG.tex
to illustrate ambiguity in the MSC broad-field imputation.

The thesis "The Bootstrap Method for Markov Chains" (1989) fires two
competing keyword signals:
  • bootstrap → Statistics (weight 2.5)
  • Markov    → Probability (weight 2.0)
and the network (advisor + siblings) leans toward Probability, producing
a near-tie: Probability 0.530, Statistics 0.343.

Usage:
    python3 example_ambiguous_imputation.py [--db ~/Data/MG/mg.db]
"""

import argparse
import sqlite3
from collections import defaultdict
from pathlib import Path

from msc_impute import (
    MSC_TO_FIELD, ALL_FIELDS, FIELD_PRIOR,
    year_factor, title_scores, normalize, _zero_scores,
)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default="~/Data/MG/mg.db")
    args = parser.parse_args()

    conn = sqlite3.connect(Path(args.db).expanduser())
    mgid = 194  # Cheng-Der Fuh

    # ------------------------------------------------------------------ #
    # 1. Build person_fields and advisor graph from all known-MSC records
    # ------------------------------------------------------------------ #
    person_fields: dict[int, list[str]] = defaultdict(list)
    for m, msc in conn.execute(
        "SELECT DEGMGID, DEGMSC FROM DEGREES WHERE DEGMSC IS NOT NULL"
    ).fetchall():
        person_fields[m].append(MSC_TO_FIELD.get(msc, "Pure Math"))

    student_to_advisors: dict[int, list[int]] = defaultdict(list)
    advisor_to_students: dict[int, list[int]] = defaultdict(list)
    for adv, stu in conn.execute("SELECT ADVISOR, STUDENT FROM STUDENTS").fetchall():
        student_to_advisors[stu].append(adv)
        advisor_to_students[adv].append(stu)

    # ------------------------------------------------------------------ #
    # 2. Subject record
    # ------------------------------------------------------------------ #
    subject = conn.execute("""
        SELECT p.FNAME, p.LNAME, d.TITLE, d.DEGYEAR,
               mi.FIELD, mi.CONFIDENCE, mi.METHODS
        FROM PERSONS p
        JOIN DEGREES d ON d.DEGMGID = p.MGID
        JOIN MSC_IMPUTE mi ON mi.DEGREE_ID = d.DEGREE_ID
        WHERE p.MGID = ? AND mi.METHODS != 'observed'
    """, (mgid,)).fetchone()

    fname, lname, title, year, imp_field, conf, methods = subject
    print("=" * 60)
    print(f"SUBJECT: {fname} {lname}  (MGID {mgid})")
    print(f"  Year:    {year}")
    print(f"  Title:   {title}")
    print(f"  Imputed: {imp_field}  (confidence={conf:.4f}, signals={methods})")

    # ------------------------------------------------------------------ #
    # 3. Recompute full probability distribution
    # ------------------------------------------------------------------ #
    t_raw = title_scores(title or "")
    t_total = sum(t_raw.values())
    tw = min(t_total / 8.0, 1.0)

    net_votes = _zero_scores()
    for adv in student_to_advisors.get(mgid, []):
        for f in person_fields.get(adv, []):
            net_votes[f] += 1.0
    for adv in student_to_advisors.get(mgid, []):
        for sib in advisor_to_students.get(adv, []):
            if sib == mgid:
                continue
            for f in person_fields.get(sib, []):
                net_votes[f] += 0.35
    for stu in advisor_to_students.get(mgid, []):
        for f in person_fields.get(stu, []):
            net_votes[f] += 0.6

    net_total = sum(net_votes.values())
    nw = min(net_total / 3.0, 1.0)
    total_w = tw + nw

    combined = _zero_scores()
    if total_w > 0:
        for f in ALL_FIELDS:
            t_c = (t_raw[f] / t_total) if t_total > 0 else 0.0
            n_c = (net_votes[f] / net_total) if net_total > 0 else 0.0
            combined[f] = (tw * t_c + nw * n_c) / total_w
    else:
        combined = dict(FIELD_PRIOR)

    for f in ALL_FIELDS:
        combined[f] *= year_factor(f, year)
    combined = normalize(combined)

    print("\n  Title keyword scores (nonzero):")
    for f, w in sorted(t_raw.items(), key=lambda x: -x[1]):
        if w > 0:
            print(f"    {f:<20} {w:.1f}")

    print("\n  Network votes (nonzero):")
    for f, w in sorted(net_votes.items(), key=lambda x: -x[1]):
        if w > 0:
            print(f"    {f:<20} {w:.2f}")

    print("\n  Posterior probability distribution (nonzero):")
    for f, p in sorted(combined.items(), key=lambda x: -x[1]):
        if p > 0.001:
            print(f"    {f:<20} {p:.4f}")

    # ------------------------------------------------------------------ #
    # 4. Advisors
    # ------------------------------------------------------------------ #
    print("\n" + "=" * 60)
    print("ADVISORS:")
    for adv_id in student_to_advisors.get(mgid, []):
        r = conn.execute("""
            SELECT p.FNAME, p.LNAME, d.TITLE, d.DEGYEAR, mi.FIELD, d.DEGMSC
            FROM PERSONS p
            LEFT JOIN DEGREES d ON d.DEGMGID = p.MGID
            LEFT JOIN MSC_IMPUTE mi ON mi.DEGREE_ID = d.DEGREE_ID
            WHERE p.MGID = ? LIMIT 1
        """, (adv_id,)).fetchone()
        if r:
            print(f"  {r[0]} {r[1]}  (MGID {adv_id})")
            print(f"    Year:  {r[3]}")
            print(f"    Title: {r[2]}")
            print(f"    Field: {r[4]}  (MSC={r[5]})")

    # ------------------------------------------------------------------ #
    # 5. Academic siblings (co-students of same advisor)
    # ------------------------------------------------------------------ #
    print("\n" + "=" * 60)
    print("ACADEMIC SIBLINGS:")
    seen = set()
    for adv in student_to_advisors.get(mgid, []):
        for sib in advisor_to_students.get(adv, []):
            if sib == mgid or sib in seen:
                continue
            seen.add(sib)
            r = conn.execute("""
                SELECT p.FNAME, p.LNAME, d.TITLE, d.DEGYEAR, mi.FIELD, d.DEGMSC
                FROM PERSONS p
                JOIN DEGREES d ON d.DEGMGID = p.MGID
                JOIN MSC_IMPUTE mi ON mi.DEGREE_ID = d.DEGREE_ID
                WHERE p.MGID = ? LIMIT 1
            """, (sib,)).fetchone()
            if r:
                print(f"  {r[0]} {r[1]}  (MGID {sib})")
                print(f"    Year:  {r[3]}")
                print(f"    Title: {r[2]}")
                print(f"    Field: {r[4]}  (MSC={r[5]})")

    # ------------------------------------------------------------------ #
    # 6. Doctoral students (descendants)
    # ------------------------------------------------------------------ #
    students = advisor_to_students.get(mgid, [])
    if students:
        print("\n" + "=" * 60)
        print("DOCTORAL STUDENTS:")
        for stu in students:
            r = conn.execute("""
                SELECT p.FNAME, p.LNAME, d.TITLE, d.DEGYEAR, mi.FIELD, d.DEGMSC
                FROM PERSONS p
                JOIN DEGREES d ON d.DEGMGID = p.MGID
                JOIN MSC_IMPUTE mi ON mi.DEGREE_ID = d.DEGREE_ID
                WHERE p.MGID = ? LIMIT 1
            """, (stu,)).fetchone()
            if r:
                print(f"  {r[0]} {r[1]}  (MGID {stu})")
                print(f"    Year:  {r[3]}")
                print(f"    Title: {r[2]}")
                print(f"    Field: {r[4]}  (MSC={r[5]})")

    conn.close()


# ------------------------------------------------------------------ #
# Equivalent SQL for the key queries (for reference)
# ------------------------------------------------------------------ #
SQL_SUBJECT = """
-- Subject record (Cheng-Der Fuh, MGID 194)
SELECT p.FNAME, p.LNAME, d.TITLE, d.DEGYEAR,
       mi.FIELD, mi.CONFIDENCE, mi.METHODS
FROM PERSONS p
JOIN DEGREES d ON d.DEGMGID = p.MGID
JOIN MSC_IMPUTE mi ON mi.DEGREE_ID = d.DEGREE_ID
WHERE p.MGID = 194 AND mi.METHODS != 'observed';
"""

SQL_ADVISORS = """
-- Advisors of MGID 194
SELECT p.FNAME, p.LNAME, d.TITLE, d.DEGYEAR, mi.FIELD
FROM STUDENTS s
JOIN PERSONS p ON p.MGID = s.ADVISOR
LEFT JOIN DEGREES d ON d.DEGMGID = p.MGID
LEFT JOIN MSC_IMPUTE mi ON mi.DEGREE_ID = d.DEGREE_ID
WHERE s.STUDENT = 194;
"""

SQL_SIBLINGS = """
-- Academic siblings of MGID 194 (co-students of same advisor)
SELECT p.FNAME, p.LNAME, d.TITLE, d.DEGYEAR, mi.FIELD
FROM STUDENTS s1
JOIN STUDENTS s2 ON s1.ADVISOR = s2.ADVISOR AND s2.STUDENT != 194
JOIN PERSONS p ON p.MGID = s2.STUDENT
JOIN DEGREES d ON d.DEGMGID = p.MGID
JOIN MSC_IMPUTE mi ON mi.DEGREE_ID = d.DEGREE_ID
WHERE s1.STUDENT = 194
GROUP BY p.MGID;
"""


if __name__ == "__main__":
    main()
