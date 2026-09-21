# Research thread — background work under foreground QoS

**Status: four findings, all measured, none of them EXP-003's.** EXP-003 asked
whether canonical state can be rebuilt in the background at all, and answered
yes. These are about what that background work costs the requests it shares a
GPU with, which is a different question and was settled by a different set of
runs. Nothing here revises EXP-003; its conclusions and its datasets stand as
published.

The runs behind this are in
[`data/recovery-foreground-qos/`](../data/recovery-foreground-qos/). Evidence
level 3 — synthetic interactive workload, one run per cell — except where a
claim rests on the runtime's own per-slice trace, which is level 5.

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

**Recovery throughput did not move.** The same 24,576 tokens took 101.5 s,
102.2 s and 104.0 s of service at 1024, 512 and 256. Cutting the worst
foreground wait by a factor of twelve cost 2.4% of recovery throughput at the
extreme and 0.7% in the middle.

Twenty-four publications across the four cells, every one at an exact block
multiple, every one confirmed by the read-back probe that the serving path
could resolve it, and no refusal or short store anywhere. Slicing execution did
not disturb publication.

**A slice is opaque from outside, and that is the mechanism rather than a gap
in the instrument.** A trace record was added on the arrival path to time the
wait from the server side; across seventy-five arrivals at a 55.8% duty cycle,
*none* was timestamped inside a slice window. A slice holds the interpreter for
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

This one is not fixed and is recorded because it decides whether the feature
can be proposed at all.

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

**Not established:** whether that ownership is the right one. It is the only
one the existing architecture implies, which is an argument about reachability
and not about design.

## What would promote any of this

Findings 1 to 3 are mechanism claims with a measured effect on one machine, one
model and one runtime, at one workload geometry. They would be worth more with
a second model whose cache block size differs, because both the publication
grain and the interior optimum in finding 2 are stated in tokens and neither is
obviously portable.

Finding 4 needs a deployment with two models genuinely loaded and serving, which
nothing here had: every measurement in this thread ran with a single engine, so
the multi-engine claim is read out of the construction path rather than
observed.
