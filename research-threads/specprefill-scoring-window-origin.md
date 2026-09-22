# Moving draft scoring-window origin invalidates reusable draft state

*Research thread; design gate closed NO-GO. Upstream issue candidate, not filed.*

Once hybrid draft reuse worked ([hybrid draft prefix reuse in
SpecPrefill](specprefill-draft-cache-reuse.md)), a served agent workload still
missed the draft cache on turns whose prompt history had not changed. This page
is why, and why the miss is not fixed. The mechanism is established. Removing
the miss turns out to require a change of scoring semantics, and a comparison
against the dense target does not support making that change.

It is not an experiment and there is no EXP-004. The evidence ladder is
[`EVIDENCE.md`](../EVIDENCE.md); the data is
[`data/specprefill-scoring-window-origin/`](../data/specprefill-scoring-window-origin/),
recorded as a design-gate dataset.

---

## Question

When the prompt history is append-only and unchanged, why does draft scoring
sometimes rescore the whole window cold, and can the reusable draft state be
given an identity that survives it without changing what the scorer computes?

## The claim

> Any change to the draft scoring-window origin makes previously stored draft
> prefix state structurally unmatchable under the current cache keying scheme.

The origin is `score_window_start = max(target_cached, system_end)`. Only a
change that moves that maximum moves the origin: a `system_end` change below
the target's cached frontier does not, and neither does any change to a value
that is not the maximum. Target-frontier progress is one cause, a
system/tool-prefix boundary that moves past the frontier is another. The page
does not say that any particular background mechanism "invalidates the draft
cache"; the one that first exposed this is named below only as the context in
which it was seen.

## Observed

A served build carrying #3840 and #3842 was driven through four arms on
2026-09-23: one Claude Code session with background canonical-state recovery
on, one with it off, and two short scripted append-only sequences, one with it
off and one with it on. 48 scorings, 44 consecutive transitions
(`live-scorings.csv`).

- In all 44 transitions, a draft cache miss occurred exactly when
  `score_window_start` differed from the previous scoring's. **Observed** —
  each row is the runtime's own log line, and the rule is a count over them.
- A miss happened with background recovery **off**: `system_end` went from
  15,952 to 20,108 while the target's cached frontier fell to 15,360, which
  moved the maximum from 20,480 to 20,108. **Observed.**
- With recovery on, a scoring with no publication since the previous one kept
  its origin and hit in 0.6 s; the next publication moved the origin and the
  following scoring missed. **Observed**, one arm, a within-arm control rather
  than a matched pair.

This is what retired the earlier framing that background recovery causes the
miss. It causes one of the origin moves, and the rule does not need it.

## Reproduced

A standalone reproduction with no server, no scheduler loop, no background
work and no agent workload (`standalone-mechanism.csv`, 10 rows). It uses the
real 0.8B hybrid draft model, a real `BlockAwarePrefixCache` over a temporary
directory (block size 256), the real planner and the real draft scoring
function. The prompt is generated from a seed, never written out, and
identified by a hash of its token ids: P0 is 40,960 tokens, P1 is P0 plus the
next 1,024.

| case | cached | system_end | origin | restored | scoring s |
|---|---|---|---|---|---|
| A — append-only | 0 → 0 | 0 | 0 → 0 | 40,960 | 7.7 → 0.5 |
| B — frontier moves only | 0 → 1,024 | 0 | 0 → 1,024 | 0 | 7.0 → 7.0 |
| C — `system_end` moves, maximum does not | 30,000 | 16,000 → 17,000 | 30,000 | 10,240 | 1.3 → 1.1 |
| D — `system_end` moves the maximum | 15,360 | 15,952 → 20,108 | 15,952 → 20,108 | 0 | 4.6 → 3.0 |
| E — one token changed in the history | 0 | 0 | 0 → 0 | 0 | 7.0 → 7.3 |

- A and B are a matched pair: same process, same minutes, same lineage, and
  `cached_tokens` is the only difference. It turns a 40,960-token restore into
  a cold rescore. **Measured.**
