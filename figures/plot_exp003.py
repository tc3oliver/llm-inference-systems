"""Regenerate the EXP-003 figures from ../data/exp-003 and ../data/recovery-foreground-qos.

    uv run --with matplotlib python figures/plot_exp003.py

Three figures, one claim each:

  A  canonical-state debt per turn — what recovery is for
  B  the controlled session result — one workload, both arms sparse
  C  foreground QoS against execution slice — what recovery costs

Every value is read from `data/`. Nothing here reads a log, a server or a
network resource, and nothing is smoothed, interpolated or back-generated.
Figure C deliberately plots the observed client latency and the trace-derived
execution bound as two separate series: they answer different questions and
collapsing them would imply a precision neither has.
"""

import csv
import pathlib

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

ROOT = pathlib.Path(__file__).resolve().parent
EXP3 = ROOT.parent / "data" / "exp-003"
QOS = ROOT.parent / "data" / "recovery-foreground-qos"

INK = "#1b1b1b"
SPEC = "#c0563c"
PCSR = "#33556b"
ACCENT = "#7a8b3f"
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


def read(path):
    with open(path, newline="") as fh:
        return list(csv.DictReader(fh))


def num(value, default=None):
    """A blank cell is an unmeasured quantity, not a zero."""
    if value is None or value == "":
        return default
    return float(value)


def save(fig, stem):
    fig.tight_layout()
    for ext in ("svg", "png"):
        fig.savefig(ROOT / f"{stem}.{ext}", bbox_inches="tight")
    plt.close(fig)


def turns(arm):
    rows = [
        r for r in read(EXP3 / "spec-exit-always-sparse-turns.csv")
        if r["arm"] == arm and r["kind"] == "turn"
    ]
    return sorted(rows, key=lambda r: int(r["turn"]))


def fig12_canonical_debt():
    """Figure A — the logical conversation, the reusable prefix, and the gap.

    The sparse arm's reusable prefix never leaves zero, so its debt is the
    whole prompt and grows with the session. The recovery arm's prefix climbs
    in whole cache blocks behind the conversation, and the gap it leaves is
    what a later turn still has to compute.
    """
    fig, axes = plt.subplots(1, 2, figsize=(9.6, 3.9), sharey=True)
    for ax, arm, colour, title in (
        (axes[0], "spec", SPEC, "SpecPrefill only"),
        (axes[1], "pcsr", PCSR, "SpecPrefill + background recovery"),
    ):
        rows = turns(arm)
        x = [int(r["turn"]) for r in rows]
        prompt = [num(r["prompt_tokens"]) for r in rows]
        prefix = [num(r["canonical_prefix_tokens"], 0.0) for r in rows]
        ax.plot(x, prompt, color=INK, lw=1.4, marker="o", ms=4,
                label="logical prompt")
        ax.plot(x, prefix, color=colour, lw=1.8, marker="s", ms=4,
                label="reusable canonical prefix")
        ax.fill_between(x, prefix, prompt, color=colour, alpha=0.13,
                        label="uncached suffix (debt)")
        ax.set_title(title, fontsize=10)
        ax.set_xlabel("turn")
        ax.set_xticks(x)
    axes[0].set_ylabel("tokens")
    axes[0].legend(frameon=False, fontsize=8.5, loc="upper left")
    save(fig, "fig12-canonical-debt")


def fig13_session_result():
    """Figure B — per-turn TTFT and the cumulative session cost.

    One controlled workload. Both arms stay on the sparse route for every
    turn: the recovery arm never exits SpecPrefill, which is the point. What
    changes is how much of each prompt is already canonical when the turn
    starts.
    """
    fig, axes = plt.subplots(1, 2, figsize=(9.6, 3.9))
    for arm, colour, label in (("spec", SPEC, "SpecPrefill only"),
                               ("pcsr", PCSR, "SpecPrefill + recovery")):
        rows = turns(arm)
        x = [int(r["turn"]) for r in rows]
        ttft = [num(r["ttft_s"]) for r in rows]
        running, total = [], 0.0
        for value in ttft:
            total += value
            running.append(total)
        axes[0].plot(x, ttft, color=colour, lw=1.8, marker="o", ms=4, label=label)
        axes[1].plot(x, running, color=colour, lw=1.8, marker="o", ms=4, label=label)
        axes[0].set_xticks(x)
        axes[1].set_xticks(x)

    summary = {r["arm"]: r for r in read(EXP3 / "spec-exit-always-sparse-summary.csv")}
    spec_s = num(summary["spec"]["cumulative_foreground_s"])
    pcsr_s = num(summary["pcsr"]["cumulative_foreground_s"])
    axes[1].annotate(
        f"{spec_s:.1f} s → {pcsr_s:.1f} s",
        xy=(max(int(r["turn"]) for r in turns("pcsr")), pcsr_s),
        xytext=(-6, 14), textcoords="offset points",
        ha="right", fontsize=9, color=MUTED,
    )
    axes[0].set_xlabel("turn"); axes[0].set_ylabel("TTFT (s)")
    axes[1].set_xlabel("turn"); axes[1].set_ylabel("cumulative foreground (s)")
    axes[0].legend(frameon=False, fontsize=8.5, loc="upper left")
    save(fig, "fig13-session-result")


