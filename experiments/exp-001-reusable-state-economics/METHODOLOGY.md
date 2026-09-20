# Methodology

## Platform

One machine: Apple silicon M4 Max, 64 GB unified memory. Every number in this
study comes from that machine. There is no second host, no cluster and no
cloud arm, and nothing here has been checked on another vendor's accelerator.

The serving model is a 27B-class mixture-of-experts model quantized to 4-bit,
running with multi-token prediction. The sparse prefill path is SpecPrefill,
an attention-based sparse prefill mechanism, which uses a separate
0.8B 4-bit scorer model to select which tokens of the prompt receive full
attention computation: it keeps the top 20% of tokens and engages only on
prompts above an 8192-token threshold. Prefix reuse is provided by a
block-structured KV store, which is the component that decides whether a new
request can restore a previous request's state instead of recomputing it.

Those two mechanisms interact, and the interaction is what this experiment
turned out to be about. SpecPrefill produces a partially computed block. The
prefix cache will not accept a partially computed block as a restore point, so
a sparsified suffix does not advance the normal reusable dense prefix state.

## Workloads

**Cold single-shot long prompts.** One prompt, one response, no continuation.
Prompt sizes of 14.3K, 16K and 32K tokens, each run dense and then accelerated.
This is the regime where a latency number is the complete story, and it is the
regime where the optimization looks best.

**Synthetic interactive workload.** A multi-turn session parameterized by the
idle time between turns — 15 s, 10 s, 5 s and 0 s — run in a dense-only arm
and a hybrid arm with SpecPrefill plus background dense recovery, on an
experimental branch whose recovery job was designed to fail closed and later
failed a review of that design ([Prototype safety
review](../../ENGINEERING.md#prototype-safety-review)). The parameter is the
point: background recovery needs wall-clock time in which to run, and varying
the idle time varies how much of it exists.

**Five real coding-agent sessions.** Real trajectories against the real
server, not replays, in three groups. Three are the arms of one paired
comparison, dense, sparse and hybrid, one session each, which produced the
per-turn hit rates in `data/exp-001/session-turns.csv` and the wall times
that prompted the study. One is a separate session that produced the clean
request-level trace (`data/exp-001/trace-b-*.csv`): SpecPrefill enabled
and no background densification code present in the build, so no second
mechanism can be offered as an explanation for what the trace shows. The
fifth is the third-regime observation, a session whose prefix cache stayed
healthy throughout. Whenever this study says "real session" it means one of
these five, and it never pools them.

## What was controlled

Within each comparison: the same server process configuration, the same build,
and the same model weights and quantization across arms. The isolated
qualification runs — the ones producing the 16K ANE-plus-sparse throughput
figure — were done as smoke runs with a single resident engine, so no second
model was competing for memory bandwidth or for the accelerator.

The trace-B session was chosen specifically because of what was absent from
the build. Background densification was not compiled in. That removes the most
plausible confound for the checkpoint behaviour.

## What was not controlled

The agent trajectory. The three paired-comparison sessions took different
paths through their tasks, issued different numbers of tool calls, and
produced different amounts of text. Any wall-clock ratio between them reflects
the trajectories at least as much as the prefill mode, and I treat it
accordingly in [FINDINGS.md](FINDINGS.md).

Prompt content. Real agent sessions build their own prompts; I did not fix them
across arms.

Wall-clock conditions. These are runs on a personal workstation over a period
of time, not on a quiesced benchmark rig with thermal state pinned.

## n

n=1 per arm, everywhere, unless a row in `data/exp-001/think-time.csv` carries
a repeat value — two of the eight synthetic cells, the hybrid arm at 15 s and
at 5 s of idle, have a second run, and those are the only repeats in the
study. All five measured figures rest on single-run arms, apart from those two
cells. I state that as n=1
rather than dressing it up, and
[LIMITATIONS.md](LIMITATIONS.md) works through what that does and does not
allow the study to conclude.

The trace-B result is different in kind from the rest. It is also one session,
but it is twenty consecutive requests within that session, each one an
observation of the same mechanism, and the shape is monotone. One session with
twenty internally consistent events is stronger evidence for a mechanism than
one A/B pair is for an effect size.

## Reproducibility

No experiment in this study can be re-run from this repository, by design. The
repository ships the extracted measurements, the figure code that reads only
those measurements, and the reasoning. It does not ship the model weights, the
server build, the agent, or the prompts — the prompts in particular would mean
publishing third-party system prompts and tool definitions, which is not
something I am willing to put in a public git history. What you can check here
is that every number in the prose appears in `data/`, and that every figure
regenerates from those files with `figures/plot.py`.
