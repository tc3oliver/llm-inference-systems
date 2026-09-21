# EXP-003 — Progressive Canonical State Recovery

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

**It works, once three publication defects are out of the way.** Canonical
state published by the recovery job is restored by the ordinary serving path —
`fetch_cache` matches it, `reconstruct_cache` returns all 64 layers, the request
attaches with the prefix already in place and prefills only the suffix — and the
completion is byte-identical to a pure dense run. Time to first token on the
measured probe fell from 67.34 s to 18.72 s on the same prompt.

**Primary claim.** Progressive publication is what makes background canonical
recovery worth running. Recovery-End and PCSR run the same recovery and differ
only in when they commit; under a session whose idle gap is shorter than one
recovery target, Recovery-End received *more* compute (146.9 s against 133.3 s)
and left one fifth as much canonical state behind (4,096 tokens against 20,480).
A recovery that is always interrupted is worth only what it has committed. Both
of those numbers were measured before two recovery-job defects were found and
fixed, and the size of the gap between the two modes is not established on the
fixed build; the direction is, because it follows from when each mode commits.

**Secondary result.** Every non-zero recovery budget *lowers* foreground
latency rather than costing it, because the prefix it restores saves more than
the recovery spends. Raising the budget from 5% to 20% bought twice the service
and no further canonical progress, and that is what a job which could not reach
its target and then re-read it would produce, so the budget is not established
as the non-binding parameter it first looked like.

## Status of each claim

| | |
|---|---|
| A sparse turn leaves zero canonical state | **established** |
| Published state is restorable by the ordinary serving path | **established** |
| Restoring it does not change the output | **established for this comparison** |
| Progressive publication commits under interruption and terminal publication does not | **established as a direction** |
| How much more progressive publication leaves behind | **not established** — 20,480 against 4,096 is a pre-fix measurement |
| A 5% recovery budget costs the foreground nothing | **established for this workload and idle gap** |
| Raising the budget above 5% buys no further progress | **not established** — pre-fix, and what the defects produce |
| Decode-throughput regression under 5% | **not established** — no decode sample |
| The mechanism's own recovery rate, after both fixes | **not established** — one observation, no matched pair |
| Behaviour at zero idle, or on a real agent workload | **not established** |

## What was measured

Four arms over one append-heavy session — 8K, 16K, 24K tokens, no idle inside a
turn, 90 s between turns — on `Qwen3.8-27B-oQ4e-mtp`, greedy, multi-token
prediction off, one research instance alone on the machine.

- **Dense** — dense prefill, no background work. The control that shows what a
  healthy checkpoint is worth.
- **Spec** — SpecPrefill, no background work. The control that shows what it
  costs.
- **Recovery-End** — SpecPrefill plus the dense re-read, publishing only when the
  whole target completes.
- **PCSR** — SpecPrefill plus the dense re-read, publishing at every safe
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
