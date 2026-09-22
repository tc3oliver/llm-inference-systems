# Hybrid draft prefix reuse in SpecPrefill

*Research thread; upstream validation pending.*

SpecPrefill scores a prompt with a small draft model to decide which tokens the
target model must compute densely. That scoring pass has a prefix cache of its
own, so a continuation should only have to score the part of the prompt that is
new. On a hybrid recurrent-plus-attention draft model it never did: every
scoring re-read the whole prompt, however much of it the cache already held.

This page is the investigation of why. It is not an experiment and there is no
EXP-004. The mechanism is established and the two defects behind it are fixed
and submitted; what is missing is a matched end-to-end comparison, and the
runtime evidence below says exactly where it stops being one.

Conventions carry over from the rest of the repository. The evidence ladder is
[`EVIDENCE.md`](../EVIDENCE.md); the data is
[`data/specprefill-draft-cache-reuse/`](../data/specprefill-draft-cache-reuse/).

---

## Question

On a hybrid recurrent + attention draft model, why does SpecPrefill re-score
the whole prompt on every turn although its draft prefix cache is enabled?

Two questions, and the useful thing about them is that they fail in different
places:

1. When a restored draft cache **does** exist, does the runtime actually use
   the prefix it restored?
2. Does the runtime ever produce a checkpoint a hybrid recurrent draft cache
   could be restored **from**?

The answer to both is no, for two unrelated reasons, and either one alone is
enough to make the cache useless. That is why the reuse looked like a single
missing feature for as long as it did.

## Observed

- Historical hybrid draft scoring: draft cache hits **0**, over every session
  observed. **Observed** — the runtime's own counter.
- Walk-back over the stored blocks finds no valid recurrent checkpoint and says
  so, once per scoring:
  `ArraysCache layer 0: partial prefix match detected (placeholder in last
  matched block). Rejecting cache to prevent stale GDN state.`
- After an experimental path made the first cache hits reachable, a restored
  prefix was **still** scored as though it were empty: `cached_len` came out 0
  with a non-empty cache attached.
- A real coding-agent workload is what raised the question. Its session wall
  time is not treated as an effect size here, and the numbers below are
  per-prompt rather than per-session for that reason.

The last of those is the reason the two mechanisms below are stated separately
rather than as one fix. Making state available and reading its position
correctly are independent contracts, and repairing one exposes the other.

## Mechanism A — restored state existed and was read as empty

`score_tokens()` derived both the cached length and the pre-lookahead offset
from `cache[0].offset`, guarded by a `hasattr` check. Layer 0 of a hybrid GDN
model is `linear_attention`, backed by an `ArraysCache`, which carries no
logical token `offset` at all. The guard therefore answered **0** rather than
failing, on every hybrid model, every time.

What follows from that:

- A restored draft cache was interpreted as sitting at position 0.
- The full prompt was re-prefilled **on top of** a cache that already held its
  prefix, rather than only the uncached suffix.
- Token-importance computation reads the same cache, so the selection could be
  computed against contents that do not correspond to the positions it assumed.

The fix derives the position from the model's attention layers through the
existing attention-layer-to-cache-index mapping, takes the leaf offset of a
composite cache, cross-checks every position-bearing entry against the others,
and raises rather than guessing when no entry can settle it. No layer index is
hard-coded and the importance computation is untouched.

| | |
|---|---|
| Root cause | **Source-established and reproduced** — 19 regression tests, 16 of which fail against the unfixed source |
| Speed effect | **Measured** only once Mechanism B's path made hits reachable at all; see below |
| Output equivalence on a real session | **Not established** |

