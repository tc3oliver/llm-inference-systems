# EXP-003 data

Two CSVs from one four-arm run of the append-heavy session, plus this README.
Both are written by `analysis/exp003.py` from the runner's JSON record; nothing
here is hand-edited, smoothed or back-generated, and a quantity the run did not
report is an empty cell rather than a zero.

## `arm-summary.csv` — 4 rows

Columns: `arm`, `cumulative_foreground_s`, `probe_ttft_s`,
`longest_canonical_prefix_tokens`, `canonical_debt_tokens`,
`final_prompt_tokens`, `shadow_service_s`, `shadow_publishes`.

One row per arm. `cumulative_foreground_s` sums the three session turns only;
the dense probe is an instrument and its latency is the separate
`probe_ttft_s` column. `longest_canonical_prefix_tokens` is what the probe
restored, and `canonical_debt_tokens` is `final_prompt_tokens` minus it.
`shadow_service_s` and `shadow_publishes` come from the runtime's own counters
and are empty for the two arms that have no shadow, which is how a build
without the instrumentation stays distinguishable from one reporting zeros.

## `session-turns.csv` — 16 rows

Columns: `arm`, `kind`, `turn`, `prompt_tokens`, `cached_tokens`,
`uncached_suffix`, `ttft_s`, `prefill_s`, `decode_s`, `decode_tps`,
`output_tokens`, `wall_s`, `output_sha`, and ten `shadow_*` counters.

Four rows per arm: three session turns and one `dense-probe`. `decode_tps` is
empty throughout because each turn generates 24 tokens against a prefill of
tens of thousands, so the runtime reported no separate decode-rate sample; the
column is kept rather than filled.

`cached_tokens` is **0 on every sparse turn in every arm**, including turns the
server log shows reconstructing a prefix. That is a property of the SpecPrefill
reporting path, not a measurement of the cache, and it is why the canonical
prefix is read from the probe instead. No quantity in the study is derived from
a sparse turn's cache counters.

`output_sha` is a hash of the completion, used only to compare arms against
each other. No prompt text and no completion text is stored anywhere.

## Provenance

One run per arm, 2026-09-21, on a research instance of the runtime alone on the
machine with the long-running local service stopped. Model
`Qwen3.8-27B-oQ4e-mtp`, greedy, multi-token prediction off, cache block size
4096 (raised from 256 by the runtime for this model's hybrid cache). Arms are
separated by a server restart with the cache directory removed, not by a cache
clear. Full configuration in the experiment's `METHODOLOGY.md`.

Evidence level 3 — synthetic interactive workload, one run per arm. The
mechanism claim it supports is level 5, because it rests on the runtime's own
per-request counters and log lines rather than on the wall-clock column.

Every `shadow_*` counter in these files comes from a build in which the
recovery job carried two defects, found and fixed on 2026-09-21, so its rate
and budget figures measure that build rather than the mechanism; the experiment's
[LIMITATIONS.md](../../experiments/exp-003-progressive-shadow-prefill/LIMITATIONS.md)
says what they bound.

## Later rounds

### `progressive-control-{turns,summary}.csv` — 8 and 2 rows

Recovery-End against PCSR over three appending turns of ~8K, 35 s of idle
between them — deliberately shorter than one recovery target takes, so every
recovery is interrupted, which is the only condition under which the two
publication modes can differ. Same columns as the files above. One run per arm.

### `budget-sweep.csv` — 4 rows

Columns: `cell`, `budget_pct`, `actual_service_s`, `actual_service_share`,
`cumulative_foreground_s`, `turn0_ttft_s`, `turn1_ttft_s`, `turn2_ttft_s`,
`canonical_prefix_tokens`, `canonical_debt_tokens`, `publishes`,
`probe_ttft_s`.

The requested budget and the share actually received are both columns because
they disagree: a budget is a ceiling on service, it is enforced between chunks,
and a chunk cannot be interrupted, so a cell can exceed its own ceiling by part
of one chunk. The `0.0` cells are measurements — the recovery job was admitted
and never served — not absent values.

### `multiturn-80k-{turns,summary}.csv` — 24 and 4 rows

The four arms over five appending turns reaching 81,610 tokens, 35 s of idle,
5% recovery budget. One run per arm, identical token sequences.

The `shadow_service_s` column in the PCSR rows is cumulative and stops
advancing after turn 1. That is the measurement that explains the arm: the
budget is a share of wall time since the scheduler started, so service taken
early puts the job permanently over its ceiling and it is never served again.

## Provenance for all rounds

Research instance of the runtime alone on the machine, long-running local
service stopped. Model `Qwen3.8-27B-oQ4e-mtp`, greedy, multi-token prediction
off, cache block size 4096. Arms separated by a server restart with the cache
directory removed. Evidence level 3; the mechanism claims built on the
runtime's own per-request counters and trace records are level 5.

Every round above ran on the pre-fix build described in the first provenance
note, so the recovery rates, the budget sweep and the catch-up ratio are
measurements of that build; the experiment's
[LIMITATIONS.md](../../experiments/exp-003-progressive-shadow-prefill/LIMITATIONS.md)
says what they bound.
