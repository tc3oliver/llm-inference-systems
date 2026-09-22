# specprefill-scoring-window-origin

Four CSVs, 69 rows in total, behind
[Moving draft scoring-window origin invalidates reusable draft state](../../research-threads/specprefill-scoring-window-origin.md).
This is a design-gate dataset, not an experiment. One file is observation from
a served instance. The other three come from standalone harnesses that load the
models directly and call the runtime's own functions, with no server between.

No file holds prompt text or model output. The standalone prompts are generated
from a seed (`workloads/generator.py`, kind `code`, seed 3842) and truncated at
the token level. They are identified by the first 16 hex digits of a sha256
over the token-id list, and are never written out.

## `live-scorings.csv` — 48 rows

Columns: `arm`, `scoring_seq`, `time`, `prompt_tokens`, `system_end`,
`conv_tokens`, `target_cached`, `score_window_start`, `scored_tokens`,
`scoring_s`, `draft_cache_hit`, `draft_matched_tokens`,
`draft_boundary_published`, `selected`, `keep_pct`, `pcsr_published_since_last`.

One row per SpecPrefill draft scoring on a served build carrying #3840 and
#3842, 2026-09-23, local clock:

| arm | rows | workload | background recovery budget |
|---|---|---|---|
| `treatment-budget10` | 22 | one Claude Code session | 10% |
| `control-budget0` | 13 | one Claude Code session | 0 (off) |
| `reproA-budget0` | 6 | scripted append-only sequence | 0 |
| `reproB-budget10` | 7 | the same sequence, after a restart | 10% |

**Observed.** Every column except `score_window_start` and
`pcsr_published_since_last` is the runtime's own log line for that scoring.
`score_window_start` is `max(target_cached, system_end)`: **Derived**, one
comparison, and the same expression the planner uses. `pcsr_published_since_last`
counts canonical publications logged between that scoring and the previous one
in the same arm. The arms are not matched pairs: the two sessions steered
themselves, and `reproB` ran after `reproA` against the draft cache it left,
which is why its first row is a hit. `draft_boundary_published` is empty where
no boundary was reachable. The served draft `block_size` is 1024. Parsed by an
extractor kept with the run's preservation copy.

The rule quoted on the thread page — a miss exactly when `score_window_start`
differs from the previous row in the same arm, 44 of 44 transitions — is a count
over this file.

## `standalone-mechanism.csv` — 10 rows

Columns: `case`, `step`, `prompt_sha`, `prompt_tokens`, `cached_tokens`,
`system_end`, `window_start`, `position_offset`, `tokens_to_score`,
`window_sha`, `first_block_sha`, `draft_fetch_matched_tokens`, `draft_hit`,
`draft_restored_tokens`, `scoring_s`, `wall_s`, `stored_len`,
`published_boundary`, `selected`.

Five cases (A–E) of two scorings each. Every case starts from a fresh draft
prefix cache: a real `BlockAwarePrefixCache` over a temporary directory, block
size 256, with the real 0.8B hybrid draft model, the real planner and the real
`run_specprefill_draft_scoring`. The lookahead RNG is seeded before every
scoring.

- `draft_fetch_matched_tokens` is what the lookup matched.
- `draft_restored_tokens` is what survived restore. It is smaller when the
  deepest recurrent snapshot sits below the matched length, which is C, and 0
  when none does, which is E.
- `draft_hit` means restored > 0.

**Measured** for hit, miss and token counts. `scoring_s` and `wall_s` are
**Observed** only, because the served instance on the same machine was
running background recovery at the time.

## `draft-scoring-semantics.csv` — 5 rows

Columns: `prompt_sha`, `prompt_tokens`, `window_start`, `region_tokens`,
`spearman_importance`, `max_abs_diff`, `repeat_noise_max_abs`,
`selected_window`, `selected_stable`, `selected_overlap`, `jaccard`,
`only_window`, `only_stable`, `warm_vs_cold_stable_overlap`,
`restored_tokens`, `spearman_warm_vs_cold`, `max_abs_warm_vs_cold`,
`warm_cold_selected_overlap_c0`.

One row per frontier c. Each row compares cold draft importance under the
moving window (`prompt[c:]`) with cold full-prompt importance restricted to
`[c:]`. Selection is the runtime's `select_chunks` at keep 0.2 on each.

- `repeat_noise_max_abs` is the difference between two identical cold
  full-prompt scorings.
- The `warm_*` and `restored_tokens` columns compare full-prompt scoring
  restored from a stored 40,960-token hybrid state against the same scoring
  run cold. They are properties of the prompt, so they repeat on every row
  except `warm_vs_cold_stable_overlap`, which is per frontier.

**Measured.**

## `target-correctness.csv` — 6 rows

Columns: `cached_tokens`, `path`, `region_tokens`, `selected`, `tail_covered`,
`jaccard_current_vs_stable`, `dense_repeat_max_abs`, `path_repeat_max_abs`,
`seconds`, `next_top1_agree`, `next_top10_overlap`, `next_kl`,
`forced_top1_agree_rate`, `forced_top10_overlap_mean`, `forced_kl_mean`,
`forced_kl_max`.

For each frontier, one row per sparse path, `current` and `stable`. Each row
is compared with a dense reference from the same target cached state.

- The dense reference is `prompt[:c]` prefilled densely with the same
  chunking, then the rest dense.
- Both sparse paths select the same number of tokens from the same region,
  with the same chunking and the same 512-token tail.
- Both go through the runtime's `plan_specprefill_target` and
  `run_specprefill_target_prefill`, and the kickoff token is run separately.
- The 27B-class target ran on the plain GPU prefill path, with the served
  instance stopped for the whole run.

The metrics:

- `next_*` compares the ordinary next-token logits.
- `forced_*` covers a fixed 64-token teacher-forced continuation taken from
  the same generated text: no sampling, one forward pass over the kickoff
  token plus the first 63 continuation tokens.
- KL is KL(dense ‖ path) in nats, computed in fp32.
- `*_repeat_max_abs` is the largest logit difference between two identical
  runs. It is 0.0 on every row.
- `seconds` is the wall time for both runs of that path and is not a latency
  measurement.

**Measured**, one prompt.
