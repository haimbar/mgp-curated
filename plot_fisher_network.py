"""
plot_fisher_network.py  –  Academic genealogy network for Ronald A. Fisher.

Layout:
  • Ancestors (Gen −1 … −3) fan upward in a symmetric arc above Fisher.
  • Gen +1 & +2 individuals fan downward in nested half-circles.
  • Gen +3 : one pie-chart node per Gen+2 parent; area ∝ student count,
             slices = field distribution of ALL that parent's students.
  • Gen +2 shows only those who became advisors; Gen+1 nodes carry a
    "+N" badge for hidden terminal Gen+2 children.

Output: fisher_network.pdf, fisher_network.svg
Usage:  python3 plot_fisher_network.py [--db ~/Data/MG/mg.db]
"""

import argparse
import math
import sqlite3
from collections import defaultdict, Counter
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
import matplotlib.patheffects as pe
from matplotlib.patches import Wedge, Circle, FancyArrowPatch
import numpy as np

# ------------------------------------------------------------------ #
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
    "Unknown":     "#DDDDDD",
}
ALL_FIELDS = list(FIELD_COLORS.keys())

ROOT_MGID      = 46924   # Ronald A. Fisher
GENS_UP        = 3
MAX_G2_PER_G1  = 10      # cap on shown Gen+2 nodes per Gen+1 parent

# Radial layout parameters
R_UP   = 2.8    # radius step per ancestor generation
R1     = 3.2    # Gen+1 radius
R2     = 6.6    # Gen+2 radius
R3_OFF = 1.1    # extra radial offset beyond R2 for pie centres

# Descendant fan: 175° total, centred at 270° (straight down)
FAN_LO = math.radians(183)
FAN_HI = math.radians(357)

NODE_R     = 0.30   # individual node radius in data units
PIE_MAX_R  = 0.70
PIE_MIN_R  = 0.20


# ------------------------------------------------------------------ #
# DB helpers
# ------------------------------------------------------------------ #
def person_info(conn, mgid):
    row = conn.execute("""
        SELECT p.FNAME, p.LNAME, mi.FIELD
        FROM PERSONS p
        LEFT JOIN DEGREES d    ON d.DEGMGID    = p.MGID
        LEFT JOIN MSC_IMPUTE mi ON mi.DEGREE_ID = d.DEGREE_ID
        WHERE p.MGID = ? LIMIT 1
    """, (mgid,)).fetchone()
    if row is None:
        return "", "", "Unknown"
    f, l, field = row
    return (f or ""), (l or ""), (field or "Unknown")


def get_advisors(conn, mgid):
    return [r[0] for r in conn.execute(
        "SELECT ADVISOR FROM STUDENTS WHERE STUDENT=? AND ADVISOR > 0",
        (mgid,)).fetchall()]


def get_students_ranked(conn, mgid):
    return [r[0] for r in conn.execute("""
        SELECT s.STUDENT,
               (SELECT COUNT(*) FROM STUDENTS s2 WHERE s2.ADVISOR=s.STUDENT) nstu
        FROM STUDENTS s WHERE s.ADVISOR=? ORDER BY nstu DESC
    """, (mgid,)).fetchall()]


def student_count(conn, mgid):
    return conn.execute(
        "SELECT COUNT(*) FROM STUDENTS WHERE ADVISOR=?", (mgid,)).fetchone()[0]


def field_of(conn, mgid):
    row = conn.execute("""
        SELECT mi.FIELD FROM DEGREES d
        JOIN MSC_IMPUTE mi ON mi.DEGREE_ID = d.DEGREE_ID
        WHERE d.DEGMGID=? LIMIT 1
    """, (mgid,)).fetchone()
    return row[0] if row and row[0] else "Unknown"


