# EXP-003 — Progressive Shadow Prefill

A sparse prefill makes a request fast and leaves nothing behind. On the build
measured here it is not that the reusable checkpoint stops advancing, which is
what EXP-001 found; it is that a SpecPrefill request stores nothing at all, so
every later turn in the session recomputes the whole conversation. Three turns
in, the sparse arm is paying 23.4 s of prefill for a prompt the dense arm answers
with 8,200 uncached tokens and a flat 37 s.

The obvious repair is to do the dense read later, when nobody is waiting, and
publish what it produces as ordinary reusable state. This experiment builds that
— a dense re-read owned by the scheduler rather than by a thread of its own,
running only while the engine is idle, publishing at safe boundaries — and
measures it against the three arms that can tell you whether it worked.

**It did not work, and the reason is worth more than a win would have been.**
The shadow was never starved: it was runnable on 32 scheduler steps, scheduled
on 14, and took 231.75 seconds, 53% of the session's wall time. It was not too
slow: it densely read 24,575 of its 24,576-token target. It cost the foreground
nothing measurable: 48.00 s against the sparse control's 47.93 s. And the
session ended with a canonical prefix of zero, because every block it published
came back at the next restore as a placeholder and was rejected.

**Primary claim.** In this runtime the binding constraint on repaying
canonical-state debt is not scheduling, budget or recovery throughput. It is
whether the recovered state can be published in a form the cache will accept —
and on a hybrid model whose non-sliceable state lives only at block boundaries,
it currently cannot.

**Secondary result, and the reason for the fourth arm.** Shadow-End and PASS
published very differently — one publication against five — and finished
identically, to the token. Progressive publication is not what is missing. An
experiment run without the Shadow-End control would have read the flat result as
"background recovery does not help here", which is what EXP-001 already says and
is not what this data shows.

## Status of each claim

| | |
|---|---|
| A sparse turn leaves zero canonical state | **established** |
| The shadow receives ample service and completes its target | **established** |
| The shadow costs the foreground nothing at a 90 s idle gap | **established for that gap** |
| Progressive publication is not the binding constraint | **established** |
| Published blocks are rejected at the next restore | **observed**, every restore |
| Why they are rejected inside the store | **not established** |
| Whether a corrected publication would repay the debt | **not established** |
| Behaviour at zero idle, or on a real agent workload | **not established** |

## What was measured

Four arms over one append-heavy session — 8K, 16K, 24K tokens, no idle inside a
turn, 90 s between turns — on `Qwen3.8-27B-oQ4e-mtp`, greedy, multi-token
prediction off, one research instance alone on the machine.

- **Dense** — dense prefill, no background work. The control that shows what a
  healthy checkpoint is worth.
- **Spec** — SpecPrefill, no background work. The control that shows what it
  costs.
- **Shadow-End** — SpecPrefill plus the dense re-read, publishing only when the
  whole target completes.
- **PASS** — SpecPrefill plus the dense re-read, publishing at every safe
  boundary.

Each arm ends with a dense probe: the final prompt re-sent with sparse prefill
forced off. What it restores is the longest canonical prefix the arm left
behind, and it is the only way to read that quantity, because a SpecPrefill turn
reports no cache hit whatever the cache holds. The probe's latency is reported
beside the session total, never inside it.

## Read next

- [FINDINGS.md](FINDINGS.md) — the five results and the evidence level of each.
- [METHODOLOGY.md](METHODOLOGY.md) — what the source audit changed before any
  run, and the two measurement decisions the first runs forced.
- [LIMITATIONS.md](LIMITATIONS.md) — starting with the 4,096-token publication
  grain, which bounds the result rather than qualifying it.
- [`data/exp-003/`](../../data/exp-003/) — both CSVs and their provenance.

## Where this sits

EXP-001 ended on two open questions: whether a prefill optimization can be made
to leave a valid reusable checkpoint behind, and whether the debt it creates can
be repaid. This is the second one, attempted. The answer is that the repayment
is affordable — it is nearly free at a generous idle gap — and that affording it
is not the problem. The problem is the same seam EXP-001 named: the boundary
where work gets divided, and whether state captured at one is state the cache
will vouch for.

Nothing here is proposed upstream except one unrelated bug found on the way,
which is [described in the findings](FINDINGS.md#7-one-bug-found-on-the-way-out-unrelated-to-any-of-this).
