# Negative results

Four hypotheses I held during EXP-001, acted on, and could not sustain. Each
is stated as I believed it, then as the evidence left it, and they are
organized by hypothesis rather than by the order in which they broke.

## Always-on sparse prefill improves agent sessions

False under continuation-heavy workloads that reach the cache cliff.

The hypothesis was reasonable on the microbenchmark: the cold prefill path got
three to four times faster, agent sessions are full of large prompts, so agent
sessions should get faster. What actually happens is that sparse prefill output
is not eligible for the prefix cache, so a session that uses it stops
advancing its reusable checkpoint. In the request-level trace the checkpoint
holds at 28,672 tokens for ten consecutive requests after the cliff while the
uncached suffix grows from 17,060 to 33,979. The per-turn hit rate in the real
session arms falls 88.2% → 63.9% → 51.0% → 40.7% → 27.1% while the dense arm
stays between 75.6% and 98.6%.

The correct scope for the optimization is the disposable request, where there
is no later request to harm.

## Background dense recovery gets both properties back

True in the controlled workload I ran, and false in it too once context
growth outran recovery. Six of the eight cells are single runs.

Serve the request sparse, then quietly recompute it dense while the user is
reading — on the synthetic workload this worked. At 15 s of think time the
session goes from 108.7 s to 83.1 s, and at 10 s from 108.1 s to 84.5 s. About
24% either way.

The condition hiding inside that result is idle time. At 5 s the gain is nearly
gone, 108.1 s against 104.4 s. At zero idle the hybrid arm runs 119.7 s against
108.1 s dense, 11% slower, because the recovery work never gets scheduled and
the session pays the sparse penalty with none of the repayment. Recovery
throughput has to exceed the rate at which the context grows, and in a busy
agent session it does not.

## Forcing a dense request after a sparse one repays the debt

Structurally negative in the observed regime. 0/20 positive across observed
events.

If sparse prefill breaks the checkpoint, then a following dense request should
rebuild it and amortize the cost. In the trace it never did, and the reason is
arithmetic rather than implementation. The dense request that would repay the
debt has to recompute the entire suffix accumulated since the last good
checkpoint, and that suffix is larger than the work the sparse requests
saved. I want to be exact about what this is: I read it off the twenty
restores that already existed. I did not run an arm that forces a dense
request after every sparse one.
The saving is bounded by the fraction of tokens the selector drops. The
recovery cost grows with the session.

Across the twenty restores in the trace, no request recovered the checkpoint.
It sat at 28,672 from request 11 to request 20, and the stores stopped
entirely after 44,032 tokens.

## A synthetic interactive workload is enough validation

False. Real workload geometry changed the conclusion.

The synthetic workload was built to model an interactive session, and it did
model one — it even produced the zero-idle warning that the real sessions later
confirmed. What it could not produce was the shape of a real agent's context:
how fast the prompt grows, how often tools fire, how much of the previous turn
survives into the next one. Those are the variables that decide whether a
session reaches the cliff, stays below the threshold entirely, or sits between
the two.

Three real observations produced three regimes. The synthetic workload produced
one, and it was the one where the optimization looks good.
