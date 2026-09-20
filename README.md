# llm-inference-systems

An agent's next request is mostly its previous request again. So when an
inference optimization is judged on the one request it speeds up, the part
that matters most to an agent goes unmeasured: what that request leaves behind
for the ones after it.

This repository is where I study that. It holds two experiments, with their
data, their figures, the runtime work behind them, and the two upstream pull
requests that came out of the first one.

Three principles run through it.

**Optimize the workload, not the microbenchmark.** A microbenchmark answers a
question nobody is asking in production. The same change that cut cold
long-context time-to-first-token by three to four times was followed by a
real coding-agent session that came out slower. The two agents took
different trajectories, so that wall-clock gap is the observation that
started the investigation, not a measured effect size. What the investigation
then found, request by request, is the substance of this repository.

**Fast but wrong is a regression.** While measuring throughput I found that
the boundary protecting the system prompt was derived by subtraction, and in
the study's own configuration it fell as little as 37 tokens short of the
real boundary once tools were in play. Tokens the runtime contract required
to stay fully computed became eligible for sparse processing. I did not
measure a downstream semantic failure from it, so I do not claim the model
ignored those instructions; a protected-prefix contract violation is a
defect on its own terms, whatever it does for latency.

**The cost of an optimization includes the reusable state it creates, or fails
to create.** This is the claim EXP-001 exists to support.

## The experiments

[`experiments/exp-001-reusable-state-economics/`](experiments/exp-001-reusable-state-economics/)
— reusable state dynamics in interactive inference. SpecPrefill, an
attention-based sparse prefill mechanism, makes a cold request much faster,
and a sparsified suffix does not advance the normal reusable dense prefix
state. In a continuation-heavy session the reusable checkpoint stops
advancing, the uncached suffix grows, and the session pays back more than the
acceleration saved.

The order in the trace decides what caused what. Request 10 restored 37,888
tokens with a 6,902-token suffix, below the 8192-token threshold, and ran
dense. The restore at request 11 found only 28,672 tokens, because the cache
layer rejected a partial prefix match to avoid stale state. That restore is
the cache cliff, and it happened before any sparse admission. The 17,060-token
miss it left crossed the threshold, SpecPrefill engaged, and from then on
every observed suffix was sparsified, so the checkpoint never recovered. The
request-11 sparse admission did not cause the request-11 cliff, because the
restore came first; but the log names a partial match whose last matched
block held a placeholder, and the surviving trace does not establish when or
how that placeholder was created. What the trace does establish is
everything after the cliff. The recomputation accumulating from there is the
prefix-cache debt. The
request-level trace in `data/exp-001/` shows the checkpoint pinned at 28,672
tokens for ten consecutive requests while the suffix climbs from 17,060 to
33,979.

Start with the experiment README, then Figure 3.

[`experiments/exp-002-speculative-decoding-economics/`](experiments/exp-002-speculative-decoding-economics/)
— a cost model for speculative decoding. The number everybody reports for this
mechanism is the acceptance rate. It is the wrong number. What decides whether
speculation makes a request finish sooner is the price of one verify cycle
measured in dense decode steps, and that price belongs to the model's
architecture. On a 35B mixture-of-experts a four-position verify forward costs
2.43 dense steps, so a fixed draft depth of 3 comes out 10% slower than dense
decoding on code and 43% slower on prose. On a dense 27B the same forward costs
1.37 and the mechanism is 1.81x faster on a matched coding prompt. Both models,
same runtime, same prompts, 36 matched runs.

The runtime's adaptive depth controller already handles this. It turned every
loss into parity or a small deficit, and in the one cell where the fixed depth
won it won by more, by drafting shallower and buying a cheaper cycle. So the
finding came with no upstream proposal attached, which is the honest outcome
when the code under test is already right.

One thing it does not settle. With speculation on, greedy generation stopped
being reproducible: two runs of one prompt gave two different completions, both
different from the dense one. Whether that difference reaches the answer was not
measured, and saying it does not would be as unfounded as saying it does. It is
filed as the fourth case in the
[correctness thread](research-threads/inference-correctness.md).

Start with that experiment's README, then Figure 11.

## What was engineered

The finding was not available to someone who only benchmarked. Getting to it
meant building, in order:

- a heterogeneous prefill path splitting the model's layers between the GPU
  and the neural engine, on a 1024-token tile matched to the cache block
