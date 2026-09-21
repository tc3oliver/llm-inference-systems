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

## 8. The budget has two settings, not four

| budget | recovery share of wall time | session | Spec Exit | canonical at turn 6 |
|---:|---:|---:|---:|---:|
| 5% | 26.6% | **158.12 s** | turn 5 | 36,864 |
| 10% | 26.6% | 158.41 s | turn 5 | 36,864 |
| 20% | 26.6% | 158.46 s | turn 5 | 36,864 |
| uncapped | 12.6% | 214.87 s | turn 1 | 36,864 |

**Three of the four cells are one run.** 5%, 10% and 20% hold the same canonical
prefix at every turn, to the token, and end within 0.35 s of each other. Their
per-turn times to first token agree to within 0.06 s on six of the seven turns
and to 0.27 s on the other. All three received 26.6% of wall time against
ceilings of 5%, 10% and 20%.

That is a floor rather than an overshoot, and it is derivable. A recovery chunk
is one 4,096-token cache block, because the prefill step clamps to the next
block boundary whenever boundary snapshots are on, and they are on for this
model. In the uncapped cell the recovery received 83.90 s over five chunks, so
a chunk is about 16.8 s. The budget window is 30 s, so the allowance is 1.5 s at
5% and 6 s at 20%. An allowance smaller than one chunk cannot bind: the chunk
cannot be interrupted, so it runs whole, its overshoot is carried, the next
window opens spent and grants nothing, and the cycle is one chunk per two
windows at any of these settings. That predicts 16.8 s in 60 s, or 28%, against
the 26.6% all three cells received. The cap stops binding below one chunk per
window, about 55% here, so the knob has two attainable values on this
configuration and the sweep found both of them.

**The uncapped cell is 36% slower and ends with the same canonical prefix.** It
recovered 20,480 tokens in the first idle window, which put the tail under the
8,192-token admission threshold at turn 1 and moved the foreground onto the
dense route for every turn afterwards. The capped cells stayed sparse until turn
5, and while sparse their time to first token fell with the tail — 23.80, 19.56,
17.74, 14.90, 11.39 s — because a shorter suffix is less to score. Both reach
36,864 canonical tokens by turn 6. The 57 seconds between them are the route
change, paid six times instead of twice. Its 12.6% share is a share of a longer
wall time and is not comparable with the other three as a rate.

So the budget's effect in this configuration is not on recovery throughput. It
is on when the foreground is moved onto the more expensive route, and a slower
recovery is better because it keeps the cheaper route alive longer. That follows
from the admission threshold, which is a runtime policy rather than part of
PCSR, and §11 measures the two routes against each other directly.

`data/exp-003/spec-exit-budget-*.csv`. **Evidence level:** measured, one run per
cell, identical token sequences and one variable. The chunk length and the share
the cycle predicts are derived from the recovery counters in the same file. The
30 s window is a configuration setting, not a measurement.

## 9. The four controls, and what a sparse session hands the next request

| arm | session | canonical at turn 6 | probe TTFT | Spec Exit |
|---|---:|---:|---:|---:|
| Dense | 285.78 s | 36,864 | 12.29 s | turn 0 |
| Spec | 228.44 s | **0** | **195.19 s** | none |
| Recovery-End | 214.73 s | 36,864 | 12.29 s | turn 1 |
| PCSR | 214.78 s | 36,864 | 12.31 s | turn 1 |
| PCSR at 5%, from §8 | **158.12 s** | 36,864 | — | turn 5 |

**The sparse arm's probe is EXP-001 in one number.** Spec runs seven turns,
restores nothing on any of them, and leaves a cache that answers the same final
prompt in 195.19 s. Every other arm answers it in about 12.3 s. The sparse
session is 20% shorter than the dense one and hands the next request a
sixteen-fold bill.

**Recovery-End and PCSR are the same run here, and that is the control §3
needed.** 214.73 s against 214.78 s, the same canonical prefix, the same route
on every turn. At an idle gap longer than one recovery target the job completes
inside the first window, so terminal and progressive publication commit at the
same moment and nothing separates them. §3 measured the case where the job is
always interrupted; this measures the case where it never is. They are two
halves of one claim, and together they are why §3's factor is a property of the
idle gap and cannot be carried to any other one. The publication counters still
separate the arms — 1 against 5 — where the sessions do not, which is the design
working rather than a discrepancy: the extra commits buy nothing when the job
finishes inside the window.

Dense's first turn costs 103.72 s against Spec's 23.82 s, so the sparse route is
4.4x faster cold. That is EXP-001's level-1 result reproduced on this build.

The dense arm carries one route disagreement, on turn 0, where the runtime's
admission record and the analysis's derivation of the route do not agree. The
analysis reports the disagreement rather than choosing between them. Every other
turn of every other arm agrees.

### A compaction keeps one block

