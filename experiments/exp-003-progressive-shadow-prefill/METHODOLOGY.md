# EXP-003 — Methodology

## The audit came first, again

EXP-002 opened by saying that nothing was measured until the mechanism had been
read out of the source of the build that would run it. The same rule produced
four facts here, and three of them changed the design before a single run.

**A sparse prefill does not merely fail to advance the checkpoint; the runtime
refuses to store it at all.** `_cleanup_finished` sets `raw_cache = None` for any
request whose `specprefill_indices` is set, which skips tensor extraction as well
as the write. EXP-001 observed a checkpoint that stopped advancing and inferred a
placeholder; on this build the refusal is explicit and upstream of the
placeholder logic.

**The safe publication boundary is a property of the model, not of the config.**
The scheduler logs `Enlarging paged cache block_size=256 to 4096 for ArraysCache
hybrid model`. Canonical state for a non-sliceable layer exists only at a block
boundary, so on this model progressive publication has a 4,096-token grain and a
recovery interrupted before its first boundary publishes nothing at all. That is a
ceiling on how much better progressive publication can be than terminal
publication, and it is known before any arm is run.

**SpecPrefill installs a RoPE wrapper on the shared model.** `sparse_prefill`
adds `_OffsetAdjustedRoPE` to every attention layer and it stays installed until
`cleanup_rope` runs after generation. A dense forward taken while it is installed
reads the sparse request's position offset. Any background dense work therefore
has a hard exclusion window, and the guard has to read the model rather than the
scheduler's bookkeeping — because the OOM retry path clears the bookkeeping
without removing the wrapper.

**A SpecPrefill turn reports `cached_tokens: 0` whatever the cache holds.** This
is why no quantity in this study is derived from a sparse turn's cache counters.

## Platform

One machine, alone. Apple silicon M4 Max, 16 CPU / 40 GPU / 64 GB unified
memory, macOS 26.6.2 arm64. The long-running local inference service on this
machine was stopped for the duration, so no other process held model weights or
issued GPU work; every earlier attempt at these runs shared the machine and is
not reported.

| | |
|---|---|
| model | `Qwen3.8-27B-oQ4e-mtp`, dense 64 layers with non-sliceable hybrid state |
| quantization | optimised 4-bit with per-layer 5-bit exceptions |
| drafter | `mlx-community/Qwen3.5-0.8B-MLX-4bit`, named by its org-qualified id |
| context | 131072 |
| cache block size | 4096, raised from 256 by the runtime for this model |
| multi-token prediction | **off** — EXP-002 established that it makes greedy output non-reproducible, and this study compares output hashes |
| sampling | greedy, `temperature 0` |
| memory guard | 45 GB |
| recovery budget window | 30 s, tumbling |
| sparse-prefill threshold | 8,192 tokens, and 1 in the always-sparse round, which is the only setting separating that round from the controls |

Every value in that table is a setting the runs were given, not a quantity they
produced. The last two are there because two results turn on them: the budget
window is why a cap below one chunk cannot bind, and the threshold is the whole
of the difference between the always-sparse arm and the control it is matched
against.

The server is a local research build of the runtime on its own loopback port
with its own base path, cache directory and log. It is never a long-running
instance.

## The four arms

| arm | foreground | background |
|---|---|---|
| Dense | dense prefill | none |
| Spec | SpecPrefill | none |
| Recovery-End | SpecPrefill | dense re-read, published only when the whole target finishes |
| PCSR | SpecPrefill | dense re-read, published at every safe boundary |

Recovery-End is the arm it would be easy to leave out and the one the design
turns on. It runs exactly the same background recovery as PCSR and differs only
in when it publishes, so the difference between the two isolates the value of
progressive publication from the value of background recovery itself. Without
it, a PCSR result cannot be distinguished from "any background recovery would
have done this", which is a claim EXP-001 already supports.

## The session

An append-only session: the instruction is fixed and first, and each turn
appends another block of generated code to the tail, so turn *N*'s prompt is a
strict token prefix of turn *N+1*'s. Getting this wrong is worth recording,
because the first version of this experiment put the instruction last. Every
restore then floored to the block below the divergence point, and the result
measured block granularity rather than the mechanism.

All arms receive the identical token sequence, the identical idle gap between
turns and the identical generation budget. The corpus is generated; nothing in
it is copied from a real prompt.

## Two measurement decisions

**Arms are separated by a server restart, not a cache clear.** The admin cache
clear deletes the SSD block files and leaves the in-memory index listing them,
so a later restore finds the block, fails to load its file, falls back to a
placeholder and is rejected — a sequence indistinguishable from the mechanism
under study. Each arm starts from a freshly started server with the cache
directory removed.

