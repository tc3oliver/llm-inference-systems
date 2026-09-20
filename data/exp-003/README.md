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