Six turns, with the conversation discarded after turn 2 and the session
restarted from the same head plus a summary. At the discontinuity the canonical
prefix falls from 24,576 tokens to 4,096 — one block.

The two streams share a head the harness **estimates** at about 7,856 tokens,
and the estimate is an estimate: this machine has no tokenizer for the served
model, so the generator counts at four characters per token and the round marks
every row approximate. The 4,096 is not an estimate. It is what the serving path
restored on the first post-compact turn, read from that turn's admission record.
Canonical state exists only at block boundaries, so the block holding the
divergence is unusable and a shared prefix rounds down — up to 4,095 tokens of a
genuinely shared prefix are discarded by the grain. Why the two numbers differ by
roughly a factor of two is **not established** here.

PCSR loses this round by 22%: 129.16 s against Spec's 105.63 s. That is not a
contradiction of §8 or §10. The session reaches 28,120 tokens and is then cut
back to 8,306, which is too short for canonical debt to accumulate, so there is
nothing to set against the cost of the route change PCSR provokes at turn 1. The
compaction also did not return the session to the sparse route: the compacted
context is small enough that the tail stays under the threshold, so the
foreground stayed dense. On this shape a compaction is a loss of canonical state
without a return to the cheap route.

`data/exp-003/spec-exit-controls-*.csv` and `data/exp-003/spec-exit-compaction-*.csv`.
**Evidence level:** measured, one run per arm, identical token sequences; the
probe figures and the surviving prefix are observed in the runtime's own
admission records. The shared-head length is the harness's estimate and nothing
measured it.

## 10. At 15 s of idle, and with no route change in it

| turn | prompt | canonical | tail | PCSR TTFT | Spec TTFT |
|---:|---:|---:|---:|---:|---:|
| 0 | 24,567 | 0 | 24,567 | 23.82 s | 23.84 s |
| 1 | 27,649 | 4,096 | 23,553 | 23.68 s | 26.27 s |
| 2 | 30,706 | 8,192 | 22,514 | 23.83 s | 29.40 s |
| 3 | 33,797 | 12,288 | 21,509 | 23.98 s | 32.58 s |
| 4 | 36,865 | 16,384 | 20,481 | 23.89 s | 35.55 s |
| 5 | 39,970 | 20,480 | 19,490 | 23.76 s | 38.69 s |
| 6 | 43,065 | 24,576 | 18,489 | 24.12 s | 42.16 s |

167.06 s against 228.50 s, which is 26.9% less, and the best result in the
experiment contains no route change at all. The canonical prefix gains 4,096
tokens a turn against roughly 3,000 of growth, so the tail falls slowly, never
reaches the 8,192-token threshold, and the route never changes — **Spec Exit:
none**. PCSR's time to first token stays between 23.68 and 24.12 s across seven
turns while the control's rises to 42.16 s. What PCSR bought here was not a
cheaper route. It was a route whose cost stopped growing.

The cell is uncapped, so nothing bounded the recovery except the idle gap it was
offered. It received 34.7% of the session's wall time and published exactly once
per idle window, which is the 4,096 tokens a turn the table shows.

It lands near EXP-001's think-time table, which had the hybrid arm winning by
about 24% at the same 15 s gap. The two are not the same comparison — EXP-001's
hybrid arm is measured against a dense-only control and this is a PCSR arm
against a sparse one, on a different build — so the agreement is a resemblance.
**Not established:** that this reproduces EXP-001's number.

One reporting artefact, and it is in the file. Turn 5's published-token counter
reads 0 for that turn while the restored prefix reads 20,480. The counter is
read from the live recovery job and the job was replaced at that instant, so it
flickers to zero on a turn where the job is rebuilt. The restored figure is
unaffected and it is the one the table uses.

`data/exp-003/spec-exit-idle15-*.csv`. **Evidence level:** measured, one run per
arm, identical token sequences and idle gaps.

## 11. The route held, and the tail is what PCSR is worth

Same token sequence, same idle gap, same uncapped budget as §9, with one
variable: the SpecPrefill admission threshold set to 1, so the foreground takes
the sparse route at every tail and the only thing that changes across turns is
how much canonical state has arrived underneath it.

| turn | prompt | canonical | tail | TTFT |
|---:|---:|---:|---:|---:|
| 0 | 24,567 | 0 | 24,567 | 23.83 s |
| 1 | 27,649 | 20,480 | 7,169 | 17.30 s |
| 2 | 30,706 | 24,576 | 6,130 | 7.72 s |
| 3 | 33,797 | 28,672 | 5,125 | 6.92 s |
| 4 | 36,865 | 32,768 | 4,097 | **5.76 s** |
| 5 | 39,970 | 32,768 | 7,202 | 9.17 s |
| 6 | 43,065 | 36,864 | 6,201 | 8.34 s |

79.06 s against the sparse control's 228.38 s, which is 65.4% less, with no
route change at all. Time to first token falls from 23.83 s to 5.76 s by turn 4
and ends at 8.34 s, tracking the tail rather than the prompt: turns 5 and 6 rise
because the tail rises, not because the prompt does.

