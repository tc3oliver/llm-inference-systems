"""Shared figure style, matching figures/plot.py.

Figures are written as SVG. The palette, spine treatment and font sizes are the
same ones figures/plot.py uses, so a figure built from a run file sits beside
the hand-built ones without looking like it came from somewhere else.
"""

from __future__ import annotations

import pathlib

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

INK = "#1b1b1b"
DENSE = "#33556b"
SPARSE = "#c0563c"
MUTED = "#8a8a8a"
FAINT = "#d9d9d9"

SERIES = [DENSE, SPARSE, MUTED, "#6b8f6b", "#8b6b8f", "#8f7a4a"]

BASE_FONT = 10
LABEL_FONT = 8
ANNOT_FONT = 8.5
TITLE_FONT = 10.5

RC = {
    "figure.dpi": 130,
    "savefig.dpi": 130,
    "font.size": BASE_FONT,
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
}


def use_style() -> None:
    """Apply the repository figure style to the global matplotlib state."""
    plt.rcParams.update(RC)


def new_figure(width: float = 7.0, height: float = 4.3, **kwargs):
    """A styled figure and axes."""
    use_style()
    return plt.subplots(figsize=(width, height), **kwargs)


def annotate(ax, text: str, xy, offset=(0, 6), color: str = MUTED) -> None:
    ax.annotate(text, xy=xy, xytext=offset, textcoords="offset points",
                fontsize=ANNOT_FONT, color=color, ha="center")


def caption(ax, text: str) -> None:
    """A muted italic note under the axes, for a measurement caveat."""
    ax.text(0.0, -0.18, text, transform=ax.transAxes, fontsize=LABEL_FONT,
            color=MUTED, style="italic", va="top")


def title(ax, text: str) -> None:
    ax.set_title(text, loc="left", fontsize=TITLE_FONT)


def save(fig, path: str | pathlib.Path, also_png: bool = False) -> pathlib.Path:
    """Write the figure as SVG, and optionally a PNG beside it."""
    path = pathlib.Path(path).with_suffix(".svg")
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, bbox_inches="tight")
    if also_png:
        fig.savefig(path.with_suffix(".png"), bbox_inches="tight")
    plt.close(fig)
    return path


def bars_with_spread(ax, labels, medians, lows, highs, color: str = DENSE) -> None:
    """Median bars with a min-to-max whisker, the repository's default comparison."""
    positions = range(len(labels))
    lower = [m - lo for m, lo in zip(medians, lows)]
    upper = [hi - m for m, hi in zip(medians, highs)]
    ax.bar(positions, medians, width=0.55, color=color,
           yerr=[lower, upper], capsize=3, ecolor=MUTED, error_kw={"linewidth": 0.9})
    ax.set_xticks(list(positions))
    ax.set_xticklabels(labels, fontsize=LABEL_FONT + 0.5)
    for position, median in zip(positions, medians):
        ax.text(position, median, f"{median:.3g}", ha="center", va="bottom",
                fontsize=LABEL_FONT)
