# Research thread — background work under foreground QoS

**Status: five findings of its own, all measured, plus two of EXP-003's recorded for context.** EXP-003 asked
whether canonical state can be rebuilt in the background at all, and answered
yes. These are about what that background work costs the requests it shares a
GPU with, which is a different question and was settled by a different set of
runs. Nothing here revises EXP-003; its conclusions and its datasets stand as
published.

The runs behind this are in
[`data/recovery-foreground-qos/`](../data/recovery-foreground-qos/). Evidence
level 3 — synthetic interactive workload, one run per cell — except where a
claim rests on the runtime's own per-slice trace, which is level 5.

## The seven findings, and what each one is worth

Two of these are EXP-003's and are recorded here only so the set can be read in
one place; their home is
[`experiments/exp-003-progressive-shadow-prefill/`](../experiments/exp-003-progressive-shadow-prefill/)
and nothing here revises them. The other five are this thread's.

| # | Finding | Level | Where |
|---|---|---|---|
| 1 | Sparse foreground execution creates reusable canonical-state debt | **Measured** | EXP-003 |
| 2 | Progressive recovery reduces the future uncached suffix without the foreground leaving the sparse route | **Measured** | EXP-003 |
| 3 | The recovery budget controls collision *frequency* | **Measured** | §1 below |
| 4 | Recovery execution granularity controls collision *severity* | **Measured** | §2 below |
| 5 | Execution granularity and canonical publication granularity are independent | **Measured** | §2 below |
| 6 | Event-driven parking removes idle scheduler spin without materially changing recovery cadence | **Measured** | §3 below |
| 7 | Background accelerator work needs process-global ownership when engines share a device | **Measured** (one loaded pair) | §4 below |

Read against the ladder in [`EVIDENCE.md`](../EVIDENCE.md), each one carries a
different amount:

**1 — Measured.** The sparse arm's reusable canonical prefix stays at 0 for all
seven turns while the prompt grows to 43,065 tokens
([`spec-exit-always-sparse-turns.csv`](../data/exp-003/spec-exit-always-sparse-turns.csv)).
*Derived* from it: the debt equals the whole prompt, because the prefix is zero.
*Not established*: that this holds for sparse prefill implementations other than
this runtime's, where the refusal to extract a cache with `specprefill_indices`
set is what creates the debt.

**2 — Measured.** Same run, recovery arm: the canonical prefix reaches 36,864
tokens and cumulative foreground falls 228.379 s → 79.062 s, with `spec_exit_turn`
empty in both arms — the foreground took the sparse route on every turn of both.
*Not established*: any universal speedup, and any claim about which route the
foreground *should* take. One workload, one geometry.

**3 — Measured.** At a 5% cap 3 of 24 probes collided; uncapped 6 of 24
([`collision-summary.csv`](../data/recovery-foreground-qos/collision-summary.csv)).
*Not established*: that frequency scales linearly with the percentage, which two
points cannot show.

**4 — Measured.** Worst observed foreground TTFT 15.08 s at block grain against
1.299 s at a 512-token slice, same budget, same probe schedule.
*Derived*: the worst single uninterruptible slice the runtime traced, 2.39 s —
the maximum over both traced slice kinds, because a slice that ends on a
publication boundary carries the extract, the store and the read-back inside the
same unit and is the longer one at every slice size. That is the bound a request
could have waited for, rather than one that was observed.
*Not established*: **QoS acceptability**. No foreground latency target was
defined before the runs, so no value here is established as acceptable, and 512
is a measured operating point rather than an optimum — 256 had the lower traced
bound (1.25 s) and the *higher* observed maximum (1.495 s).

**5 — Measured.** Every slice setting that ran reached identical publication
boundaries in identical order — three capped sizes plus the uncapped block
grain — and recovery throughput was flat (the same 24,576 tokens in 101.5 to
104.0 s of service). The five-size boundary equality is a *unit* result, not
this sweep's. *Derived*: the publication critical section costs 84–128 ms,
two orders below the value it was suspected of setting.
*Inferred*: that the independence generalises, because it rests on
`clamp_prefill_chunk_to_boundary` refusing to overshoot a boundary — a property
of this runtime's chunk loop, not of the idea.

**6 — Measured.** Idle CPU 4.33 s → 1.86 s per 150 s with both arms publishing
4 blocks ([`idle-cost.csv`](../data/recovery-foreground-qos/idle-cost.csv)).
*Derived*: 1.82 s predicted from the per-block cost alone, so the remaining
spin is essentially zero. *Not established*: the effect on a process serving
several models, where the parked loop is one of many.

**7 — Measured, narrowly.** Two engines, one budget object: `budget_shared`
True and `budget_owners` 2 on both, with `budget_windows` 7,
`budget_overshoot_s` 5.910009 and `budget_window_service_s` 4.408818 identical
to six decimals across both engines
([`shared-budget-two-model.csv`](../data/recovery-foreground-qos/shared-budget-two-model.csv)).
*Derived*: aggregate share 10.36% against a 10% cap, the excess bounded by one
grant→execute→charge slice because the charge necessarily lands after the grant.
*Not established*: behaviour under real contention. One engine carried load; the
process was otherwise idle. *Withdrawn*: an earlier claim that mutual exclusion
alone bounds the aggregate share — it bounds concurrency, and serialising is
mildly worse for share because overlapping slices self-limit through contention.

