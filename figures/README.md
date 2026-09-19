# Figures

Six figures, each as SVG and PNG. Four are drawn from measured data; two are
diagrams and say so on their own face. Every caption names its evidence level
from [`EVIDENCE.md`](../EVIDENCE.md) and the file it reads.

Regenerate all six:

    uv run --with matplotlib python figures/plot.py

`plot.py` reads only from `data/` and needs nothing but matplotlib.

## fig1-cold-prefill

Cold-start prefill, dense against accelerated, at three prompt sizes:
time-to-first-token on the left, prefill throughput on the right. At 16,384
tokens TTFT falls from 57.84 s to 19.24 s and throughput rises from 302 tok/s
to 1046 tok/s. This is the win that started the study, and it is the only
thing a single-request benchmark can see.

Evidence level 1, microbenchmark. Source: `data/exp-001/cold-prefill.csv`.

## fig2-two-axes

**Conceptual diagram, no measured data.** Cost paid now on one axis, reusable
state created on the other. A latency number reads only the horizontal axis,
which is why sparse prefill looks like an improvement in Figure 1 and like a
regression by Figure 3: a continuation-heavy session moves it from the lower
left of this plane to the lower right, and nothing in a single-request
measurement registers the move.

Not an evidence level. Source: none — this is the framing the rest of the
study argues for, drawn so the argument has a picture.

## fig3-cache-cliff

The reusable checkpoint and the uncached suffix across 20 consecutive
prefix-cache restores in one session. The checkpoint climbs from 28,672 to
37,888 as a step line, drops back to 28,672 at request 11, and holds there for
the remaining ten requests. The suffix crosses it at the same point and keeps
going, 17,060 to 33,979. One session, one arm, sparse prefill only, with no
background densification in the build.

This is the figure the study is built on. Follow the two lines and the
mechanism is visible before anyone explains it.

Evidence level 5, request-level mechanism trace. Source:
`data/exp-001/trace-b-restores.csv`.

## fig4-scorer-cost

Scorer wall time against tokens scored, across the ten scorer invocations in
the same session: 2.7 s at 8,535 tokens, 5.7 s at 33,389. The series is not
monotone — the second call is 2.4 s at 16,918 tokens — but the trend is the
point. The scorer runs over the uncached suffix, so as the suffix grows after
the cliff the optimization's own overhead grows with it.

Evidence level 5, request-level mechanism trace. Source:
`data/exp-001/trace-b-scorer.csv`.

## fig5-think-time

Total session wall time against think time — the idle gap between turns — for
the dense-only and hybrid arms of the synthetic interactive workload. Dense
stays flat near 108 s across the sweep. Hybrid wins at 15 s and 10 s of idle,
narrows at 5 s, and at zero idle is slower than dense, 119.7 s against 108.1
s. Two of the four settings were run twice and both points are plotted.

The right-hand end of this plot is the controlled version of what the real
session later showed: background recovery only pays when there is idle time
to pay it with.

Evidence level 3, synthetic interactive workload. Source:
`data/exp-001/think-time.csv`.

## fig6-three-regimes

**Conceptual diagram, no measured data.** The three workload regimes the study
ends up with — cold long-context, where sparse prefill is a clear win;
continuation-heavy agent sessions, where it hits the cliff; and sessions whose
prefix cache stays healthy, where it never triggers at all. The verdict
depends on which of the three you are in, which is why the change that shipped
upstream is a per-path default rather than a global switch.

Not an evidence level. Source: none — it summarises the regimes described in
the experiment's findings; the numbers behind each regime are in `data/` and
in the findings document, not in this figure.
