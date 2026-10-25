"""
plot_msc_imputation.py  –  Compare field distribution before and after imputation.

Produces two side-by-side 100% stacked bar charts (by decade):
  Left  : known MSC only  (METHODS = 'observed')
  Right : known + imputed (all rows in MSC_IMPUTE)

Output: msc_imputation_comparison.pdf
Usage:  python3 plot_msc_imputation.py [--db ~/Data/MG/mg.db]
"""

import argparse
import sqlite3
import numpy as np
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
from pathlib import Path

FIELDS_ORDER = [
    "Pure Math", "CS", "Physics", "Statistics",
    "Economics", "Numerical", "Probability", "OR", "Biology", "Education",
]

FIELD_COLORS = {
    "Pure Math":   "#4878CF",
    "CS":          "#6ACC65",
    "Physics":     "#D65F5F",
    "Statistics":  "#B47CC7",
    "Economics":   "#C4AD66",
    "Numerical":   "#77BEDB",
    "Probability": "#F28E2B",
    "OR":          "#E15759",
    "Biology":     "#76B7B2",
    "Education":   "#A0A0A0",
}

DECADE_LABELS = [
    "pre-1950", "1950s", "1960s", "1970s", "1980s", "1990s", "2000s", "2010s", "2020s"
]


def load_counts(conn, observed_only: bool) -> dict[str, dict[str, int]]:
    """Return {decade_label: {field: count}}."""
    methods_filter = "AND mi.METHODS = 'observed'" if observed_only else ""
    query = f"""
        SELECT
            CASE
                WHEN CAST(d.DEGYEAR AS INTEGER) < 1950  THEN 'pre-1950'
                WHEN CAST(d.DEGYEAR AS INTEGER) < 1960  THEN '1950s'
                WHEN CAST(d.DEGYEAR AS INTEGER) < 1970  THEN '1960s'
                WHEN CAST(d.DEGYEAR AS INTEGER) < 1980  THEN '1970s'
                WHEN CAST(d.DEGYEAR AS INTEGER) < 1990  THEN '1980s'
                WHEN CAST(d.DEGYEAR AS INTEGER) < 2000  THEN '1990s'
                WHEN CAST(d.DEGYEAR AS INTEGER) < 2010  THEN '2000s'
                WHEN CAST(d.DEGYEAR AS INTEGER) < 2020  THEN '2010s'
                ELSE '2020s'
            END AS decade,
            mi.FIELD,
            COUNT(*) AS n
        FROM MSC_IMPUTE mi
        JOIN DEGREES d ON d.DEGREE_ID = mi.DEGREE_ID
        WHERE d.DEGYEAR GLOB '[0-9][0-9][0-9][0-9]'
          AND CAST(d.DEGYEAR AS INTEGER) BETWEEN 1800 AND 2025
          {methods_filter}
        GROUP BY decade, mi.FIELD
    """
    result: dict[str, dict[str, int]] = {dl: {f: 0 for f in FIELDS_ORDER} for dl in DECADE_LABELS}
    for decade, field, n in conn.execute(query).fetchall():
        if decade in result and field in result[decade]:
            result[decade][field] = n
    return result


def counts_to_shares(counts: dict[str, dict[str, int]]) -> dict[str, dict[str, float]]:
    shares = {}
    for decade, field_counts in counts.items():
        total = sum(field_counts.values())
        shares[decade] = {f: (field_counts[f] / total if total else 0.0) for f in FIELDS_ORDER}
    return shares


def plot_panel(ax, shares: dict[str, dict[str, float]], title: str):
    decades = DECADE_LABELS
    x = np.arange(len(decades))
    bottoms = np.zeros(len(decades))

    for field in FIELDS_ORDER:
        vals = np.array([shares[d][field] for d in decades])
        ax.bar(x, vals, bottom=bottoms, color=FIELD_COLORS[field],
               label=field, width=0.7, edgecolor="white", linewidth=0.4)
        bottoms += vals

    ax.set_xticks(x)
    ax.set_xticklabels(decades, rotation=35, ha="right", fontsize=9)
    ax.set_ylim(0, 1)
    ax.set_ylabel("Share of degrees")
    ax.set_title(title, fontsize=11, fontweight="bold")
    ax.yaxis.set_major_formatter(plt.FuncFormatter(lambda y, _: f"{y:.0%}"))
    ax.grid(axis="y", linestyle="--", linewidth=0.4, alpha=0.6)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default="~/Data/MG/mg.db")
    args = parser.parse_args()

    conn = sqlite3.connect(Path(args.db).expanduser())

    known_counts   = load_counts(conn, observed_only=True)
    full_counts    = load_counts(conn, observed_only=False)
    conn.close()

    known_shares = counts_to_shares(known_counts)
    full_shares  = counts_to_shares(full_counts)

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(13, 5), sharey=True)
    plot_panel(ax1, known_shares, "Known MSC only (n = 201,240)")
    plot_panel(ax2, full_shares,  "Known + imputed (n = 339,179)")

    # Shared legend below both panels
    handles = [mpatches.Patch(color=FIELD_COLORS[f], label=f) for f in FIELDS_ORDER]
    fig.legend(handles=handles, loc="lower center", ncol=5,
               frameon=False, fontsize=9, bbox_to_anchor=(0.5, -0.08))

    fig.suptitle("Broad-field distribution by decade: before and after MSC imputation",
                 fontsize=12, y=1.01)
    fig.tight_layout()
    fig.savefig("msc_imputation_comparison.pdf", bbox_inches="tight")
    print("Saved msc_imputation_comparison.pdf")


if __name__ == "__main__":
    main()