## 1. A budget controls how often background work collides. It cannot control how much it costs when it does.

A recovery work unit cannot be interrupted once it is handed to the model, so a
request arriving while one is running waits for it to finish. The instrument is
a request small enough that its own cost is negligible: one large sparse turn
queues a recovery job, then a train of twenty-four tiny requests is fired
through the idle window that follows.

With the work unit at a whole cache block, at a 5% budget three of twenty-four
probes collided and at no budget six did — while the worst collision stayed in
the same band either way, 12.30 s against 15.08 s. Lowering the percentage
halved the *number* of affected requests and moved the worst case by 18%.

That is what a share-of-wall-time budget is: a ceiling on how often the work
runs, applied between units. It has no term for the duration of a unit, so it
cannot bound the tail. Two variables, and the one that was being tuned was the
wrong one.

## 2. The execution slice and the publication grain are separate control variables.

They had been the same number, and nothing required it.

Publication is genuinely fixed. Canonical state for a non-sliceable layer
exists only at a cache block boundary — 4,096 tokens on the model measured —
and publishing a partial block is not a smaller win but a corrupt one.

Execution is not fixed. The prefill state survives between calls, the chunk
clamp already refuses to let a slice overshoot a boundary, and the publication
floor is a pure function of the block size with no slice term in it. So a
smaller slice reaches the same boundaries, publishes the same prefixes in the
same order, and recomputes nothing.

Sweeping the execution slice with publication untouched:

| execution slice | probe p50 | probe p90 | probe max | probes over 2 s |
|---|---|---|---|---|
| one block | 0.710 s | 13.847 s | **15.080 s** | 6 of 24 |
| 1024 | 0.837 s | 1.217 s | 4.508 s | 2 of 24 |
| 512 | 0.889 s | 1.166 s | **1.299 s** | 0 of 24 |
| 256 | 1.068 s | 1.366 s | 1.495 s | 0 of 24 |

Three things in that table are worth separating.

**The worst case tracks the slice, and the publication critical section is not
the floor.** The runtime's own per-slice trace times the blocking unit
directly: a slice that publishes costs 84 ms, 109 ms and 128 ms more than one
that does not, at 256, 512 and 1024 tokens respectively. Extract, a full
evaluation over the live cache and every boundary snapshot, a synchronous store
and an independent read-back together cost about a tenth of a second. Two
orders of magnitude below the number they were suspected of setting.

**It is not free in the other direction.** A smaller slice means more slices,
so *more* requests are touched even as each wait shrinks: probes over one
second go 6, 7, 9, 13 as the slice falls. The trade is tail latency against
jitter, and the two do not move together.

That is also why the table does not name a best slice size. In this probe
distribution 512 gave better observed percentile behaviour than 256, while 256
had the lower worst-case bound from the trace — 1.25 s against 2.39 s. Which
matters depends on an arrival distribution and a latency target, and this
workload supplies a synthetic version of the first and none of the second. 512
is the best measured operating point for *these* probes, not an established
optimum and not a default.

**Recovery throughput did not move.** The same 24,576 tokens took between
101.5 s and 104.0 s of service across 1024, 512 and 256. Cutting the worst
foreground wait by a factor of twelve cost about 2.4% of recovery throughput at
the extreme. The per-cell service totals are not in `data/` — `slice-timing.csv`
aggregates more than one run at 512 — so the range is the claim and the
individual figures are not.

Twenty-four publications across the four cells, every one at an exact block
multiple, every one confirmed by the read-back probe that the serving path
could resolve it, and no refusal or short store anywhere. Slicing execution did
not disturb publication.

**A slice is opaque from outside, and that is the mechanism rather than a gap
in the instrument.** A trace record was added on the arrival path to time the
wait from the server side; across every arrival in the probe series, with
recovery running for most of the window, *none* was timestamped inside a slice
window. (The arrival count and the duty cycle were read from the run log and
are not in `data/`, so they are not quoted here.) A slice holds the interpreter for
its whole duration, so the event loop cannot record an arrival until the slice
ends: the instrument is blocked by the thing it would measure. The consequence
is not that the measurement is hard but that a running slice is invisible to
everything in the process that would otherwise notice a request waiting — no
admission path, no fairness gate and no timer can act inside one. Client-side
latency is the only instrument that sees the wait, which is why every number in
this section is measured from outside the server.

## 3. Event-driven parking removed the scheduler's idle spin without costing recovery anything.

A live background job has to keep the engine loop stepping, or the idle moment
it needs never arrives. The first implementation did that unconditionally,
including while the job was out of budget and could not run. Each of those
steps advances the counter that gates a process-global buffer-pool clear and a
garbage collection, on a pool shared with every other model in the process.

The correction rests on a property of the loop rather than on an argument: it
re-reads its work predicate once per step interval whether or not it stepped,
so a parked job is picked up within 50 ms of its window replenishing either
way. The reasoning it replaced — that parking would leave the window to
replenish unwatched — was simply not true of the loop.

