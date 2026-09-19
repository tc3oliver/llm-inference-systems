#!/usr/bin/env python3
"""Regenerate every figure in EXP-001 from the CSVs in ../data/exp-001.

    uv run --with matplotlib python figures/plot.py

Nothing here reads a log, a server or a network resource. Figures 2, 6, 8 and
9 are diagrams and carry no measured data; they are labelled as such in the
caption file and in the figure itself.
"""

import csv
import pathlib

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import FancyArrowPatch, Rectangle  # noqa: F401

ROOT = pathlib.Path(__file__).resolve().parent
DATA = ROOT.parent / "data" / "exp-001"

INK = "#1b1b1b"
DENSE = "#33556b"
SPARSE = "#c0563c"
MUTED = "#8a8a8a"
FAINT = "#d9d9d9"

plt.rcParams.update({
    "figure.dpi": 130,
    "savefig.dpi": 130,
    "font.size": 10,
    "axes.edgecolor": INK,
    "axes.labelcolor": INK,
    "text.color": INK,
    "xtick.color": INK,
    "ytick.color": INK,
    "axes.spines.top": False,
    "axes.spines.right": False,
    "axes.grid": True,
    "grid.color": FAINT,
    "grid.linewidth": 0.6,
    "figure.facecolor": "white",
    "axes.facecolor": "white",
})


def rows(name):
    with open(DATA / name, newline="") as fh:
        return list(csv.DictReader(fh))


def num(value):
    return float(value) if value not in ("", None) else None


def save(fig, stem):
    for ext in ("svg", "png"):
        fig.savefig(ROOT / f"{stem}.{ext}", bbox_inches="tight")
    plt.close(fig)
    print(f"wrote {stem}.svg / {stem}.png")


def fig1_cold_prefill():
    data = rows("cold-prefill.csv")
    labels = ["14.3K", "16K", "32K"]
    ttft_dense = [num(r["dense_ttft_s"]) for r in data]
    ttft_fast = [num(r["accelerated_ttft_s"]) for r in data]
    pp = [(num(r["dense_pp_tok_s"]), num(r["accelerated_pp_tok_s"])) for r in data]

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(9.4, 3.9))
    x = range(len(labels))
    w = 0.36
    ax1.bar([i - w / 2 for i in x], ttft_dense, w, label="dense", color=DENSE)
    ax1.bar([i + w / 2 for i in x], ttft_fast, w, label="accelerated", color=SPARSE)
    for i, (d, f) in enumerate(zip(ttft_dense, ttft_fast)):
        ax1.text(i - w / 2, d + 2, f"{d:g}", ha="center", fontsize=8)
        ax1.text(i + w / 2, f + 2, f"{f:g}", ha="center", fontsize=8)
        ax1.text(i, max(d, f) + 12, f"{d / f:.1f}x", ha="center", fontsize=9, color=MUTED)
    ax1.set_xticks(list(x))
    ax1.set_xticklabels(labels)
    ax1.set_ylabel("time to first token (s)")
    ax1.set_xlabel("prompt size")
    ax1.set_ylim(0, 150)
    ax1.legend(frameon=False)
    ax1.set_title("Cold prefill latency", loc="left")

    have = [(labels[i], a, b) for i, (a, b) in enumerate(pp) if a and b]
    x2 = range(len(have))
    ax2.bar([i - w / 2 for i in x2], [h[1] for h in have], w, label="dense", color=DENSE)
    ax2.bar([i + w / 2 for i in x2], [h[2] for h in have], w, label="accelerated", color=SPARSE)
    for i, h in enumerate(have):
        ax2.text(i - w / 2, h[1] + 20, f"{h[1]:g}", ha="center", fontsize=8)
        ax2.text(i + w / 2, h[2] + 20, f"{h[2]:g}", ha="center", fontsize=8)
    ax2.set_xticks(list(x2))
    ax2.set_xticklabels([h[0] for h in have])
    ax2.set_ylabel("prefill throughput (tok/s)")
    ax2.set_xlabel("prompt size")
    ax2.set_ylim(0, 1300)
    ax2.legend(frameon=False)
    ax2.set_title("Cold prefill throughput", loc="left")
    save(fig, "fig1-cold-prefill")


