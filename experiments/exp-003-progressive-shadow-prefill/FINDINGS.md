# Findings

Progressive canonical state recovery works. A dense re-read of the range a
sparse prefill skipped, owned by the scheduler and run only while the engine is
idle, publishes canonical state that the ordinary serving path restores, gives
byte-identical output, and cuts session latency rather than costing it.

Getting there took three publication defects, and the most useful result in the
study is the one that separates progressive from terminal publication — which
was invisible until the third was fixed.

Two further defects in the recovery job were found after every run below, on
2026-09-21, and fixed in the runtime. A job could not reach its own target: it
targeted the last whole cache block, and the prefill path holds the final token
of a range back for the generation kickoff, so the job stopped one token under
the boundary and its publication floored to the block beneath. And a job that
had reached its target stayed runnable, so on every later idle window it
rebuilt its state, restored its committed prefix, re-read its whole target,
published nothing and charged all of it to the recovery budget. Every
recovery-rate, budget and catch-up number here was measured before that fix.
They are measurements of that build and they are kept as such; where a section
drew a conclusion about the mechanism's own rate from them, it now says the
rate is not established. [LIMITATIONS.md](LIMITATIONS.md) carries the same
statement once for the whole study.

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

## 3. Progressive publication survives interruption and terminal publication does not

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

Both arms ran the pre-fix build. Recovery-End's single publication is turn 0's
target completing, and it committed 4,096 tokens of an 8,192-token target
because a job could not reach its boundary. Recovery-End is also the only arm
here that completed a job, and a completed job stayed runnable, so its extra
service is what the second defect produces rather than a property of terminal
publication. What does not depend on either defect is the direction: a terminal
publisher that is interrupted commits nothing, and that follows from when it
commits rather than from what these two runs measured. The factor of five and
the service comparison do not follow from it. **Not established:** how much
more progressive publication leaves behind on the fixed build.

This refutes what an earlier round of this experiment concluded. That round
found the two arms identical and said progressive publication was not the
binding constraint. It was measured while publication was broken for both arms,
and it is withdrawn.

`data/exp-003/progressive-control-*.csv`. **Evidence level:** measured, one run
per arm on the pre-fix build, identical token sequences and idle gaps; the
direction is a property of when each mode commits and does not rest on the
measurement.

## 4. The recovery budget costs the foreground nothing

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
column is empty rather than filled. The two recovery-job defects do not flatter
this reading. The pre-fix build did strictly more recovery work and published
strictly less canonical state than the fixed one would, so the foreground was
tested against more contention for less benefit. What the fixed build costs the
foreground is not measured here.

**The ceiling is not exact.** 5% requested, 6.5% received. It is enforced
between chunks and a chunk cannot be interrupted, so a cell can overshoot by
part of one chunk. The 5% and 10% cells received the same 15.70 s to the
microsecond — one chunk each — so neither ceiling bound its cell. Reported as
measured rather than as the setting.

**Raising the budget bought service and no more progress, and on this build
that is what a defect predicts.** 20% received 32.1 s against 5%'s 15.7 s — two
chunks against one — and committed exactly the same 4,096 tokens on the same
single publication. A job that could not reach its target and then re-read the
whole of it on the next window produces that: a second chunk of compute and no
second commit. The earlier reading of this row — that the recovery was limited
by the idle window and the 4,096-token publication grain rather than by the
budget, so 5% is the smallest budget that reaches the achievable rate — is
withdrawn. Whether 5% is enough is **not established**.

`data/exp-003/budget-sweep.csv`. **Evidence level:** measured, one run per cell
on the pre-fix build. That the second chunk committed nothing because the job
was re-reading its own work is inferred: it is what the two defects produce and
no cell here isolates it.

## 5. At 16K a turn, recovery on this build ran at half the speed context grew

The controlled five-turn session, appending ~16K a turn to 81,610 tokens, 35 s
of idle, one run per arm.

| arm | cumulative foreground | canonical prefix | canonical debt |
|---|---:|---:|---:|
| Dense | 502.02 s | 77,824 | 3,786 |
| Spec | 249.24 s | 36,864 | 44,746 |
| Recovery-End | 249.34 s | 36,864 | 44,746 |
| PCSR, 5% budget | **236.60 s** | 36,864 | 44,746 |
| PCSR, no budget cap | 241.28 s | **45,056** | 36,554 |

Recovery-End is Spec to within 0.1 s and to the token: across five turns it
never completed a target, so it never published, and every second of recovery
it was given was thrown away. That is the §3 result again at four times the
scale.