Measured as CPU seconds consumed across a fixed 150 s idle window, on one
build, with one line differing:

| | blocks published | idle CPU | share of wall |
|---|---|---|---|
| recovery off | 0 | 0.66 s | 0.44% |
| waiting job, polling | 4 | 4.33 s | 2.89% |
| waiting job, parked | 4 | 1.86 s | 1.24% |

Both recovery cells published four blocks, which is what makes their CPU
figures comparable. Pricing a block from the uncapped cell at 0.29 s of CPU
predicts 1.82 s for the parked cell against 1.86 s measured — the parked cell's
idle cost is accounted for entirely by the work it did, leaving about 2.5 s per
150 s that was previously pure spin.

Publication spacing was unchanged between the two, 16/35/61/61 s against
16/36/60/61 s. **Parking was not bought with throughput.**

## 4. A per-engine budget does not give a process-global bound.

Of the four this is the one that decided whether the feature could be proposed
at all, and unlike the others it was found by reading the construction path
rather than by measuring — every run behind findings 1 to 3 had a single engine
loaded, where the defect is unreachable.

The budget is constructed per scheduler, from a percentage that is copied by
value. A process can hold several loaded models, each with its own scheduler,
its own loop and its own executor, all sharing one accelerator. Each measures
its own share against its own wall clock, so M loaded engines grant M times the
configured share and nothing aggregates them — and each reports its own share
in isolation, so the sum is not visible on the wire either.

**Mutual exclusion does not substitute for it.** Serializing background work so
that at most one unit runs at a time constrains the count of concurrent units,
not the integral of "some unit is running" over time, and that integral can
approach the whole wall clock while every engine reports compliance. It is
mildly *worse* for the aggregate: overlapping units contend and therefore
charge more wall seconds per token, an accidental self-limiter that serializing
removes. Mutual exclusion is a latency bound; a shared budget is a share bound;
neither is the other.

What the runtime does supply is the shape of an answer. It already carries one
cross-engine resource ceiling — a shared hot-cache budget, created by the
engine pool before any engine loads and reaching every scheduler as an object
on the configuration, with a global LRU across owners. Scalars on that
configuration are copied by value at two points; object references are not. So
the difference between the working precedent and the broken case is that one is
an object and the other is a float.

Its teardown discipline is the other half: there is no global reset anywhere.
A departing owner deregisters its own accounting and the shared total and every
other owner are untouched. A shared budget that kept the current per-scheduler
`reset()` would, on one engine's reset, forgive another engine's spent
allowance mid-window, erase its carried overshoot and splice its telemetry onto
a new clock — and under memory pressure a pool unloads and reloads repeatedly,
so that would happen on every cycle.

That is the ownership the fix took: one budget object created by the pool
before any engine loads, on the shared config, adopted by every scheduler
through the copies. Two properties of the precedent came with it. Teardown
**deregisters** rather than resetting, because one engine discarding the
service its peers have spent would lift their ceiling mid-window — and under
memory pressure a pool unloads and reloads often enough for that to be most
cycles. And carried overshoot is **global**: per-owner debt would let the
aggregate overrun scale with the engine count, which is the property being
removed.

Two engines, two models loaded at once, a sparse turn each and then an idle
window, against a 10% aggregate cap:

| | 27B engine | 0.8B engine |
|---|---|---|
| that engine's own recovery service | 18.124 s | 1.285 s |
| budget windows | 7 | 7 |
| carried overshoot | 5.910009 s | 5.910009 s |
| current window service | 4.408818 s | 4.408818 s |
| **aggregate share of wall time** | **10.36%** | **10.35%** |

The two engines report the same window count and the same carried overshoot and
window service **to six decimal places**, which is not something two
independent budgets produce. The aggregate lands at 10.4% against a 10% cap —
over by less than one slice, which is inherent: a slice is uninterruptible, so
the charge lands after the grant and the ceiling is enforced in arrears. Under
the per-engine budget each of these engines would have had a 10% allowance of
its own.

**Two things this does not settle.** Mutual exclusion still is not a share
bound — serialising recovery constrains how many slices run at once, not the
integral of "some slice is running", so a separate execution claim does that
job and the budget does this one. And the cap remains a share of *wall* time,
which is not a hardware-invariant measure of work: two slices contending on one
accelerator each take longer and therefore each charge more seconds for the
same tokens, so the ceiling tightens under contention and loosens when idle. A
token-based cap would be invariant to that. Nothing here measured whether it
matters, and it is the assumption the whole budget rests on.

## What would promote any of this

Findings 1 to 3 are mechanism claims with a measured effect on one machine, one
model and one runtime, at one workload geometry. They would be worth more with
a second model whose cache block size differs, because both the publication
grain and the interior optimum in finding 2 are stated in tokens and neither is
obviously portable.

Finding 4 was measured with two models loaded but only one of them under any
real load, in a process that was otherwise idle. What it does not cover is
contention: two engines both serving foreground traffic while both have
recovery debt, where the aggregate share and the wall-time unit interact. That
is where the token-versus-wall-time question above would first bite.