def fig2_two_axes():
    fig, ax = plt.subplots(figsize=(6.4, 4.6))
    ax.set_xlim(0, 10)
    ax.set_ylim(0, 10)
    ax.set_xticks([])
    ax.set_yticks([])
    ax.grid(False)
    for side in ("left", "bottom"):
        ax.spines[side].set_visible(True)
    ax.set_xlabel("cost paid by this request  →", labelpad=8)
    ax.set_ylabel("reusable state left for later requests  →", labelpad=8)
    ax.axhline(5, color=FAINT, lw=1)
    ax.axvline(5, color=FAINT, lw=1)

    def quad(cx, cy, title, body, colour):
        ax.text(cx, cy + 1.35, title, ha="center", fontsize=10.5, color=colour, weight="bold")
        ax.text(cx, cy + 0.85, body, ha="center", va="top", fontsize=8.8, color=INK,
                linespacing=1.6)

    quad(2.5, 7.5, "cheap and reusable", "the healthy case:\nincremental dense prefill\non a warm prefix", DENSE)
    quad(7.5, 7.5, "expensive, reusable", "a full dense prefill\non a cold prompt", MUTED)
    quad(2.5, 2.5, "cheap, leaves nothing", "sparse prefill:\nserved fast, stores no\ncheckpoint", SPARSE)
    quad(7.5, 2.5, "expensive, leaves nothing", "recomputation after\nthe cliff", SPARSE)

    ax.text(5, 0.95, "a continuation-heavy session moves sparse prefill\nfrom the lower left to the lower right",
            ha="center", fontsize=8.8, color=SPARSE, linespacing=1.6)
    ax.text(5, 0.1, "conceptual diagram — no measured data", ha="center",
            fontsize=8, color=MUTED, style="italic")
    ax.set_title("A single-request latency number reads only the horizontal axis", loc="left", fontsize=10.5)
    save(fig, "fig2-two-axes")


def fig3_cliff():
    data = rows("trace-b-restores.csv")
    seq = [int(r["seq"]) for r in data]
    cached = [int(r["cached_tokens"]) for r in data]
    suffix = [int(r["uncached_suffix"]) for r in data]
    cliff = next(int(r["seq"]) for r in data if r["phase"] == "cliff")

    fig, ax = plt.subplots(figsize=(8.6, 4.6))
    ax.step(seq, cached, where="post", color=DENSE, lw=2.1, label="reusable checkpoint (cached tokens)")
    ax.plot(seq, suffix, color=SPARSE, lw=2.1, marker="o", ms=3.4, label="uncached suffix recomputed per request")
    ax.axvline(cliff, color=MUTED, lw=1, ls=(0, (4, 3)))
    ax.annotate("checkpoint collapses to 28,672\nand never recovers",
                xy=(cliff, 28672), xytext=(cliff + 0.6, 12500), fontsize=9, color=INK,
                arrowprops=dict(arrowstyle="-|>", color=MUTED, lw=1))
    ax.set_xticks(seq)
    ax.set_xlabel("prefix-cache restore, in order")
    ax.set_ylabel("tokens")
    ax.set_ylim(0, 47000)
    ax.set_yticks([0, 10000, 20000, 30000, 40000])
    ax.set_yticklabels(["0", "10K", "20K", "30K", "40K"])
    ax.legend(frameon=False, loc="upper left")
    ax.set_title("One session, 20 requests: the checkpoint flatlines while the suffix grows", loc="left")
    save(fig, "fig3-cache-cliff")


