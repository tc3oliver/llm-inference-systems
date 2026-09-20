# Findings

The design works and does not pay. A scheduler-owned dense re-read of a range a
sparse prefill already served can be made to run safely, receives ample service
when the machine is idle, and reads the whole range back at full speed — and at
the end of the session the reusable prefix is still zero, because what it
publishes is not accepted at the next restore.

**Status: PARTIAL — CANONICAL PUBLICATION VALIDATED; SERVING-CACHE BINDING BUG IDENTIFIED.** Canonical publication
itself is established as working. What is broken is narrower than the
architecture and narrower than the cache contract:

- canonical publication at the 4,096-token boundary **succeeds**;
- the KV block and the GDN sidecar are **both written correctly**;
- a restore against the publishing cache **recovers 4,096 tokens**, GDN
  endpoint hit, zero walkback, no placeholder layers;
- the foreground serving cache **cannot see that state**;
- the cause is that the recovery job publishes into a *different*
  `BlockAwarePrefixCache` instance from the one that served the request.

So this is neither a PCSR architecture failure nor a hybrid/GDN
canonicalization failure. It is a binding bug between the recovery job and the
cache instance that serves restores.

The scheduler, the budget and the recovery throughput are all doing their job,
and each of those is ruled out by a number below rather than by an argument.
The open question is confined to the publication and restore path, and until
one progressive canonical boundary can be restored by the ordinary path, no
performance question about this design has been answered either way.

---

## 1. What a sparse turn actually costs, on this build

| turn | prompt | dense cached | dense TTFT | spec cached | spec TTFT |
|---:|---:|---:|---:|---:|---:|
| 0 | 8,275 | 0 | 33.95 s | 0 | 8.73 s |
| 1 | 16,409 | 8,192 | 35.39 s | 0 | 15.76 s |
| 2 | 24,584 | 16,384 | 37.66 s | 0 | 23.44 s |

The dense arm's checkpoint climbs and its uncached suffix stays flat near 8,200
tokens, so its per-turn cost is flat: 33.95 → 37.66 s across a prompt that
tripled. The sparse arm restores nothing on any turn and recomputes the whole
prompt every time, so its cost is linear in the conversation: 8.73 → 15.76 →
23.44 s.

At this size sparse still wins the session — 47.93 s against 107.01 s — and the
two series are converging. EXP-001 described a checkpoint that *stopped*
advancing; on this build a sparse turn stores nothing at all, so the debt is not
merely frozen, it is the entire prompt and it grows with it.

`data/exp-003/session-turns.csv`. **Evidence level:** measured, one run per arm,
identical token sequences, level 3.

## 2. The four arms

| arm | session | canonical prefix | canonical debt | probe TTFT |
|---|---:|---:|---:|---:|
| Dense | 107.01 s | 24,576 | 8 | 1.46 s |
| Spec | 47.93 s | 0 | 24,584 | 103.50 s |
| Recovery-End | 48.00 s | 0 | 24,584 | 116.07 s |
| PCSR | 48.00 s | 0 | 24,584 | 115.78 s |

The canonical prefix is read by a dense probe — the final prompt re-sent with
sparse prefill forced off — because a SpecPrefill turn reports `cached_tokens: 0`
whatever the cache holds. In the dense arm the probe restores 24,576 of 24,584
tokens and answers in 1.46 s. In all three sparse arms it restores nothing and
pays 103–116 s.

Two things follow, and the second is the point of having four arms.

**The recovery job is free and worthless.** 48.00 s against Spec's 47.93 s is a 0.15%
difference on a single run and is not a measurable foreground cost; the QoS
constraint of "under 5% TTFT regression" is satisfied by a mechanism that
achieves nothing. Both recovery arms recover exactly zero.

**Recovery-End and PCSR reach the same place by different routes, and the
difference between the routes is the finding.** They differ only in when they
publish. PCSR published five times and advanced its committed prefix
0 → 4,096 → 12,288 → 20,480; Recovery-End published once and stopped at 4,096. By
the runtime's own accounting PCSR recovered five times as much. The probe
restored **zero in both**.

So progressive publication does exactly what it was built to do at the
publication layer, and buys nothing at the layer that matters. The two arms are
identical in every quantity a user would feel — 48.00 s each, zero canonical
prefix each — while differing by a factor of five in the number the runtime
reports. That disagreement between the internal counter and the probe is the
sharpest single piece of evidence in this study, and it is only visible because
two instruments measured the same thing from opposite ends.

Had the experiment run PCSR alone against Spec, the flat result would have been
read as "background recovery does not work here", which is the conclusion
EXP-001 already supports and which this data does not support.

`data/exp-003/arm-summary.csv`. **Evidence level:** measured, one run per arm,
level 3.

## 3. Where the time went, which is not where it was expected to go

The runtime's own counters, per arm, at the end of the session:

