# EXP-002 — Limitations

## The platform is one machine

Apple silicon, one vendor, one runtime, 64 GB of unified memory, two models.
Nothing here transfers to another accelerator, another serving stack, or another
draft-head implementation without being measured again. The cost ratio in
FINDINGS §2 is a property of a particular model on a particular kernel set; the
method of computing it is what generalizes, not the number.

## Two models is not an architecture result

The experiment reports a mixture-of-experts model losing and a dense model
winning, and offers an explanation in terms of what widening a forward pass costs
each of them. That explanation is labelled **inferred** and it is supported by
exactly two data points. A third model of either kind could break it. Nothing was
run that isolates expert routing from kernel shape.

## The prompts are synthetic, and less predictable than the real thing

Prompts come from the repository's generator: assembled function definitions and
assembled prose, at a target token count. On generated code the measured
acceptance is about 56%. The observational dataset for the same model on real
coding-agent traffic has a median of 88.6%. That gap is large, it is in the
direction that matters, and at 88.6% the model A code cells would sit near or
above their break-even of 71%.

So the model A losses in FINDINGS §1 should be read as: at the acceptance this
synthetic content produces, fixed depth 3 loses. Whether real agent code on the
same model clears its break-even was not measured here, and the observational
acceptance figure cannot settle it, because it comes from a different content
distribution and carries no matched dense arm of its own.

The copy cell exists to bound this from the other side. It is deliberately the
most predictable content that can be generated without copying anything real, it
reaches 74% acceptance under the controller, and it wins.

## One concurrency level

Every run is a single request against an otherwise idle server. The runtime
shares one verify forward across a multi-row batch, which changes the arithmetic
in FINDINGS §2 substantially and in a direction this experiment cannot guess. It
also divides the backbone timer by the row count before attribution, so the
per-request component costs the cost model is built from would stop being
measurements under concurrency. Extending this to a loaded server needs a
different instrumentation story, not just more runs.

## Cold prefill in every cell

The prefix caches are cleared before every request, which makes every arm
comparable and makes every request the worst case for a mechanism that charges
at prefill. FINDINGS §4 reports the end-to-end consequence honestly and declines
to generalize it. An interactive agent turn arrives against a warm prefix where
prefill is small; that geometry was not run.

## One output length

256 tokens everywhere. The prefill-time priming cost is fixed per request and the
decode saving grows with output length, so the end-to-end crossover in
FINDINGS §4 moves with this parameter and the experiment says nothing about where
it lands. This was a deliberate cut: sweeping output length would have doubled
the matrix to re-measure an effect whose direction is already arithmetic.

## The correctness check is weak, and weakest where it matters

Model A did not run at the temperature the requests asked for, so its dense arm
is not reproducible and cannot serve as a reference. Model B is reproducible
dense and not reproducible with speculation enabled. The result is that this
experiment establishes that the mechanism changes the output and does not
establish that the change is harmless. Nothing here measured output quality, and
a token-level comparison against a reference implementation was not run.

## The cost model is arithmetic, not a fit

`predicted_speedup` is computed from the runtime's component timers and the
matched dense arm's per-token cost. It has no free parameters and is not fitted
to the measured speedups, which is why it is reported beside them. It is also not
independent evidence: it and the measurement share the same eleven cells, and
agreeing with the thing it was constructed to describe is a weaker claim than it
looks. What would test it is a prediction made before a measurement on a model
neither number came from.

## Two local runtime changes stand behind the numbers

The fixed-depth pin and the per-request telemetry are local changes to the build
that ran the experiment, described in METHODOLOGY. They are small and they are
not in the served build. A reader reproducing this on a stock build will find
that fixed depth 2 or more is not reachable and that acceptance is log-scraping
only.

## Seventeen production sequences is not a production study

The observational rows in `data/exp-002/production-mtp-sequences.csv` were read
from the served instance's log during this work. They are enough to establish
that the mechanism is active in production and roughly where it sits, and not
enough for a distribution. They have no matched dense arm, which is the same gap
that kept the research thread from being an experiment.

## The restart control covers drift, not configuration

Dense code/short was measured on both server processes and agreed within 1%. That
rules out a process-to-process timing shift between the fixed-depth and adaptive
arms. It does not rule out anything else that differs between two starts of a
server, and the model B arm has no equivalent control at all — it ran on a third
process, after the long-running instance on this machine was stopped to make room
for it.