**Each arm ends with a dense probe.** The final prompt is re-sent with sparse
prefill forced off. What it restores is the longest canonical prefix the arm
actually left behind, which is the study's primary state metric, and it is the
only way to read that quantity given that a sparse turn reports zero. The probe
is an instrument: its latency is reported next to the session total, never
inside it.

## What is recorded

Per turn: prompt tokens, cached tokens, uncached suffix, time to first token,
the server's own prefill duration, decode time and throughput, output tokens,
wall time, and a hash of the completion. Per turn in the recovery job arms, from the
runtime's own counters: the committed canonical prefix, the current target,
steps on which the recovery job was runnable, scheduled and yielded, chunks executed,
publications, and the service time the recovery job actually received.

Runnable and scheduled are recorded separately on purpose. "The recovery job got no
service" and "the recovery job got service and recovery was too slow" are different
results, and a single wall-clock number cannot tell them apart.

Primary state metric:

    canonical_debt = prompt_tokens - longest_committed_canonical_prefix

Primary outcome: cumulative foreground session latency.

## The route is read, not derived

Which prefill path a turn took used to be inferred from its latency. That is
the same mistake as reading a cache hit off a stopwatch: it is right until the
thing being measured changes it, and the whole point of this experiment is to
change it.

The runtime now records the route at its own admission. The sparse-prefill
policy accepts a request when the uncached suffix exceeds a threshold, so the
decision is entirely `(tail, threshold)`, and both are known at the moment the
policy runs — after the prefix-cache restore, which is what makes the tail the
post-restore figure rather than the whole prompt. The record carries the route
taken, the tail it was decided on, the restored prefix, and the threshold in
force for that request, which may be a per-request override or the build
default. The analysis prefers the recorded route to the one it can derive,
says which it used, and flags a disagreement rather than choosing a winner: a
disagreement means the two definitions have drifted, and that is worth an
error rather than a silent correction.

The restored prefix is taken from the same record for the same reason. A
sparse turn reports zero cached tokens on its usage object whatever the cache
held, so the usage figure understates reuse on exactly the turns this
experiment is about.

## Spec Exit

The outcome this experiment is built around is not canonical coverage. It is
whether the session leaves the sparse route for good:

    spec_exit_turn = the first turn whose route is not SpecPrefill, and after
                     which no turn's is either

It is empty when there is no such turn, and empty for the whole arm when any
turn's route cannot be decided — an undecided turn could have been the sparse
one the exit had to come after.

## Why the session has a head and small appends

One thing about the shape is forced, and saying why matters more than the
shape itself. In a strictly appending session, once canonical debt is fully
repaid the uncached tail is one turn's growth plus whatever of the last cache
block the previous prompt left unfilled. Canonical state is published at
4,096-token boundaries, so that remainder is up to 4,095 tokens. A session
that grows by more than `threshold - 4095` a turn can therefore never leave
the sparse route, however fast the recovery runs, and measuring one would be
measuring the block size.

So the session opens with a head well above the threshold — that is the debt
the recovery exists to repay — and appends below it thereafter. Two outcomes
are then distinguishable, which is the whole design:

    no recovery      tail = head + growth so far, and rises      -> Spec forever
    enough recovery  tail collapses to one turn's growth         -> Spec Exit

The threshold used is the build's own default of 8,192 rather than the 4,096
the earlier arms in this experiment set, because 4,096 is below the block
remainder and would admit the sparse route on the remainder alone.

## The recovery budget replenishes

The budget is a ceiling on the share of wall time the recovery job may
receive, granted per tumbling window rather than over the job's whole life.
Unused allowance is discarded at each roll, so nothing accrues; a chunk that
overruns is charged to the next window, so the cap holds across windows; and
that carried debt is capped at one allowance, so a single overrun costs at
most one window and never a lockout for the rest of the session. The earlier
lifetime accounting did exactly that, and §5 of the findings records it.

Two shares are reported, not one. The share of wall time is what the budget
caps. The share of *idle* time — the wall time a live job was actually offered
— is what says whether the budget or the workload was the limit, and a single
number cannot tell those apart.

## What this design cannot answer

The session is synthetic and its idle gaps are a parameter, not an observation.
EXP-001 measured that real coding-agent turns leave little or no idle, so a
result at a generous idle gap describes a regime this workload may not reach.
Any arm that depends on idle time is reported with the gap it was given.
