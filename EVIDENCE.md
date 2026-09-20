# The evidence ladder

The seven kinds of evidence in EXP-001 did not agree with each other. The
microbenchmark said the optimization was a large win. The real agent session
said it was a large loss. Both measurements are correct. The ladder exists
because they disagree, and because a reader who is handed one number without
being told which rung it came from will draw the wrong conclusion from it.

Every level below is illustrated from this study, because a ladder built out
of other people's results would prove nothing about this one.

## 1. Microbenchmark

One operation, in isolation, with the rest of the system arranged to stay out
of the way. It answers whether a mechanism does the thing it claims.

Cold prefill on an empty cache. At 16,384 prompt tokens, time-to-first-token
was 57.84 s dense and 19.24 s accelerated; at 32,768 tokens, 122.7 s against
33.5 s; at 14,300 tokens, 47.0 s against 11.9 s. Prefill throughput at 16K was
302 tok/s dense and 1046 tok/s accelerated. Source: `data/exp-001/cold-prefill.csv`.

What this level cannot tell you: what the request leaves behind.

## 2. Isolated qualification

The mechanism composed with the other mechanisms it will ship alongside, still
under controlled conditions. It answers whether the wins stack or interfere.

ANE offload and SpecPrefill, an attention-based sparse prefill mechanism,
together at 16K reached about 1328 tok/s
against about 300 tok/s, roughly 95-97% of the ideal product of the two
speedups. They compose almost cleanly, which is a real result and is also the
last point at which the picture stayed simple. Source:
`data/exp-001/session-aggregates.csv`, which also holds the reference run
below.

A separate dense-only long-context reference from a different configuration,
quoted only with that caveat: 59,313 tokens, cold TTFT 285.84 s at 209.79
tok/s, warm 14.97 s at 4,999.93 tok/s with a 96.7% cache hit.

## 3. Synthetic interactive workload

A repeated multi-turn interaction with a controlled parameter, standing in for
a user. It answers whether the mechanism survives being used in a loop, and it
can be repeated, which no real session can.

Session wall time against think time — the idle gap between turns, which is
the budget background dense recovery has to work in:

| think time | dense-only | hybrid |
|---|---:|---:|
| 15 s | 108.7 s | 83.1 / 83.4 s |
| 10 s | 108.1 s | 84.5 s |
| 5 s | 108.1 s | 104.4 / 105.4 s |
| 0 s | 108.1 s | 119.7 s |

The hybrid arm here runs on an experimental branch whose background
densification was designed to fail closed and later failed a review of that
design; see
[Prototype safety review](ENGINEERING.md#prototype-safety-review). The
algorithmic result below stands on its own.

Source: `data/exp-001/think-time.csv`. Read the whole column. At 15 seconds of
idle the hybrid arm wins by about 24%; at zero idle it loses. Recovery
throughput has to outrun context growth, and this table is where that showed
up first, before any real workload confirmed it.

What this level cannot tell you: whether the geometry of a real session
resembles the parameter you swept.

## 4. Real agent session

An actual agent doing actual work, instrumented. It answers whether the
conclusion from level 3 holds when nothing is controlled. It is also, here,
one run per arm with the three agents taking different trajectories through
the task, so the arms are not comparable as a ratio.

| turn | dense hit | sparse hit | sparse scorer calls |
|---:|---:|---:|---:|
| 0 | 0.0% | 0.0% | 0 |
| 1 | 82.2% | 88.2% | 0 |
| 2 | 94.0% | 63.9% | 12 |
| 3 | 75.6% | 51.0% | 7 |
| 4 | 98.6% | 40.7% | 13 |
| 8 | 98.1% | 27.1% | 2 |

Source: `data/exp-001/session-turns.csv` and, for the wall times,
`data/exp-001/session-aggregates.csv`. Session wall time was about 1676 s
dense, about 4404 s sparse and about 5248 s hybrid — one run per arm, with
different agent trajectories, so those three numbers say that something went
badly wrong and nothing more precise than that. Turn 0 took no sparse path in
either arm.

The useful signal is the shape of the sparse hit column. It rises once, then
falls every turn after, while the dense column stays high. That divergence is
what sent me looking for a mechanism.

## 5. Request-level mechanism trace

Per-request instrumentation of one session, enough to say what happened rather
than that something happened. It answers the question the previous levels
raise and cannot settle.

Trace B: one continuous session, SpecPrefill only, no background
densification in the build, so nothing competes to explain the behaviour.
Across 20 consecutive prefix-cache restores the reusable checkpoint advanced
28,672 → 32,768 → 33,792 → 36,864 → 37,888, then fell back to 28,672 at
request 11 and stayed pinned there for the remaining ten requests. The
uncached suffix went from 17,060 at the cliff to 33,979 at the end. Scorer
cost tracked the suffix directly, 2.7 s at 8,535 tokens scored to 5.7 s at
33,389. Stores ended at 44,032 and nothing was written after, because a
sparsified suffix does not advance the normal reusable dense prefix state.

The order within the trace is the part that settles causation. Request 10
restored 37,888 tokens with a 6,902-token suffix, below the 8192-token
threshold, and ran dense. The restore at request 11 found only 28,672 tokens,
because the cache layer rejected a partial prefix match to avoid stale state.
That restore is the cliff and it preceded any sparse admission. The
17,060-token miss it left crossed the threshold, SpecPrefill engaged, and
every observed suffix after that was sparsified, so the checkpoint never
recovered. The request-11 sparse admission did not cause the request-11
cliff, because the restore came first. The log names a partial match whose
last matched block held a placeholder, and the surviving trace does not
establish when or how that placeholder was created, so the origin of the
cliff is not settled here. What is settled is the ten requests after it.

Source: `data/exp-001/trace-b-*.csv` and its README, which also quotes the
single log line naming the proximate cause: a partial prefix match rejected to
prevent stale state.

This is the strongest evidence in the study. It is still one session.

## 6. Correctness result

A defect found in the mechanism itself, reproducible, and fixed. It outranks
every performance number, because a performance number measured on incorrect
behaviour is not a measurement of anything.

The static prefix boundary was derived by subtraction and fell short of the
real boundary — as little as 37 tokens once tools were in play, in this
study's own configuration — placing the tail of the tool instructions and the
operator's own system prompt inside the region sparse prefill may drop.
Tokens the runtime contract required to stay fully computed became eligible
for sparse processing. No downstream semantic failure was measured, and none
is claimed. The fix is
[oMLX PR #3756](https://github.com/jundot/omlx/pull/3756), open at the time
of writing.

## 7. Upstream consequence

A change submitted to the system everyone else runs. It answers whether any
of this mattered outside one machine. Both pull requests are open at the time
of writing and neither has been reviewed to a conclusion, so this rung is
claimed as submitted upstream and nothing more.

[oMLX PR #3762](https://github.com/jundot/omlx/pull/3762) gives the Anthropic
`/v1/messages` endpoint the per-request SpecPrefill fields the
OpenAI-compatible endpoint already had. That is its entire scope, and it
changes no upstream default. The three-regime picture from
levels 3 to 5 became a default only in my own deployment, where the agent
transport now runs dense unless a request says otherwise.

## Using the ladder

A finding inherits the weight of its highest supporting level and the caveats
of the lowest one it depends on. The cold prefill speedup is level 1 and
nothing above it contradicts it, so it stands as stated. The claim that sparse
prefill hurts agent sessions is level 5 for mechanism and level 4 for
magnitude, which is why this repository states the mechanism confidently and
refuses to state a slowdown ratio at all.
