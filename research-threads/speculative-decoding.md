# Research thread — what decides whether speculative decoding pays

**Status: promoted. The question this thread is named after was answered by
[EXP-002](../experiments/exp-002-speculative-decoding-economics/).** This page
is kept as it was written, because the observational finding in it stands on its
own and because the gap it declared is the thing the experiment went and closed.
What changed is at the [top of the gap section](#what-is-missing).

The short version: a matched arm now exists, and acceptance turned out to be the
wrong number. What decides whether speculation reduces latency is the price of
one verify cycle measured in dense decode steps. On one model a cycle costs 2.43
dense steps and speculation loses at 56% acceptance; on another it costs 1.37 and
speculation wins 1.81x at 79%.

## The question

When does native speculative decoding reduce the time an interactive workload
takes to finish, rather than only raising the decode tokens-per-second number
that gets reported?

Speculative decoding drafts several tokens cheaply and verifies them in one
pass of the full model. The accounting is different from prefill work: the
unit of waste is a rejected draft rather than a discarded checkpoint, and the
saving depends on an acceptance rate that nobody can predict from the
configuration alone. EXP-001 found that a prefill optimization's reported
speedup did not survive contact with a session. The same question applies
here and has not been answered.

## The mechanism, as implemented in this runtime

Read from the source, not measured. A head embedded in the model drafts up to
N tokens per cycle, chained, and the backbone verifies them in one forward
pass over the draft plus one bonus position. Under greedy sampling acceptance
is exact match; under stochastic sampling it is the standard speculative
acceptance rule with a residual correction, with the sampler's temperature
and truncation mirrored onto both distributions.

Three details matter for reading any number below.

**The depth setting is a maximum, not a count.** A controller chooses a depth
from 1 to N per cycle from rolling acceptance and latency estimates. It can
park a sequence back onto the dense decoder entirely and re-probe later. So a
run configured at depth 3 is not a depth-3 run; it is an adaptive run bounded
by 3.

**Prefill is not neutral.** A priming pass folds hidden states into the head
during prefill so the first draft is not made from a cold head. That cost is
paid before the first token, which is exactly where an interactive workload
is most sensitive.

**Acceptance is reported per sequence, at the end.** The runtime emits one
line per finished sequence. Under concurrency the verify time is attributed
as an equal share across rows rather than measured per request, so any
per-request backbone figure from a concurrent run is an arithmetic
construction, not a measurement.

## Finding — acceptance is a property of the model, not of the task

`data/speculative-decoding/mtp-sequences.csv`, 431 finished sequences drawn
from the server logs of coding-agent task runs recorded in September 2026.
Two models of the same size class, the same 6-bit quantization family, both
with an in-tree draft head, both at maximum draft depth 3, on the same task
definitions and the same runtime.

| Model | Sequences | Median acceptance | Median tokens per verify cycle |
|---|---:|---:|---:|
| Ornith-1.5-35B-A3B | 292 | 78.8% | 2.26 |
| Qwen3.6-35B-A3B | 139 | 88.6% | 2.71 |

Split by the kind of work the agent was doing, the picture barely moves.
All eleven groups are shown; the last two rows for each model are small
micro-benchmark categories rather than agent tasks, and they are the widest
cells, so leaving them out would flatter the point.

| Model | Task | n | Median acceptance | Median tokens/cycle |
|---|---|---:|---:|---:|
| Ornith-1.5-35B-A3B | feature | 90 | 79.0% | 2.27 |
| Ornith-1.5-35B-A3B | bugfix | 75 | 78.8% | 2.27 |
| Ornith-1.5-35B-A3B | explain | 62 | 78.8% | 2.21 |
| Ornith-1.5-35B-A3B | root-cause | 40 | 79.6% | 2.28 |
| Ornith-1.5-35B-A3B | micro | 16 | 75.6% | 2.24 |
| Ornith-1.5-35B-A3B | micro-nothink | 9 | 77.8% | 2.38 |
| Qwen3.6-35B-A3B | feature | 44 | 89.9% | 2.79 |
| Qwen3.6-35B-A3B | bugfix | 34 | 88.2% | 2.71 |
| Qwen3.6-35B-A3B | root-cause | 26 | 88.8% | 2.74 |
| Qwen3.6-35B-A3B | explain | 25 | 88.2% | 2.70 |
| Qwen3.6-35B-A3B | micro | 10 | 88.5% | 2.50 |

Across all six Ornith groups the median acceptance spans 4.0 points, and
across all five Qwen groups 1.7 points. Restricted to the four real agent
task types, the Ornith spread is 0.8 points. Either way the within-model
range sits well inside the ten-point gap between the two models, and the
tokens produced per verify cycle differ by 0.45 between them.

The useful reading is the negative one. If you are deciding whether
speculative decoding is worth enabling, the kind of work the agent is doing
is not the variable to reason about. Which draft head shipped with the model
is. That is unhelpful advice in the sense that it cannot be tuned, and it is
the advice the data supports.

The spread inside each cell is wide — the Ornith sequences run from 0% to
91.9%, the Qwen sequences from 45.5% to 100% — so the medians describe a
population, not a request. A single request's acceptance is not predictable
from these numbers.

## Observation — content that is not code behaves differently

The agent tasks above are all code-shaped, which is the natural limit of that
dataset. Three single probes in September 2026 on a research instance, same
runtime, Qwen3.6 at maximum depth 3, read from the decoder's own counters:

| Prompt | Accepted / drafted | Accept rate | Tokens per cycle |
|---|---|---:|---:|
| code continuation | 126 / 193 | 65.3% | 2.00 |
| code continuation | 66 / 99 | 66.7% | — |
| open-ended prose | 17 / 69 | 24.6% | 1.28 |

The prose sequence was parked back onto the dense decoder after 128 tokens.
These are **n = 1 per condition** with no matched dense arm, and their
absolute values sit below the agent-task medians for the same model, which is
itself a reason not to read across the two datasets. What they suggest, and
only suggest, is that the content axis that the agent-task data could not
vary does move acceptance substantially.

## What the existing data cannot support

The dense baseline that would turn any of this into a latency statement does
not exist for these workloads.

`data/exploratory/native-mtp-dense-baseline.jsonl` holds 28 records of a
controlled matrix with the mechanism **disabled**: median 100.5 decode
tokens/s on a short prompt at one request, 51.1 per request at four
concurrent, 92.3 on a cold 15.6K prompt; time to first token 0.81 s, 1.78 s
and 10.82 s respectively. The enabled arm of that matrix was never run. The
matrix was stopped deliberately and is not being resumed.

At the time this page was written there was no pair of numbers anywhere in this
repository measuring the same prompt with and without speculative decoding, and
every acceptance figure above was one arm. `data/exp-002/` is that pair, on a
different geometry: shorter outputs, cleared caches, and a matrix that was kept
deliberately small rather than resumed from this one.

<a id="what-is-missing"></a>
## What was missing, and what closed it

Four things were named here as the price of promotion. EXP-002 ran the smallest
matrix that pays three of them and states plainly that it did not pay the fourth.

1. **A matched dense arm — done.** 36 runs, five workload cells, two models,
   three policies, two repeats, prefix caches cleared before every request.
2. **Repeats — done, and small on purpose.** Two per cell, with a third only if
   the first two disagreed. None needed one.
3. **Isolation of the verify cost — done at single-request concurrency.** The
   runtime's own backbone, head, sampling and cache-op timers are now on the
   response rather than only in a log line, and a cycle's cost in dense decode
   steps is what the experiment's whole argument turns on. Under concurrency the
   backbone figure is still an equal share of one shared forward, so this remains
   unpaid above one request.
4. **A session-level measurement — not done.** Every run in EXP-002 is one
   request against a cold cache, which is the geometry least favourable to a
   mechanism that charges at prefill. EXP-002 measures that end-to-end cost and
   declines to generalize it to an interactive workload. This is still the open
   question, and it is the same one EXP-001 ended on.

The original four, as written before any of it was run:

1. A matched dense arm for the same prompts, same sampling, same repeats.
2. Repeats. The agent-task data has large n but is observational: the
   sequences were produced by agents doing different work, not by a
   controlled matrix.
3. Isolation of the verify cost. Acceptance rate is not latency. The backbone
   forward is the expense, and under concurrency this runtime does not
   measure it per request.
4. A session-level measurement. Every number here is per sequence. EXP-001's
   whole finding was that per-request numbers can point the opposite way from
   session outcomes.

Three of those were subsequently run; see above. The fourth was not.

## Provenance

The 431 sequences were parsed from server logs of agent task runs held
outside this repository, reduced to the columns in the CSV, and deduplicated
where a line appeared in both the log file and the captured standard output.
No prompt text, completion text or tool argument was read into the derived
data. The parser keeps the runtime's own fields and renames nothing.

The dense baseline records were produced by `harness/` against a research
instance on a loopback port with its own base path and cache directory, on a
build carrying an instrumentation patch that exposes the decoder's acceptance
counters on the response object. The production server on the same machine
was not used, read or restarted for any measurement in this thread.
