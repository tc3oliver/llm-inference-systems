"""Regenerate the EXP-002 figures from ../data/exp-002/matched-comparisons.csv.

    uv run --with matplotlib python figures/plot_exp002.py

Both figures carry measured data. Nothing here reads a log, a server or a
network resource; the comparison file is produced by `analysis/exp002.py` from
`data/exp-002/runs.csv`.
"""

import csv
import pathlib

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

ROOT = pathlib.Path(__file__).resolve().parent
DATA = ROOT.parent / "data" / "exp-002"

INK = "#1b1b1b"
DENSE = "#33556b"
SPEC = "#c0563c"
MUTED = "#8a8a8a"
FAINT = "#d9d9d9"

plt.rcParams.update({
    "figure.dpi": 130, "savefig.dpi": 130, "font.size": 10,
    "axes.edgecolor": INK, "axes.labelcolor": INK, "text.color": INK,
    "xtick.color": INK, "ytick.color": INK,
    "axes.spines.top": False, "axes.spines.right": False,
    "axes.grid": True, "grid.color": FAINT, "grid.linewidth": 0.6,
    "figure.facecolor": "white", "axes.facecolor": "white",
})


def rows():
    with open(DATA / "matched-comparisons.csv", newline="") as fh:
        return list(csv.DictReader(fh))


def num(value):
    return float(value) if value not in (None, "") else None


def save(fig, stem):
    for ext in ("svg", "png"):
        fig.savefig(ROOT / f"{stem}.{ext}", bbox_inches="tight")
    plt.close(fig)


def fig10_cost_ratio():
    """Predicted speedup from the cost-ratio model against the measured one."""
    data = rows()
    fig, ax = plt.subplots(figsize=(5.6, 5.0))
    lo, hi = 0.45, 1.95
    ax.plot([lo, hi], [lo, hi], color=MUTED, lw=1.0, ls="--", zorder=1)
    ax.axhline(1.0, color=FAINT, lw=1.0, zorder=0)
    ax.axvline(1.0, color=FAINT, lw=1.0, zorder=0)
    for r in data:
        x, y = num(r["predicted_speedup"]), num(r["decode_speedup"])
        if x is None or y is None:
            continue
        adaptive = r["policy"] == "adaptive"
        ax.scatter(x, y, s=64, zorder=3,
                   facecolor=DENSE if adaptive else "white",
                   edgecolor=DENSE if adaptive else SPEC, linewidths=1.6,
                   marker="o" if adaptive else "s")
        label = f"{r['content']}/{r['context']}"
        if r["model"].startswith("Qwen3.8"):
            label += " (27B)"
        ax.annotate(label, (x, y), textcoords="offset points",
                    xytext=(7, -3), fontsize=7.5, color=MUTED)
    ax.set_xlim(lo, hi)
    ax.set_ylim(lo, hi)
    ax.set_xlabel("predicted decode speedup  (tokens per cycle / cost ratio)")
    ax.set_ylabel("measured matched decode speedup")
    ax.set_title("Cycle cost, not acceptance, predicts whether speculation pays",
                 fontsize=10.5, loc="left")
    handles = [
        plt.Line2D([], [], marker="s", ls="", markerfacecolor="white",
                   markeredgecolor=SPEC, markeredgewidth=1.6, markersize=8,
                   label="fixed depth 3"),
        plt.Line2D([], [], marker="o", ls="", markerfacecolor=DENSE,
                   markeredgecolor=DENSE, markersize=8,
                   label="adaptive controller"),
        plt.Line2D([], [], color=MUTED, ls="--", lw=1.0, label="perfect prediction"),
    ]
    ax.legend(handles=handles, frameon=False, loc="upper left", fontsize=8.5)
    save(fig, "fig10-cost-ratio-model")


def fig11_policy_by_cell():
    """Matched decode speedup against dense, by workload cell and policy."""
    data = rows()
    cells, seen = [], set()
    for r in data:
        key = (r["model"], r["content"], r["context"])
        if key not in seen:
            seen.add(key)
            cells.append(key)
    by_cell = {}
    for r in data:
        by_cell[(r["model"], r["content"], r["context"], r["policy"])] = \
            num(r["decode_speedup"])

    fig, ax = plt.subplots(figsize=(7.4, 4.0))
    width = 0.36
    labels = []
    for i, key in enumerate(cells):
        fixed = by_cell.get(key + ("fixed-3",))
        adapt = by_cell.get(key + ("adaptive",))
        if fixed is not None:
            ax.bar(i - width / 2, fixed, width, color="white",
                   edgecolor=SPEC, linewidth=1.6, zorder=3)
            ax.text(i - width / 2, fixed + 0.02, f"{fixed:.2f}", ha="center",
                    fontsize=7.5, color=SPEC)
        if adapt is not None:
            ax.bar(i + width / 2, adapt, width, color=DENSE, zorder=3)
            ax.text(i + width / 2, adapt + 0.02, f"{adapt:.2f}", ha="center",
                    fontsize=7.5, color=DENSE)
        model = "27B dense" if key[0].startswith("Qwen3.8") else "35B MoE"
        labels.append(f"{key[1]}\n{key[2]}\n{model}")
    ax.axhline(1.0, color=INK, lw=1.0, zorder=4)
    ax.set_xticks(range(len(cells)))
    ax.set_xticklabels(labels, fontsize=8)
    ax.set_ylabel("matched decode speedup against dense")
    ax.set_ylim(0, 2.0)
    ax.set_title("Fixed depth 3 loses in four of five cells; the controller does not",
                 fontsize=10.5, loc="left")
    handles = [
        plt.Line2D([], [], marker="s", ls="", markerfacecolor="white",
                   markeredgecolor=SPEC, markeredgewidth=1.6, markersize=8,
                   label="fixed depth 3"),
        plt.Line2D([], [], marker="s", ls="", markerfacecolor=DENSE,
                   markeredgecolor=DENSE, markersize=8, label="adaptive controller"),
        plt.Line2D([], [], color=INK, lw=1.0, label="dense parity"),
    ]
    ax.legend(handles=handles, frameon=False, loc="upper left", fontsize=8.5)
    save(fig, "fig11-policy-by-cell")


if __name__ == "__main__":
    fig10_cost_ratio()
    fig11_policy_by_cell()
    print("wrote fig10-cost-ratio-model and fig11-policy-by-cell (svg + png)")
