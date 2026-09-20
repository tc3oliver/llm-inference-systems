# Research thread — correctness as a constraint on optimization

**Status: research thread, not a completed experiment.** Three pieces of
existing evidence point at one idea, and the idea is worth stating, but they
were collected for three different purposes and none of them was designed to
test it. There is no systematic correctness sweep in this repository and none
was run to create this page.

## The idea the evidence points at

An inference optimization is only allowed to change how fast the answer
arrives. Every optimization here changes the arithmetic that produces it, so
for each one the question is whether the difference reaches the output. The
three cases below answer that question three different ways, and the useful
part is that the answers are not the same.

They also suggest where to look. Each failure or near-failure appeared at a
boundary: a block boundary in the cache, a chunk boundary in attention
routing, a role boundary in the prompt template. Optimizations are built
around boundaries, because a boundary is where work can be divided, and a
boundary is therefore where the division can be wrong.

## Case 1 — restoring a cached prefix does not change the output

`data/correctness/cache-restore-output-identity.csv`, September 2026. Seven
prompts, each issued twice against the same server: once in a state where
little or none of it was cached, and once when the prefix cache could serve
most of it. Prompts were synthetic records, generated for the test. Output
was hashed and compared.

| Case | Pass 1 | Pass 2 | Cached tokens 1 → 2 | Output |
|---|---:|---:|---|---|
| short | 5.985 s | 1.560 s | 0 → 1,024 | identical |
| short tail A | 1.472 s | 1.447 s | 1,024 → 1,024 | identical |
| short tail B | 1.442 s | 1.447 s | 1,024 → 1,024 | identical |
| long | 56.312 s | 2.314 s | 1,024 → 13,824 | identical |
| long tail A | 2.879 s | 2.312 s | 13,824 → 13,824 | identical |
| long tail B | 2.422 s | 2.420 s | 13,824 → 13,824 | identical |
| long reasoning | 2.803 s | 2.805 s | 13,824 → 13,824 | identical |

Seven of seven identical. The long case went from 56.3 s to 2.3 s, a factor
of twenty-four, and produced the same bytes.

This is the optimization that reuses state, and on this evidence it is exactly
neutral on semantics. That is the property EXP-001 relies on when it treats a
restored checkpoint as free: the cache is not an approximation, it is
memoisation, and the only thing it can cost you is the recomputation you were
going to do anyway. Fourteen runs is not a proof, and the test does not cover
quantized cache entries or the recurrent-state path.

## Case 2 — changing the attention route changes the logits and not the answer

`data/correctness/attention-route-logit-divergence.csv`, September 2026.
Thirteen runs of one 68,034-token prompt across three builds that differ in
how the attention kernel is routed at long sequence lengths. The independent
variable is how many of the 256 routing decisions took the bounded path: zero
in one build, 144 in another, all 256 in the third. The prompt is a real
captured agent prompt and is not published; only its length and the hashes
below leave the machine.

| Quantity | Result |
|---|---|
| Distinct logit-vector hashes | 3 — one per build |
| Top-3 logit values | differ between builds by up to about 0.4 |
| Distinct argmax tokens | 1 |
| Distinct top-3 token sets | 1 |
| Distinct output hashes | **1** |

Three implementations that are not numerically identical, at 68K context,
produced the same sampled output on every run. Within a build the logit hash
was stable across repeats, so each build is deterministic; the difference is
between builds, not between runs.

The methodological point is the one worth keeping. A logit-level comparison
would have flagged all three builds as divergent and told you nothing about
whether any of them was wrong. An output-level comparison found them
equivalent for this prompt. Neither check subsumes the other: the logit hash
detects a change the output hash cannot see, and only the output hash speaks
to what a user would receive. A correctness harness that reports one of them
is reporting half the question.

The limit is obvious and should be stated: one prompt. The margin between the
top two logits on this prompt was wide enough to absorb a 0.4 perturbation.
A prompt where the top two candidates sit close together is exactly where
this would break, and no such prompt was tested.

## Case 3 — the optimization that did change what the model saw

This one is written up in full in EXP-001, and is summarised here because it
is the case where the answer came out the other way.

Sparse prefill must not discard the system prompt and tool definitions, so
the implementation protected a prefix of the prompt. The boundary of that
protected region was derived by subtracting a re-render of the non-system
messages from a render of the whole prompt, which assumes a chat template
emits the same text regardless of which roles are present. The template in
use does not. With tools in play the derived boundary fell as little as 37
tokens short, which placed the end of the tool instructions and the start of
the operator's system prompt inside the region the optimization was free to
drop.

The fix measures the boundary rather than inferring it: render the static
messages twice with two different throwaway turns and keep the token prefix
both renders share with the real prompt. Tokens that two probes agree on
cannot depend on conversation content. It was submitted upstream as
[oMLX PR #3756](https://github.com/jundot/omlx/pull/3756), which is open at
the time of writing.

Evidence and the shortfall figure:
[`experiments/exp-001-reusable-state-economics/FINDINGS.md`](../experiments/exp-001-reusable-state-economics/FINDINGS.md).

## What these three cases do and do not establish

They establish that the three optimizations sit in different places on the
semantic-risk axis, and that the placement is not guessable from how
aggressive the optimization sounds. Reusing a cached prefix sounds risky and
is exact. Rerouting an attention kernel sounds like an implementation detail
and perturbs every logit. Protecting a prefix sounds like bookkeeping and was
the one that silently changed the model's input.

They do not establish a correctness boundary as a function of context length,
which is the interesting version of the question. That would need the same
prompt family swept across lengths against a fixed reference, with a decided
threshold, and it does not exist here. Specifically missing:

- No comparison of quantized against unquantized key-value cache output.
- No comparison with speculative decoding on and off. The runtime's verify
  path routes through different kernels at draft depth two and above, so
  output identity there is not guaranteed and was never checked.
- No structured-output or tool-call validity measurement under any
  optimization.
- No reference arm at higher precision. On this machine the model does not
  fit unquantized at long context, so any reference is itself quantized, and
  a study would have to say so as a limitation rather than solve it.

## Provenance and what stays private

The case 1 prompts were synthetic and generated for the test. The case 2
prompt is a real captured agent prompt held outside this repository; it is
not published, and neither is any model output text from it. What is
published is its token count and the hashes, which reveal nothing about its
content.

The local benchmark archive these cases were drawn from contains captured
vendor system prompts. That is why it is not published and why only derived
timings, token counts and hashes appear here. The rule is in
[`DATA_POLICY.md`](../DATA_POLICY.md).
