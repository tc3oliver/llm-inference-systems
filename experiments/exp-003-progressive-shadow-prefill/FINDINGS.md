# Findings

Progressive canonical state recovery works. A dense re-read of the range a
sparse prefill skipped, owned by the scheduler and run only while the engine is
idle, publishes canonical state that the ordinary serving path restores, gives
byte-identical output, and cuts session latency rather than costing it.

Getting there took three publication defects, and the most useful result in the
study is the one that separates progressive from terminal publication — which
was invisible until the third was fixed.

---

## 1. What a sparse turn costs, on this build

| turn | prompt | dense cached | dense TTFT | spec cached | spec TTFT |
|---:|---:|---:|---:|---:|---:|
| 0 | 8,275 | 0 | 33.95 s | 0 | 8.73 s |
| 1 | 16,409 | 8,192 | 35.39 s | 0 | 15.76 s |
| 2 | 24,584 | 16,384 | 37.66 s | 0 | 23.44 s |

The dense arm's checkpoint climbs and its uncached suffix stays flat near 8,200
tokens, so its per-turn cost is flat. The sparse arm restores nothing and
recomputes the whole prompt every turn, so its cost is linear in the
conversation. EXP-001 described a checkpoint that *stopped* advancing; on this
build a sparse turn stores nothing at all, so the debt is the entire prompt.

**Evidence level:** measured, one run per arm, identical token sequences.

## 2. Three defects between a correct design and a working one

Each was found by tracing the store and restore ends of one publication and
matching them on block hash, and each looked like a different failure than it
was.

**The recovery job published into a prefix cache the request never used.** One
scheduler can hold a different `BlockAwarePrefixCache` from the one that served
a request. A job now binds to the instance that served its originating request,
carries that identity for its whole life, re-checks it before publishing, and
fails closed.

**The counter advanced on a store-side claim.** `store_cache` reporting success
is not evidence that anything is restorable. The counter now advances only after
a read-back through the serving cache's own `fetch_cache` confirms the boundary,
enforcing

    canonical_committed_tokens <= independently_restorable_tokens

**And the published block was stamped with the wrong layer count** — the one
that actually blocked delivery. `_get_boundary_store_override` returns the
boundary snapshot as its payload, and that snapshot holds the non-sliceable
layers alone: 48 of this model's 64. Storing it as `cache_data` stamps the block
`num_layers: 48`, a later restore compares that with 64, reads it as
cross-model contamination and discards the whole chain. Publication now stores
the live cache, which the alignment check has already established sits exactly
on the boundary, with the snapshot provider passed separately as
`boundary_snapshots` so the split-GDN sidecar path is untouched.

The symptom of the third was a lookup that matched and a request that ran cold:

| stage | before | after |
|---|---|---|
| `fetch_cache` match | 12,288 tokens, 3 blocks | 12,288 tokens, 3 blocks |
| `reconstruct_cache` | **None**, 0 blocks, 0.57 ms | 64 layers, 12,288 tokens, 27.7 ms |
| attached to the request | `cached_tokens 0`, 16,409 remaining | `cached_tokens 12,288`, 4,121 remaining |
| time to first token | 67.34 s | **18.72 s** |

67.34 s is a cold prefill of 16,409 tokens on this configuration; 18.72 s is the
partial-cache path. The completion hash is `ea4e50ce6feb7ca7`, identical to the
dense reference.

**Evidence level:** the match, the reconstruct and the attachment are observed
in the runtime's own records; the cold/warm judgement is derived against two
measured references on the same configuration.

## 3. Progressive publication is worth five times terminal publication

The control the design turns on. Both arms run the same background recovery and
differ only in when they publish. The session gives each turn 35 s of idle,
which is less than one recovery target takes, so every recovery is interrupted —
which is the condition the two modes are supposed to disagree under.

| | Recovery-End | PCSR |
|---|---:|---:|
| publications | 1 | 5 |
| canonical prefix left behind | 4,096 | **20,480** |
| canonical debt | 20,488 | **4,104** |
| recovery service received | **146.9 s** | 133.3 s |
| cumulative foreground latency | 68.05 s | **62.54 s** |
| probe time to first token | 102.95 s | **19.93 s** |

