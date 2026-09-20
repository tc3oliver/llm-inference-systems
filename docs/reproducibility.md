# Reproducibility

Two different things get called reproducible and this repository supports only
one of them. Every figure and every number in the prose can be regenerated
here, by anyone, offline. The runs that produced the numbers cannot be, by
anyone, including me.

## Redrawing the figures

    uv run --with matplotlib python figures/plot.py

That writes all nine figures as SVG and PNG into `figures/`, overwriting what
is committed. The script reads only files under `data/`. It opens no network
connection, loads no model, and starts no server.

matplotlib is the only dependency. No seaborn, no theme package, no style
file. If you have matplotlib already, plain `python figures/plot.py` from the
repository root does the same thing.

Figures 1, 3, 4, 5 and 7 are drawn from CSVs. Figures 2, 6, 8 and 9 are
diagrams with no measured data, drawn by the same script so they stay in one visual
vocabulary with the rest, and labelled as diagrams in the figure and in
`figures/README.md`.

## What is reproducible from this repository

Every figure, from the committed data, with the command above.

Every number in the prose. Each one is in a CSV under `data/`, at the
precision the source reported. `data/README.md` maps file to provenance and
row count. If a number in a document does not appear in `data/`, that is an
error and I want to know about it.

The derivations. The relationships the study argues for — the checkpoint
flatline, the suffix growth, the scorer cost tracking the suffix, the
think-time crossover — are all visible by reading the CSVs directly, without
running anything.

## What is not reproducible from this repository

The runs. They were one-off sessions on one machine: Apple silicon, M4 Max,
64GB unified memory, a 27B-class dense model at 4-bit. The real-agent
comparison is one run per arm and the three agents took different trajectories
through the task, so even re-running it on that machine would not reproduce
those numbers.

This repository deliberately ships no server and no model. There is no
`requirements.txt` that stands up an inference stack, no configuration, no
weights and no launcher. Publishing those would imply the runs can be
replayed, and they cannot; it would also mean publishing configuration from a
machine whose details do not belong in a public repository. What went upstream
went upstream as pull requests, which is the honest artefact.

The one-off runtime observation archived as an appendix in the experiment is
explicitly non-reproducible — the sanitized reproduction at the matching
shape passed 4 of 4 attempts, meaning the latch never recurred.

## Reproducing the phenomenon rather than the numbers

The cache cliff is a property of a class of system, not of my machine, and
someone with a different stack could establish whether it generalises. What
that takes:

A server with a prefix cache and an optional sparse prefill path — SpecPrefill,
an attention-based sparse prefill mechanism, is the one used here, but any
selective prefill will do — where the sparse path's output is not written back
to the cache.
Instrumentation at the prefix-cache restore boundary that logs, per request,
the restored checkpoint size and the uncached suffix length — those two
columns are the entire mechanism. Instrumentation on the scorer, or whatever
plays its role, recording tokens scored and wall time per call.

A continuation-heavy workload. A coding agent works; so does anything that
appends to a growing context and resubmits it. The geometry that matters is
that each request is mostly the previous request. A session whose prefix
cache stays healthy may never reach the threshold at all — that is what the
third regime in this study looked like, in one session: cache hit around
84-86%, largest true uncached suffix about 2.5K, and not a single scorer
call.

Then watch the checkpoint column over a session of twenty or more requests.
If it climbs and flatlines while the suffix column climbs past it, that is the
cliff. Watch the order as well as the shape: in this study the checkpoint fell
back at a restore, before the sparse path engaged on that request, and the
sparse path is what stopped it recovering.

If it keeps climbing, the phenomenon does not occur in that stack and I
would like to know what is different.