- SpecPrefill composed on top of it, and the measurement showing the two
  stack at 95-97% of their ideal product
- a measured, template-independent protected-prefix boundary, after the
  inferred one was found to fall 37 tokens short with tools present in this
  configuration
- a background job that rebuilds the dense prefix a sparse request skipped,
  designed to fail closed, publishing every completed 1024-token block as a
  usable checkpoint
- a scheduler that lets that job yield to requests: an inbound-request
  counter raised before executor hand-off, and a two-idle-step gate before a
  slice may start, which in the one run measured took foreground decode from
  13.5 to 47 tok/s
- request-level instrumentation of checkpoint position and uncached suffix,
  which is what made the trace in Figure 3 possible
- a transport-level request policy, once the real workload showed no single
  configuration was right for every request

[`ENGINEERING.md`](ENGINEERING.md) walks through those stages, what each one
assumed, and which stage broke that assumption. The background job and its
scheduler were an experimental branch, and a later review found four gaps in
them: [Prototype safety review](ENGINEERING.md#prototype-safety-review). None
of that code is in the served build.

## Figures and data

[`figures/`](figures/) has eleven figures as SVG and PNG, with
[`figures/README.md`](figures/README.md) giving a caption and an evidence
level for each. Seven are measured; four are labelled diagrams with no
measured data. Everything is redrawn by two scripts that read only `data/`:

    uv run --with matplotlib python figures/plot.py
    uv run --with matplotlib python figures/plot_exp002.py

[`data/`](data/) holds every number behind every figure, with provenance and
row counts in [`data/README.md`](data/README.md). Nothing in it is smoothed,
interpolated or back-generated.

## Upstream

Two pull requests went to oMLX as a result. Both are open at the time of
writing; this file will say so until that changes.

- [PR #3756](https://github.com/jundot/omlx/pull/3756) — the correctness fix
  for the protected-prefix boundary.
- [PR #3762](https://github.com/jundot/omlx/pull/3762) — per-request
  SpecPrefill fields on the Anthropic `/v1/messages` endpoint, matching the
  fields the OpenAI-compatible endpoint already had. That is its whole scope,
  and it changes no upstream default. The default-off policy for the agent
  transport is a separate local deployment choice built on that control,
  described in `ENGINEERING.md`.

## Research threads

Two studies are finished. Three other subjects have real measurement behind them
and no answer yet, and they are filed as threads rather than experiments so
the difference stays visible:
[correctness](research-threads/inference-correctness.md) (three
optimizations, three different answers on whether the difference reaches the
output), [heterogeneous compute](research-threads/heterogeneous-compute.md)
(an accelerator that compiled and never ran), and
[cross-runtime](research-threads/cross-runtime-observations.md) (no
controlled comparison exists, stated plainly).

Each thread ends with the specific thing that would promote it to an
experiment. None of those things was run in order to write these pages.

## How to read the rest

[`ENGINEERING.md`](ENGINEERING.md) is the system as it evolved.
[`RESEARCH.md`](RESEARCH.md) maps what is finished, what is a thread, and
what is still open.
[`EVIDENCE.md`](EVIDENCE.md) is the evidence ladder the findings are graded
against. [`docs/terminology.md`](docs/terminology.md) defines the two terms
this study introduces and the supporting vocabulary.
[`docs/reproducibility.md`](docs/reproducibility.md) says what can be
reproduced here and what cannot.
[`DATA_POLICY.md`](DATA_POLICY.md) says what gets published and what never
does.

## Platform

Everything here was run on one machine: Apple silicon, M4 Max, 64GB unified
memory, a 27B-class dense model at 4-bit. Three exceptions, all on the same
machine: the replication in EXP-001 is that same model on an older build of
the server; the speculative-decoding thread uses two 35B-A3B models at 6-bit;
and EXP-002 measures the 35B-A3B and the 27B side by side, which is the closest
thing here to a second configuration and is still one machine. One vendor, one
runtime. Read every result with that in front of you.

## Author and licence

Oliver Yu (tc3oliver) — <https://github.com/tc3oliver/llm-inference-systems>.
Code is MIT ([`LICENSE`](LICENSE)); prose and figures are CC BY 4.0
([`LICENSE-CONTENT.md`](LICENSE-CONTENT.md)).
