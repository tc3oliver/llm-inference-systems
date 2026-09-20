# Research thread — heterogeneous compute on one machine

**Status: research thread, not a completed experiment.** One finding here is
solid and slightly embarrassing; the rest is a configuration history rather
than a study. Nothing was re-run to write this page.

## The question

An Apple silicon machine has a GPU and a neural engine sharing one pool of
memory. Prefill is the part of inference that looks most like the dense
matrix work a neural engine is built for. Under what conditions does moving
part of prefill onto it actually help a server, as opposed to a benchmark?

## Finding — an accelerator that compiles and never runs

The clearest result is a null one, and it is about a boundary, not about
silicon.

The neural-engine prefill path compiles for a fixed tile length. The server
also divides prefill into cache blocks, and the size of a delivered prefill
chunk is clamped to that block boundary. When the deployed block size was
512 tokens and the compiled tile was 2048, no delivered chunk could ever fill
a tile. The path initialised, compiled, reported itself as enabled, and never
executed a single tile. The runtime says so in its own log line: the engine
will compile but never execute.

Nothing about this is visible in a throughput number. The configuration was
"neural engine on", the server agreed it was on, and the contribution was
exactly zero. What made the path usable was raising the tile alignment to
match the block structure — a change to how work is divided, not to how it is
computed.

The general form is worth keeping: on a heterogeneous device, the unit of
work an accelerator compiles for and the unit of work the serving layer
hands out are two different decisions, usually made by two different people,
and an accelerator that never sees a full unit contributes nothing while
appearing to be enabled.

Source: the deployment record for the September 2026 configuration decision,
held outside this repository. This is a configuration and log observation,
not a measured comparison, and it is reported as one.

## Finding — the heterogeneous win was real and did not survive a session

When the tile alignment was fixed and the path did run, combined with sparse
prefill, the isolated numbers were large:

| Isolated case | Dense | Neural engine + sparse |
|---|---:|---:|
| 8K fresh tail | 35.88 s, 230 tok/s | 7.51 s, 1,102 tok/s |
| 16K fresh tail | 73.84 s, 223 tok/s | 14.44 s, 1,140 tok/s |

Nearly five times faster. Over a real coding-agent task the same
configuration recomputed 7.2 times as many tokens, spent twice as long in
prefill in total, and ended the session at a 63.1% cache hit rate against
99.5%.

That comparison is written up with its per-turn data as section 6 of
[EXP-001's findings](../experiments/exp-001-reusable-state-economics/FINDINGS.md),
because it is the same finding as the rest of that study rather than a
separate one. It belongs in this thread only as the answer to the question
this thread asks: the heterogeneous path did its job, and the thing that
decided the outcome was somewhere else entirely.

Note what the isolated numbers cannot distinguish. Two mechanisms were on at
once — work moved to the neural engine, and sparse prefill skipping most
tokens. The five-times figure is the pair. EXP-001 separately measured them
composing at 95-97% of the product of their individual speedups in an
isolated qualification — 1,328 against 300 prefill tokens/s on a 16K prompt,
`data/exp-001/session-aggregates.csv`, rows `isolated_16k_*` and
`stacking_fraction_of_ideal_pct`, a single smoke run with one resident
engine. That is where the decomposition lives; there is no clean
per-mechanism split in the session data.

## What is missing

- No current measurement of the neural-engine path at a matched tile and
  block size, with sparse prefill off, which is the only configuration that
  would isolate the heterogeneous contribution by itself.
- No power or thermal measurement. On a machine where both engines draw from
  one budget, "faster" and "cheaper" are not the same question, and only the
  first has ever been asked here.
- No measurement of what the neural-engine path costs the GPU path when both
  are busy, which is the question that matters under concurrency.

## Scope

One machine, one vendor, unified memory. Nothing here transfers to a discrete
accelerator with its own memory, and the load-bearing finding — that the
serving layer's block size can silently nullify an accelerator — is about the
interface between the two, which every heterogeneous system has in some form.
