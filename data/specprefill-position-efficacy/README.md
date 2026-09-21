# SpecPrefill positional contract — efficacy control

Four CSVs from one three-arm control, run to answer a single question about an
upstream fix: once SpecPrefill writes its selected tokens at their real
positions on an mRoPE VLM, what actually changes?

**Finding.** The fix restores the sparse positional contract and consistently
moves first-token logits closer to the dense baseline — 6 of 6 turns by mean
absolute difference and by relative L2 — but this did not translate into
improved greedy-output agreement on this workload. No measurable SpecPrefill
performance regression was observed on matched turns.

| Claim | Level |
|---|---|
| Position correctness restored | **Measured** |
| First-token logits move toward dense | **Measured** |
| Greedy-output agreement improves | **Not observed** |
| Task-quality improvement | **Not established** |
| Performance regression | **Not observed on matched turns** |

`EVIDENCE.md` defines those words. "Not observed" and "not established" are
different: the first is a measurement that came back negative, the second is a
question this workload cannot answer at all.

## What was run

One six-turn session, the same session in every arm, replayed three times:

- **A — dense.** `config/04-semantic-control-dense.yaml`. SpecPrefill off.
  The reference every other arm is differenced against.
- **B — SpecPrefill, before the fix.** `config/04-semantic-control-pcsr.yaml`.
- **C — SpecPrefill, after the fix.** The same config as B.

B and C differ in exactly one bit — a research-build switch that suppresses the
explicit sparse `position_ids` — so nothing else about the two runs can account
for a difference between them. All three arms ran on separate server sessions
with the cache directory removed between them, per `METHODOLOGY.md`.

Model `Qwen3.8-27B-oQ4e-mtp`, served locally through the mlx_vlm runtime;
`mtp_enabled: false` in every arm, because EXP-002 established that
multi-token prediction makes greedy output non-reproducible. Draft model
`mlx-community/Qwen3.5-0.8B-MLX-4bit`, SpecPrefill threshold 8,192.
Temperature 0, sampler seed 3, workload
`workloads/shapes/append-heavy-80k-probe.yaml` at generator seed 301, six
turns of 192 output tokens each. Prompts are generated from the seed and never
written to disk, so the arms receive the identical token sequence by
construction rather than by comparison.

Every run was gated before it was read: the output file must not have
pre-existed, the server must be a process started by that script, the harness
must report writing six runs and never "already recorded", and the probe must
have captured exactly six single-token kickoff forwards with one logits row
each. `harness.run` is resumable, and a resumed run that executed nothing looks
exactly like one that reproduced.

## Selected-index provenance

The sparse arms do not select the same tokens on every turn, and a comparison
that ignores that is comparing two different questions. Each row of
`specprefill-position-contract.csv` carries the ordered selected-index hash the
runtime actually consumed, with its min and max.

`specprefill-position-timing.csv` carries
`cache_state_matched_across_sparse_arms`, true only where B and C restored the
same prefix **and** selected the same set — turns t1 through t4. On t0 the two
arms selected different sets (a cold-start selection nondeterminism that is
observed here and not investigated); on t5 they restored different prefixes.
Those two turns are excluded from every comparative claim above.

## The files

### `specprefill-position-contract.csv` — 12 rows

Columns: `arm`, `turn`, `prompt_tokens`, `restored_prefix_tokens`,
`selected_tokens`, `conversation_tokens`, `selected_min`, `selected_max`,
`selected_hash`, `position_offset`, `chunk0_first_position`,
`chunk0_last_position`, `chunk0_length`, `chunk0_position_span`,
`chunk0_is_dense_run`.

The positions the target forward was actually handed for the first sparse
chunk, read out of the forward rather than re-derived from the plan. The
contract is `selected_indices + position_offset`; two consequences of it are
checkable from a digest, and both are columns here. A chunk whose span equals
its length is a dense consecutive run, which a sparse selection cannot be.

Before the fix, all six chunks are dense runs, and three of six start at
position 0 despite restored prefixes of 4,096 to 12,288 tokens. After it, none
is dense and every one starts at its `position_offset`.

### `specprefill-position-logits.csv` — 12 rows

Columns: `arm`, `turn`, `vocab`, `max_abs_diff_vs_dense`,
`mean_abs_diff_vs_dense`, `relative_l2_vs_dense`, `argmax_token_id`,
`dense_argmax_token_id`, `argmax_agrees_with_dense`.

Full-vocabulary (248,320) logits at generated position 0, differenced against
the dense arm's row for the same turn. That position is where all three arms
are asked the same thing from their own state, and it is the last point before
sampling introduces its own path.

The captured float32 rows are not in this repository: 17 MB of raw model
activations is not evidence anyone needs to re-read, and the four numbers
derived from each one are here instead.

### `specprefill-position-output.csv` — 12 rows

Columns: `arm`, `turn`, `output_tokens`, `dense_output_tokens`,
`exact_match_dense`, `first_differing_token_index`, `prefix_agreement_ratio`,
`multiset_agreement_ratio`, `output_sha256`.

Greedy text against the dense arm's. Token counts come from re-tokenizing the
recorded text with the model's own tokenizer: the harness records text, not
emitted ids, so this is a faithful proxy and not the emitted sequence. All
outputs are the same length in every arm, so the comparison needs no alignment
rule beyond index.

This is where the negative result lives. The first divergence from dense sits
at the same token index before and after the fix on every turn, and multiset
agreement rises on two turns and falls on four.

### `specprefill-position-timing.csv` — 18 rows

Columns: `arm`, `turn`, `prompt_tokens`, `restored_prefix_tokens`,
`foreground_route`, `ttft_s`, `prefill_s`, `e2e_s`, `output_tokens`,
`cache_state_matched_across_sparse_arms`.

On the four matched turns, B and C agree to within 0.03 s on both `prefill_s`
and `ttft_s`, and both remain far below the dense arm. The explicit positions
cost nothing measurable.

## Limitations

- **One workload, one model, one host.** Six turns of one generated coding
  session on one Apple-silicon machine. Nothing here is a benchmark claim.
- **Four comparable turns, not six.** t0 and t5 are excluded for the reasons
  above, and no claim is made from them.
- **The workload has no ground truth.** It is generated code with no correct
  answer, so task quality cannot be graded; agreement with the dense arm is the
  only available proxy, and it is reported as itself, not as quality.
- **Logit distance is reported at one position.** Position 0 is where the arms
  are comparable; nothing here measures how the distance evolves through
  decoding.
- **Sparse prefill remains a lossy approximation.** It keeps roughly a fifth of
  the conversation tokens, so a large residual distance to dense is expected
  whether or not the positions are right, and the improvement measured here is
  a reduction in that residual, not its removal.
- **The repeat turn cannot be used as a self-consistency check in the sparse
  arms.** t5 re-sends t4's prompt, but in both sparse arms it arrives with a
  different restored prefix and a different selected set.
- **Cold-start selection nondeterminism is observed, not explained.** The first
  turn of a fresh server selected a different set on every run at the same seed
  while turns 2 through 6 were identical across all runs.