| | Recovery-End | PCSR |
|---|---:|---:|
| steps the recovery job was runnable | 32 | 32 |
| steps it was scheduled | 14 | 14 |
| chunks executed | 14 | 14 |
| service received | 231.75 s | 231.74 s |
| share of wall time | 53.2% | 53.2% |
| tokens densely read | 24,575 of 24,576 | 24,575 of 24,576 |
| publications | 1 | 5 |
| canonical committed tokens, by the runtime's own count | 4,096 | 20,480 |
| canonical prefix the probe could restore | 0 | 0 |

The recovery job read the entire target. It was never starved: it was runnable on 32
steps, scheduled on 14 of them, and took 53% of wall-clock time across the
session's idle gaps. Recovery throughput was not the limit either — it finished.

So the three explanations the experiment was built to separate resolve cleanly:

- **recovery got no service** — refuted, 231.75 s;
- **recovery too slow** — refuted, it read 24,575 of 24,576 tokens;
- **foreground contention** — refuted, 48.00 s against 47.93 s.

What is left is publication, and that is where the failure is.

**Evidence level:** observed, from the runtime's own per-request counters.

## 4. The mechanism: a published block is rejected at the next restore

Traced in the server's own log across the PCSR arm. Publication progresses
exactly as designed — 4,096 → 8,192 → 12,288 → 16,384 → 20,480 tokens, with the
store reporting the full amount each time — and every subsequent restore ends:

    Partial cache reconstruction: 1/5 blocks, 4096 tokens
    ArraysCache layer 0: partial prefix match detected (placeholder in last
    matched block). Rejecting cache to prevent stale GDN state. Request will
    reprocess from scratch.

This is the same rejection EXP-001 found at its cliff, now reached from the
other direction: not because a sparse prefill left a placeholder, but because
what the recovery job published is read back as one.

A diagnostic added to the publish path rules out the obvious explanation. At the
moment of publication the boundary snapshot exists and is used:

    Shadow: publishing 4096 tokens — override=True, captured boundaries=[4096],
    snapshot_need=True

So the state is captured, the boundary-aligned payload is assembled from it, and
the store reports the tokens persisted. The block is nevertheless reconstructed
as a placeholder. **Whether the snapshot is not written into the block, or is
written and not recognised on the restore path, is not established here** — no
instrumentation in this study reaches inside `store_cache` per block. That is
the next probe, and it is a small one.

**Evidence level:** the publication sequence and the rejection are observed in
the runtime's log across every turn of two arms. The cause inside the store is
**not established**.

## 4b. Where the two ends diverge: the recovery job publishes into a second prefix cache

A structured trace of one PCSR run, keyed on block hash across the publication
boundary, settles it. The relevant records, in causal order:

| seq | event | cache | split-GDN | tokens |
|---:|---|---:|---|---:|
| 0 | foreground store, request `a7645705…` | `…888976` | **off** | 8,275 |
| 1 | recovery job queued for that same request | `…673744` | **on** | 8,192 |
| 2 | recovery store | `…673744` | on | 4,096 |
| 3 | sidecar checkpoint committed | `…673744` | on | `committed=true`, `has_checkpoint_now=true` |
| 5 | foreground restore | `…888976` | off | rejects, 18 placeholder layers |
| 18 | restore on the other cache | `…673744` | on | **hit** |
| 19 | `restore.gdn.hit` | `…673744` | on | endpoint 4,096, walkback 0 |

Two facts, and the second is the one that changes the conclusion.

**There are two `BlockAwarePrefixCache` instances for one served model**, with
two `PagedSSDCacheManager`s and, independently, two different values of
`gdn_ssd_split_enabled`. The originating request's foreground store and restore
use one; `_cleanup_finished`, the recovery job and its publication all run on
the other.

**The published canonical state is correct and restorable — by the cache it was
published into.** Record 19 is a positive control that was not planned: a
restore on cache `…673744` found the sidecar, loaded it with zero walkback, and
the subsequent placeholder scan reported no problematic layers at all. The
split-GDN contract is honoured end to end: placeholder in the block, real
recurrent state in the sidecar, retrieved by block hash under the matching
signature.

So the publication is not broken. It lands in a cache that no foreground
request reads.

That reclassifies every candidate cause. The snapshot reaches `store_cache`; it
is persisted; it is committed under a hash and signature that its own restore
path resolves; the index exposes it; the restore validates and loads it. What
fails is the choice of publication target, and the first point at which the
information is lost is earlier than any of them: when the recovery job resolves
`self.block_aware_cache`, it resolves the wrong instance.

`canonical committed tokens` then counts a publication that is real, valid and
invisible — which is why the counter and the probe disagreed, and why the
counter must not be read as evidence of canonical publication until the
ordinary restore path can consume the boundary.

**Evidence level:** observed, one instrumented run, store and restore records
matched on block hash.

## 4c. After the binding fix: the invariant holds, the restore still does not

Two corrections to §4b, both from a later instrumented run. They matter because
§4b's conclusion was partly wrong.

**The second cache is the SpecPrefill draft model's, not a second serving
cache.** `_draft_prefix_cache` is built for the drafter with no
`gdn_ssd_split_enabled` argument, so it defaults to embedded while the target's
cache is split. The two ids in §4b were the target cache and the drafter's, and
the placeholder rejection observed there belongs to the drafter's own prefix,
not the target's. "Two serving caches for one model" was an over-reading.