Recovery-End received *more* compute and kept one fifth as much of it. Its one
publication is turn 0's target completing; every later target is extended before
it finishes, so it never publishes again and the work is thrown away each time.
PCSR commits at every safe boundary, so an interrupted recovery is worth the
prefix it reached.

This refutes what an earlier round of this experiment concluded. That round
found the two arms identical and said progressive publication was not the
binding constraint. It was measured while publication was broken for both arms,
and it is withdrawn.

`data/exp-003/progressive-control-*.csv`. **Evidence level:** measured, one run
per arm, identical token sequences and idle gaps.

## 4. The recovery budget: 5% is enough, and costs nothing

| budget | service received | share of wall time | session | canonical prefix | probe TTFT |
|---:|---:|---:|---:|---:|---:|
| 0% | 0.0 s | 0.0% | 52.40 s | 0 | 105.13 s |
| 5% | 15.7 s | 6.5% | 45.80 s | 4,096 | 89.44 s |
| 10% | 15.7 s | 6.5% | 45.75 s | 4,096 | 89.47 s |
| 20% | 32.1 s | 13.3% | 45.60 s | 4,096 | 89.46 s |

Three things worth reading off it.

**There is no foreground regression to trade against.** Per-turn TTFT at 0% is
10.22 / 17.23 / 24.96 s; at 5% it is 10.24 / 13.86 / 21.70 s. Turn 0 is
unchanged — nothing has been published yet — and every later turn is *faster*.
The QoS constraint of under 5% regression is satisfied in the sense that the
regression is negative. The decode-throughput constraint is **not established**:
the runtime reported no decode-rate sample at these output lengths, and the
column is empty rather than filled.

**The ceiling is not exact.** 5% requested, 6.5% received. It is enforced
between chunks and a chunk cannot be interrupted, so a cell can overshoot by
part of one chunk. Reported as measured rather than as the setting.

**Above 5% buys service but not progress.** 20% received twice the compute of
5% and committed exactly the same 4,096 tokens. In this configuration the
recovery is limited by the idle window and the 4,096-token publication grain,
not by the budget, so 5% is the smallest budget that reaches the achievable
recovery rate.

`data/exp-003/budget-sweep.csv`. **Evidence level:** measured, one run per cell.

## 5. Correctness

The dense probe, restoring canonical state that PCSR published, produced
`ea4e50ce6feb7ca7` — byte-identical to the dense reference on the same prompt.
Restoring recovered state does not change the answer.

Sparse turns produce a different completion from dense ones. That is
SpecPrefill's own behaviour, it is present in the Spec arm which runs no
recovery job, and no output difference in this study is attributed to PCSR
without that control agreeing.

The split-GDN path is exercised and unchanged: every published block stores a
structural ArraysCache placeholder with its recurrent state in a sidecar, and
restores resolve the sidecar with zero walkback and no placeholder layers at the
committed endpoint.

## 6. One bug found on the way out, unrelated to any of this

`_requeue_or_fail_prefill` clears `_specprefill_active_request_id` under a
comment saying it is clearing the SpecPrefill RoPE patch. It is not: the patch
lives on the shared model and only `cleanup_rope` removes it, and once the id is
clear the normal cleanup will not run either. After a SpecPrefill prefill hits
the memory ceiling and is requeued, the model keeps that request's position
offset installed for the retry and every later request on that engine.

**Evidence level:** a code defect with a test. No run here reached the
OOM-requeue path, so no downstream effect is observed and none is claimed.

---

## Status of each claim

| | |
|---|---|
| A sparse turn leaves zero canonical state | **established** |
| PCSR-published state is restorable by the ordinary serving path | **established** — match, reconstruct, attach and TTFT all agree |
| Restoring it does not change the output | **established for this comparison** — byte-identical, 64-token greedy completion |
| Progressive publication beats terminal publication | **established** — 20,480 against 4,096, on more compute for the loser |
| A 5% recovery budget costs the foreground nothing | **established for this workload and idle gap** |
| Decode-throughput regression under 5% | **not established** — no decode-rate sample at these output lengths |
| Behaviour at zero idle, or on a real agent workload | **not established** — not run |