def fig4_scorer():
    data = rows("trace-b-scorer.csv")
    scored = [int(r["scored_tokens"]) for r in data]
    secs = [float(r["seconds"]) for r in data]
    fig, ax = plt.subplots(figsize=(6.6, 4.2))
    ax.plot(scored, secs, color=SPARSE, lw=1.8, marker="o", ms=4.5)
    ax.annotate(f"{secs[0]} s", xy=(scored[0], secs[0]), xytext=(6, -12),
                textcoords="offset points", fontsize=8.5, color=MUTED)
    ax.annotate(f"{secs[-1]} s", xy=(scored[-1], secs[-1]), xytext=(-34, 6),
                textcoords="offset points", fontsize=8.5, color=MUTED)
    ax.set_xlabel("tokens scored in the call")
    ax.set_ylabel("scorer time (s)")
    ax.set_ylim(0, 6.5)
    ax.set_xlim(0, 36000)
    ax.set_xticks([0, 10000, 20000, 30000])
    ax.set_xticklabels(["0", "10K", "20K", "30K"])
    ax.set_title("The optimization's own overhead scales with the debt it created", loc="left")
    save(fig, "fig4-scorer-cost")


def fig5_think_time():
    data = rows("think-time.csv")
    think = [int(r["think_time_s"]) for r in data]
    dense = [float(r["dense_only_s"]) for r in data]
    hybrid = [float(r["hybrid_s"]) for r in data]
    repeat = [num(r["hybrid_repeat_s"]) for r in data]

    fig, ax = plt.subplots(figsize=(7.0, 4.3))
    ax.plot(think, dense, color=DENSE, lw=2, marker="s", ms=5, label="dense only")
    ax.plot(think, hybrid, color=SPARSE, lw=2, marker="o", ms=5, label="sparse + background recovery")
    shown = False
    for t, r in zip(think, repeat):
        if r:
            ax.plot([t], [r], marker="o", ms=5, mfc="white", mec=SPARSE, lw=0,
                    label="repeat run" if not shown else None)
            shown = True
    ax.axhline(108.1, color=FAINT, lw=1)
    ax.annotate("at zero idle the hybrid is 11% slower\nthan dense: recovery never runs",
                xy=(0, 119.7), xytext=(2.2, 128), fontsize=9,
                arrowprops=dict(arrowstyle="-|>", color=MUTED, lw=1))
    ax.set_xlabel("idle time between turns (s)")
    ax.set_ylabel("session wall time (s)")
    ax.set_xticks(think)
    ax.set_ylim(70, 140)
    ax.invert_xaxis()
    ax.legend(frameon=False, loc="lower right")
    ax.set_title("Background recovery only pays when there is idle time to pay it with", loc="left")
    save(fig, "fig5-think-time")


def fig6_regimes():
    fig, ax = plt.subplots(figsize=(10.2, 3.6))
    ax.set_xlim(0, 12)
    ax.set_ylim(0, 4.4)
    ax.axis("off")
    boxes = [
        (0.2, "disposable cold request", DENSE,
         "one long prompt, no follow-up\n\nno reusable state is wasted,\nbecause none would be reused\n\nsparse prefill wins outright"),
        (4.1, "continuation, cliff reached", SPARSE,
         "long session, prompt keeps growing\n\ncheckpoint stops advancing;\nevery later request repays it\n\nsparse prefill loses"),
        (8.0, "healthy incremental", MUTED,
         "cache hit stays high, suffix small\n\nthe threshold is never crossed\n\nsparse prefill never triggers"),
    ]
    for x, title, colour, body in boxes:
        ax.add_patch(Rectangle((x, 0.35), 3.7, 3.5, fill=False, ec=colour, lw=1.4))
        ax.text(x + 1.85, 3.4, title, ha="center", fontsize=10.2, color=colour, weight="bold")
        ax.text(x + 1.85, 2.0, body, ha="center", va="center", fontsize=8.5, linespacing=1.6)
    ax.text(6, 0.02, "conceptual diagram — no measured data", ha="center",
            fontsize=8, color=MUTED, style="italic")
    save(fig, "fig6-three-regimes")