Upstream: [omlx#3840](https://github.com/jundot/omlx/pull/3840), open at the
time of writing.

A second thing fell out of the fix and is worth recording separately, because
it is the kind of defect that only appears once a dead path comes alive. With
`cached_len` pinned at 0, the exact-cache-hit branch in the same function was
unreachable on every hybrid model. Once the position was read correctly the
branch ran, and it scored `n_prompt + 1` tokens on an exact hit. Both call
sites are now clamped to the prompt length.

## Mechanism B — reusable recurrent state was never published

Draft scoring called `store_cache()` without `boundary_snapshots`, which the
target path has passed since the parameter was added.

The asymmetry between the two kinds of layer is the whole mechanism:

- A **sliceable** KV layer can be re-cut out of the live cache afterwards at
  any block boundary, so the store needs nothing extra from the producer.
- A **recurrent** layer cannot. Its state at an earlier boundary is not
  recoverable from the terminal live state; the transformation is not
  invertible and nothing retains the intermediate.

So every stored full block carried a placeholder for each recurrent layer,
while the only live recurrent state sat past the trailing partial block — which
`store_cache` skips, because a partial block is not a reusable unit. Walk-back
then scanned the stored blocks, correctly found no usable checkpoint, and
reported a miss. Every time, for every conversation. The rejection was not a
bug in the cache; the cache was right.

The fix captures the recurrent state during the draft prefill, at a block
boundary the prefill actually reaches, and passes it to `store_cache` keyed by
absolute token count, exactly as the scheduler already does for the target
model:

- **At most one capture per scoring**, at the latest reachable boundary.
  Materializing state at every block boundary and keeping the last costs a full
  extraction per block for nothing.
- **A restored suffix that completes in a single chunk still publishes**, as
  long as a boundary was reached — the earlier defect was that it did not, so
  the very sessions the cache helps most stopped advancing it.
- **Sliceable layers are nulled, not carried.** Pinning them into a snapshot
  would hold the whole growing KV for no benefit; `store_cache` re-slices them.
- **The boundary is found by walking the prefill loop, not by solving it.** The
  last chunk is truncated to leave a token for the logits call, so the reported
  positions are not multiples of the step size. A closed form got this wrong
  over a wide range of inputs and the sweep test is what caught it.
- Restored state is then consumed by Mechanism A's corrected position read, so
  a warm scoring prefills `n_prompt - cached` and nothing more.

| | |
|---|---|
| Root cause | **Observed** — the rejection log line, 9 times in the baseline arm and 0 times in the treatment arm |
| Mechanism | **Source-established and reproduced** — 17 regression tests, including a sweep against the real prefill loop |
| Speed effect | **Measured**, with the divergence caveat below |

Upstream: [omlx#3842](https://github.com/jundot/omlx/pull/3842), open at the
time of writing, stacked on #3840 and explicitly not to be merged before it.

## Memory ownership is the third contract

Recorded separately because it is not a variant of either mechanism above, and
because a hit-rate measurement is blind to it by construction.

The draft cache has to be allocated inside the prefill helper, since
`score_tokens` hands its own cache back only after scoring — far too late to
read state at a boundary. So its lifetime becomes that function's problem, and
four names can end up pointing into it: `used_cache`, `draft_cache`, the
reconstructed cache from a prefix-cache hit, and the payload extracted out of
it for the snapshot. Any one of them still live at `sync_and_clear_cache()`
keeps the draft KV alive past the single point that returns those buffers.

Two things go wrong at once, and they are easy to mistake for each other:

- **Real headroom.** The buffers are not returned, so the memory is simply
  still held, at a moment the runtime believes it has just been freed.
- **Accounting.** The reclaim delta is measured across that call, so a retained
  alias makes the runtime under-report what it holds — the error is in the
  direction that hides itself.

A `weakref` taken across the clear is now the regression test, and it was worth
more than the fix it guards: writing it exposed two ways to accidentally
measure the test instead of the code. A `MagicMock` installed with
`side_effect=` records its call arguments and so holds the cache itself; and a
weak reference to the cache *list* is satisfied while an extracted payload
still holds the individual layers. Both versions passed while proving nothing.

> Cache reuse is not correct merely because a lookup hits. The runtime must
> preserve the right state, interpret its logical position correctly, and
> release every transient owner at the intended reclamation boundary. Those are
> three independent contracts and each one fails silently on its own.

## Runtime evidence

Two arms, back to back, same corpus, same machine, hybrid GDN pair — 27B
target, 0.8B draft. The baseline arm is the treatment's own parent commit,
Mechanism A alone, so the only difference between the arms is the
boundary-snapshot plumbing.

| | baseline | treatment |
|---|---|---|
| scorings | 10 | 27 |
| draft cache hits | **0** | **23** |
| `partial prefix match` rejections | **9** | **0** |
| sparse prefills completed | 10 of 10 | 27 of 27 |
| scoring wall time | 33.1 s | 38.7 s actual |

Raw totals are not the comparison and the row above is not read as one — each
session took its own trajectory, so the two arms did not see the same prompts.
The comparison is per-prompt, against a least-squares fit of the baseline arm's
own scoring time against tokens prefilled, `-1.05 + n * 1.920e-4` s, fitted on
n = 13,291..34,010.

| n_prompt | cached | suffix | actual | baseline@n | |
|---|---|---|---|---|---|
| 13,224 | 0 (cold) | 13,224 | 1.6 s | 1.5 s | 0.9x |
| 16,556 | 12,288 | 4,268 | 0.8 s | 2.1 s | 2.7x |
| 20,446 | 19,456 | 990 | 0.3 s | 2.9 s | 9.6x |
| 26,904 | 19,456 | 7,448 | 1.6 s | 4.1 s | 2.6x |
| 38,495 | 0 (cold) | 38,495 | 6.6 s | 6.3 s | 1.0x |
| 47,978 | 47,104 | 874 | 0.6 s | 8.2 s | 13.6x |

Six representative rows; all 37 are in
[`runtime-scoring.csv`](../data/specprefill-draft-cache-reuse/runtime-scoring.csv).
Summed over the 27 treatment scorings, 38.7 s actual against 140.4 s of fitted
baseline equivalent — **Derived**, and derived from a fit that is extrapolated
for 13 of those 27 rows.

Three things in that table matter more than the ratios.

**The published boundary tracks the conversation.** 21 captures across the arm,
18 distinct boundaries, rising from 12,288 to 47,104 as the session grows. A
warm scoring then prefills exactly `n_prompt - cached`, which is what makes the
cheap rows cheap.

**The four cold scorings cost nothing.** They land at 0.9x, 1.0x, 1.0x and 0.9x
of the fitted baseline. Since the capture machinery runs on those turns too,
that is the evidence that it is not being paid for when it does not pay off.

**One capture per scoring.** The `extractions` column is 1 on each of the 21
scorings that reached a boundary and 0 on the 6 that did not — never more.

## Correctness

Established by regression test, which in this repository means
**source-established and reproduced** and does not enter the ladder at
**Measured**:

- Cold and warm scoring agree on the computed importance vector.
- Cold and warm scoring agree on the selected token set.
- A restored warm cache prefills the suffix only, with no duplicated prefix.
- An exact cache hit is trimmed rather than scoring one token past the prompt.
- At every position the prefill loop reports, the cache holds exactly that many
  tokens — so a checkpoint cannot be stored under a token count it does not
  have.
- The published boundary equals the token count of the state published with it.
- Nothing names the restored cache, or state pulled out of it, when the buffers
  are returned.

**Not established:** that two real stochastic sessions produce the same output.
Both arms reached the same answer on the same task, which is two samples
agreeing and not an output-equivalence result. There is no fixed-seed matched
pair here, and the correctness thread's standing point applies — a mechanism
can be output-identical on seven paired cases and still be the thing that
changed the answer on the eighth.

## Limitations

- Validated on one hybrid draft topology. Nothing here says what happens on
  another.
- An **all-recurrent** draft model, where no layer is sliceable, is not a
  supported target and its snapshot-base behaviour is not established. The
  reviewer's own check confirmed the topology in use cannot reach that path; no
  untested guard was added for a configuration that cannot occur.
- No fixed-seed matched end-to-end output comparison exists.
- The runtime result requires **both** #3840 and #3842. Either alone leaves the
  draft cache unusable, which is why the treatment arm is measured against a
  parent that already carries #3840.
- The two arms' prompt trajectories diverge, so raw session totals are not a
  matched effect size and are not reported as one.
- The runs used a draft `block_size` of 1024, which is this machine's configured
  value; the upstream rule would select 2048 for the same model. Replaying the
  measured prompt sequence through the boundary arithmetic publishes the same
  boundary set at 1024 and 2048 and a smaller one at 4096, so the block size
  does not change the conclusion on this workload. That replay is **Derived** —
  it re-runs the arithmetic, not the server — and its arithmetic lives in the
  pull request rather than in `data/`.
- Both pull requests are open. Nothing here is upstream behaviour yet.

## Where this connects

EXP-001 ended on a question this thread answers half of. The cache cliff there
was a partial prefix match rejected at a restore because the last matched block
held a placeholder, and the study's closing question was whether a prefill
optimization can be made to leave a valid reusable checkpoint behind rather
than a placeholder the cache must reject. Mechanism B is that, for the draft
cache: the placeholder is there because nobody published the recurrent state,
and publishing it at a reachable boundary removes it.

It is half an answer and not a whole one. EXP-001's cliff was on the target
model's prefix cache, on a different code path, and no measurement here reaches
it. That the two failures share a shape is **Inferred**, and no control in this
repository separates a shared mechanism from a shared symptom.
