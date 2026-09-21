# PCSR — minimal reproductions and the evidence-to-code map

Internal working document. Its purpose is that every claim made in public about
background canonical-state recovery can be walked back to a dataset, to the
production mechanism that produced it, and to a test that fails if the
mechanism stops behaving. Nothing here is a new experiment; it is a map of what
already exists.

Two conventions carry over from the rest of the repository. The evidence ladder
is [`EVIDENCE.md`](../EVIDENCE.md). Paths are never renamed to match a change
in wording — `exp-003-progressive-shadow-prefill` keeps its directory name.

---

## 1. Minimal reproductions

Three claims are worth being able to reproduce from nothing. Each one below is
deterministic, synthetic, needs no private transcript, and — for A and C —
needs no model at all. B is stated twice: once as a unit-level proof of the
mechanism and once as the runtime measurement, because the unit test proves the
boundaries do not move and only the runtime shows what a client waits.

### A. Recovered canonical state becomes restorable by ordinary serving

| | |
|---|---|
| **Question** | When recovery reports a prefix as committed, can a later ordinary request actually restore it? |
| **Setup** | `tests/test_scheduler_shadow_prefill.py`, publication group. A `Scheduler` with a stubbed prefix cache, one sparse request, a job driven to a block boundary. |
| **Expected signal** | The committed counter advances only after `block_aware_cache.fetch_cache` — the same lookup a real request takes — resolves the boundary under a throwaway id. A store reporting success is not sufficient. |
| **Acceptance** | `job.committed_tokens` stays at its previous value whenever the read-back returns fewer tokens than the store claimed, and the refusal is logged. `canonical_committed_tokens <= independently_restorable_tokens` holds at every step. |
| **Prerequisites** | None beyond the test suite. `uv run pytest tests/test_scheduler_shadow_prefill.py -k Publication` in the oMLX fork. |
| **Known limitation** | The prefix cache is a double. The end-to-end version is EXP-003's own run, where the probe turn restored 36,864 tokens through the serving path; that needs the model and about twenty minutes. |

### B. A smaller execution slice reduces foreground blocking without moving publication boundaries

| | |
|---|---|
| **Question** | Is the execution slice independent of the publication grain, and does shrinking it cost recovery throughput? |
| **Setup (unit)** | `tests/test_shadow_prefill_slice.py`. Drives a job to 4 blocks at 256 / 512 / 1024 / 2048 / 4096 tokens per slice, using the runtime's own `clamp_prefill_chunk_to_boundary` rather than a re-implementation. |
| **Setup (runtime)** | Single engine, one sparse turn of ~24.5K tokens, then a fixed probe schedule against a fixed idle window, sweeping the slice with everything else held. Recorded in [`collision-summary.csv`](../data/recovery-foreground-qos/collision-summary.csv) and [`slice-timing.csv`](../data/recovery-foreground-qos/slice-timing.csv). |
| **Expected signal** | Identical `published_boundaries` at every slice size; `processed_tokens` monotone; worst foreground TTFT falls with the slice while recovery throughput does not. |
| **Acceptance** | Unit: the five slice sizes produce the same boundary list and the same total. Runtime: 15.08 s → 1.30 s worst observed TTFT with 24,576 tokens still recovered in 101.5–104.0 s. |
| **Prerequisites** | Unit: none. Runtime: one hybrid model whose `paged_cache_block_size` is enlarged (4096 here), SpecPrefill enabled with a threshold low enough to force the sparse route. |
| **Known limitation** | One cache-block size. Both the publication grain and the useful slice range are stated in tokens and neither is shown to be portable. The arrival distribution is synthetic and its own instrumentation cannot timestamp an arrival that lands inside a slice — that blindness is a property of the mechanism, not a defect of the harness. |

### C. Two engines share one aggregate recovery budget

| | |
|---|---|
| **Question** | Does a process with several engines grant one ceiling, or one per engine? |
| **Setup (unit)** | `tests/test_shadow_prefill_shared_budget.py`. Two `Scheduler`s built from one `SchedulerConfig` carrying a `ShadowBudget`, exercised through register / spend / reset / reload / teardown. |
| **Setup (runtime)** | Two models loaded, one sparse turn each, 150 s idle, 10% server-level cap, then a short probe to each. [`shared-budget-two-model.csv`](../data/recovery-foreground-qos/shared-budget-two-model.csv). |
| **Expected signal** | Both engines report the same budget object: `budget_shared` True, `budget_owners` 2, and the window counters identical. Under a per-engine budget the two would be independent and the aggregate would be 2×. |
| **Acceptance** | `budget_windows`, `budget_overshoot_s` and `budget_window_service_s` identical to six decimals across both engines; aggregate share within one slice of the cap. |
| **Prerequisites** | Unit: none. Runtime: two models the host can hold at once; the smaller one only needs to produce debt. Note the served model id has no repository prefix. |
| **Known limitation** | The process was otherwise idle and only one engine carried load. Genuine two-engine contention — both serving, both with debt — is unmeasured, and it is where the wall-time unit and the aggregate share would first interact. |

