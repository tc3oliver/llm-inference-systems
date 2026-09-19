# Findings

Five questions, in the order that makes the argument. They are not in the order
I asked them.

---

## 1. Can cold prefill be made faster?

Yes, by roughly three to four times, and the effect is larger on longer
prompts.

| prompt | dense TTFT | accelerated TTFT | dense prefill | accelerated prefill |
|---|---:|---:|---:|---:|
| 14.3K | 47.0 s | 11.9 s | — | — |
| 16K | 57.84 s | 19.24 s | 302 tok/s | 1046 tok/s |
| 32K | 122.7 s | 33.5 s | 277 tok/s | 1112 tok/s |

Dense prefill throughput falls as the prompt grows, 302 tok/s at 16K down to
277 tok/s at 32K, which is the ordinary quadratic pressure of attention. The
accelerated path goes the other way, 1046 to 1112 tok/s, because a larger
prompt gives the token selector more to discard.

There is also an isolated qualification run stacking the accelerator and sparse
prefill on a 16K prompt: about 1328 tok/s against about 300 tok/s on the same
shape, with the two mechanisms composing at 95-97% of the ideal product of
their separate speedups. Two independent mechanisms that nearly multiply is
worth knowing, and it is also the most fragile number here, being a smoke run
with one resident engine rather than a serving configuration.

`figures/fig1-cold-prefill.svg`.

**Evidence level:** measured, single run per cell, controlled build and model.
This is a microbenchmark and it behaves like one.

---

## 2. Does a faster cold request make the session faster?

No. Under a continuation-heavy agent workload it made the session worse, and
the mechanism is that sparse prefill output is not eligible for the prefix
cache. The request is served. It leaves no checkpoint. The reusable state the
next request would have restored from does not advance.

Cache hit rate per turn, one real coding-agent session per arm:

| turn | dense hit | sparse hit | sparse scorer calls |
|---:|---:|---:|---:|
| 0 | 0.0% | 0.0% | 0 |
| 1 | 82.2% | 88.2% | 0 |
| 2 | 94.0% | 63.9% | 12 |
| 3 | 75.6% | 51.0% | 7 |
| 4 | 98.6% | 40.7% | 13 |
| 8 | 98.1% | 27.1% | 2 |

Turn 0 is 0.0% in both arms and took no sparse path in either; it is a cold
start with nothing to restore. Turn 1 is the last turn where sparse is ahead,
and it is ahead because it has not yet done anything: zero scorer calls means
the threshold was not crossed. From turn 2 the scorer engages and the two arms
separate and keep separating. By turn 8 the dense arm is restoring 98.1% of its
context and the sparse arm is restoring 27.1%.

Session wall-clock came out at about 1676 s dense, 4404 s sparse and 5248 s
hybrid. I report those because I have them and it would be dishonest to hide
the magnitude that started the investigation, but they are n=1 per arm and the
agents took different trajectories through different work, so that spread is
not a measurement of how much slower sparse prefill is. It is what made me go
and build a trace where the mechanism could be seen directly.

**Evidence level:** measured, n=1 per arm, uncontrolled trajectories. The hit
rate column is the signal; the wall-clock column is an observation that
motivated further work and nothing more.

---

## 3. What happens after the cliff?

The checkpoint stops advancing, the suffix grows without bound within the
session, and the cost of the optimization itself grows with it. This is the
strongest evidence in the study. It comes from a single continuous session
traced at request level, with no background densification in the build, so
there is no second mechanism available to explain the shape.

Twenty consecutive prefix-cache restores. The reusable checkpoint climbs
28,672 -> 32,768 -> 33,792 -> 36,864 -> 37,888 over the first ten requests. At
request 11 it comes back as 28,672 and it is pinned at 28,672 for the remaining
ten requests. Nothing recovers it. The uncached suffix each request must
recompute goes from 17,060 tokens at the cliff to 33,979 at request 20 —
roughly double — while the prompt itself grows far less than that. The gap is
what the session pays for having accelerated request 11.

Checkpoint writes tell the same story from the other side. Seven stores, ending
at 44,032 tokens, and then nothing. No further checkpoint is ever written,
because a sparse prefill has nothing the cache will accept.

The proximate cause is logged at the transition, and it is the cache layer
doing the right thing:

    ArraysCache layer 0: partial prefix match detected (placeholder in last
    matched block). Rejecting cache to prevent stale GDN state. Request will
    reprocess from scratch.

A sparse prefill leaves a placeholder in the block it did not fully compute.
The cache refuses to build on a block it cannot vouch for. Accepting it would
mean serving later requests from state that was never computed, which is a
correctness failure and much worse than being slow. So the last good checkpoint
is the one from before sparse prefill began, and that is the 28,672 the trace
is stuck at.

The scorer makes it compound. Its cost grows with the suffix, not smoothly
(the second call is 2.4 s at 16,918 tokens, below the first) but decisively:
2.7 s at 8,535 tokens scored, 5.7 s at 33,389. The token selector that exists to make
prefill cheaper becomes more expensive in proportion to the recomputation it
caused.

`figures/fig3-cache-cliff.svg` and `figures/fig4-scorer-cost.svg`.