- C and D show the variable is the maximum, not either input. **Measured.**
- E fails closed. The fetch matched 4,864 tokens (19 blocks); no recurrent
  snapshot sat at or below that point, so nothing was restored. **Measured.**
- In C the fetch matched 10,752 tokens and 10,240 were restored, which is where
  the previous scoring published its recurrent checkpoint. That is the #3842
  contract doing what it says, not a discrepancy.

The scoring times were taken while the served instance on the same machine
was running background recovery, so they are **Observed** and not a latency
measurement. The hit/miss outcomes do not depend on them.

## Source-established

- The planner builds the draft input from what the target cache left:
  `remaining_tokens = prompt[cached_tokens:]`, then drops
  `max(0, system_end - cached_tokens)` more, so the first scored token is at
  absolute position `max(cached_tokens, system_end)`.
- Draft scoring fetches and stores the draft prefix cache keyed on exactly that
  sequence, and maps selected indices back to absolute positions by adding the
  same origin.
- The paged cache hashes block 0 against a fixed root seed and every later
  block against its parent's hash. Both lookup paths walk from block 0 of the
  sequence they are given, and neither can begin mid-chain.

Moving the scoring-window origin therefore changes the root token sequence
the draft prefix hash chain is built from. The old and new chains cannot match
even when the new scoring sequence is a suffix of unchanged prompt history.
Nine regression tests over the real planner and a real prefix cache pin every
row of the table above, plus a check that a block-aligned suffix of a stored
window never matches. They are unit results and describe the implementation.

One qualification: the chain is keyed by content, not by position. A moved
window whose leading tokens happen to equal a stored window's leading tokens
does match, and that match is valid, because a fresh draft run depends only on
the tokens it is given.

## Why the key cannot simply be changed

The draft scorer sees `prompt[origin:]`. The state that contract calls for,
for a window starting at `w′`, is a fresh run over `prompt[w′:]`, and no state
stored for a window starting at `w` is that:

- the recurrent (GDN) state after `prompt[w:K]` is not the state of a fresh run
  over `prompt[w′:K]`;
- attention keys and values past the first layer depend on every earlier token
  in the window as well.

So no suffix lookup, no dropping of leading blocks and no rebasing of
recurrent state is valid, and under the current contract the miss is correct.
Keeping the draft state across an origin move is only possible by changing
what the scorer is asked to represent. **Source-established** for the cache
path; that the two recurrent states differ is a property of the model family,
not something measured here.

## The design gate

The candidate was a stable full-prompt lineage. The draft always scores
`prompt[0:]`, its cache identity follows the tokens alone, and the target's
cached frontier only decides which part of the importance vector is selected
from. Two further options were considered and not carried forward. Anchoring
at `system_end` still changes semantics whenever the frontier is past the
system prefix, which is the common agent case, and it discards all draft state
on every `system_end` move. An explicit session anchor adds interface without
deciding the question.

### Does it change what the scorer computes?

Yes (`draft-scoring-semantics.csv`, 5 rows). The same prompt was scored cold
under both semantics, over the same selection region, with the lookahead seeded
(repeat noise 0.0):

| frontier | region | rank correlation of importance | selected-set Jaccard |
|---|---|---|---|
| 0 | 41,984 | 1.0000 | 1.000 |
| 1,024 | 40,960 | 0.9564 | 0.772 |
| 4,096 | 37,888 | 0.9504 | 0.756 |
| 16,384 | 25,600 | 0.9016 | 0.749 |
| 30,720 | 11,264 | 0.8494 | 0.691 |

About 13–18% of the selected positions differ. **Measured.** The stable path
restored from stored hybrid state against the same path scored cold gives a
rank correlation of 0.9998 and differs in 0 to 64 of the selected positions
(0.4% at frontier 0). That is chunk-boundary numerics, the same kind the shipped hit path
already has, and 11–33× smaller than the semantic change at the same frontier.

### Is the new selection closer to what the target computes densely?

