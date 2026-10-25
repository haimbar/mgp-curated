"""
plot_by_year.py  –  Plot number of degrees by year (log scale).

Output: countbyyear.pdf
Usage:  python3 plot_by_year.py [--db ~/Data/MG/mg.db]
"""

import argparse
import sqlite3
import matplotlib.pyplot as plt
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default="~/Data/MG/mg.db")
    args = parser.parse_args()

    conn = sqlite3.connect(Path(args.db).expanduser())
    rows = conn.execute("""
        SELECT CAST(DEGYEAR AS INTEGER) AS yr, COUNT(*) AS n
        FROM DEGREES
        WHERE DEGYEAR GLOB '[0-9][0-9][0-9][0-9]'
          AND CAST(DEGYEAR AS INTEGER) BETWEEN 1800 AND 2025
        GROUP BY yr
        ORDER BY yr
    """).fetchall()
    conn.close()

    years  = [r[0] for r in rows]
    counts = [r[1] for r in rows]

    fig, ax = plt.subplots(figsize=(9, 4))
    ax.bar(years, counts, width=0.9, color="steelblue", linewidth=0)
    ax.set_yscale("log")
    ax.set_xlabel("Year")
    ax.set_ylabel("Logarithm (base 10) of the number of degrees")
    ax.set_title("Math Genealogy Project: degrees by year")
    ax.set_xlim(1798, 2026)
    ax.yaxis.set_major_formatter(plt.FuncFormatter(lambda x, _: f"{int(x):,}"))
    ax.grid(axis="y", linestyle="--", linewidth=0.5, alpha=0.7)
    fig.tight_layout()
    fig.savefig("countbyyear.pdf", bbox_inches="tight")
    print("Saved countbyyear.pdf")


if __name__ == "__main__":
    main()
