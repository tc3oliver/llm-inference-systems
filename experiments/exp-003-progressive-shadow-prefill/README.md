# EXP-003 — Progressive Canonical State Recovery

**Progressive canonical state recovery is worth running because it shrinks the
suffix the next turn has to prefill, not because it ends the sparse route. The
exit is not the outcome; the tail is.**

That is not what this experiment set out to measure. It was built around Spec
Exit — the turn on which a session stops taking the sparse route — as its
outcome, and the runs refuted the objective rather than the mechanism. The
mechanism works. The thing it is worth doing is not the thing it was pointed at.

A sparse prefill makes a request fast and leaves nothing behind. On the build
measured here it is not that the reusable checkpoint stops advancing, which is
what EXP-001 found; it is that a SpecPrefill request stores nothing at all, so
every later turn in the session recomputes the whole conversation. Three turns
in, the sparse arm is paying 23.4 s of prefill for a prompt the dense arm answers
with 8,200 uncached tokens and a flat 37 s.

The repair is to do the dense read later, when nobody is waiting, and publish
what it produces as ordinary reusable state. This experiment builds that — a
dense re-read owned by the scheduler rather than by a thread of its own, running
only while the engine is idle, publishing at safe boundaries — and measures it
against the arms that can tell you whether it worked.

**It works, once three publication defects are out of the way.** Canonical state
published by the recovery job is restored by the ordinary serving path —
`fetch_cache` matches it, `reconstruct_cache` returns all 64 layers, the request
attaches with the prefix already in place and prefills only the suffix — and the
completion is byte-identical to a pure dense run. In the four-arm control the
sparse arm's own cache answers its final prompt in 195.19 s; every arm that ran
the recovery answers it in about 12.3 s.

**What it buys is a tail that stops growing.** At a 15 s idle gap the canonical
prefix gains 4,096 tokens a turn against roughly 3,000 of growth, so the uncached
tail falls, time to first token stays between 23.7 and 24.1 s across seven turns
while the sparse control's rises to 42.16 s, and the session is 26.9% shorter.
That arm never leaves the sparse route. It is the best result in the experiment
and it contains no route change at all.

**Leaving the sparse route is a cost, not a reward.** In every budget cell the
exit turn is the most expensive turn of its session — one cell runs a
8,193-token tail sparse in 11.39 s and then a *smaller* 7,202-token tail dense in
37.60 s. The fastest configuration measured, with the sparse route never
withdrawn, runs the session in 79.06 s against the sparse control's 228.38 s and
exits nowhere. What decides this is the runtime's admission threshold, which is
not part of PCSR.

**A caution on that last number.** The dense turns were paused by the runtime's
prefill memory throttle and the sparse turns were not, so the four-fold gap
between the two routes at a matched tail is the route together with a throttle
that fired only on one side. These runs do not separate them, and
[FINDINGS.md](FINDINGS.md) §11 says so where the number appears.

## Status of each claim

The full table, with an evidence level on every row, is in
[FINDINGS.md](FINDINGS.md#status-of-each-claim). The rows a reader should not
leave without:

| | |
|---|---|
| A sparse turn leaves zero canonical state | **established** |
| Published state is restorable by the ordinary serving path | **established** |
| Restoring it does not change the output | **established for this comparison** |
| PCSR holds the uncached tail flat at a 15 s idle gap | **measured** — one run per arm |
| Spec Exit is a success condition | **refuted for this build and workload** |
| The sparse route is cheaper than the dense route at the same tail | **measured as a difference and not established as the route's** — the throttle fired only on the dense side |
| Raising the recovery budget above 5% buys no further progress | **established for this configuration** — an allowance under one chunk cannot bind |
| How much more progressive publication leaves behind | **not established** — one idle gap, and at a longer one the two modes are the same run |
| Decode-throughput regression under 5% | **not established** — no decode sample |
| PCSR is worth running on a session too short for debt to accumulate | **refuted for this shape** — the compaction round loses by 22% |
| Behaviour at zero idle, or on a real agent workload | **not established** |

## What was measured

Six rounds on `Qwen3.8-27B-oQ4e-mtp`, greedy, multi-token prediction off, one
research instance alone on the machine, one run per arm or cell throughout. The
first round ran on a build carrying two recovery-job defects and the five
`spec-exit-*` rounds ran after they were fixed;
[LIMITATIONS.md](LIMITATIONS.md) says what the first bounds.

Four arms recur across the rounds:

- **Dense** — dense prefill, no background work. The control that shows what a
  healthy checkpoint is worth.
- **Spec** — SpecPrefill, no background work. The control that shows what it
  costs.
- **Recovery-End** — SpecPrefill plus the dense re-read, publishing only when the
  whole target completes.
- **PCSR** — SpecPrefill plus the dense re-read, publishing at every safe
  boundary.

Recovery-End and PCSR differ only in when they commit, which is the comparison
the design turns on. At an idle gap shorter than one recovery target they
separate; at a longer one the job finishes inside the first window and they are
the same run. Both halves are measured, and together they are why the gap between
them is a property of the idle gap rather than of the two modes.

Each arm ends with a dense probe: the final prompt re-sent with sparse prefill
forced off. What it restores is what the ordinary serving path can actually
restore, which is a different claim from how much was published. Its latency is
reported beside the session total, never inside it.

## Read next

- [FINDINGS.md](FINDINGS.md) — eleven results and the evidence level of each.
- [METHODOLOGY.md](METHODOLOGY.md) — what the source audit changed before any
  run, what Spec Exit is and why it is not the outcome, and the measurement
  decisions the runs forced.
- [LIMITATIONS.md](LIMITATIONS.md) — starting with the 4,096-token publication
  grain, which bounds the result rather than qualifying it.
- [`data/exp-003/`](../../data/exp-003/) — every CSV and its provenance.

## Where this sits

EXP-001 ended on two open questions: whether a prefill optimization can be made
to leave a valid reusable checkpoint behind, and whether the debt it creates can
be repaid. This is the second one, attempted, and the answer is yes — the debt is
repayable in the background, affordably, and the state it produces is state the
cache will vouch for.

What the answer cost was the question. EXP-001 framed the trade as dense costing
more now and leaving reusable state against sparse being cheap now and leaving
none. PCSR breaks that trade by making the state in the background, and once it
is broken the foreground has no reason to move to the expensive route at all.
The 15 s cell also lands near EXP-001's think-time table, which had its hybrid
arm winning by about 24% at the same gap — a resemblance rather than a
replication, on a different build and against a different control.

Nothing here is proposed upstream except one unrelated bug found on the way,
which is [described in the findings](FINDINGS.md#7-one-bug-found-on-the-way-out-unrelated-to-any-of-this).
