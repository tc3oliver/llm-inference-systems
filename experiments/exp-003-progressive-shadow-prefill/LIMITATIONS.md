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

The probe reads the longest canonical prefix an arm left behind, because a
SpecPrefill turn reports `cached_tokens: 0` whatever the cache holds. It is a
faithful measurement of that quantity and it is not a measurement of what the
next real turn would have cost: it is forced dense, and a real next turn in the
sparse arms would not be.

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
