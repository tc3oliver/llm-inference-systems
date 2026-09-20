# EXP-002 — Methodology

## The audit came first

Nothing was measured until the mechanism had been read out of the source of the
build that would run it. Two things that a configuration file would have told me
incorrectly:

**A draft depth setting is a maximum, not a count.** When `mtp_num_draft_tokens`
is greater than 1 the runtime constructs an adaptive depth controller and every
cycle drafts whatever depth that controller currently prefers. It scores each
candidate depth from a per-position acceptance EMA against a wall-clock cost
EMA, keeps a depth-0 escape hatch that runs an ordinary single-token step, and
after a sustained losing streak parks the sequence back onto the standard
decoder with an exponential re-entry cooldown. So a run configured "at depth 3"
is an adaptive 1..3 run. Only depth 1 is genuinely fixed, because the controller
is not constructed at all below 2.

Pinning a fixed depth of 2 or more therefore needs a code change. The research
build carries one: an environment variable that skips controller construction
and leaves every cycle drafting the configured depth. It is a pin on an
already-exercised path — the multi-row batch policy runs fixed depths that way —
and it changes nothing when unset. It was written before this experiment, for
this purpose, and every run in the fixed-depth arm records in its own notes that
the variable was set on the server process it talked to.

**Per-request acceptance telemetry is not in the inference API by default.** The
served build writes one summary line per finished sequence to its log and
exposes nothing on the response. Under concurrency those lines interleave and
cannot be attributed. The research build carries a second change that publishes
the terminal counters onto the usage object: cycles, accepted, drafted, accept
rate, parked-cycle count, and the runtime's own backbone, head, sampling and
cache-op timers. Every number in this experiment that is labelled as a component
cost is one of those timers, read from the response of the request it belongs
to.

Both changes predate the experiment and are local. Neither is proposed upstream;
see [FINDINGS.md](FINDINGS.md) §6 for why no upstream change is proposed at all.

## Platform

One machine. Apple silicon, M4 Max, 64 GB unified memory, macOS 26.6.2.

| | model A | model B |
|---|---|---|
| identity | Qwen3.6-35B-A3B-oQ6-mtp | Qwen3.8-27B-oQ4e-mtp |
| architecture | mixture of experts, ~3B active | dense, 64 layers |
| quantization | optimised 6-bit | optimised 4-bit, mixed 5-bit layers |
| draft head | in-tree, ships inside the weights | in-tree, ships inside the weights |
| role here | the model the isolated matrix ran on | the model this machine actually serves |

Model B is the one the local serving instance had as its default model at the
time of the experiment. Model A was the default earlier and is the model the
431-sequence observational dataset in the research thread was drawn from, which
is why both are here.

The inference server is a local research build of the runtime, one commit of a
private branch, run as a separate process on its own loopback port with its own
base path, cache directory and log. It is never the long-running instance on
this machine. The long-running instance was stopped before model B was measured,
because 64 GB will not hold two models of that size, and restarted and health
checked afterwards.

## Controls

Held fixed across every arm of a cell:

- the prompt, generated from a fixed seed by `workloads/generator.py`, and the
  task instruction appended to it;
- output budget, 256 tokens;
- request sampling parameters, sent identically in every arm;
- prefix caches, hot and paged, cleared before every single request, so every
  measured run is a cold prefill with zero cached tokens;
- one uncounted warm-up request per policy group, and the model unloaded and
  reloaded whenever a cell's server settings changed, so a load-time setting
  actually took effect;
- `specprefill_enabled`, `turboquant_kv_enabled`, `dflash_enabled`, thinking
  mode, thinking budget and grammar-constrained decoding all off. Grammar
  decoding is a hard disable for the mechanism and the others are separate axes.

The only intended difference between the arms of a cell is the speculation
policy.

## Sampling, and a control that did not hold

The requests asked for `temperature = 0` with a fixed seed. On model A that did
not take effect: the instance's per-model settings carry `force_sampling`, which
by design overrides the request's token-selection parameters, and model A ran at
temperature 0.7 / top-p 0.8 / top-k 20 — which is the configuration the serving
instance uses for it, so the arm is production-shaped rather than greedy. Model B
has no per-model settings entry, so its requests were greedy as asked.

This was found by reading the resolution code after the runs, not before, and it
is recorded here rather than corrected, for two reasons. The comparison is
unaffected: all three policies in a cell went through the same resolution and got
the same sampler, and the acceptance rule mirrors the sampler's temperature and
truncation onto both the draft and the target distribution, so speculation stays
exact under it. And it is the sampling the model is actually served with. What it
does cost is the strong form of the correctness check — see §Correctness.

## Measurement

One JSON record per run, validated against `schemas/run.schema.json`, every
metric nullable and a metric that was not measured written as `null` rather than
zero. No prompt text and no completion text is written to the run file.

Decode time is the client-measured interval from the first content delta to the
last. On model B the stream delivered no incremental deltas — time-to-first-token
and end-to-end were equal to the millisecond — so the client could not measure
decode at all, and the server's own `generation_duration` is used instead. That
substitution is recorded per run in a `decode_time_source` column and both arms
of the model B comparison use it. The two sources are never mixed inside a
comparison.

Per-token decode cost is `decode_s / (output_tokens - 1)`, because the first
emitted token is not a decode step.

## Derived quantities

Three, defined in `analysis/exp002.py` and computed only from measured columns.

`tokens per cycle` is `(accepted + cycles) / cycles`. The runtime emits
`accepted + 1` tokens per verify cycle, so this is what a cycle delivers. It is
deliberately not `output_tokens / cycles`: a sequence the controller parks keeps
producing tokens that no cycle produced, and dividing by cycles would credit
speculation with them.

`speculated share` is `(accepted + cycles) / output_tokens` — the fraction of the
completion speculation actually produced. For a parked sequence it is small, and
that is the controller's decision made visible in the data.

`cost ratio` is the price of one verify cycle in dense decode steps:
`(backbone + head + sample + cache-op milliseconds) / cycles`, divided by the
matched dense arm's measured milliseconds per token. Speculation pays on the part
of the output it produced exactly when tokens per cycle exceeds the cost ratio,
and the predicted whole-request speedup follows from applying that to the
speculated share.

Predicted and measured speedups are reported side by side. Neither is derived
from the other: the prediction uses the runtime's component timers and the dense
arm's per-token cost, the measurement uses the two arms' decode times.

## Stopping rules, and where they fired

Two repeats per cell. A third only if the two disagreed in direction or spread
more than roughly 10%. No cell needed one: within-arm spread over the whole
experiment is under 4% except for one adaptive prose cell, where the two repeats
parked at different points and the spread is the finding rather than noise.

The matrix was cut short deliberately in three places. The fixed-depth arm was
not extended past five cells once four of them had lost in the same direction by
far more than the repeat spread. The high-predictability cell was added — it is
not in the original plan — because without a cell where speculation wins, the
cost model could only be shown to predict losses. The model B arm was reduced to
one cell, dense against adaptive, because the production question is whether to
keep the mechanism on and the answer at 1.81x does not need a second cell.

A restart control was run because the fixed-depth arm requires a server process
started with the pin and the adaptive arm requires one started without it. Dense
code/short was measured on both processes: 9.81 and 9.77 ms per token against
9.70 and 9.81. The two processes are within 1%, so the two arms are comparable.

## Configurations

The five harness configurations that produced the 36 runs are in
[`config/`](config/), in the order they were run. Each one names its cells, its
settings, its workload seeds and its repeat count, and the runner derives a run
id from the cell name and a hash of the server settings, so changing a setting
produces new runs rather than silently mixing two conditions under one name.