**The 5% cell exposes a defect in the budget, not in the design.** Its
cumulative service counter reads 15.70 s at turn 1 and the same 15.70 s at
turns 2, 3 and 4: the job was served once and never again. `ShadowBudget`
measures service as a share of wall time *since the scheduler started*, so a
job that overshoots early — and it does, because the ceiling is enforced
between uninterruptible chunks — is over its ceiling for the rest of the
session and is locked out. A per-window or decaying budget would not behave
this way. This is worth stating plainly because the 5% cell is otherwise the
fastest arm in the table, and it is fastest while its recovery is switched off
by accident. This one is not a defect artifact. The counter shows a single
chunk and a single publication, so the 15.70 s that put the job over its
ceiling is one uninterruptible chunk of real recovery and not a re-read.

**Uncapped, the recovery advances steadily and still loses the race:**

| turn | prompt | committed | Δ committed | Δ context | catch-up ratio | debt |
|---:|---:|---:|---:|---:|---:|---:|
| 0 | 16,428 | 0 | 0 | 16,428 | 0.00 | 16,428 |
| 1 | 32,722 | 12,288 | 12,288 | 16,294 | 0.75 | 20,434 |
| 2 | 48,940 | 20,480 | 8,192 | 16,218 | 0.51 | 28,460 |
| 3 | 65,256 | 28,672 | 8,192 | 16,316 | 0.50 | 36,584 |
| 4 | 81,610 | 36,864 | 8,192 | 16,354 | **0.50** | 44,746 |

Two 4,096-token boundaries per idle window against roughly 16,300 new tokens a
turn. The ratio settles at 0.50 and the debt grows every turn. EXP-001 put this
as a condition — recovery throughput has to outrun context growth — and the
condition is unchanged; what this table measures is one build's position
against it. The build is the pre-fix one, in which a job could not reach its
target and a finished job re-read its own work at the budget's expense. The
counters here do not say which of the two fired in this cell, and that is the
reason 0.50 cannot be read as the mechanism's ratio rather than a reason to
doubt the table. The mechanism's catch-up ratio at this geometry is **not
established**.

So the mechanism is established and the regime is not, and on the pre-fix build
the regime is the part the defects reach. At 8K a turn PCSR ends a
session holding 20,480 of 24,584 tokens canonical and answers the probe in
19.93 s against 102.95 s; at 16K a turn it holds 45,056 of 81,610 and the
session is within 3% of Spec. Nothing here says the ratio cannot be moved —
a finer publication grain, a longer idle, or a slower-growing conversation all
change it directly — and nothing here measures any of those.

`data/exp-003/multiturn-80k-*.csv`. **Evidence level:** measured, one run per
arm, identical token sequences; the catch-up ratio is derived from the
runtime's own committed-token counter against the prompt lengths.

## 6. Correctness

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

## 7. One bug found on the way out, unrelated to any of this

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

Every row resting on a recovery-rate number was re-read against the two
defects. The status below is the status after that re-reading.

| | |
|---|---|
| A sparse turn leaves zero canonical state | **established** — no recovery job runs in either arm of §1 |
| PCSR-published state is restorable by the ordinary serving path | **established** — match, reconstruct, attach and TTFT all agree, and none of them depends on how much was published |
| Restoring it does not change the output | **established for this comparison** — byte-identical, 64-token greedy completion |
| Progressive publication commits under interruption and terminal publication does not | **established as a direction** — it follows from when each mode commits |
| How much more progressive publication leaves behind | **not established** — the 20,480 against 4,096, and the service comparison behind it, are pre-fix measurements of both defects |
| A 5% recovery budget costs the foreground nothing | **established for this workload and idle gap** — the pre-fix build did more recovery work for less published state, so the defects do not flatter it |
| Raising the budget above 5% buys no further canonical progress | **not established** — the 20% cell's second chunk committing nothing is what the two defects produce |
| Decode-throughput regression under 5% | **not established** — no decode-rate sample at these output lengths |
| Recovery keeps up with context growth at 16K a turn | **not established** — the catch-up ratio of 0.50 is a pre-fix build's ratio; it was previously read as a refutation and a defective build cannot refute it |
| The 5% budget's own accounting starves the job after turn 1 | **established** — cumulative service frozen at 15.70 s on one chunk and one publication, so the lockout is not a re-read artifact |
| The mechanism's own recovery rate, after both fixes | **not established** — one post-fix observation at one geometry, no matched pair |
| Whether a finer grain or longer idle changes the ratio | **not established** — not varied; the post-fix observation moves idle, budget and build together and separates none of them |
| Behaviour at zero idle, or on a real agent workload | **not established** — not run |
