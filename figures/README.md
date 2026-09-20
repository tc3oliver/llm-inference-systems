# Figures

Nine figures, each as SVG and PNG. Five are drawn from measured data; four
are diagrams and say so on their own face. Every caption names its evidence level
from [`EVIDENCE.md`](../EVIDENCE.md) and the file it reads.

Regenerate all nine:

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
going, 17,060 to 33,979. One session, one arm, SpecPrefill — an
attention-based sparse prefill mechanism — only, with no background
densification in the build.

This is the figure the study is built on. Follow the two lines and the
mechanism is visible before anyone explains it. Read the drop at request 11 as
an event at a restore: it precedes the sparse admission on that request, and
the flat line after it is what SpecPrefill failed to repair.

Evidence level 5, request-level mechanism trace. Source:
`data/exp-001/trace-b-restores.csv`.

## fig4-scorer-cost

Scorer wall time against tokens scored, across the ten scorer invocations in
the same session: 2.7 s at 8,535 tokens, 5.7 s at 33,389. The series is not
monotone — the second call is 2.4 s at 16,918 tokens — but the trend is the
point. The scorer runs over the uncached suffix, so as the suffix grows after
the cliff the selector's own cost grows along with it.

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
continuation-heavy agent sessions, where the observed session reached the
cliff; and sessions whose
prefix cache stays healthy, where it never triggers at all. The verdict
depends on which of the three you are in, which is why the local deployment uses a
per-transport default rather than a global switch.

Not an evidence level. Source: none — it summarises the regimes described in
the experiment's findings; the numbers behind each regime are in `data/` and
in the findings document, not in this figure.

## fig7-background-recovery

Two panels from the experimental build that rebuilds the dense prefix in the
background. Left: how long each of five consecutive turns waited on the job
while it was still running, 10.16 s falling to 1.77 s, because each turn
found more of the prefix already stored. Right: foreground decode throughput
with a background slice running, 13.5 tok/s while slices overlapped decode
and 47 tok/s after the scheduler learned to yield. Single run per point.

Evidence level 1, isolated runtime measurement. Source:
`data/exp-001/waiting-turn-cost.csv` and `data/exp-001/hybrid-runtime.csv`.

## fig8-system-evolution

**Conceptual diagram, no measured data.** The eleven stages in
[`ENGINEERING.md`](../ENGINEERING.md) as a sequence, from the dense baseline
to the transport-level policy, with the stages that broke an assumption
marked in red. The numbers printed inside the boxes are quotations from
`data/`; the diagram itself measures nothing.

Not an evidence level. Source: none.

## fig9-hybrid-architecture

**Conceptual diagram, no measured data.** The sparse-first, dense-later
runtime: a request above the threshold is served by sparse prefill, its
prompt is queued truncated to whole cache blocks, a background job rebuilds
the dense prefix one 1024-token slice per idle window and stores each
completed block, and the scheduler gates every slice so that a request never
waits behind more than the slice already in flight. It was designed to fail
closed; a later review of that design found four gaps, recorded in
[Prototype safety review](../ENGINEERING.md#prototype-safety-review). This is
the build measured in Figures 5 and 7 and then set aside after the real
workload.

Not an evidence level. Source: none.
