# Terminology

Two terms here are mine. I needed names for two things I kept describing in
full sentences, and I use them throughout EXP-001. Neither is established
vocabulary, neither appears in the literature, and you should not expect
anyone else to recognise them.

## Terms introduced by this study

### Cache cliff

*Introduced by this study. Not established terminology.*

The event where the reusable dense checkpoint falls behind the current
context and stops advancing. Before the cliff, each request extends the
checkpoint and the next request starts from further along. After it, the
checkpoint is frozen at some earlier position while the conversation keeps
growing, so every subsequent request recomputes a suffix that gets longer each
time.

In Trace B the cliff is request 11: the checkpoint goes from 37,888 tokens
back to 28,672 and stays there for the remaining ten requests, while the
uncached suffix goes from 6,902 to 17,060 in the same step and reaches 33,979
by the end of the session. The proximate cause was a partial prefix match
rejected by the cache layer to prevent stale state.

The cliff is an event at a restore, and it comes first. Request 10 restored
37,888 tokens with a 6,902-token suffix, below the 8192-token threshold, and
ran dense. The 17,060-token miss the request-11 restore left is what crossed
the threshold and engaged SpecPrefill. So the request-11 sparse admission did
not cause the request-11 cliff. The log names a partial match whose last
matched block held a placeholder, and the trace does not establish when or
how that placeholder was created, so what triggered the cliff is not settled.
What is settled is that the checkpoint did not recover afterwards.

The word is chosen for the shape in Figure 3. It is a step down followed by a
flat line, not a gradual degradation.

### Prefix-cache debt

*Introduced by this study. Not established terminology.*

The repeated recomputation accumulated after the reusable dense state falls
behind the current context and fails to recover: the running total of tokens
reprocessed while the checkpoint stays behind, growing request by request as
the context grows. The definition is observational on purpose. Calling it
debt says what the session keeps paying, not what it would have paid in a
counterfactual where the optimization never ran, which was never measured.

The debt has a second-order cost. The scorer that selects which tokens to
compute sparsely runs over the uncached suffix, so its own cost grows with the
debt: 2.7 s at 8,535 tokens scored, 5.7 s at 33,389. The mechanism's overhead
scales with the problem the mechanism created.

Calling it debt is a claim about repayment, and in the regime I observed it
was never repaid, because it was never attempted: every request after the
cliff ran sparse. A dense request that did repay it would have to recompute
the whole suffix since the last good checkpoint, which the suffix series puts
above what the sparse requests saved. That is arithmetic from the trace; I
did not run an arm that forces the dense request.

## Supporting terms

These are ordinary vocabulary, defined because the two terms above do not make
sense without them.

**Prefix cache.** Server-side storage of the computed key-value state for a
prompt prefix, keyed so that a later request sharing that prefix can skip
recomputing it. The reason a second agent turn is faster than the first.

**Reusable checkpoint.** The furthest position in the current context for
which valid cached state exists. A request restores from the checkpoint and
computes forward from there. In the trace CSVs this is the `cached_tokens`
column.

**Uncached suffix.** The tokens between the checkpoint and the end of the
prompt, which must be computed on this request. The `uncached_suffix` column.
This is the work a request actually does at prefill, and it is what latency
tracks.

**Dense prefill.** Computing every token of the uncached suffix. Slow, and it
produces state that can be written back to the prefix cache and reused.

**Sparse prefill.** Computing only a selected subset of the uncached suffix
and approximating the rest. Much faster on a cold request. The blocks it did
not fully compute carry a placeholder, so the result is not eligible for the
prefix cache and the sparsified suffix does not advance the normal reusable
dense prefix state. That asymmetry is the whole subject of EXP-001.

**SpecPrefill.** The attention-based sparse prefill mechanism used in this
study: a small scorer model selects which tokens of the uncached suffix get
full attention computation, and the mechanism engages only above an
8192-token threshold. It is the specific implementation behind every "sparse
prefill" result here. It is a prefill mechanism and not a form of speculative
decoding, and this repository does not use the word *speculative* for it.

**Scorer.** The component that decides which tokens sparse prefill will
actually compute. It runs over the uncached suffix on every sparse request, so
its cost is a function of suffix length rather than of the saving it enables.
Its per-call cost and token count are in `data/exp-001/trace-b-scorer.csv`.

## Taxonomy

Where these sit, and what the word *speculative* is reserved for:

    Prefill
      ├── Dense Prefill
      └── Sparse / Selective Prefill
            └── SpecPrefill

    Decode
      └── Speculative Decoding
            ├── Native MTP
            ├── VLM MTP
            └── DFlash / other speculative decode paths

*Speculative* belongs to the decode branch, where a draft is proposed and
then verified or rejected. SpecPrefill proposes nothing and verifies nothing;
it selects which tokens to compute. SpecPrefill is not a form of speculative
decoding, and the two branches share no mechanism.
