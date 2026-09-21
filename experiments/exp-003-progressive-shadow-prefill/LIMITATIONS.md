# EXP-003 — Limitations

These are the reasons a reader should not take a number here further than it
goes. They are listed before the findings on purpose, because two of them bound
the result rather than qualify it.

## The publication grain is 4,096 tokens on this model

Canonical state for a non-sliceable layer exists only at a cache block boundary,
and the runtime raises the block size from 256 to 4,096 for this model's hybrid
cache. Progressive publication therefore cannot commit anything until a recovery job
job has densely read 4,096 tokens, and a job interrupted before that publishes
nothing at all.

This is a ceiling on the size of the effect progressive publication can have,
and it is a property of one model's cache layout rather than of the design. A
model whose state were sliceable, or whose block size stayed at 256, would give
the same design sixteen times as many commit points. Nothing here measures such
a model, so the size of the effect is not transferable.

## Spec Exit is bounded by the geometry before it is bounded by recovery

Once canonical debt is repaid, the uncached tail is one turn's growth plus
whatever of the last cache block the previous prompt left unfilled — up to
4,095 tokens on this model. A session growing by more than
`threshold - 4095` a turn cannot leave the sparse route at any recovery rate,
and a session growing by much less than that leaves it at almost any rate.
The interesting range is narrow and it is a property of the arithmetic, not
of the mechanism. Every Spec Exit result here states the growth per turn and
the threshold in force, and neither generalizes without both.

## Two defects bound what the earlier recovery-rate numbers measure

The recovery job carried two defects until they were found on 2026-09-21. Its
target was floored to a cache block boundary, but the prefill path holds the
final token of a range back for the generation kickoff, so every job stopped
one token short of its boundary and published the block below it. And a job
that had reached its target stayed runnable, so it rebuilt its state and
re-read its whole target on every later idle window, publishing nothing and
charging all of it to the recovery budget.

Every recovery-rate and catch-up-ratio number measured before that date is a
measurement of that build. They are kept, because they are real measurements
and because the second defect is the reason the budget sweep found that more
budget bought no more progress. They are not measurements of the mechanism's
rate, and the findings say so where they appear.

## One machine, one model, one runtime

Apple silicon, unified memory, one inference server. EXP-001 and EXP-002 carry
the same limitation and it has not improved. The mechanism arguments are about
this runtime's cache and scheduler; the numbers are about this machine.

## The idle gap is a parameter, not an observation

The session is synthetic and the gap between turns is set by the runner. EXP-001
measured that real coding-agent turns leave little or no idle, and its
zero-idle row is where background recovery lost. Any result here that depends on
the recovery job receiving service is a result about the gap it was given, and the
gap is stated wherever such a result is.

## Repeats

Single run per arm unless a cell says otherwise. That is enough to establish a
mechanism and not enough to state an effect size. Where two arms are close, the
study says they are close rather than ranking them.

## What the dense probe does and does not measure

The probe re-sends the session's final prompt with sparse prefill forced off, so
what it restores is what the ordinary serving path can actually restore. That is
the claim it exists for, and it is a different claim from how much was published:
a publication counter says what the recovery job believes it committed, and the
probe says what a request gets back.

It was also, when this study began, the only way to read the canonical prefix at
all, because a sparse turn was read as reporting no cache hit whatever the cache
held. It is no longer the only way — the runtime records the post-restore prefix
at admission and the later rounds read it from every turn — and
[METHODOLOGY.md](METHODOLOGY.md) says what that changed and what it did not.

What the probe is not is a measurement of what the next real turn would have
cost. It is forced dense, and a real next turn in the sparse arms would not be.

## The output-hash comparison is narrow

Completions are 24 greedy tokens with multi-token prediction off. Two arms
agreeing on that hash establishes that they produced the same short completion
from the same state, not that they are semantically equivalent over a long
generation. EXP-002 left exactly this question open for speculative decoding and
this study does not close it either.

## SpecPrefill is not output-preserving, and that is not the recovery job's doing

The sparse arm's completions differ from the dense arm's on some turns. This is
expected of a mechanism that drops roughly 80% of the conversation tokens, it is
present in the Spec arm which runs no recovery job, and no output difference in
this study is attributed to the recovery job without that control agreeing.

## The correctness argument is structural, not exhaustive

The recovery job's published state is what a dense prefill of the same range produces,
because that is literally how it is produced, and the existing store/restore
round-trip was shown output-identical in EXP-001 across seven paired cases.
What this study adds is the guards that keep the recovery job from publishing anything
else: the RoPE exclusion, the boundary alignment check, the unsupported-cache
re-check and the store-result check. Each is a test. None of them is a proof,
and the prefill partition's own nondeterminism on recurrent hybrids — measured
elsewhere on this model, three distinct partitions and two distinct replies from
four runs of one prompt — is a source of variation this study inherits and does
not control.