---

## 2. Evidence-to-code map

Branch: `upstream/pcsr-background-recovery` in the oMLX fork. Every test path
below is in that branch; every data path is in this repository.

| Finding | Dataset / trace | Production mechanism | Regression test | PR section |
|---|---|---|---|---|
| Sparse execution creates canonical debt | `data/exp-003/spec-exit-always-sparse-turns.csv` | `note_shadow_candidate` admits a job only when `request.specprefill_indices` is set | `test_scheduler_shadow_prefill.py::TestCandidateAdmission` | Opening problem statement |
| Recovery shrinks the future suffix without leaving the sparse route | `data/exp-003/spec-exit-always-sparse-summary.csv` (`spec_exit_turn` empty in both arms) | Nothing in the feature reads or writes a route decision | *by construction* — no routing symbol exists in the diff | Performance evidence, with its qualifier |
| Published state is independently restorable | `data/exp-003/spec-exit-always-sparse-summary.csv` (`final_canonical_prefix_source=equal`) | `_shadow_readback_tokens` → `fetch_cache` under a throwaway id, before `note_published` | `test_scheduler_shadow_prefill.py::TestPublication` | Correctness contract |
| Publication is floored to a whole block | `data/exp-003/spec-exit-always-sparse-turns.csv` (`canonical_prefix_tokens` are block multiples) | `safe_publish_boundary`, plus the live-state equality check at publish time | `test_shadow_prefill_policy.py::TestPublication`, `test_shadow_prefill_slice.py` | Correctness contract |
| Budget controls collision frequency | `data/recovery-foreground-qos/collision-summary.csv`, `collision-probes.csv` | `ShadowBudget.allows` — roll and comparison in one critical section | `test_shadow_prefill_policy.py::TestBudget` | Evidence → foreground interference |
| Execution granularity controls collision severity | `collision-summary.csv`, `slice-timing.csv` | `shadow_slice_cap` in `_step_prefill_chunk` | `test_shadow_prefill_slice.py::TestTheCapAppliesToRecoveryOnly` | Evidence → foreground interference |
| Slice and publication grain are independent | `slice-timing.csv` | `clamp_prefill_chunk_to_boundary` + `safe_publish_boundary`; no shared term | `test_shadow_prefill_slice.py::TestPublicationDoesNotMoveWithTheSlice` | Execution granularity |
| Parking removes idle spin | `data/recovery-foreground-qos/idle-cost.csv` | `_has_shadow_work` returns False on a spent window or a busy peer | `test_shadow_prefill_engine_loop.py::TestASpentWindowCostsNoSteps` | Serving safety |
| Background work needs process-global ownership | `data/recovery-foreground-qos/shared-budget-two-model.csv` | `EnginePool.configure_shadow_budget` → `SchedulerConfig.shadow_budget`, adopted through the existing shallow copy | `test_shadow_prefill_shared_budget.py::TestOneBudgetForTheProcess` | Scheduler and resource semantics |
| Foreground priority is engine-global | *mechanism only* | `_shadow_foreign_engine_busy` reads the decode registry and the prefill tracker | `test_shadow_prefill_cross_engine.py` | Serving safety |
| Recovery fails closed | *mechanism only* | thirteen fault paths, each dropping or parking without advancing the counter | `test_shadow_prefill_failure_modes.py` | Failure behaviour |
| Recovery is not a user request | *mechanism only* | `Request.is_shadow`, skipped by the three `self.requests` sweeps | `test_shadow_prefill_failure_modes.py::TestTheRecoveryRequestIsInvisibleToTheRequestSweeps` | The recovery request is not a user request |

Two rows carry no dataset and say so. They are serving-safety properties that a
test can prove and a measurement cannot: there is no number that demonstrates
"no fault path advances the committed counter", only an enumeration of the
paths.

---

## 3. What is deliberately not in the PR

The matched-tail Dense-vs-SpecPrefill comparison
([`data/matched-tail-routing/matched-tail.csv`](../data/matched-tail-routing/matched-tail.csv))
is not PCSR evidence and does not appear in the pull request. It measures which
foreground route is cheaper at a fixed restored prefix, which is an admission
question. PCSR does not change admission, and a PR that showed that table would
imply it did. The table belongs to *Speculative Prefill Admission Economics*,
which is a separate thread with a separate experiment still to run.