`target-correctness.csv`, 6 rows. The same 27B target model, the same prompt,
and for each frontier c ∈ {0, 1,024, 30,720} three paths from an identical
target cached state (`prompt[:c]` prefilled densely with the same chunking):
dense prefill of the rest, sparse prefill with the moving-window selection, and
sparse prefill with the full-prompt selection. Both sparse paths select the
same number of tokens from the same region with the same chunking and the same
512-token tail, and go through the runtime's own target sparse-prefill
function. Both draft scorings are cold, so cache-hit state is not a variable.
The target is never sampled. Each path is scored at the ordinary next token and
over a fixed 64-token teacher-forced continuation; the distribution metric is
KL(dense ‖ path). Every path ran twice and repeated exactly.

| frontier | path | next-token KL | mean KL, 64 positions | top-1 agreement | mean top-10 overlap |
|---|---|---|---|---|---|
| 0 | current | 0.00015 | 0.0232 | 0.906 | 5.20 |
| 0 | stable | 0.00015 | 0.0232 | 0.906 | 5.20 |
| 1,024 | current | 0.00006 | **0.0175** | 0.906 | 5.38 |
| 1,024 | stable | 0.00008 | 0.0219 | 0.906 | **5.67** |
| 30,720 | current | 0.00012 | **0.0012** | 0.922 | **6.36** |
| 30,720 | stable | 0.00025 | 0.0018 | 0.922 | 5.91 |

- At frontier 0 the two paths are identical, as they must be: the origin is 0
  for both.
- At both moved frontiers the stable selection is **further** from dense on the
  distribution metric: mean KL 25% higher at 1,024 and 52% higher at 30,720,
  and next-token KL higher at both. **Measured**, one prompt.
- Top-1 agreement is identical on every row. Top-10 overlap is split, one
  frontier each way.
- Both sparse paths are close to dense in absolute terms. The difference
  between them is real in the sense that it repeats exactly, and small.

### Decision: NO-GO

The gate required the stable semantics to be at least no worse than the
current ones against dense, especially at moved frontiers. It is worse on the
distribution metric at both, and mixed on the rest. There is no evidence for
the semantic change, so the stable-origin redesign was not implemented.

The finding therefore stands as:

> Moving scoring-window origin structurally invalidates reusable draft state
> under the current SpecPrefill cache/scoring contract, but eliminating the
> miss requires changing scoring semantics, and current evidence does not
> justify that semantic change.

That the moving window is closer *because* the draft attends only to what the
target still has to prefill is **Inferred**; nothing here isolates why.

**Not established:** that the full-prompt selection gives worse, or better,
answers. Closeness to dense logits is a correctness proxy and not answer
quality, and one prompt is not a distribution.

## Limitations

- One generated prompt of one kind (code), one draft model, one target model.
  The direction held at both moved frontiers, but nothing measures how it
  varies across prompts.
- The target comparison ran on the plain GPU prefill path. The served
  configuration also routes part of the target's prefill to the neural engine;
  that path was not in the comparison, identically for all three arms.
- `system_end` was 0 throughout the correctness comparison. A system prefix
  included in the draft's context is part of the full-prompt semantics and was
  not separately measured.
- The standalone reproduction used a draft block size of 256; the served
  instance uses 1,024. The chain argument does not depend on the block size.
- One path that advances the target's cached count, a retry after eviction
  during external prefill, was not traced to see whether it re-enters draft
  scoring. If it does, it is one more thing that moves the origin.
- The cost of the miss is real and is not recovered: a cold rescore of the
  whole window each time the origin moves.

## What would reopen it

A target-side comparison over many prompts, several kinds and several
frontiers, showing the full-prompt selection no worse than the moving window
against dense — or an answer-level evaluation that makes the proxy moot. Either
would reopen the design, and the stable-lineage architecture above is where it
would start. Short of that, the upstream-facing artifact is an issue recording
the coupling and the standalone reproduction, not a pull request.

## Where this connects

It is the third step of [hybrid draft prefix reuse in
SpecPrefill](specprefill-draft-cache-reuse.md): #3840 made a restored draft
position readable, #3842 made hybrid draft state restorable, and running both
in production exposed that the restorable state is keyed by a window whose
origin the target's own progress moves. It is also the first place in this
repository where a cache miss turned out to be the correct behaviour of the
contract rather than a defect in it.