def fig14_foreground_qos():
    """Figure C — what a foreground request waits behind, by execution slice.

    Two series, deliberately not merged. The circles are what a client
    actually observed; the bars are the worst single execution slice the
    runtime trace recorded, which is the bound a request could have waited.
    The bound is above the observation at every slice size, and the gap is
    the arrival distribution's luck rather than a property of the design.

    The block-grain point is the same runtime with the slice cap off.

    The bound is the maximum over both traced slice kinds. A slice that ends on
    a publication boundary runs the extract, the store and the read-back inside
    the same uninterruptible unit, so it is the longer one at every slice size
    and it is the one a foreground request can actually land behind.
    """
    summary = [r for r in read(QOS / "collision-summary.csv")
               if r["recovery"] == "on" and r["budget_pct"] == "100.0"]
    timing = {int(r["slice_tokens"]): r for r in read(QOS / "slice-timing.csv")}

    points = []
    seen = {}
    for row in summary:
        slice_tokens = int(row["slice_tokens"])
        seen[slice_tokens] = seen.get(slice_tokens, 0) + 1
        if slice_tokens == 0:
            label = "4096\n(block grain,\ncap off)"
        elif seen[slice_tokens] > 1:
            label = f"{slice_tokens}\n(repeat run)"
        else:
            label = str(slice_tokens)
        # The worst uninterruptible unit, which is what a request waits
        # behind. A slice that publishes is still one slice: it carries the
        # extract, the store and the read-back inside the same unit, and it
        # is the longer of the two every time.
        bound = None
        if slice_tokens in timing:
            bound = max(num(timing[slice_tokens]["duration_max_s"]),
                        num(timing[slice_tokens]["publish_duration_max_s"]))
        points.append((label, slice_tokens, num(row["probe_ttft_max_s"]), bound))
    # Widest slice first, so the axis reads coarse -> fine.
    points.sort(key=lambda p: (1e9 if p[1] == 0 else p[1]), reverse=True)

    fig, ax = plt.subplots(figsize=(8.0, 4.3))
    x = list(range(len(points)))
    traced = [i for i, p in enumerate(points) if p[3] is not None]
    ax.bar([i + 0.18 for i in traced], [points[i][3] for i in traced],
           width=0.34, color=ACCENT, alpha=0.75,
           label="trace-derived worst execution slice")
    ax.plot([i - 0.18 for i in x], [p[2] for p in points], ls="", marker="o",
            ms=8, color=PCSR, label="observed client max TTFT")
    for i, p in enumerate(points):
        if p[3] is None:
            ax.annotate("slice not traced\nat this grain", xy=(i + 0.18, 0.4),
                        ha="center", va="bottom", fontsize=7.5, color=MUTED)
        ax.annotate(f"{p[2]:.2f}", xy=(i - 0.18, p[2]), xytext=(0, 8),
                    textcoords="offset points", ha="center", fontsize=7.5,
                    color=PCSR)
    ax.set_xticks(x)
    ax.set_xticklabels([p[0] for p in points], fontsize=8.5)
    ax.set_xlabel("recovery execution slice (tokens)")
    ax.set_ylabel("seconds")
    ax.set_ylim(0, 17.5)
    ax.legend(frameon=False, fontsize=8.5, loc="upper center")
    fig.text(0.5, -0.06,
             "Publication boundaries are identical at every slice size. The two "
             "series answer different questions and are not\ninterchangeable: the "
             "markers are what one arrival distribution happened to observe, the "
             "bars are what a request could\nhave waited. No foreground latency "
             "target was defined, so none of these values is established as "
             "acceptable.",
             ha="center", fontsize=7.8, color=MUTED)
    save(fig, "fig14-foreground-qos")


if __name__ == "__main__":
    fig12_canonical_debt()
    fig13_session_result()
    fig14_foreground_qos()
    print("wrote fig12-canonical-debt, fig13-session-result, fig14-foreground-qos (svg + png)")