# ------------------------------------------------------------------ #
# Build graph data
# ------------------------------------------------------------------ #
def build_graph(conn):
    nodes     = {}      # mgid -> {'gen','fname','lname','field'}
    edges     = []      # (src, dst) pairs
    node_gen  = {}
    gen3_pies = {}      # gen2_mgid -> {'total', 'fields': Counter}
    hidden_g1 = {}      # gen1_mgid -> n hidden

    def add(mgid, gen):
        if mgid in node_gen:
            return
        node_gen[mgid] = gen
        fn, ln, f = person_info(conn, mgid)
        nodes[mgid] = dict(gen=gen, fname=fn, lname=ln, field=f)

    add(ROOT_MGID, 0)

    # Ancestors
    frontier = {ROOT_MGID}
    for gen in range(1, GENS_UP + 1):
        nxt = set()
        for m in frontier:
            for adv in get_advisors(conn, m):
                add(adv, -gen)
                edges.append((adv, m))
                nxt.add(adv)
        frontier = nxt

    # Gen +1
    gen1 = get_students_ranked(conn, ROOT_MGID)
    for s in gen1:
        add(s, 1)
        edges.append((ROOT_MGID, s))

    # Gen +2 (only those who became advisors, capped)
    gen2_nodes = set()
    for g1 in gen1:
        all_g2  = get_students_ranked(conn, g1)
        with_stu = [s for s in all_g2 if student_count(conn, s) > 0]
        shown    = with_stu[:MAX_G2_PER_G1]
        hidden   = len(all_g2) - len(shown)
        if hidden:
            hidden_g1[g1] = hidden
        for s in shown:
            add(s, 2)
            edges.append((g1, s))
            gen2_nodes.add(s)

    # Gen +3 pies
    for g2 in gen2_nodes:
        all_g3 = get_students_ranked(conn, g2)
        if all_g3:
            fc = Counter(field_of(conn, s) for s in all_g3)
            gen3_pies[g2] = {'total': len(all_g3), 'fields': fc}

    return nodes, edges, node_gen, gen2_nodes, gen3_pies, hidden_g1


# ------------------------------------------------------------------ #
# Radial layout
# ------------------------------------------------------------------ #
def compute_positions(nodes, edges, node_gen, gen3_pies):
    pos      = {}
    gen3_pos = {}

    pos[ROOT_MGID] = (0.0, 0.0)

    # ── Ancestor layout (upper half, symmetric) ─────────────────────
    anc_layers = defaultdict(list)
    for m, g in node_gen.items():
        if g < 0:
            anc_layers[g].append(m)

    # Determine parent positions to centre each ancestor layer
    parent_of = {}
    for src, dst in edges:
        if node_gen[src] < 0 and node_gen[dst] <= 0:
            parent_of[src] = dst   # ancestor src advised node dst

    for gen in range(-1, -GENS_UP - 1, -1):
        layer = sorted(anc_layers.get(gen, []))
        n     = len(layer)
        if n == 0:
            continue
        r    = -gen * R_UP
        base = math.pi / 2   # always centred directly above Fisher
        half = math.radians(max(18, 22 * n))
        for i, m in enumerate(layer):
            angle = base if n == 1 else base - half + 2 * half * i / (n - 1)
            pos[m] = (r * math.cos(angle), r * math.sin(angle))

    # ── Descendant layout (lower half, nested arcs) ─────────────────
    children_of = defaultdict(list)   # parent -> [child] for downward edges
    for src, dst in edges:
        if node_gen[src] >= 0:
            children_of[src].append(dst)

    fan_range = FAN_HI - FAN_LO

    gen1_nodes = children_of[ROOT_MGID]

    # Allocate angular sectors for Gen+1 proportional to Gen+2 child count
    weights = [max(1, len(children_of.get(g, []))) for g in gen1_nodes]
    total_w = sum(weights)

    max_students = max((v['total'] for v in gen3_pies.values()), default=1)

    def pr(n):
        return PIE_MIN_R + (PIE_MAX_R - PIE_MIN_R) * math.sqrt(n / max_students)

    cum = 0.0
    for g1, w in zip(gen1_nodes, weights):
        lo = FAN_LO + fan_range * cum / total_w
        hi = FAN_LO + fan_range * (cum + w) / total_w
        th1 = (lo + hi) / 2
        pos[g1] = (R1 * math.cos(th1), R1 * math.sin(th1))

        g2_children = children_of.get(g1, [])
        n_g2 = len(g2_children)
        if n_g2:
            margin   = max(0.05, (hi - lo) * 0.1)
            sub_lo   = lo + margin
            sub_hi   = hi - margin
            if sub_hi <= sub_lo:
                sub_lo, sub_hi = th1 - 0.05, th1 + 0.05

            for j, g2 in enumerate(g2_children):
                th2 = sub_lo if n_g2 == 1 else sub_lo + (sub_hi - sub_lo) * j / (n_g2 - 1)
                pos[g2] = (R2 * math.cos(th2), R2 * math.sin(th2))

                if g2 in gen3_pies:
                    r_pie = pr(gen3_pies[g2]['total'])
                    r3    = R2 + R3_OFF + r_pie
                    gen3_pos[g2] = (r3 * math.cos(th2), r3 * math.sin(th2))

        cum += w

    return pos, gen3_pos, max_students


