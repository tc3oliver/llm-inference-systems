# llm-inference-systems

An agent's next request is mostly its previous request again. So when an
inference optimization is judged on the one request it speeds up, the part
that matters most to an agent goes unmeasured: what that request leaves behind
for the ones after it.

This repository is where I study that. It holds one experiment so far, with
its data, its figures, and the two upstream changes that came out of it.

Three principles run through it.

**Optimize the workload, not the microbenchmark.** A microbenchmark answers a
question nobody is asking in production. The same change that cut cold
long-context time-to-first-token by three to four times made a real coding
agent session slower, and only a workload-shaped test could show that.

**Fast but wrong is a regression.** While measuring throughput I found that
the boundary protecting the system prompt was derived by subtraction and could
fall as little as 37 tokens short of the real boundary once tools were in
play. An optimization that changes what the model is allowed to see is invalid
whatever it does for latency.

**The cost of an optimization includes the reusable state it creates, or fails
to create.** This is the claim EXP-001 exists to support.

## The experiment

[`experiments/exp-001-reusable-state-economics/`](experiments/exp-001-reusable-state-economics/)
— reusable state economics in interactive inference. Sparse prefill makes a
cold request much faster and leaves nothing behind in the prefix cache. In a
continuation-heavy session the reusable checkpoint stops advancing, the
uncached suffix grows, and the session pays back more than the acceleration
saved. The request-level trace in `data/exp-001/` shows the checkpoint pinned
at 28,672 tokens for ten consecutive requests while the suffix climbs from
17,060 to 33,979.

Start with the experiment README, then Figure 3.

## Figures and data

[`figures/`](figures/) has six figures as SVG and PNG, with
[`figures/README.md`](figures/README.md) giving a caption and an evidence
level for each. Four are measured; two are labelled diagrams with no measured
data. Everything is redrawn by one script that reads only `data/`:

    uv run --with matplotlib python figures/plot.py

[`data/`](data/) holds every number behind every figure, with provenance and
row counts in [`data/README.md`](data/README.md). Nothing in it is smoothed,
interpolated or back-generated.

## Upstream

Two changes went into oMLX as a result:

- [PR #3756](https://github.com/jundot/omlx/pull/3756) — the correctness fix
  for the prefix boundary.
- [PR #3762](https://github.com/jundot/omlx/pull/3762) — the deployment
  policy: the continuation-heavy agent path defaults to dense prefill with an
  explicit per-request sparse override, and the long-context path keeps its
  existing behaviour.

## How to read the rest

[`RESEARCH.md`](RESEARCH.md) is the program and the open questions.
[`EVIDENCE.md`](EVIDENCE.md) is the evidence ladder the findings are graded
against. [`docs/terminology.md`](docs/terminology.md) defines the two terms
this study introduces and the supporting vocabulary.
[`docs/reproducibility.md`](docs/reproducibility.md) says what can be
reproduced here and what cannot.
[`DATA_POLICY.md`](DATA_POLICY.md) says what gets published and what never
does.

## Platform

Everything here was run on one machine: Apple silicon, M4 Max, 64GB unified
memory, a 27B-class MoE model at 4-bit. One machine, one model family, one
runtime. Read every result with that in front of you.

## Author and licence

Oliver Yu (tc3oliver) — <https://github.com/tc3oliver/llm-inference-systems>.
Code is MIT ([`LICENSE`](LICENSE)); prose and figures are CC BY 4.0
([`LICENSE-CONTENT.md`](LICENSE-CONTENT.md)).
