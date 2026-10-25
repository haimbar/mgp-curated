"""
plot_confusion_matrix.py  –  Cross-validation confusion matrix for MSC imputation.

Holds out a random 10% of known-MSC degree records, rebuilds the network signal
from the remaining 90%, runs the imputation, and plots the resulting 10×10
confusion matrix (row = true field, column = predicted field).

Output: msc_confusion_matrix.pdf
Usage:  python3 plot_confusion_matrix.py [--db ~/Data/MG/mg.db] [--seed 42]
"""

import argparse
import random
import sqlite3
import numpy as np
import matplotlib.pyplot as plt
import matplotlib.colors as mcolors
from collections import defaultdict
from pathlib import Path

# Import shared logic from msc_impute.py
from msc_impute import (
    MSC_TO_FIELD, ALL_FIELDS, FIELD_PRIOR,
    year_factor, title_scores, normalize, _zero_scores,
)

FIELDS_DISPLAY = [
    "Pure Math", "CS", "Physics", "Statistics",
    "Economics", "Numerical", "Probability", "OR", "Biology", "Education",
]


def impute_one(mgid, title, year, person_fields, student_to_advisors, advisor_to_students):
    """Run the three-signal imputation for one record; return predicted field."""
    # title signal
    t_raw = title_scores(title or "")
    t_total = sum(t_raw.values())
    title_strength = min(t_total / 8.0, 1.0)

    # network signal
    net_votes = _zero_scores()
    advisor_ids = student_to_advisors.get(mgid, [])
    student_ids = advisor_to_students.get(mgid, [])

    for adv in advisor_ids:
        for f in person_fields.get(adv, []):
            net_votes[f] += 1.0

    for adv in advisor_ids:
        for sib in advisor_to_students.get(adv, []):
            if sib == mgid:
                continue
            for f in person_fields.get(sib, []):
                net_votes[f] += 0.35

    for stu in student_ids:
        for f in person_fields.get(stu, []):
            net_votes[f] += 0.6

    net_total = sum(net_votes.values())
    net_strength = min(net_total / 3.0, 1.0)

    tw, nw = title_strength, net_strength
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
    if not combined:
        combined = dict(FIELD_PRIOR)

    return max(combined, key=combined.get)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--db",   default="~/Data/MG/mg.db")
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()

    random.seed(args.seed)
    conn = sqlite3.connect(Path(args.db).expanduser())

    # Load all known-MSC records
    print("Loading known-MSC records ...", flush=True)
    known = conn.execute(
        "SELECT rowid, DEGMGID, DEGMSC, TITLE, DEGYEAR FROM DEGREES WHERE DEGMSC IS NOT NULL"
    ).fetchall()

    # 90/10 split
    random.shuffle(known)
    split = int(0.9 * len(known))
    train_rows = known[:split]
    test_rows  = known[split:]
    print(f"  Train: {len(train_rows):,}   Test: {len(test_rows):,}", flush=True)

    # Build person_fields from training set only
    person_fields: dict[int, list[str]] = defaultdict(list)
    for _, mgid, msc, _, _ in train_rows:
        person_fields[mgid].append(MSC_TO_FIELD.get(msc, "Pure Math"))

    # Load advisor graph
    print("Loading advisor/student graph ...", flush=True)
    student_to_advisors: dict[int, list[int]] = defaultdict(list)
    advisor_to_students: dict[int, list[int]] = defaultdict(list)
    for advisor, student in conn.execute("SELECT ADVISOR, STUDENT FROM STUDENTS").fetchall():
        student_to_advisors[student].append(advisor)
        advisor_to_students[advisor].append(student)
    conn.close()

    # Run imputation on test set and accumulate confusion matrix
    print("Running cross-validation ...", flush=True)
    field_idx = {f: i for i, f in enumerate(FIELDS_DISPLAY)}
    n = len(FIELDS_DISPLAY)
    cm = np.zeros((n, n), dtype=int)

    for _, mgid, msc, title, year in test_rows:
        true_field = MSC_TO_FIELD.get(msc, "Pure Math")
        pred_field = impute_one(mgid, title, year,
                                person_fields, student_to_advisors, advisor_to_students)
        ti = field_idx.get(true_field)
        pi = field_idx.get(pred_field)
        if ti is not None and pi is not None:
            cm[ti, pi] += 1

    # Overall accuracy
    accuracy = cm.diagonal().sum() / cm.sum()
    print(f"Overall accuracy: {accuracy:.1%}")

    # Normalize rows to percentages for display
    row_sums = cm.sum(axis=1, keepdims=True)
    cm_pct = np.where(row_sums > 0, cm / row_sums * 100, 0.0)

    # Plot
    fig, ax = plt.subplots(figsize=(10, 8))

    # Use a white→blue colormap; cap at 100 so diagonal saturates
    cmap = plt.cm.Blues
    im = ax.imshow(cm_pct, cmap=cmap, vmin=0, vmax=100, aspect="auto")

    # Annotate each cell
    for i in range(n):
        for j in range(n):
            val = cm_pct[i, j]
            color = "white" if val > 55 else "black"
            ax.text(j, i, f"{val:.0f}", ha="center", va="center",
                    fontsize=8, color=color)

    short = [f.replace(" Math", "\nMath").replace(" Analysis", "\nAnal.")
               .replace("Probability", "Prob.").replace("Education", "Educ.")
               .replace("Economics", "Econ.").replace("Numerical", "Numer.")
               .replace("Statistics", "Stat.").replace("Physics", "Phys.")
               for f in FIELDS_DISPLAY]

    ax.set_xticks(range(n))
    ax.set_yticks(range(n))
    ax.set_xticklabels(short, fontsize=9)
    ax.set_yticklabels(short, fontsize=9)
    ax.set_xlabel("Predicted field", fontsize=11)
    ax.set_ylabel("True field", fontsize=11)
    ax.set_title(
        f"MSC imputation: confusion matrix (10% cross-validation holdout)\n"
        f"Overall accuracy = {accuracy:.1%}  |  n = {cm.sum():,} test records",
        fontsize=11,
    )

    cbar = fig.colorbar(im, ax=ax, fraction=0.046, pad=0.04)
    cbar.set_label("% of true-field records", fontsize=9)

    fig.tight_layout()
    fig.savefig("msc_confusion_matrix.pdf", bbox_inches="tight")
    print("Saved msc_confusion_matrix.pdf")

    # Per-field accuracy summary
    print("\nPer-field accuracy (diagonal %):")
    for i, f in enumerate(FIELDS_DISPLAY):
        print(f"  {f:<20} {cm_pct[i, i]:5.1f}%  (n={cm[i].sum():,})")


if __name__ == "__main__":
    main()