def fig7_background_recovery():
    waits = rows("waiting-turn-cost.csv")
    turns = [int(r["turn"]) for r in waits]
    secs = [num(r["seconds"]) for r in waits]
    runtime = {r["metric"]: r for r in rows("hybrid-runtime.csv")}
    fg = runtime["foreground_generation_tok_s"]
    before, after = num(fg["before"]), num(fg["after"])

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(9.4, 3.9), gridspec_kw={"width_ratios": [1.5, 1]})
    ax1.plot(turns, secs, color=DENSE, lw=2.1, marker="o", ms=4.2)
    for t, v in zip(turns, secs):
        ax1.text(t, v + 0.45, f"{v:g}", ha="center", fontsize=8)
    ax1.set_xticks(turns)
    ax1.set_xlabel("turn arriving while the background job is still running")
    ax1.set_ylabel("time the turn waited on the job (s)")
    ax1.set_ylim(0, 12.5)
    ax1.set_title("each turn finds more of the prefix already stored", fontsize=10)

    ax2.bar([0, 1], [before, after], 0.55, color=[SPARSE, DENSE])
    ax2.text(0, before + 1.2, f"{before:g}", ha="center", fontsize=9)
    ax2.text(1, after + 1.2, f"{after:g}", ha="center", fontsize=9)
    ax2.set_xticks([0, 1])
    ax2.set_xticklabels(["slices overlap\ndecode", "after the\nscheduler fix"], fontsize=8.5)
    ax2.set_ylabel("foreground decode (tok/s)")
    ax2.set_ylim(0, 56)
    ax2.set_title("with a background slice running", fontsize=10)
    fig.text(0.5, -0.02, "measured, single run per point — data/exp-001/waiting-turn-cost.csv, hybrid-runtime.csv",
             ha="center", fontsize=8, color=MUTED, style="italic")
    fig.tight_layout()
    save(fig, "fig7-background-recovery")


def fig8_system_evolution():
    stages = [
        ("dense baseline", "fast warm turns,\nslow cold prefill", DENSE),
        ("heterogeneous prefill", "GPU + neural engine,\n1024-token tile", DENSE),
        ("sparse prefill stacked", "cold 16K 57.84 s -> 19.24 s;\n8-turn session slower", DENSE),
        ("boundary fix", "protected prefix measured,\nnot inferred (PR #3756)", SPARSE),
        ("sparse first, dense later", "background rebuild of\nthe skipped prefix", DENSE),
        ("incremental checkpoints", "every finished 1024-token\nblock published at once", DENSE),
        ("cooperative scheduler", "arrival counter, two-idle-step\ngate; 13.5 -> 47 tok/s", DENSE),
        ("controlled win", "24% faster at 15 s idle,\n11% slower at zero idle", DENSE),
        ("real workload", "context growth outruns\nrecovery; design set aside", SPARSE),
        ("mechanism trace", "20 restores, checkpoint\npinned at 28,672", SPARSE),
        ("transport policy", "caller declares the shape;\nper-request control (#3762)", DENSE),
    ]
    cols, w, h, gap, rowgap = 6, 2.6, 1.3, 0.26, 0.7
    fig, ax = plt.subplots(figsize=(14.8, 4.1))
    ax.set_xlim(-0.15, cols * (w + gap) - gap + 0.15)
    ax.set_ylim(-0.25, 2 * h + rowgap + 0.25)
    ax.axis("off")
    for i, (title, body, colour) in enumerate(stages):
        r, c = divmod(i, cols)
        x, y = c * (w + gap), (1 - r) * (h + rowgap)
        ax.add_patch(Rectangle((x, y), w, h, fill=False, ec=colour, lw=1.4))
        ax.text(x + 0.1, y + h - 0.2, title, fontsize=8.2, color=colour, weight="bold", va="top")
        ax.text(x + 0.1, y + 0.12, body, fontsize=7.5, va="bottom", linespacing=1.45)
        if c < cols - 1 and i < len(stages) - 1:
            ax.annotate("", xy=(x + w + gap, y + h / 2), xytext=(x + w, y + h / 2),
                        arrowprops=dict(arrowstyle="-|>", color=MUTED, lw=1.1))
        elif r == 0 and i < len(stages) - 1:
            mid = y - rowgap / 2
            ax.plot([x + w / 2, x + w / 2, w / 2], [y, mid, mid], color=MUTED, lw=1.1)
            ax.annotate("", xy=(w / 2, y - rowgap), xytext=(w / 2, mid),
                        arrowprops=dict(arrowstyle="-|>", color=MUTED, lw=1.1))
    ax.text((cols * (w + gap) - gap) / 2, -0.2,
            "conceptual diagram — no measured data; the numbers are quoted or derived from data/exp-001, and ENGINEERING.md names each source",
            ha="center", fontsize=8, color=MUTED, style="italic")
    save(fig, "fig8-system-evolution")


