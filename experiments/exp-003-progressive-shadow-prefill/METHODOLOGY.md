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

## What this design cannot answer

The session is synthetic and its idle gaps are a parameter, not an observation.
EXP-001 measured that real coding-agent turns leave little or no idle, so a
result at a generous idle gap describes a regime this workload may not reach.
Any arm that depends on idle time is reported with the gap it was given.
