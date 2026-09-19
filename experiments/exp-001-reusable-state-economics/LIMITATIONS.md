# Limitations

## Single-run arms

Almost everything here is n=1. The cold prefill table is one run per cell. The
real-agent session comparison is one session per arm. Six of the eight
synthetic think-time cells are single runs; the hybrid arm at 15 s and at 5 s
of idle has a repeat, and where a repeat exists it is printed alongside the
first run rather than averaged into it.

That is enough to establish that a mechanism exists and not enough to put an
interval on any effect size. A reader should take every ratio in this study as
the ratio observed once, on this machine, that day.

## The two real sessions took different trajectories

The dense, sparse and hybrid session arms were real coding agents doing real
work, and they did different work. Different tool calls, different amounts of
generated text, different numbers of turns before the task resolved. The
session wall figures — about 1676 s, 4404 s and 5248 s — therefore measure the
trajectories at least as much as the prefill mode. They are reported because
they are what prompted the investigation. They are not a measurement of how
much slower sparse prefill is, and no conclusion in this study rests on them.

The per-turn cache hit column from the same sessions is more defensible,
because a hit rate is a property of what the cache could restore for a given
prompt rather than of how long the agent chose to work. It is still one session
per arm.

## One machine, one model family, one runtime

Apple silicon, one 27B-class MoE model at 4-bit, one serving runtime. The cache
cliff as described here is a consequence of a specific interaction: a sparse
prefill leaving a placeholder in a block, and a block-structured KV cache
refusing partial prefix matches. A runtime with a different cache granularity,
or one that recomputes rather than rejects a partial block, would show
something different. I have not tested one. Nothing here should be read as a
statement about sparse prefill in general, only about sparse prefill composed
with this class of prefix cache.

## The third regime is one session, reported approximately

The healthy-incremental observation — cache hit around 84-86%, largest true
uncached suffix around 2.5K, zero scorer calls — is a single real session, and
the figures are approximate because they come from session-level aggregates
rather than from a request-level trace like trace B. It is used only to refute
the universal claim that enabling sparse prefill changes session behaviour. It
cannot support anything stronger, such as how common that regime is.

## Two figures are diagrams

`figures/fig2-two-axes.svg` and `figures/fig6-three-regimes.svg` carry no
measured data. They are labelled as conceptual in the figure itself and in
`figures/plot.py`. If either is reproduced elsewhere, that label has to travel
with it.

## One claim cut for lack of a source

A pair of prefill throughput figures comparing GPU-only execution against
dual-ANE execution appeared in my planning notes for this study. I could not
find the file they came from, so I am not reproducing the values here. They
are not in `data/`, and I will not re-run the measurement inside the scope of
this write-up to manufacture one.

That comparison is therefore not used anywhere in this study: not in the
prose, not in a figure, not as a supporting detail. I record the cut here rather than deleting
it silently, because a number that was believed and then dropped for want of
provenance is part of the honest record of what this study does and does not
know.

## No reproduction path from this repository

Stated in [METHODOLOGY.md](METHODOLOGY.md) and repeated here because it is a
limitation and not a design detail worth burying: no experiment in this study
can be re-run from this repository. The weights, the server build, the agent
and the prompts are all absent, the prompts deliberately so. What is
reproducible is the path from the extracted measurements in `data/exp-001/` to
the figures, and nothing before that point.
