# EXP-003 — What production hardening found that the experiment did not

The experiment answered its question with a research build. Taking the same
mechanism through review for upstream — [omlx#3793](https://github.com/jundot/omlx/pull/3793) —
put it under conditions the runs never created: a second model loaded in the
same process, multi-token prediction enabled, an eviction arriving mid-job, a
prompt whose length happens to be an exact multiple of the cache block.

Six defects came out of that. None of them is visible in `data/`, because none
of them was reached by the workloads that produced `data/`. That is the reason
this file exists rather than a changelog entry: **the conditions a measurement
does not create are not conditions the mechanism survives.** Each finding below
is written so the invariant outlives this particular runtime.

Read against [`EVIDENCE.md`](../../EVIDENCE.md). Most of what follows is
**source-established and reproduced by a failing test**, which is a different
and lower thing than **measured** — a test that fails before a fix and passes
after it demonstrates that the code does what the code does. Exactly one row
here carries a measurement, and it is careful about which half of itself is
measured.

## The six

| # | Finding | Class | Evidence |
|---|---|---|---|
| 1 | Recovery created MTP prompt-priming state that nothing consumed or released | Resource leak | Source-established, reproduced |
| 2 | A parked recovery job kept owning its materialized dense cache | Resource ownership | **Measured** (retained size, MLX release); effect on foreground **not established** |
| 3 | Foreground arrival was invisible to a peer engine until the first chunk executed | Priority inversion | Source-established, reproduced |
| 4 | The inbound marker was mutated from two threads without a lock | Concurrency defect | Source-established, reproduced |
| 5 | `committed_tokens` could outlive the cache blocks backing it | Bookkeeping divergence | Source-established, reproduced |
| 6 | A prompt of exactly N whole blocks recovered only N−1 of them | Off-by-one, boundary | Source-established, reproduced |

Three of them generalise past this runtime, and those three are written up
separately at the end.

---

## 1. Background work must not acquire request-scoped state it will never give back

**Question.** A recovery job is a synthetic request. What per-request
machinery does the runtime attach to a request without being asked, and who
releases it when the request never finishes the ordinary way?

**Observed / reproduced behavior.** On a model with both SpecPrefill and
Lightning-MTP enabled, the recovery job's synthetic request acquired an MTP
prompt-priming sidecar. `prepare_prefix_context` built a plan and filed it
under the recovery request id; each recovery prefill chunk re-activated it; the
forward folded hidden states into it. Nothing ever read it, and no release path
visited that id, because a recovery job does not end through the ordinary
finish path. Nine of the thirteen tests written for the contract failed against
the pre-fix build.

**Mechanism.** MTP prompt priming is owned *by request id*, and its lifecycle
is bound to the foreground request lifecycle — admission creates, decode reads,
finish releases. The recovery job borrows the request shape without any of
those three events. Two separate costs follow: state retained under an id no
sweep visits, and head-history folding executed on forwards whose hidden states
are not part of any generation the head will ever serve.

**Invariant / contract.**

> Canonical-state recovery must not create, capture, publish or retain
> request-owned MTP prompt-priming state, and foreground priming must be
> unchanged.

**Fix.** Three independent mechanisms, because each closes a hole the others
do not.

1. `_prepare_prefix_cache_for_request` skips `prepare_prefix_context` entirely
   when `request.is_canonical_recovery` is set — the sidecar is never built.
2. Every recovery slice runs inside `prompt_priming.suppress_capture()`, so a
   forward that reaches the model with capture armed by some other route folds
   nothing.
3. Every lifecycle exit — finish, park, retire, drop-on-failure,
   drop-on-replacement, cancel — releases the synthetic request id anyway.

**Regression coverage.** `tests/test_canonical_recovery_mtp_isolation.py`, 13
tests in three groups matching the three mechanisms, including a parametrised
sweep over all six exit paths and a foreground control on each group that fails
if the fix is a global disable.

**Remaining limitation.** The model double in those tests satisfies
`_host_eligible` by construction. The interaction was never exercised against a
real MTP checkpoint under recovery, so what is established is the ownership
discipline, not the absence of any other MTP interaction.

**Evidence level.** Source-established; reproduced by a failing test.

---

## 2. Background work must yield ownership, not only execution

**Question.** When recovery stops running — because the foreground arrived,
because its budget window is spent, because the prefill memory throttle fired —
does it also stop *holding* what it had allocated?

**Observed / reproduced behavior.** No. `job.prefill_state.cache` is a
materialized dense cache, allocated by `make_prompt_cache` and filled by dense
forwards, or reconstructed from a block table into freshly allocated arrays. It
is not a set of references into the paged pool. It stayed resident across all
four pause paths: foreground or foreign-engine busy, `_PrefillEvictionNeeded`,
`_PrefillAbortedError`, and a spent budget window. Twelve of the eighteen tests
written for the contract failed against the pre-fix build.

The `_PrefillEvictionNeeded` path is the sharp one. That throttle fires
*because of memory pressure*, and the pre-fix handler reclaimed the Metal cache
while still holding the largest single allocation the recovery job owns.

**Mechanism.** Two parts, and the second is why the first was not simply a
missing `= None`.

*Ownership.* Live reconstruction state is materialized, so parking the job
parks the compute and not the memory.

*Scheduling.* The engine loop calls `step()` only while `has_requests()` is
true, and `has_requests()` ORs in `_has_canonical_recovery_work()`. That
predicate returned False for a spent window and for a peer holding the claim —
so the loop stopped stepping, and there was no executor-thread moment left in
which anything could be freed. MLX state must not be destroyed from the asyncio
thread, and `has_requests()` is called from it.

**Invariant / contract.**

> Published canonical state is durable; in-progress unpublished recovery state
> is disposable. Foreground pressure must not preserve recovery state at the
> expense of foreground headroom.

**Fix.** A single `_canonical_recovery_stand_down()`, called from
`_canonical_recovery_after_step` on the engine thread and nowhere else. Its
trigger set is explicit — foreground work on this engine, a foreground request
that has arrived anywhere in the process, foreground work on another engine, or
a spent budget window — and it deliberately excludes the two-idle-step spacing
rule and a peer holding the recovery claim, both of which clear within a step
or two. The throttle and abort handlers retire the state *before* calling the
reclaim rather than after. `_has_canonical_recovery_work()` gained a clause
that answers True while a live state exists, which is a pure attribute read
safe on the asyncio thread and holds the loop awake for exactly the one step
that frees it.

Retiring costs at most one block of re-read, because publication floors to a
block and runs after every chunk. The job's lineage and its `committed_tokens`
survive; a later idle window rebuilds from the published prefix through the
ordinary prefix-cache path.

**Regression coverage.**
`tests/test_canonical_recovery_state_retirement.py`, 18 tests: four foreground
arrival shapes, two controls that must *not* retire, the spent window, the
throttle ordering (asserted by recording `job.prefill_state` at the moment
reclaim is called), the abort, what survives retirement including a weakref
check that nothing still refers to the dropped state, a foreground-state
control, and four tests that the loop stays awake long enough to free it —
one of which asserts the asyncio-side predicate never calls the retirement path.
Plus a rewritten `TestChunkYields` in
`tests/test_canonical_recovery_failure_modes.py`.

**Remaining limitation.** See the measurement below: the *effect on foreground
headroom* is not established, and the fix is justified by the ownership
contract rather than by a demonstrated foreground improvement.

**Evidence level.** Mixed, and the split matters:

- **Measured** — retained state size, and its release. On
  `Qwen3.5-0.8B-MLX-4bit` (`qwen3_5`, `full_attention_interval` 4) the parked
  state is 18.63 MiB fixed plus 12 KiB per token, exactly linear across 4,096 /
  8,192 / 16,384 tokens, and `mx.get_active_memory()` falls by that amount plus
  a constant 0.21 MiB — the state object's non-cache arrays — at all three
  sizes, returning to its point-A value to within two bytes.
- **Derived** — the production geometry. 64 layers, `full_attention_interval`
  4, 4 KV heads, head_dim 256, bf16 gives 16 × 2 × 4 × 256 × 2 = **64 KiB per
  token**: 2 GiB at 32k, 8 GiB at 128k. Arithmetic over the config, not a
  measurement of that model.
- **Not established** — any foreground effect. `get_phys_footprint()` did not
  respond once the allocator had converged, and was not reproducible run to run
  (1,764 MiB and 4,700 MiB settled, for identical work). The scheduler's own
  guard is `max(mx.get_active_memory(), phys_footprint − hot_cache_cpu)` and
  phys dominates it at these sizes, so retiring the state does not move the
  number the guard reads.

Data and provenance:
[`data/recovery-foreground-qos/`](../../data/recovery-foreground-qos/) —
`recovery-state-retirement.csv` and `recovery-state-headroom-probe.csv`.

---

## 3. Foreground priority needs visibility before execution, not after it

**Question.** A process can hold several loaded models sharing one
accelerator. How does a background job on engine A learn that a foreground
request has arrived for engine B?

**Observed / reproduced behavior.** It learned late. The cross-engine check
read the decode registry and the prefill tracker — both of which are *progress*
instruments, populated once a chunk has executed. A request could arrive, be
admitted, and be waiting for its first forward while a peer engine's recovery
slice was granted against a device that was about to be contended. The inbound
marker that would have covered the gap was per-scheduler, so it was invisible to
the peer; and it was cleared at admission, so even locally it did not cover
admission-to-first-chunk.

**Mechanism.** Progress is not arrival. Every instrument the scheduler had was
downstream of execution, and the window that matters to a background job is
entirely upstream of it. Two windows were open: *arrival → admission* on a peer
engine, and *admission → first chunk* on any engine.

**Invariant / contract.**

> A foreground arrival must be visible process-wide from the moment it arrives
> until the request departs — not from its first executed chunk, and not only
> until admission.

**Fix.** A new `omlx/foreground_arrivals.py`: a process-global registry with
its own lock, holding `note_arrival` / `note_admission` / `note_departure` and
a TTL-expiring `count`. `EngineCore.add_request` records the arrival on the
asyncio loop before the executor hand-off; admission re-stamps rather than
clears; departure removes, including on the admission-failure path. Recovery's
own synthetic request never registers, or the mechanism would stand itself
down.

**Regression coverage.**
`tests/test_canonical_recovery_engine_loop.py::TestForegroundPriorityIsEngineGlobal`,
`tests/test_canonical_recovery_cross_engine.py::TestAnotherEnginesArrivalWithdrawsTheChunk`,
and `tests/test_canonical_recovery_shared_budget.py::TestTheFirstSliceIsNotInvisible`
and `::TestForegroundOnOneEngineBlocksRecoveryOnTheOther`.

**Remaining limitation.** The registry is process-global and the process is
the boundary. Two server processes sharing one accelerator see nothing of each
other, which is the same limit the shared budget has.

**Evidence level.** Source-established; reproduced by a failing test. No
foreground latency effect was measured for this change, and none is claimed —
[`data/recovery-foreground-qos/collision-summary.csv`](../../data/recovery-foreground-qos/collision-summary.csv)
measured a single-engine build and does not cover it.

---

## 4. A marker written on the event loop and expired on the executor needs a lock

**Question.** `note_inbound_request` runs on the asyncio loop;
`_canonical_recovery_inbound_count` runs on the executor and expires stale
entries as it reads. Is that safe?

**Observed / reproduced behavior.** No. The reader iterates the mapping and
then deletes from it, and a dict that grows mid-iteration raises
`RuntimeError: dictionary changed size during iteration` — surfacing inside the
recovery predicate, on the engine loop.

**Mechanism.** The surrounding code's idiom is `_pending_abort_ids`, a set
where `add` and `pop` are one bytecode each and the GIL makes them atomic. That
reasoning does not transfer to a dict comprehension over a mapping another
thread is writing. At realistic arrival rates the comprehension finishes inside
one switch interval, so the race is rare rather than absent — which is exactly
the failure mode that reaches production.

**Invariant / contract.**

> Any bookkeeping a background predicate reads from the executor while the
> event loop writes it must be guarded, whatever the surrounding code's idiom
> for simpler containers.

**Fix.** A lock around the mapping, with the same discipline applied to the new
process-global arrival registry.

**Regression coverage.** `tests/test_canonical_recovery_inbound_concurrency.py`,
6 tests. Three drive both sides concurrently with the window deliberately
widened — the interpreter switch interval dropped to a microsecond and the
mapping pre-loaded to 50,000 entries — and assert that nothing was raised,
which cannot produce a false failure. Three more pin the semantics so the lock
cannot silently change what the counter counts. Without the amplification these
same tests pass against the unlocked dict, and the file says so.

**Remaining limitation.** The amplification is what makes it a test rather than
a coin flip; the tests demonstrate the defect class, not its frequency in
production.

**Evidence level.** Source-established; reproduced by a failing test.

---

## 5. A cache watermark is not cache truth

**Question.** `committed_tokens` records what recovery published and verified.
Can the thing it is a claim *about* disappear underneath it?

**Observed / reproduced behavior.** Yes. The invariant
`canonical_committed_tokens <= independently_restorable_tokens` is verified at
publication, by storing the boundary and reading it back through the ordinary
serving path. That is the right check at the right moment and the two sides
move independently afterwards: the watermark only ever rises, while the blocks
backing an already-published prefix can be evicted — and under `hot_cache_only`
an evicted block is dropped rather than demoted, so a hole opens low in a chain
that was verified when it was written.

`_canonical_recovery_publish` re-probes whenever a *higher* boundary comes
along. The gap is a session that never reaches one: publish 12,288, lose
blocks, then take turns that all stay inside the same block. The job sits parked
with a claim the cache cannot honour — and because `publishable_boundary`
refuses anything at or below the watermark, if it ever resumes it declines to
republish exactly the range that went missing.

**Mechanism.** A monotone counter is a record of work done. Restorability is a
property of a cache under an eviction policy nobody asked the counter about.
Treating the first as evidence for the second is only safe at the instant they
were compared.

**Invariant / contract.**

> `committed_tokens` is bookkeeping; the serving cache is the source of truth.
> The watermark must be able to move backward.

**Fix.** `CanonicalRecoveryJob.note_ground_lost`, called from
`_canonical_recovery_revalidate_ground` at state build — the moment a resuming
job has *just* asked the serving cache what it can restore for this prompt, so
the authoritative number is already in hand. The cache is not asked a second
time and no reference is taken that would have to be given back. The path only
ever lowers, and floors to a whole block; raising remains a publication's job,
because a publication is the only event that verified a boundary was written. A
zero from a model whose cache cannot be reconstructed is the absence of an
answer, not a report of loss, and is ignored.

**Regression coverage.** `tests/test_canonical_recovery_stale_ground.py`, 8
tests: five on the arithmetic of the claim, three walking the whole scenario —
publish, lose the ground, take a turn inside the block, resume — including the
one that shows why walking back is not the same as merely refusing to advance.

**Remaining limitation.** The window between an eviction and the next state
build is not closed and is not closeable at this cost: the job holds a stale
claim during it. That is inert, because a parked job publishes nothing and
decides nothing, but it is stale.

**Evidence level.** Source-established; reproduced by a failing test. The
eviction itself was not induced in a live server.

---

## 6. Generation-prefill semantics are not recovery semantics

**Question.** What happens to a session whose prompt length is an exact
multiple of the cache block size?

**Observed / reproduced behavior.** It recovers one block less than it should.
`_begin_prefill` splits its token list into `tokens[:-1]` and `tokens[-1:]` and
hands the last token to `insert()` as the generation kickoff. Publication
floors to a block boundary, so:

    prompt 10,000, block 4,096 -> last whole block 8,192, prefill reaches
    8,192, publish 8,192.

    prompt  8,192, block 4,096 -> last whole block 8,192, prefill reaches
    8,191, publish 4,096.

Half a two-block session, lost to a token that request never uses. Seven of the
eleven tests written for the contract failed against the pre-fix build.

**Mechanism.** The hold-back exists because a foreground request needs a
kickoff token for generation. A recovery job never samples, never calls
`insert`, and never reads `state.last_token`; it exists only for what it leaves
in the cache. The split was unconditional because until now every caller was a
generation.

The first workaround asked for one token *past* the boundary. That works
whenever the prompt is longer than the boundary and cannot work when the prompt
ends on it — there is no such token — which is precisely the case that was
broken.

**Invariant / contract.**

> A reconstruction prefill is not a generation prefill. It must cover exactly
> the range it intends to publish, with no synthetic token and no compensation
> after the fact.

**Fix.** `_begin_prefill(..., hold_back_last=False)` for recovery states, and
the recovery target is the last whole block itself rather than one token past
it. Nothing artificial is pushed through the model: these are the session's own
tokens, and the range prefilled is exactly the range published. The progress
denominator, which had been spelled `total_length - 1` on the assumption that a
hold-back always happens, became `total_length - len(state.last_token)` — the
same number for a foreground state and the right one for a recovery state.

**Regression coverage.** `tests/test_canonical_recovery_exact_block.py`, 11
tests across three levels: the target chosen at candidate admission
(parametrised over exact-one-block, exact-two-block, a remainder and one token
past), the state actually built and what it will push through the model with a
foreground control that must still hold its token back, and the boundary the
job can then publish.

**Remaining limitation.** None known for this path. The general shape —
borrowing a generation primitive for a non-generation purpose — is the lesson,
and this file cannot enumerate the other places it might apply.

**Evidence level.** Source-established; reproduced by a failing test, and the
arithmetic above is checkable without running anything.

---

## Three lessons that outlive this runtime

### A. Background work must yield ownership, not only execution

A background task that stops computing has not necessarily stopped consuming.
If it holds materialized state — a reconstructed cache, a decoded buffer, a
staged batch — it is still occupying the resource the foreground wants, and
every scheduling primitive that reasons about *execution* will report it as
idle.

The policy this produced is worth stating without the runtime in it:

> Published output is durable. In-progress, unpublished intermediate state is
> disposable. Foreground pressure must not preserve the second at the expense
> of the first's consumers.

Two design consequences followed and neither is obvious.

*The retirement trigger must be an explicit list, not the negation of
"runnable".* The scheduler's runnability predicate is false for benign,
transient reasons — here, an idle-step spacing rule that is false immediately
after every slice by construction. Retiring on "not runnable" would have
rebuilt the state before every single slice.

*Freeing needs a thread it is allowed to happen on.* The same predicate that
said "no work" was what kept the loop stepping, so the pause that created the
need to free also removed the opportunity. The predicate had to be widened —
with a pure attribute read, not by touching accelerator state from the event
loop — to hold the loop awake for exactly the one step that does the freeing.

What this does **not** establish: that yielding the state improves foreground
latency, admission headroom or throughput. It was justified by the ownership
contract and by a measured retained size, and the foreground effect was
measured for and not found at the precision available.

### B. Foreground priority needs visibility before execution

Progress instruments — a decode registry, a prefill tracker, a step counter —
answer "is something running". A background scheduler needs "is something
waiting", and the interval between those two is exactly the interval in which
yielding is still useful.

> Background work sharing an accelerator cannot infer foreground priority only
> from decode or prefill progress. Arrival visibility must exist process-wide,
> before execution begins, and must persist from arrival to departure rather
> than to admission.

The scoping errors are two and they are independent: a marker can be *late*
(populated by execution) and it can be *local* (populated per engine). Both
were present here and each alone leaves a real window open.

### C. A cache watermark is not cache truth

> A monotone counter records work completed. Restorability is a property of a
> cache under an eviction policy. The first is evidence for the second only at
> the instant they were compared, and any component that carries a watermark
> across time must be able to move it backward.

The corollary that decided the fix's shape: revalidate where the authoritative
answer is already in hand. A resuming job has just asked the cache what it can
restore; asking again would be a second question, a second reference, and a
second thing to get wrong.

---

## What hardening did not change

The experiment's results stand. Nothing here revises a number in
[`FINDINGS.md`](FINDINGS.md) or a row in [`data/`](../../data/), because none
of these defects was reachable by the workloads that produced them — single
engine, MTP off, no eviction during a recovery job, and no prompt landing on an
exact block multiple.

That is a statement about the coverage of the runs, not a defence of it. The
honest reading is the one at the top of this file: six defects lived inside the
envelope the measurements did not cover, and every one of them was found by
reading the source against a contract rather than by a workload that failed.

One limitation the hardening confirmed rather than removed:

> Recovery currently keeps one job per engine. Interleaved independent lineages
> replace one another rather than queue, bounding background state but
> potentially reducing recovered-token yield to zero under fan-out.

That is a design and economic limitation, not a correctness defect — the
replacement path is orderly and publishes nothing it has not verified. It is
pinned by `tests/test_canonical_recovery_lineage.py::TestB7FanOutIsBoundedByHavingOneSlot`
and carried in [`LIMITATIONS.md`](LIMITATIONS.md). No queue is proposed here.

---

Taking the mechanism into a real agent workload afterwards found two more
SpecPrefill defects, and they are deliberately **not** findings 7 and 8. Both
are in the draft-cache path rather than in this mechanism, and both are
recorded in
[hybrid draft prefix reuse in SpecPrefill](../../research-threads/specprefill-draft-cache-reuse.md)
as [omlx#3840](https://github.com/jundot/omlx/pull/3840) and
[omlx#3842](https://github.com/jundot/omlx/pull/3842). The six above were found
by reading this source against a contract; those two were found by instrumenting
a workload that would not activate as expected.
