# llm-inference-systems

An agent's next request is mostly its previous request again. So when an
inference optimization is judged on the one request it speeds up, the part
that matters most to an agent goes unmeasured: what that request leaves behind
for the ones after it.

This repository is where I study that. It holds three experiments, with their
data, their figures, the runtime work behind them, and the upstream pull
requests that came out of them.

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

Start with the experiment README, then Figure 3. Written up at length in
[當 prefill 變快，agent 反而變慢](https://study.meowcoder.com/posts/260920-inference-reusable-state/)
(Traditional Chinese).

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

Start with that experiment's README, then Figure 11. Written up at length in
[推測解碼何時真的會加速？](https://study.meowcoder.com/posts/260920-speculative-decoding-cost-model/)
(Traditional Chinese).

[`experiments/exp-003-progressive-shadow-prefill/`](experiments/exp-003-progressive-shadow-prefill/)
— repaying the debt, and finding out that affording it was never the problem.
EXP-001 ended asking whether the reusable state a sparse prefill fails to create
could be rebuilt in the background. It can: a dense re-read owned by the
scheduler, running only while the engine is idle, was runnable on 32 scheduler
steps, received 231.75 seconds of service — 53% of the session's wall time —
read 24,575 of its 24,576-token target, and cost the foreground 48.00 s against
the sparse control's 47.93 s. Then the session ended with a canonical prefix of
zero, because every block it published came back at the next restore as a
placeholder and was rejected.

Starvation, recovery throughput and foreground contention were each refuted by
the runtime's own counters, which is the whole value of instrumenting the
background task rather than timing the session. What was left was publication.
A fourth arm isolated it: one arm published once and stopped at a committed
prefix of 4,096 tokens, the other published five times and reached 20,480 — and
the probe restored zero from both, in the same 48.00 s. Progressive publication
did exactly what it was built to do, and it was still not what was missing.

What was missing has since been found. `_get_boundary_store_override` returns
the boundary snapshot as its payload, and that snapshot holds the model's
non-sliceable layers alone: 48 of its 64. Stored as `cache_data`, the block is
stamped `num_layers: 48`, and a later restore compares that with 64, reads it
as cross-model contamination, and discards the whole chain it has just matched.
Publication now stores the live cache instead, and state the recovery job
published is restored by the ordinary serving path: 12,288 tokens match across
3 blocks, 64 layers reconstruct, the request attaches with 12,288 cached and
4,121 left to prefill, time to first token falls from 67.34 s cold to 18.72 s,
and the completion is byte-identical to the dense reference. That is one
matched comparison, and it settles whether published state is restorable rather
than how fast it is produced.

Two of the numbers above belong to a build measured before that fix. The job
read 24,575 of its 24,576-token target because it could not reach that target:
the target was floored to a cache block and the prefill path holds the last
token of a range back for the generation kickoff. The 4,096 against 20,480 was
measured with that defect and one other in the build. Both were fixed on
2026-09-21, the direction of the gap between the two publication modes survives,
and its size is not established.

Five rounds after that fix changed what the experiment is about. It was built
around Spec Exit — the turn on which a session stops taking the sparse route —
as its outcome, and the runs refuted the objective rather than the mechanism.
Progressive canonical state recovery is worth running because it shrinks the
suffix the next turn has to prefill, not because it ends the sparse route: the
exit is not the outcome, the tail is. At a 15 s idle gap the recovery holds time
to first token flat across seven turns while the sparse control's rises to
42.16 s, for a session 26.9% shorter and no route change at all. The exit turn is
the most expensive turn of its session in every cell that has one, and the
fastest configuration measured never exits. The dense turns in these rounds were
paused by the runtime's prefill memory throttle where the sparse ones were not,
so how much of the gap between the two routes is the route is not established.

One unrelated bug fell out. The prefill OOM-requeue path clears the SpecPrefill
bookkeeping under a comment saying it clears the RoPE patch, and it does not, so
after a requeued memory failure the model keeps that request's position offset
installed for every later request on that engine.

Taking the mechanism through review for upstream then found six defects the
experiment's own workloads could not reach — a second model in the process,
multi-token prediction on, an eviction mid-job, a prompt whose length is an
exact multiple of the cache block. None of them changes a number here, and
that is a statement about the coverage of the runs rather than a defence of
it. They are written up with their invariants in
[`HARDENING.md`](experiments/exp-003-progressive-shadow-prefill/HARDENING.md).

Start with that experiment's README, then Figures 12 to 14. Written up at length
in [償還 reusable state 的債](https://study.meowcoder.com/posts/260921-canonical-state-debt-recovery/)
(Traditional Chinese).

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

[`figures/`](figures/) has fourteen figures as SVG and PNG, with
[`figures/README.md`](figures/README.md) giving a caption and an evidence
level for each. Ten are measured; four are labelled diagrams with no
measured data. Everything is redrawn by three scripts that read only `data/`:

    uv run --with matplotlib python figures/plot.py
    uv run --with matplotlib python figures/plot_exp002.py
    uv run --with matplotlib python figures/plot_exp003.py

[`data/`](data/) holds every number behind every figure, with provenance and
row counts in [`data/README.md`](data/README.md). Nothing in it is smoothed,
interpolated or back-generated.

## Upstream

Ten pull requests went to oMLX as a result — from the experiments, from the
threads, and from running the server the experiments needed. **Two are merged.
Eight are open at the time of writing** and none is a draft; this file will say
so until that changes. An open pull request is a proposal, not an outcome,
which is why the merged ones and the open ones are counted apart.

- [PR #3664](https://github.com/jundot/omlx/pull/3664) — **merged 2026-09-17**.
  `convert_responses_tools()` kept only bare function tools, so a namespace
  group — the shape a Codex client sends for each MCP server — was dropped
  whole and its members never reached the chat template. Found while bringing
  up the agent transport this work runs on.
- [PR #3685](https://github.com/jundot/omlx/pull/3685) — open. The SDPA256
  prefill route chose between two different floating-point reductions from live
  guard headroom, so the same request could take different numeric paths in two
  otherwise identical processes. It belongs to the correctness thread's
  question rather than to any experiment.
- [PR #3746](https://github.com/jundot/omlx/pull/3746) — **merged 2026-09-21**.
  The neural-engine prefill path required a sequence length that block-aware
  caching could not deliver, because boundary snapshots cut every chunk at the
  next cache-block edge. That is the same block-size mismatch the
  heterogeneous-compute thread is about; the merged change makes the geometry
  report itself as impossible instead of recommending a width the accelerator
  cannot accept.
- [PR #3756](https://github.com/jundot/omlx/pull/3756) — open. The correctness
  fix for the protected-prefix boundary.
- [PR #3762](https://github.com/jundot/omlx/pull/3762) — open. Per-request
  SpecPrefill fields on the Anthropic `/v1/messages` endpoint, matching the
  fields the OpenAI-compatible endpoint already had. That is its whole scope,
  and it changes no upstream default. The default-off policy for the agent
  transport is a separate local deployment choice built on that control,
  described in `ENGINEERING.md`.
- [PR #3792](https://github.com/jundot/omlx/pull/3792) — open. The SpecPrefill
  RoPE patch is left installed when a prefill is requeued after OOM, so the
  retry and every request after it run through a stale position offset. Found
  while building EXP-003 and unrelated to it.
- [PR #3793](https://github.com/jundot/omlx/pull/3793) — open. The EXP-003
  mechanism itself: progressive canonical state recovery for sessions served by
  sparse prefill. It carries an open question for the maintainers about whether
  its background-scheduling primitives should converge with related work already
  in progress upstream, and it depends on #3811 below. Six defects found while
  preparing it for review are written up in
  [`HARDENING.md`](experiments/exp-003-progressive-shadow-prefill/HARDENING.md);
  none of them changes a number in `data/`, and the reason is that none of
  them was reachable by the workloads that produced those numbers.
- [PR #3811](https://github.com/jundot/omlx/pull/3811) — open. SpecPrefill wrote
  its selected tokens at compacted rather than original positions on mRoPE VLMs.
  Validating PCSR is what exposed it; PCSR did not cause it, and the defect is
  present with the recovery job switched off. The three-arm control that
  measured what the fix changes is in
  [`data/specprefill-position-efficacy/`](data/specprefill-position-efficacy/),
  and its honest summary is that the positional contract is restored and
  first-token logits move toward the dense baseline while greedy-output
  agreement does not improve on this workload.
- [PR #3840](https://github.com/jundot/omlx/pull/3840) — open. SpecPrefill read
  the draft cache's logical position from `cache[0].offset`, and layer 0 of a
  hybrid model is recurrent with no offset, so a restored draft cache was
  silently scored as empty and the whole prompt re-prefilled on top of it. The
  position is now derived from the model's attention layers, and fails closed
  rather than guessing.
- [PR #3842](https://github.com/jundot/omlx/pull/3842) — open, and stacked on
  #3840: it should not merge first. Draft scoring never published a recurrent
  checkpoint, so every stored block carried a placeholder and the walk-back
  correctly found nothing to restore. Capturing the recurrent state at a
  reachable block boundary is what makes a hybrid draft prefix cache produce a
  hit at all. Both are written up in
  [hybrid draft prefix reuse in SpecPrefill](research-threads/specprefill-draft-cache-reuse.md),
  with the runtime evidence in
  [`data/specprefill-draft-cache-reuse/`](data/specprefill-draft-cache-reuse/).

## Research threads

Three studies are finished. Three other subjects have real measurement behind
them and no answer yet, and they are filed as threads rather than experiments so
the difference stays visible:
[correctness](research-threads/inference-correctness.md) (five
optimizations, five different answers on whether the difference reaches the
output), [heterogeneous compute](research-threads/heterogeneous-compute.md)
(an accelerator that compiled and never ran), and
[cross-runtime](research-threads/cross-runtime-observations.md) (no
controlled comparison exists, stated plainly).

[`research-threads/`](research-threads/) holds eight more pages in seven other
states, and [`RESEARCH.md`](RESEARCH.md) sorts them: one thread with five
measured findings of its own on
[background work under foreground QoS](research-threads/background-work-under-foreground-qos.md),
two whose mechanism is established and whose upstream validation is pending —
[hybrid draft prefix reuse](research-threads/specprefill-draft-cache-reuse.md)
and
[prefix-cache instances and their state-preservation contracts](research-threads/prefix-cache-instance-consistency.md),
the second keeping its original, still unanswered question apart from the half
that now has one — one closed at its design gate,
[the moving draft scoring-window origin](research-threads/specprefill-scoring-window-origin.md),
where removing a cache miss would have meant changing what the scorer computes
and the dense-target comparison did not support it — one matched pair showing that
[adopting recovered state less eagerly than it is published](research-threads/pcsr-foreground-adoption-grain.md)
removes most of the resulting rescores without slowing recovery — one promoted thread kept as it was written, one recorded candidate with no experiment open, and one internal map
from every PCSR claim to its dataset and its regression test.

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

Outside this repository, <https://meowcoder.com/work/llm-inference-systems/>
is the short version of the whole program — what it investigates, what it
found, and what changed as a result — for a reader who has not decided yet
whether to open the data.

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
