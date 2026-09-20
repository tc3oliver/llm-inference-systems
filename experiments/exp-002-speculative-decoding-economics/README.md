# EXP-002 — A Cost Model for Speculative Decoding Under Interactive Workloads

Native multi-token prediction drafts several tokens cheaply and verifies them
in one pass of the full model. The number everyone reports is the acceptance
rate, and on the model this machine served for most of September it sits near
80%. The
[research thread this experiment grew out of](../../research-threads/speculative-decoding.md)
had 431 sequences of that number and could not say whether any of it made a
request finish sooner, because no arm of it ran the same prompt with the
mechanism off.

Thirty-six matched runs later, the answer is that acceptance is the wrong
number to look at. What decides whether speculation pays is the price of one
verify cycle measured in dense decode steps, and that price is a property of
the model's architecture, not of the content. On a dense 27B a four-position
verify forward costs 1.37 dense steps; on a 35B mixture-of-experts with 3B
active parameters the same forward costs 2.43. The first model wins 1.81x from
speculation. The second one loses, on the same runtime, at a higher acceptance
rate, in four workload cells out of five.

**Primary claim.** Whether speculative decoding reduces latency is predicted by
`tokens per verify cycle` against `cycle cost in dense steps`, both of which the
runtime already measures, and not by the acceptance rate on its own. Where the
two disagree, the cost ratio is right.

**Secondary result.** The adaptive depth controller already in this runtime
avoids every losing region the experiment found. It does so by dropping draft
depth and, where that is not enough, parking the sequence back onto the standard
decoder. No upstream change is proposed, because none is needed.

## Status of each claim

The study is closed at these strengths, and the difference between the rows is
the part worth reading.

| | |
|---|---|
| Performance mechanism | **established** — the cost ratio is measured, and it predicts the matched speedup across 0.56x–1.81x |
| Adaptive-controller behaviour | **established for the tested cells** — five cells on one model, one on the other |
| Production speed decision | **established for the tested production-like workload** — one matched cell on the served model, single request, cold cache |
| Semantic equivalence to dense decoding | **not established** — the outputs differ and nothing here measured whether the difference matters |
| Cross-model or cross-hardware generalization | **not established** — two models, one machine, one runtime |

The unresolved correctness question is not pursued further here. It belongs to
[the correctness thread](../../research-threads/inference-correctness.md), which
already names it as a gap and now records what EXP-002 measured of it.

## What was measured

Five workload cells across two models, three policies, two repeats, all on one
machine:

| axis | levels |
|---|---|
| content | generated code, generated prose, verbatim copy of the prompt |
| context | ~3,000 and ~13,600 prompt tokens, prefix caches cleared before every request |
| policy | dense (mechanism off), fixed draft depth 3, the runtime's adaptive controller |
| model | a 35B-A3B mixture-of-experts at 6-bit, a dense 27B at 4-bit |

Output budget 256 tokens, one warm-up per policy group, everything else held
fixed. Platform, sampling, cache state and the exact isolation rules are in
[METHODOLOGY.md](METHODOLOGY.md); the runtime audit that had to come first is
summarized there too.

## What was found

[FINDINGS.md](FINDINGS.md) states each result with its evidence label. In short:

- Fixed depth 3 is slower than dense in four of the five cells it was measured
  in, by 10% on code and by 43% on prose, at acceptance rates of 56% and 25%.
- The adaptive controller turns all four of those losses into parity or a small
  deficit, and beats fixed depth 3 by 12% in the one cell where fixed depth wins.
- On the model this machine actually serves, a dense 27B, the controller is
  1.81x faster than dense decoding on a matched 13.6K-token coding prompt. That
  is the production-relevant number and it is not close.
- A cost model built from the runtime's own timers predicts the measured
  matched speedup across the whole 0.56x–1.81x range, with the largest
  disagreement on a sequence the controller had parked.
- Greedy decoding is bit-reproducible with the mechanism off and is not
  reproducible with it on: two runs of one prompt produced two different
  completions, both differing from the dense one. The runtime's verify path
  routes through different quantized matmul kernels at draft depth 2 and above,
  which is the execution path this divergence is associated with. Whether the
  difference reaches anything a reader would care about was not measured.

## Figures

- [`fig10-cost-ratio-model`](../../figures/fig10-cost-ratio-model.svg) — predicted
  against measured matched decode speedup, every cell.
- [`fig11-policy-by-cell`](../../figures/fig11-policy-by-cell.svg) — dense, fixed
  depth 3 and adaptive, by cell.

Both read `data/exp-002/matched-comparisons.csv` and nothing else.

## Data

[`data/exp-002/`](../../data/exp-002/) holds the 36 measured runs, the 11 matched
comparisons derived from them, and 17 sequences of production telemetry read
from the served instance's own log. No prompt text and no completion text is in
any of it.

Regenerate the comparison table and the figures:

    uv run python -m analysis.exp002
    uv run --with matplotlib python figures/plot_exp002.py

## What this does not establish

[LIMITATIONS.md](LIMITATIONS.md). The short version: one machine, two models,
one runtime, synthetic prompts, single-request concurrency, and a cost model
fitted to nothing — it is arithmetic over measured timers, which is why it is
reported next to the measurement rather than instead of it.
