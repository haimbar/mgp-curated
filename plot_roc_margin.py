"""
plot_roc_margin.py  –  ROC curves for four broad fields, using the confidence
                        margin (p1 − p2) as the decision threshold.

For each field F and threshold t:
  - A record is "called F" if predicted top field = F AND margin ≥ t
  - TPR = (true F records called F) / (all true F records)
  - FPR = (non-F records called F) / (all non-F records)

Ground truth comes from observed records (DEGMSC IS NOT NULL).
The imputation model is applied to those records with their MSC hidden.

Also saves:  roc_predictions.csv  with columns
    MGID, TRUE_FIELD, PRED_FIELD, P1, P2, MARGIN

Output: roc_margin.pdf
Usage:  python3 plot_roc_margin.py [--db ~/Data/MG/mg.db]
"""

import argparse
import csv
import sqlite3
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from collections import defaultdict
from pathlib import Path
from sklearn.metrics import roc_curve, auc

from msc_impute import (
    MSC_TO_FIELD, ALL_FIELDS, FIELD_PRIOR,
    year_factor, title_scores, normalize, _zero_scores,
)

FIELDS = ["Pure Math", "Statistics", "Physics", "Biology"]

FIELD_COLORS = {
    "Pure Math": "#4878CF",
    "Statistics": "#B47CC7",
    "Physics":    "#D65F5F",
    "Biology":    "#76B7B2",
}