**The matched pair.** Turns 5 and 6 of this arm and of the uncapped control in
§9 share a prompt, a canonical prefix and a tail. The route is the only thing
that differs.

| tail | sparse | dense |
|---:|---:|---:|
| 7,202 | 9.17 s | 37.56 s |
| 6,201 | 8.34 s | 33.11 s |

**What that four-fold gap is, and what it is not.** The dense turns in every
cell hit the runtime's adaptive prefill memory throttle and were paused and
requeued. No sparse turn in any cell did. The server's own log records the
pauses, and they cluster on the two largest prompts of each cell. So the gap
above is the route together with the throttle the route provokes, and these runs
cannot separate the two. There is a mechanism that would make the throttle part
of what the dense route costs rather than a confound — a dense prefill of 7,000
tokens at 40,000 tokens of context allocates far more transient memory than a
sparse prefill of the same tail — and no run here isolates it, so it is a
hypothesis and not a control. **Observed:** the dense turns triggered the
throttle and the sparse turns did not. **Measured:** the difference in time to
first token at a matched prompt, prefix and tail. **Not established:** whether
the dense route is slower than the sparse route at the same tail without the
pause. An earlier round of this experiment fitted two cost lines over those
paged dense turns and concluded they never cross at any positive tail; that fit
is withdrawn and the matched pair above replaces it.

The threshold is the whole of the difference between 79.06 s and 214.78 s, and
it is a runtime admission policy rather than part of PCSR. What PCSR does is
shrink the suffix the sparse route has to score, and here it does that without
the route changing at all.

`data/exp-003/spec-exit-always-sparse-*.csv`, with `data/exp-003/spec-exit-controls-*.csv`
for the matched pair. **Evidence level:** measured — the pair is matched on
prompt, restored prefix and tail with the route as the only variable — one run
per arm. The throttle is observed in the runtime's log and no run here separates
it from the route.

---

## Status of each claim

Sections 1 to 7 ran on the build that carried the two recovery-job defects;
sections 8 to 11 ran after they were fixed. Every row says which, because a row
that does not is a row a reader will take for the mechanism.

| | |
|---|---|
| A sparse turn leaves zero canonical state | **established** — no recovery job runs in either arm of §1, and §9's Spec arm restores nothing across seven turns |
| PCSR-published state is restorable by the ordinary serving path | **established** — match, reconstruct, attach and TTFT all agree, and §9's probe answers in 12.3 s against the sparse arm's 195.19 s |
| Restoring it does not change the output | **established for this comparison** — byte-identical, 64-token greedy completion |
| Progressive publication commits under interruption and terminal publication does not | **established as a direction** — it follows from when each mode commits |
| How much more progressive publication leaves behind | **not established** — the pre-fix 20,480 against 4,096 is one idle gap, and at §9's longer gap the two modes are the same run |
| A 5% recovery budget costs the foreground nothing | **established for this workload and idle gap** — and at §8's geometry the 5% cell is the fastest arm measured anywhere in the study |
| Raising the budget above 5% buys no further canonical progress | **established for this configuration** — on the fixed build 5%, 10% and 20% are one run, because an allowance under one chunk cannot bind |
| Decode-throughput regression under 5% | **not established** — no decode-rate sample at these output lengths |
| Recovery keeps up with context growth at 16K a turn | **not established** — the 0.50 catch-up ratio is the pre-fix build's; §8 and §10 exceed 1 at roughly 3,000 tokens a turn, which is a different geometry and not an answer to this row |
| The 5% budget's own accounting starves the job after turn 1 | **established for the pre-fix build** — cumulative service frozen at 15.70 s on one chunk and one publication; the lifetime accounting has since been replaced by a tumbling window |
| The recovery rate on the fixed build | **measured at the two idle gaps run** — 20,480 tokens in the first 75 s window, 4,096 per 15 s window, one run per cell |
| A longer idle gap changes what recovery reaches | **measured** — 15 s against 75 s on the same token sequence, §10 against §9 |
| Whether a finer publication grain changes it | **not established** — not varied |
| Spec Exit is a success condition | **refuted for this build and workload** — the exit turn is the most expensive turn of its session in all four §8 cells, and the fastest configuration measured never exits at all |
| The sparse route is cheaper than the dense route at the same tail | **measured as a difference and not established as the route's** — §11's matched pair is four-fold, and the dense turns were paused by the memory throttle where the sparse turns were not |
| What survives a compaction | **established for this shape** — one 4,096-token block against a shared head the harness estimates at about 7,856; why the two differ is **not established** |
| PCSR is worth running on a session too short for debt to accumulate | **refuted for this shape** — §9's compaction round loses by 22% |
| Behaviour at zero idle, or on a real agent workload | **not established** — 15 s and 75 s of idle are run, zero idle and a real workload are not |
