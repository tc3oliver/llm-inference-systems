# Claim

The cost of an inference optimization must include the reusable state it
creates, or fails to create, for later requests.

A single-request latency measurement reads one axis. It asks what this request
paid. In an interactive system the request also either leaves a reusable
checkpoint behind or does not, and that second axis decides what every
subsequent request in the session will pay. An optimization that serves request
11 three times faster and destroys the checkpoint has not made the session
faster; it has moved cost forward in time, out of the number being reported and
into requests 12 through 20 where nobody is looking.

The latency numbers are real. The 16K cold prefill went from 57.84 s to
19.24 s, and on a disposable single-shot prompt that is the whole story,
because no later request exists to be harmed. What the number leaves out is
the state, and in a session the state is most of the cost.

`figures/fig2-two-axes.svg` draws the two axes. It is a diagram, not data.

## Two terms introduced here

Both terms below are introduced by this study. Neither is established
vocabulary, and I use them because I needed to name two things that kept
recurring in the traces and had no short name.

**Cache cliff** — the event where the reusable dense checkpoint falls behind
the current context. In the clean trace it is a single request: the checkpoint
sits at 37,888 tokens, the next restore comes back at 28,672, and it never
advances again. Across the ten restores before the cliff the uncached suffix
ranges from 176 to 6,902 tokens. Across the ten after it, from 17,060 to
33,979.

**Prefix-cache debt** — the recomputation accumulated after the cliff, once
that state fails to recover. It is not a fixed penalty. It compounds, because
the context keeps growing while the checkpoint does not, so each request
recomputes a longer suffix than the one before it: 17,060 tokens at the cliff,
33,979 tokens ten requests later. The token-selection scorer scales with it
too, which means the mechanism that caused the debt gets more expensive as the
debt grows.

The distinction matters when deciding what to do about it. A cliff is an event
and you can try to prevent it. Debt is a stock and repaying it requires
throughput that exceeds the rate at which the context is growing — which is the
condition that the think-time measurements in [FINDINGS.md](FINDINGS.md) show
is not always available.
