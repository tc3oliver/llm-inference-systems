# data/exp-002

Every number in [EXP-002](../../experiments/exp-002-speculative-decoding-economics/)
and in `figures/fig10-*` and `figures/fig11-*` comes from a file here. Nothing
is smoothed, interpolated or back-generated. No prompt text and no completion
text appears in any of it.

## raw/*.jsonl — 36 records, the primary artifact

One validated run record per measured run, exactly as `harness/run.py` wrote
it, in five files named after the configuration that produced them. This is the
source; everything else in this directory is derived from it and can be rebuilt:

    uv run python -m analysis.exp002_flatten   # raw/ -> runs.csv
    uv run python -m analysis.exp002           # runs.csv -> matched-comparisons.csv

## runs.csv — 36 rows, one per measured run

The flat form of the records above, written by `analysis/exp002_flatten.py`.
Produced by `harness/` against a research instance on a loopback port with its
own base path, cache directory and log, on a local build of the runtime carrying
the two changes described in the experiment's METHODOLOGY. Each row is one
request. Every metric is nullable and a metric that was not measured is empty,
never `0`.

| column | meaning |
|---|---|
| `run_id` | cell, repeat and a hash of the server settings that produced it |
| `model`, `policy`, `content`, `context`, `repeat` | the cell |
| `prompt_tokens`, `output_tokens` | as the server counted them |
| `ttft_s`, `decode_s`, `e2e_s` | client-measured, in seconds |
| `decode_time_source` | `client`, or `server` where the stream carried no incremental deltas and the server's own `generation_duration` was used instead |
| `decode_ms_per_token` | `decode_s / (output_tokens - 1)` |
| `cycles`, `accepted`, `drafted`, `accept_rate` | the runtime's own per-sequence counters, read off the response |
| `spec_tokens`, `spec_share`, `tok_per_cycle` | derived; see the experiment's METHODOLOGY |
| `zero_cycles` | cycles the controller ran at depth 0 |
| `backbone_ms`, `head_ms`, `sample_ms`, `cache_ops_ms` | the runtime's component timers, summed over the request |
| `backbone_ms_per_cycle`, `spec_ms_per_cycle` | derived |
| `depth_drafted`, `depth_accepted` | per draft position, pipe-separated |
| `output_sha256` | SHA-256 of the completion, so two runs can be compared without the text being kept |
| `server_git_sha`, `settings_hash`, `notes` | provenance, including the server environment the run was issued against |

Four rows of `dense/code/short` on model A rather than two: that cell was
measured on both server processes as the restart control.

## matched-comparisons.csv — 11 rows

Written by `analysis/exp002.py` from `runs.csv`. One row per speculative arm,
paired against the dense arm of the same model, content and context. Carries the
measured decode and end-to-end speedups, the cost ratio, the predicted speedup,
the repeat spread of both arms, and three booleans recording whether each arm's
output was reproducible across repeats and whether the two arms agreed.

## production-mtp-sequences.csv — 17 rows

Observational, not matched. One row per finished sequence, parsed out of the
served instance's own log by `harness/mtp_log.py` during this work, reduced to
the runtime's counters and timers. No prompt fragment, no completion fragment,
no request identifier and no timestamp is carried across. These rows establish
that the mechanism is active on the served model and roughly where its
acceptance and cycle cost sit. They have no dense arm and cannot support a
latency claim on their own.

## What is not here

The completions themselves. The harness can write them to a sidecar file for the
hash comparison, and that file is not committed: it is model output to a
synthetic prompt, it adds nothing the hashes do not, and the character-level
divergence points quoted in the findings are the only thing that was read out of
it.