def fig9_hybrid_architecture():
    fig, ax = plt.subplots(figsize=(9.6, 4.4))
    ax.set_xlim(0, 12)
    ax.set_ylim(0, 5.2)
    ax.axis("off")

    def box(x, y, w, h, title, body, colour):
        ax.add_patch(Rectangle((x, y), w, h, fill=False, ec=colour, lw=1.4))
        ax.text(x + w / 2, y + h - 0.28, title, ha="center", va="top", fontsize=9.4, color=colour, weight="bold")
        if body:
            ax.text(x + w / 2, y + 0.18, body, ha="center", va="bottom", fontsize=7.9, linespacing=1.45)

    def arrow(x0, y0, x1, y1, label=None, dy=0.16):
        ax.annotate("", xy=(x1, y1), xytext=(x0, y0),
                    arrowprops=dict(arrowstyle="-|>", color=MUTED, lw=1.2))
        if label:
            ax.text((x0 + x1) / 2, (y0 + y1) / 2 + dy, label, ha="center", fontsize=7.6, color=MUTED)

    box(0.2, 3.3, 2.2, 1.4, "request", "prompt above the\n8192-token threshold", INK)
    box(3.4, 3.3, 2.6, 1.4, "sparse prefill", "scorer keeps 20%;\nfirst token in seconds", SPARSE)
    box(7.0, 3.3, 2.2, 1.4, "response", "served now;\nleaves no checkpoint", INK)
    arrow(2.4, 4.0, 3.4, 4.0)
    arrow(6.0, 4.0, 7.0, 4.0)

    box(3.4, 0.9, 2.6, 1.5, "background job", "one 1024-token slice\nper idle window", DENSE)
    box(7.0, 0.9, 2.2, 1.5, "prefix cache", "every completed block\nstored as a checkpoint", DENSE)
    arrow(4.7, 3.3, 4.7, 2.4)
    ax.text(4.85, 2.8, "queue", fontsize=7.6, color=MUTED)
    arrow(6.0, 1.65, 7.0, 1.65, "store", dy=0.16)

    box(10.0, 0.9, 1.8, 3.8, "scheduler", "", INK)
    ax.text(10.9, 3.9, "inbound counter\nraised before\nexecutor hand-off\n\ntwo idle steps\nbefore a slice\nmay start\n\nslice yields to\nany request",
            ha="center", va="top", fontsize=7.6, linespacing=1.45)
    ax.plot([10.9, 10.9, 4.2], [0.9, 0.55, 0.55], color=MUTED, lw=1.2)
    ax.annotate("", xy=(4.2, 0.9), xytext=(4.2, 0.55), arrowprops=dict(arrowstyle="-|>", color=MUTED, lw=1.2))
    ax.text(7.6, 0.6, "gates every slice", ha="center", va="bottom", fontsize=7.6, color=MUTED)

    ax.text(1.55, 2.05, "next turn restores\nfrom the furthest\nstored block", ha="center", fontsize=7.9, color=DENSE)
    ax.plot([8.6, 8.6, 0.55], [0.9, 0.2, 0.2], color=DENSE, lw=1.2)
    ax.annotate("", xy=(0.55, 3.3), xytext=(0.55, 0.2), arrowprops=dict(arrowstyle="-|>", color=DENSE, lw=1.2))

    ax.set_ylim(-0.55, 5.2)
    ax.text(6, -0.4, "conceptual diagram — no measured data. This is the experimental build that was measured in Figure 5 and Figure 7, then set aside.",
            ha="center", fontsize=8, color=MUTED, style="italic")
    save(fig, "fig9-hybrid-architecture")


if __name__ == "__main__":
    fig1_cold_prefill()
    fig2_two_axes()
    fig3_cliff()
    fig4_scorer()
    fig5_think_time()
    fig6_regimes()
    fig7_background_recovery()
    fig8_system_evolution()
    fig9_hybrid_architecture()