**The recovery job's binding was still wrong, for a different reason**, and is
now fixed: the job resolves the instance that served the originating request,
carries it for its whole life, re-checks it before publishing, and fails closed.
The counter stopped being a store-side claim — it advances only after a
read-back through the serving cache's own `fetch_cache` confirms the boundary.

With that in place, the invariant holds exactly:

| boundary | store reported | independently restorable |
|---:|---:|---:|
| 4,096 | 4,096 | 4,096 |
| 8,192 | 8,192 | 8,192 |
| 12,288 | 12,288 | 12,288 |

And the ordinary serving path does find the state. The dense probe's own
admission lookup matched **12,288 tokens across 3 blocks**, with 4,121
remaining, on the serving cache.

**It still did not restore.** The probe's time to first token was 67.34 s for
16,409 tokens. A cold dense prefill of 8,275 tokens on this configuration costs
33.95 s, and the dense arm answers a 16,409-token prompt with 8,192 tokens
cached in 35.39 s. 67.34 s is cold. The request matched 12,288 tokens and then
re-prefilled all 16,409, and reported `cached_tokens: 0`.

So the divergence has moved one step later in the same path, and is now
between:

    fetch_cache matches 12,288 tokens across 3 blocks
    the request runs with none of them

Nothing between those two points is instrumented yet, and no explanation is
offered here. The leading suspect is a consequence of this experiment's own
`retain_request_entry`: blocks kept registered to a live recovery job are never
released for eviction, and a reconstruct for a *different* request may not be
able to load a payload that was never queued for its durable write. That is a
hypothesis with a named probe, not a finding.

**Evidence level:** the match is observed in the runtime's own lookup; the
failure to deliver is derived from latency against two measured references on
the same configuration. The cause is **not established**.

## 5. The safe boundary is 4,096 tokens, and that bounds the design

The runtime raises this model's cache block size from 256 to 4,096 for its
hybrid cache, and canonical state for a non-sliceable layer exists only at a
block boundary. Progressive publication therefore has a 4,096-token grain: a job
interrupted before its first boundary publishes nothing at all, and the
advantage it can hold over terminal publication is bounded by how often a
4,096-token boundary falls inside an interruption.

This was known before any arm ran, from the source and one log line, and it is
the reason the PCSR-versus-Recovery-End gap was never going to be large on this
model. It says nothing about a model whose state is sliceable, or one that keeps
a 256-token block, which would give the same design sixteen times as many commit
points. Nothing here measures such a model.

**Evidence level:** observed configuration, derived consequence.

## 6. Five defects the implementation had to survive first

None of these is a finding about canonical state recovery; they are recorded because the
result above only means something if the mechanism it measures was real.

The engine loop stops calling `step()` when idle, so a background task allowed to
run only when idle becomes eligible at exactly the moment nothing will run it —
reporting live recovery work in `has_requests()` fixes that, and then immediately
breaks it, because the recovery job's own job then counts as the foreground work that
resets its idle counter. A publish reported success while the store wrote zero
tokens. The memory throttle's pause was treated as a failure and threw away a
12,288-token prefix. A live job made the model impossible to unload, and every
later request to it was refused with 409.

Each is now a condition with a test rather than a comment. The full list, with
what each one cost, is in `../../ENGINEERING.md`.

## 7. One bug found on the way out, unrelated to any of this

`_requeue_or_fail_prefill` clears `_specprefill_active_request_id` under a
comment saying it is clearing the SpecPrefill RoPE patch. It is not: the patch
is installed on the shared model and only `cleanup_rope` removes it, and once
the id is clear the normal cleanup will not run either. After a SpecPrefill
prefill hits the memory ceiling and is requeued, the model keeps that request's
position offset installed, and the retry and every later request on that engine
decode through it.

Found because a background dense pass taken through a stale offset would produce
positionally wrong KV and publish it as ordinary reusable state, so the guard
had to read the model rather than the bookkeeping. The fix is one call and it is
correct independently of anything in this experiment.

**Evidence level:** a code defect with a test. No run here reached the
OOM-requeue path, so no downstream effect is observed and none is claimed.

---

## Status of each claim

| | |
|---|---|
| A sparse turn stores nothing on this build | **established** — 0 cached tokens on every sparse turn, three arms |
| The recovery job is not starved and recovery is not too slow | **established** — 231.75 s of service, 24,575 of 24,576 tokens read |
| The recovery job costs the foreground nothing at this idle gap | **established for this gap** — 48.00 s against 47.93 s, one run |
| Progressive publication advances the committed prefix as designed | **established** — 20,480 against 4,096 |
| And is still not the binding constraint | **established** — both arms restore 0, both finish at 48.00 s |
| The published block is rejected at restore | **observed** — every restore, both recovery arms |
| Why the block is rejected inside the store | **not established** |
| Whether a fixed publication would repay the debt | **not established** — it has not been made to work |
| Any result at zero idle, or on a real agent workload | **not established** — not run |