def compute_probs(mgid, title, year, person_fields, s2a, a2s):
    t_raw   = title_scores(title or "")
    t_total = sum(t_raw.values())
    tw      = min(t_total / 8.0, 1.0)

    net = _zero_scores()
    for adv in s2a.get(mgid, []):
        for f in person_fields.get(adv, []):
            net[f] += 1.0
    for adv in s2a.get(mgid, []):
        for sib in a2s.get(adv, []):
            if sib == mgid:
                continue
            for f in person_fields.get(sib, []):
                net[f] += 0.35
    for stu in a2s.get(mgid, []):
        for f in person_fields.get(stu, []):
            net[f] += 0.6

    net_total = sum(net.values())
    nw        = min(net_total / 3.0, 1.0)
    total_w   = tw + nw

    combined = _zero_scores()
    if total_w > 0:
        for f in ALL_FIELDS:
            t_c = (t_raw[f] / t_total) if t_total > 0 else 0.0
            n_c = (net[f] / net_total) if net_total > 0 else 0.0
            combined[f] = (tw * t_c + nw * n_c) / total_w
    else:
        combined = dict(FIELD_PRIOR)

    for f in ALL_FIELDS:
        combined[f] *= year_factor(f, year)
    return normalize(combined) or dict(FIELD_PRIOR)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default="~/Data/MG/mg.db")
    args = parser.parse_args()

    conn = sqlite3.connect(Path(args.db).expanduser())

    # Build person_fields from ALL observed records (full set used for network)
    person_fields = defaultdict(list)
    all_observed = conn.execute(
        "SELECT DEGMGID, DEGMSC FROM DEGREES WHERE DEGMSC IS NOT NULL"
    ).fetchall()
    for m, msc in all_observed:
        person_fields[m].append(MSC_TO_FIELD.get(msc, "Pure Math"))

    s2a = defaultdict(list)
    a2s = defaultdict(list)
    for adv, stu in conn.execute("SELECT ADVISOR, STUDENT FROM STUDENTS").fetchall():
        s2a[stu].append(adv)
        a2s[adv].append(stu)

    # Test set: all observed records
    test_rows = conn.execute(
        "SELECT DEGMGID, DEGMSC, TITLE, DEGYEAR FROM DEGREES WHERE DEGMSC IS NOT NULL"
    ).fetchall()
    conn.close()

    print(f"Running model on {len(test_rows):,} observed records ...", flush=True)

    records = []  # (mgid, true_field, pred_field, p1, p2, margin)
    for i, (mgid, msc, title, year) in enumerate(test_rows):
        true_field = MSC_TO_FIELD.get(msc, "Pure Math")

        # Hide this record's field from person_fields during prediction
        # (leave-one-out: remove mgid's contribution)
        own_fields = person_fields[mgid]
        del person_fields[mgid]  # hide ground truth

        probs = compute_probs(mgid, title, year, person_fields, s2a, a2s)

        person_fields[mgid] = own_fields  # restore

        ranked = sorted(probs.items(), key=lambda x: -x[1])
        pred_field = ranked[0][0]
        p1         = ranked[0][1]
        p2         = ranked[1][1] if len(ranked) > 1 else 0.0
        margin     = p1 - p2
        records.append((mgid, true_field, pred_field, p1, p2, margin))

        if (i + 1) % 40000 == 0:
            print(f"  {i+1:>7,} / {len(test_rows):,}", flush=True)

    print(f"Done. Saving predictions ...")

    # Save predictions CSV
    with open("roc_predictions.csv", "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["MGID", "TRUE_FIELD", "PRED_FIELD", "P1", "P2", "MARGIN"])
        for row in records:
            mgid, tf, pf, p1, p2, mg = row
            writer.writerow([mgid, tf, pf, f"{p1:.6f}", f"{p2:.6f}", f"{mg:.6f}"])
    print("Saved roc_predictions.csv")

    # ── ROC curves ────────────────────────────────────────────────────────────
    true_fields  = np.array([r[1] for r in records])
    pred_fields  = np.array([r[2] for r in records])
    margins      = np.array([r[5] for r in records])

    fig, axes = plt.subplots(2, 2, figsize=(12, 10))
    axes = axes.flatten()

    for ax, field in zip(axes, FIELDS):
        # Binary labels: 1 if true field = F, 0 otherwise
        y_true = (true_fields == field).astype(int)

        # Score: margin if predicted field = F, else 0
        # (only records predicted as F can be "called" F at any threshold)
        score = np.where(pred_fields == field, margins, -1.0)

        fpr, tpr, thresholds = roc_curve(y_true, score)
        roc_auc = auc(fpr, tpr)

        color = FIELD_COLORS.get(field, "#888888")
        ax.plot(fpr, tpr, color=color, linewidth=2.0,
                label=f"AUC = {roc_auc:.3f}")
        ax.plot([0, 1], [0, 1], "k--", linewidth=0.8, alpha=0.5)

        # Annotate a few threshold values on the curve
        for t_val in [0.1, 0.3, 0.5, 0.7, 0.9]:
            idx = np.searchsorted(thresholds[::-1], t_val)
            idx = len(thresholds) - 1 - idx
            idx = max(0, min(idx, len(fpr) - 1))
            ax.annotate(
                f"{t_val:.1f}",
                (fpr[idx], tpr[idx]),
                fontsize=7, color="#444444",
                xytext=(4, 2), textcoords="offset points",
            )
            ax.plot(fpr[idx], tpr[idx], "o", color=color, markersize=4)

        n_pos = y_true.sum()
        n_neg = len(y_true) - n_pos
        ax.set_title(
            f"{field}  (pos={n_pos:,}  neg={n_neg:,})",
            fontsize=13, fontweight="bold",
        )
        ax.set_xlabel("False Positive Rate", fontsize=11)
        ax.set_ylabel("True Positive Rate", fontsize=11)
        ax.legend(fontsize=11, loc="lower right")
        ax.set_xlim(0, 1)
        ax.set_ylim(0, 1.02)
        ax.tick_params(labelsize=10)
        ax.set_aspect("equal")

    fig.suptitle(
        "ROC curves by margin threshold  ($p_1 - p_2$)\n"
        "Ground truth: observed MSC records  ·  Leave-one-out prediction",
        fontsize=14, y=1.01,
    )
    fig.tight_layout()
    fig.savefig("roc_margin.pdf", bbox_inches="tight")
    print("Saved roc_margin.pdf")


if __name__ == "__main__":
    main()
