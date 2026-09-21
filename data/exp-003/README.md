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

### `spec-exit-budget-{turns,summary}.csv` — 28 and 4 rows

Four recovery budgets — 5%, 10%, 20% and 100% — over one seven-turn PCSR
session, written one runner JSON per cell and combined into a single pair of
tables by `analysis/exp003.py` with `--prefix spec-exit-budget`. One run per
cell.

The four cells share a token sequence and differ only in the budget: a
24,000-token head target that generated 23,328 actual tokens, 3,000 tokens
appended per turn, 75 s of idle between turns, an 8,192-token dense
threshold, and one arm (`pcsr`) each. Those five
values are on every row as `head`, `append`, `idle_s`, `threshold` and
`budget_pct`, read from each file's `meta`, because a row of a combined table
means nothing without the cell it came from.

`spec-exit-budget-turns.csv` — 41 columns, 7 turns per cell: the columns of
`session-turns.csv` with `cell`, `budget_pct`, `idle_s`, `threshold`, `head`
and `append` in front.

`spec-exit-budget-summary.csv` — 28 columns, one row per cell and arm: the
columns of `arm-summary.csv` with the same six in front, reordered so that
`budget_pct` sits beside `measured_recovery_share` and the exit's cost beside
the exit turn. `cumulative_foreground_s` is the runner's `session_s`.

`measured_recovery_share` is the share of wall time the recovery job received
and `budget_pct` is the ceiling it was given; both are columns because they
are the reading. `spec_exit_turn` is the last turn on which the sparse route
was taken, plus one. It is not a success metric: `ttft_before_exit_s` and
`ttft_at_exit_s` are beside it because in every cell here the exit turn costs
more than the turn before it.

Evidence level 3 — synthetic interactive workload, one run per cell. The
mechanism claims rest on the runtime's own per-request admission records,
which is level 5; `route_disagreements` is 0 in all four cells, so the
runtime's route and the analysis's derivation of it agree on every turn.

This round and the four below it ran on server build `269dabd1`, read from
the research instance's own startup record rather than asserted: the instance
writes the git sha it started from, and every cell reads the same one. No
commit landed between the first cell starting and the last finishing. The
pre-fix note at the end of this file does not cover these five rounds.

### `spec-exit-controls-{turns,summary}.csv` — 32 and 4 rows

The four arms — dense, spec, recovery-end, pcsr — over one seven-turn session,
one file, one run per arm. A 24,567-token first turn, roughly 3,000 tokens
appended per turn, 75 s of idle, an uncapped budget, an 8,192-token threshold.
Seven turns and one `dense-probe` per arm, 41 and 28 columns, the same as the
budget sweep's.

`probe_ttft_s` is the column to read. The probe re-sends the session's final
prompt through the ordinary serving path, so what it restores is what the
cache actually holds: 12.30 s, 12.29 s and 12.31 s for dense, recovery-end and
pcsr against **195.19 s for spec**. `longest_canonical_prefix_tokens` says why
— 40,960 tokens for the three, 0 for spec — and it is read from the probe's
own admission record, not from its usage object, because a probe long enough
to be paused by the memory guard reports its own prefill there.

### `spec-exit-idle15-{turns,summary}.csv` — 16 and 2 rows

The same session at 15 s of idle instead of 75 s, PCSR and Spec, written one
file per arm and combined into one table. One run per arm. Same columns.

### `spec-exit-always-sparse-{turns,summary}.csv` — 16 and 2 rows

The same session again with the SpecPrefill threshold set to 1, so every turn
is admitted sparse and the foreground can never leave that route whatever the
canonical prefix reaches. Spec and PCSR, 75 s of idle, one run per arm. Same
columns. The `threshold` column reads 1 on every row, which is the only thing
separating this round from the controls.

### `spec-exit-compaction-{turns,summary}.csv` — 12 and 2 rows

Six turns with the conversation discarded after turn 2, Spec and PCSR, 75 s of
idle, one run per arm. This round has a different runner and carries four
columns the others do not: `post_compact` per turn, and `compact_at_turn`,
`longest_common_token_prefix` and `token_counts_approx` per cell — 45 and 31
columns rather than 41 and 28. There is no dense probe, so the four
probe-derived summary columns are empty rather than zero. Turn sizes are
roughly 21,900, 25,000 and 28,100 tokens before the discontinuity and 8,300,
11,400 and 14,500 after it.

Two numbers describe the prefix that survived the compaction and they are not
the same kind of number.

`longest_common_token_prefix` is **7,856 and it is an estimate**. This machine
has no tokenizer for the served model, so the generator counts at four
characters per token and sets `token_counts_approx`, which is `True` on every
row of this round. It is the harness's estimate of how many leading tokens the
two streams share, and nothing measured it.

The surviving canonical prefix is **4,096 tokens and it is measured**: it is
what the serving path restored on the first post-compact turn, read from that
turn's admission record as `route_cached_tokens`. It is a real block boundary
of a real cache.

The two disagree by roughly a factor of two, and nothing here reconciles them.
Whether the gap is the estimate being wrong, the cache storing only whole
blocks, or the two measuring different things is **not established** by this
round.

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
