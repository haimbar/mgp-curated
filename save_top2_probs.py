"""
save_top2_probs.py  –  Compute and cache the top-2 predicted probabilities
                        for all imputed (missing-MSC) degree records.

Output: top2_probs.csv  with columns:
    MGID      – Math Genealogy person ID
    FIELD1    – broad field with highest predicted probability
    P1        – highest predicted probability
    FIELD2    – broad field with second-highest predicted probability
    P2        – second-highest predicted probability

Usage:  python3 save_top2_probs.py [--db ~/Data/MG/mg.db]
"""

import argparse
import csv
import sqlite3
from collections import defaultdict
from pathlib import Path

from msc_impute import (
    MSC_TO_FIELD, ALL_FIELDS, FIELD_PRIOR,
    year_factor, title_scores, normalize, _zero_scores,
)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--db",  default="~/Data/MG/mg.db")
    parser.add_argument("--out", default="top2_probs.csv")
    args = parser.parse_args()

    conn = sqlite3.connect(Path(args.db).expanduser())

    # Build person_fields from all known-MSC records
    person_fields = defaultdict(list)
    for m, msc in conn.execute(
        "SELECT DEGMGID, DEGMSC FROM DEGREES WHERE DEGMSC IS NOT NULL"
    ).fetchall():
        person_fields[m].append(MSC_TO_FIELD.get(msc, "Pure Math"))

    # Advisor graph
    s2a = defaultdict(list)
    a2s = defaultdict(list)
    for adv, stu in conn.execute("SELECT ADVISOR, STUDENT FROM STUDENTS").fetchall():
        s2a[stu].append(adv)
        a2s[adv].append(stu)

    # All imputed records
    rows = conn.execute(
        "SELECT DEGMGID, TITLE, DEGYEAR FROM DEGREES WHERE DEGMSC IS NULL"
    ).fetchall()
    conn.close()

    print(f"Computing top-2 probabilities for {len(rows):,} records ...", flush=True)

    out_path = Path(args.out)
    with out_path.open("w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["MGID", "FIELD1", "P1", "FIELD2", "P2"])

        for i, (mgid, title, year) in enumerate(rows):
            # Title signal
            t_raw   = title_scores(title or "")
            t_total = sum(t_raw.values())
            tw      = min(t_total / 8.0, 1.0)

            # Network signal
            net = _zero_scores()
            for adv in s2a.get(mgid, []):
                for fld in person_fields.get(adv, []):
                    net[fld] += 1.0
            for adv in s2a.get(mgid, []):
                for sib in a2s.get(adv, []):
                    if sib == mgid:
                        continue
                    for fld in person_fields.get(sib, []):
                        net[fld] += 0.35
            for stu in a2s.get(mgid, []):
                for fld in person_fields.get(stu, []):
                    net[fld] += 0.6

            net_total = sum(net.values())
            nw        = min(net_total / 3.0, 1.0)
            total_w   = tw + nw

            combined = _zero_scores()
            if total_w > 0:
                for fld in ALL_FIELDS:
                    t_c = (t_raw[fld] / t_total) if t_total > 0 else 0.0
                    n_c = (net[fld] / net_total) if net_total > 0 else 0.0
                    combined[fld] = (tw * t_c + nw * n_c) / total_w
            else:
                combined = dict(FIELD_PRIOR)

            for fld in ALL_FIELDS:
                combined[fld] *= year_factor(fld, year)
            combined = normalize(combined) or dict(FIELD_PRIOR)

            # Sort fields by probability descending
            ranked = sorted(combined.items(), key=lambda x: -x[1])
            field1, p1 = ranked[0]
            field2, p2 = ranked[1] if len(ranked) > 1 else ("", 0.0)

            writer.writerow([mgid, field1, f"{p1:.6f}", field2, f"{p2:.6f}"])

            if (i + 1) % 20000 == 0:
                print(f"  {i+1:>7,} / {len(rows):,}", flush=True)

    print(f"Saved {out_path}  ({len(rows):,} rows)")


if __name__ == "__main__":
    main()