# ------------------------------------------------------------------ #
# Draw wedge pie at a given data-space centre
# ------------------------------------------------------------------ #
def draw_pie(ax, cx, cy, radius, counts):
    total = sum(counts.values())
    if total == 0:
        return
    start = 90.0
    for field in ALL_FIELDS:
        n = counts.get(field, 0)
        if n == 0:
            continue
        angle = 360.0 * n / total
        ax.add_patch(Wedge(
            (cx, cy), radius,
            start - angle, start,
            facecolor=FIELD_COLORS.get(field, "#DDDDDD"),
            edgecolor="white", linewidth=0.5, zorder=3,
        ))
        start -= angle
    ax.add_patch(Circle(
        (cx, cy), radius,
        fill=False, edgecolor="#444444", linewidth=0.7, zorder=4,
    ))


# ------------------------------------------------------------------ #
# Abbreviate name for label
# ------------------------------------------------------------------ #
def short_name(fname, lname, is_root=False):
    if is_root:
        return "R. A.\nFisher"
    parts = fname.split() if fname else []
    initials = " ".join(p[0] + "." for p in parts if p)
    return f"{initials} {lname}".strip() if initials else lname


# ------------------------------------------------------------------ #
# Main
# ------------------------------------------------------------------ #
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default="~/Data/MG/mg.db")
    args = parser.parse_args()

    conn = sqlite3.connect(Path(args.db).expanduser())
    print("Building genealogy graph ...", flush=True)

    nodes, edges, node_gen, gen2_nodes, gen3_pies, hidden_g1 = build_graph(conn)
    conn.close()

    print(f"  Nodes: {len(nodes)}  |  Gen+3 pies: {len(gen3_pies)}")

    pos, gen3_pos, max_students = compute_positions(nodes, edges, node_gen, gen3_pies)

    def pr(n):
        return PIE_MIN_R + (PIE_MAX_R - PIE_MIN_R) * math.sqrt(n / max_students)

    # ── figure ─────────────────────────────────────────────────────
    all_pts = list(pos.values()) + list(gen3_pos.values())
    xs = [p[0] for p in all_pts]
    ys = [p[1] for p in all_pts]
    # add pie radii to extent
    for g2, (px, py) in gen3_pos.items():
        r = pr(gen3_pies[g2]['total'])
        xs += [px - r, px + r]
        ys += [py - r, py + r]

    x_margin, y_margin = 1.5, 1.5
    # extra room on the right so the generation labels clear the nodes
    x_lo, x_hi = min(xs) - x_margin, max(xs) + x_margin + 2.0
    y_lo, y_hi = min(ys) - y_margin, max(ys) + y_margin

    data_w = x_hi - x_lo
    data_h = y_hi - y_lo

    # Target: taller than wide; scale so data fits nicely
    scale  = 1.12
    fig_w  = data_w * scale
    fig_h  = data_h * scale
    fig, ax = plt.subplots(figsize=(fig_w, fig_h))
    ax.set_aspect("equal")
    ax.set_xlim(x_lo, x_hi)
    ax.set_ylim(y_lo, y_hi)
    ax.axis("off")

    # ── edges ──────────────────────────────────────────────────────
    for src, dst in edges:
        if src not in pos or dst not in pos:
            continue
        x0, y0 = pos[src]
        x1, y1 = pos[dst]
        ax.annotate(
            "", xy=(x1, y1), xytext=(x0, y0),
            arrowprops=dict(arrowstyle="-|>", color="#aaaaaa",
                            lw=1.0, shrinkA=NODE_R*72, shrinkB=NODE_R*72),
            zorder=1,
        )

    # edges gen+2 → pie
    for g2, (px, py) in gen3_pos.items():
        if g2 not in pos:
            continue
        x0, y0 = pos[g2]
        r = pr(gen3_pies[g2]['total'])
        # edge to edge of pie
        dx, dy = px - x0, py - y0
        dist = math.hypot(dx, dy) or 1
        ex = px - dx / dist * r
        ey = py - dy / dist * r
        ax.annotate(
            "", xy=(ex, ey), xytext=(x0, y0),
            arrowprops=dict(arrowstyle="-|>", color="#aaaaaa",
                            lw=1.0, shrinkA=NODE_R*72, shrinkB=0),
            zorder=1,
        )

    # ── individual nodes ───────────────────────────────────────────
    for mgid, info in nodes.items():
        cx, cy = pos[mgid]
        color = FIELD_COLORS.get(info['field'], FIELD_COLORS["Unknown"])
        is_root = (mgid == ROOT_MGID)
        r  = NODE_R * (1.7 if is_root else 1.0)
        lw = 2.0 if is_root else 0.8
        ec = "#111111" if is_root else "white"
        ax.add_patch(Circle((cx, cy), r,
                             facecolor=color, edgecolor=ec,
                             linewidth=lw, zorder=2))

        lbl = short_name(info['fname'], info['lname'], is_root)
        if mgid in hidden_g1:
            lbl += f"\n(+{hidden_g1[mgid]})"

        fs = 20 if is_root else 17
        fw = "bold" if is_root else "normal"
        # position label radially outward from centre
        cx0, cy0 = pos.get(ROOT_MGID, (0, 0))
        dx, dy = cx - cx0, cy - cy0
        dist = math.hypot(dx, dy) or 1
        lx = cx + (r + 0.15) * dx / dist
        ly = cy + (r + 0.15) * dy / dist
        ha = "left" if dx > 0.1 else ("right" if dx < -0.1 else "center")
        va = "bottom" if dy > 0.05 else ("top" if dy < -0.05 else "center")
        ax.text(lx, ly, lbl, ha=ha, va=va,
                fontsize=fs, fontweight=fw, zorder=6,
                path_effects=[pe.withStroke(linewidth=3, foreground="white")])

    # ── gen+3 pies ─────────────────────────────────────────────────
    for g2, (px, py) in gen3_pos.items():
        r = pr(gen3_pies[g2]['total'])
        draw_pie(ax, px, py, r, gen3_pies[g2]['fields'])
        ax.text(px, py, f"n={gen3_pies[g2]['total']}",
                ha="center", va="center",
                fontsize=13, color="white",
                fontweight="bold", zorder=5)

    # ── generation arc labels ──────────────────────────────────────
    GEN_LABELS = {
        -3: "Gen −3\ngreat-grand-\nadvisors",
        -2: "Gen −2\ngrand-\nadvisors",
        -1: "Gen −1\nadvisors",
         0: "Fisher\n[root]",
         1: "Gen +1\nstudents",
         2: "Gen +2\ngrand-students\n(w/ own students)",
         3: "Gen +3\n(pie = field mix\nof all students)",
    }
    # Place labels to the right of the plot
    label_x = x_hi - 0.3
    # Label heights are chosen for readability, not to match the node rows
    # exactly: Gen -3 sits where Gen -2 used to be, Gen -2 just below it, Gen -1
    # level with the Gen -1 nodes, and the descendant labels are spaced evenly down the right margin.
    gen_ys = {
        -3:  2 * R_UP,
        -2:  2 * R_UP - 1.3,
        -1:  np.mean([pos[m][1] for m, g in node_gen.items() if g == -1]),
         0:  0,
         1: -R1 / 2,
         2: -R1,
         3: -R2,
    }
    for gen, label in GEN_LABELS.items():
        y = gen_ys.get(gen, 0)
        if y_lo <= y <= y_hi:
            ax.text(label_x, y, label,
                    ha="right", va="center",
                    fontsize=17, color="#333333",
                    bbox=dict(boxstyle="round,pad=0.45", fc="white",
                              ec="#bbbbbb", alpha=0.9))

    # ── legend ─────────────────────────────────────────────────────
    present = sorted(
        {info['field'] for info in nodes.values()}
        | {f for d in gen3_pies.values() for f in d['fields']}
    )
    handles = [mpatches.Patch(color=FIELD_COLORS.get(f, "#DDDDDD"), label=f)
               for f in present]
    ax.legend(handles=handles, fontsize=18, loc="upper left",
              framealpha=0.92, title="Broad field",
              title_fontsize=18,
              handlelength=1.8, handleheight=1.5)

    # ── title ──────────────────────────────────────────────────────
    ax.set_title(
        "Academic genealogy of Ronald A. Fisher  (MGP ID 46924)\n"
        "3 advisor generations (above)  ·  Gen+1 & Gen+2 individual nodes\n"
        "Gen+3 as field-distribution pie charts (area ∝ student count)",
        fontsize=20, fontweight="bold", pad=10,
    )

    fig.tight_layout()
    for out in ("fisher_network.svg", "fisher_network.pdf"):
        fig.savefig(out, bbox_inches="tight")
        print(f"Saved {out}")


if __name__ == "__main__":
    main()