**Evidence level:** measured, request-level, one session, twenty consecutive
observations of the same mechanism with a logged proximate cause. One session
is one session. But a monotone shape across twenty requests, with a cache
rejection message that explains exactly that shape, is a different quality of
evidence from an A/B pair.

---

## 4. Can the debt be repaid in the background?

Partly, and only where there is idle time to repay it in. The synthetic
interactive workload varies the think time between turns and runs a dense-only
arm against a hybrid arm that does sparse prefill and then densifies in the
background:

| think time | dense only | hybrid |
|---|---:|---:|
| 15 s | 108.7 s | 83.1 / 83.4 s |
| 10 s | 108.1 s | 84.5 s |
| 5 s | 108.1 s | 104.4 / 105.4 s |
| 0 s | 108.1 s | 119.7 s |

At 15 s of idle the hybrid arm is about 24% faster and at 10 s about 22%, and
the 15 s cell has a repeat run landing within 0.3 s of the first. At 5 s the advantage is
almost gone. At zero idle the hybrid arm is 119.7 s against 108.1 s dense: 11%
slower, because recovery never gets to run and the session pays the sparse
penalty with none of the repayment.

The zero-idle row belongs next to the 24% every time the 24% is quoted.
Presenting the improvement without it is selective reporting. The row is the
controlled evidence for this study's own claim that recovery throughput has
to outrun context growth. It also showed up in the
synthetic workload before the real sessions confirmed it, which is the one
place here where the synthetic result predicted the real one.

`figures/fig5-think-time.svg`.

**Evidence level:** measured, synthetic workload, n=1 for six of the eight
cells and n=2 for the two hybrid cells at 15 s and 5 s of idle. Controlled,
and not real.

---

## 5. Does the synthetic win generalize?

No, and the way it fails is more interesting than a straight refutation. A
different real agent, on the same server with sparse prefill enabled, held its
cache hit rate at roughly 84-86% across the session, never presented an
uncached suffix larger than about 2.5K tokens, and made zero scorer calls.
Sparse prefill was enabled for the entire session and never once triggered,
because the 8192-token threshold was never crossed. The prefix cache stayed
healthy, so there was nothing for the optimization to do.

That gives three regimes rather than a verdict:

- A disposable cold request with no follow-up. No reusable state is wasted
  because none would have been reused, and sparse prefill wins outright — this
  is question 1.
- A continuation-heavy session that reaches the cliff. The checkpoint stops
  advancing and every later request repays it, and sparse prefill loses — this
  is question 3.
- A healthy incremental session. The suffix stays small, the threshold is never
  crossed, and sparse prefill is inert.

`figures/fig6-three-regimes.svg`, which is a diagram. The third-regime
figures are in `data/exp-001/session-aggregates.csv`.

The practical consequence is that "is this optimization good" has no answer at
the level the question is usually asked. It has three answers, and which one
applies is a property of the workload's geometry rather than of the kernel.

**Evidence level:** one real session, figures stated as approximate because
they are read off session-level aggregates rather than a request-level trace.
Weaker than section 3, and I would not build a policy on it alone. It is enough
to refute a universal claim, which is all it is used for.

---

## Correctness

The static prefix boundary — the region of the prompt sparse prefill is
forbidden to drop tokens from — was derived by subtraction, and the derivation
fell short of the real boundary. By as little as 37 tokens once tools were in
play. That put the tail of the tool instructions and the beginning of the
operator's own system prompt inside the region the optimization was allowed to
discard.

Thirty-seven tokens is enough. It is a tool's closing schema, or the first
sentence of an operator instruction. The failure is silent: nothing crashes,
latency looks fine, and the model is quietly working from a prompt that is not
the prompt that was sent. An optimization that changes protected prompt
semantics is invalid whatever it does for latency, and this one was invalid
until it was fixed. The fix is submitted as oMLX PR
[#3756](https://github.com/jundot/omlx/pull/3756), open at the time of
writing.

This sits under findings rather than in a caveat section because it is one, and
because of when it was found: the boundary defect was present during the
performance work above, which means a latency comparison had been running
against a configuration that was not semantically equivalent to its baseline.

**Evidence level:** a code defect with a fix submitted upstream. Not a
measurement.

---

## Deployment

What shipped locally is a policy at the transport level rather than a change
to the kernel. The transport the agents use defaults to dense, with an explicit
per-request sparse override for callers that know their request is
disposable; the long-context transport keeps its existing behaviour. The
decision is made where the request shape is known, which is the only place it
can be made correctly, because the runtime cannot tell a one-shot prompt from
turn 11 of a session.

What went upstream is the control that policy needs, not the policy: oMLX PR
[#3762](https://github.com/jundot/omlx/pull/3762) adds the per-request sparse
prefill fields to the Anthropic messages endpoint, which silently dropped them
before, and changes no default on either endpoint. It is open at the time of
writing. The default-off choice is a deployment decision for this serving
setup, not a recommendation for anyone else's.

**Evidence level:** a local deployment change and an open upstream pull
request. Not a measurement.

---

## Appendix

One ANE runtime latch, observed once during the study and not reproducible
since: 4/4 pass on a sanitized reproduction at the matching shape. It is
recorded so the observation is not lost. It plays no part in any argument above
and nothing in this study depends on it.
