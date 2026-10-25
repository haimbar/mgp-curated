"""
plot_margin_grid.py  –  2×2 grid of p1 vs margin (p1−p2) hexbin plots
                         for four broad fields, read from top2_probs.csv.

Also saves per-field CSV files:  margin_<field>.csv  with columns
    MGID, P1, P2, MARGIN

Output: margin_grid.pdf
Usage:  python3 plot_margin_grid.py [--csv top2_probs.csv]
"""

import argparse
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.colors as mcolors
import pandas as pd
from pathlib import Path

FIELDS = ["Pure Math", "Statistics", "Physics", "Biology"]

FIELD_CMAPS = {
    "Pure Math": "Blues",
    "Statistics": "Purples",
    "Physics":    "Reds",
    "Biology":    "Greens",
}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--csv", default="top2_probs.csv")
    args = parser.parse_args()

    df = pd.read_csv(args.csv)
    df["MARGIN"] = df["P1"] - df["P2"]

    fig, axes = plt.subplots(2, 2, figsize=(14, 11))
    axes = axes.flatten()

    for ax, field in zip(axes, FIELDS):
        sub = df[df["FIELD1"] == field][["MGID", "P1", "P2", "MARGIN"]].copy()
        n   = len(sub)

        # Save per-field CSV
        slug = field.lower().replace(" ", "_")
        csv_out = f"margin_{slug}.csv"
        sub.to_csv(csv_out, index=False)
        print(f"Saved {csv_out}  ({n:,} rows)")

        p1     = sub["P1"].values
        margin = sub["MARGIN"].values
        cmap   = FIELD_CMAPS.get(field, "Blues")

        hb = ax.hexbin(
            p1, margin,
            gridsize=50,
            cmap=cmap,
            mincnt=1,
            norm=mcolors.LogNorm(),
            linewidths=0.2,
        )
        cb = fig.colorbar(hb, ax=ax, pad=0.02)
        cb.ax.tick_params(labelsize=8)
        cb.set_label("Records per bin (log scale)", fontsize=9)

        # Ceiling: margin = p1  (p2 = 0)
        p1_c = np.linspace(0.0, 1.0, 300)
        ax.plot(p1_c, p1_c, "r--", linewidth=1.2, alpha=0.75,
                label=r"$p_2=0$ ceiling")

        # Tie line: margin = 0
        ax.axhline(0, color="black", linestyle="--", linewidth=0.7, alpha=0.45,
                   label="Tie ($p_1=p_2$)")

        # Reference dotted lines
        ax.axvline(0.5, color="#777777", linestyle=":", linewidth=0.9, alpha=0.6)
        ax.axhline(0.3, color="#777777", linestyle=":", linewidth=0.9, alpha=0.6)

        ax.set_xlim(0, 1.02)
        ax.set_ylim(-0.02, 1.02)
        ax.set_xlabel(r"$p_1$", fontsize=12)
        ax.set_ylabel(r"$p_1 - p_2$", fontsize=12)
        ax.set_title(f"{field}  (n = {n:,})", fontsize=13, fontweight="bold")
        ax.tick_params(labelsize=10)

        conf_frac = (margin > 0.30).mean()
        tie_frac  = (margin < 0.05).mean()
        ax.text(0.03, 0.97,
                f"margin>0.30: {conf_frac:.1%}\nmargin<0.05: {tie_frac:.1%}",
                transform=ax.transAxes, fontsize=9, va="top",
                bbox=dict(boxstyle="round,pad=0.3", fc="white", alpha=0.7))

        ax.legend(fontsize=9, loc="lower right", framealpha=0.85)

    fig.suptitle(
        r"Confidence margin  $p_1 - p_2$  vs highest probability  $p_1$"
        "\nImputed MSC records  ·  LogNorm density",
        fontsize=14, y=1.01,
    )
    fig.tight_layout()
    fig.savefig("margin_grid.pdf", bbox_inches="tight")
    print("Saved margin_grid.pdf")


if __name__ == "__main__":
    main()
