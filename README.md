# llm-inference-systems

An agent's next request is mostly its previous request again. So when an
inference optimization is judged on the one request it speeds up, the part
that matters most to an agent goes unmeasured: what that request leaves behind
for the ones after it.

This repository is where I study that. It holds one experiment so far, with
its data, its figures, the runtime work behind it, and the two upstream pull
requests that came out of it.

Three principles run through it.

**Optimize the workload, not the microbenchmark.** A microbenchmark answers a
question nobody is asking in production. The same change that cut cold
long-context time-to-first-token by three to four times made one real coding
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
— reusable state economics in interactive inference. SpecPrefill, an
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
every suffix was sparsified, so the checkpoint never recovered. SpecPrefill
did not cause the cliff; it is the reason the cliff was never repaired. The
recomputation that accumulates from there is the prefix-cache debt. The
request-level trace in `data/exp-001/` shows the checkpoint pinned at 28,672
tokens for ten consecutive requests while the suffix climbs from 17,060 to
33,979.

Start with the experiment README, then Figure 3.

## What was engineered

The finding was not available to someone who only benchmarked. Getting to it
meant building, in order:

- a heterogeneous prefill path splitting the model's layers between the GPU
  and the neural engine, on a 1024-token tile matched to the cache block
- SpecPrefill composed on top of it, and the measurement showing the two
  stack at 95-97% of their ideal product
- a measured, template-independent protected-prefix boundary, after the
  inferred one was found to fall 37 tokens short with tools present
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

[`figures/`](figures/) has nine figures as SVG and PNG, with
[`figures/README.md`](figures/README.md) giving a caption and an evidence
level for each. Five are measured; four are labelled diagrams with no
measured data. Everything is redrawn by one script that reads only `data/`:

    uv run --with matplotlib python figures/plot.py

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

One study is finished. Four other subjects have real measurement behind them
and no answer yet, and they are filed as threads rather than experiments so
the difference stays visible:
[speculative decoding](research-threads/speculative-decoding.md) (431
sequences: acceptance tracks the model, not the task),
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
memory, a 27B-class MoE model at 4-bit. Two exceptions, both on the same
machine: the replication in EXP-001 is that same model on an older build of
the server, and the speculative-decoding thread uses two 35B-A3B models at
6-bit. Neither is a second platform. One machine, one vendor, one runtime.
Read every result with that in front of you.

## Author and licence

Oliver Yu (tc3oliver) — <https://github.com/tc3oliver/llm-inference-systems>.
Code is MIT ([`LICENSE`](LICENSE)); prose and figures are CC BY 4.0
([`LICENSE-CONTENT.md`](LICENSE-CONTENT.md)).
