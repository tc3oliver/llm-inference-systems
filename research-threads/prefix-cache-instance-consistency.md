# Research thread — prefix-cache instances and their state-preservation contracts

**Status: half of it is established, with two pull requests open upstream; the
half this page was opened for is still not investigated.** It started as a
recorded observation with no claim attached. What has since been established is
a *different* case of the same shape, on a path this page did not originally
name. The original observation below is unchanged and still uninvestigated, and
the two must not be read as one.

The claim this page now carries:

> Cache instances serving one request do not necessarily share a
> state-preservation contract, and on a hybrid recurrent model that asymmetry
> is not cosmetic.

What is established is stated in *The asymmetry that was established* below, and
it is the target-versus-draft form of the question. It is kept out of EXP-003's
argument deliberately: EXP-003's own failure was an integration bug on top of
the original observation, and mixing the two would make neither legible.

## The original observation — two instances for one served model

One instrumented run of one model on one server, traced at the store and
restore boundary (EXP-003's `OMLX_SHADOW_TRACE`). Within a single process,
serving a single model, two distinct `BlockAwarePrefixCache` instances were
live at the same time, each with its own `PagedSSDCacheManager`:

| | instance A | instance B |
|---|---|---|
| `gdn_ssd_split_enabled` | **false** | **true** |
| effective payload layout | embedded | `split_recurrent_v1` |
| served the foreground request's store and restore | yes | no |
| ran `_cleanup_finished` for that same request | no | yes |

Both stored blocks for ordinary, non-internal request ids during the same
session, so this is not one live cache and one vestigial object.

Two things are worth separating.

**The instances.** A request's identity crossing two cache instances means a
block written on one side of a turn is not necessarily matchable on the other.
Nothing observed here shows a *foreground* request losing state to this, and
nothing here establishes that it can; EXP-003 hit it only because it introduced
a new writer.

**The flag disagreement.** `gdn_ssd_split_enabled` differing between the two is
the more interesting half. `payload_layout` is part of the cache signature, and
the restore path consults the GDN sidecar index **only** when the restoring
instance believes the layout is split. A block written under
`split_recurrent_v1` — placeholder in the block, real recurrent state in a
sidecar — is therefore structurally unreadable by an instance that believes the
layout is embedded: it does not look for the sidecar, finds the placeholder and
rejects. That is a correct decision made on an incorrect premise.

## The asymmetry that was established

A separate pair of cache instances serves one SpecPrefill request, and that pair
is not in question: the **target** model's prefix cache and the SpecPrefill
**draft** model's prefix cache are different objects for different models, which
is by design. What was not by design is that they held different
state-preservation contracts while facing the same problem.

Both serve a hybrid recurrent + attention model, where a recurrent layer's state
at an earlier block boundary cannot be re-cut out of the terminal live state the
way a KV layer can.

| | target path | SpecPrefill draft path |
|---|---|---|
| passes `boundary_snapshots` to `store_cache` | yes | **no** |
| recurrent state in a stored full block | preserved | **placeholder** |
| restore outcome on a hybrid model | reconstructs | walk-back finds nothing; miss |
| reads the restored cache's logical position from | the model's attention layers | **`cache[0].offset`**, which layer 0 does not have |

Two consequences followed, and they are independent of each other:

1. **No reusable draft prefix could exist.** Every stored block carried a
   recurrent placeholder, so the walk-back correctly refused every candidate.
   In the recorded baseline arm that is 0 draft cache hits in 10 scorings, and
   the runtime's `partial prefix match detected` rejection 9 times.
   [omlx#3842](https://github.com/jundot/omlx/pull/3842) publishes the recurrent
   state at a reachable boundary.
2. **When one finally existed, it was read as empty.** Making the reuse path
   reachable is what exposed the second defect, which had been latent for as
   long as the first one hid it.
   [omlx#3840](https://github.com/jundot/omlx/pull/3840) derives the position
   from the attention layers instead.

The mechanism, the regression coverage and the runtime evidence are in
[hybrid draft prefix reuse in SpecPrefill](specprefill-draft-cache-reuse.md),
with the data in
[`data/specprefill-draft-cache-reuse/`](../data/specprefill-draft-cache-reuse/).
Both pull requests are open at the time of writing.

**What this does not do is settle the page's original question.** It establishes
the target-versus-draft asymmetry, which is two caches for two models. The
observation above is two caches for *one* model, with a flag disagreement
between them, and nothing here shows that an ordinary foreground request ever
crosses them. That conjecture is still **not established** and the reproduction
that would settle it has still not been run.

## Why the original observation is still not being chased

It needs its own reproduction before it can be called anything. Specifically:
whether the second instance is an artifact of this particular engine
composition, whether both instances ever serve the *same* prefix in production
traffic, and where the flag divergence originates — `SchedulerConfig`
defaults to `False` and the settings layer resolves `auto` to *enabled when the
SSD cache is on*, which is a plausible source but was not traced.

None of that is EXP-003's question, and EXP-003 does not need it answered: the
experiment's own fix is to bind the recovery job to the instance that served
the request, which is correct whether or not a second instance should exist.

## What would promote the part that is still open

A minimal reproduction that does not involve any EXP-003 code: start the
server, issue two ordinary requests, and show either that both instances serve
prefixes for the same conversation, or that they do not. If they do, the flag
disagreement is a live correctness question for ordinary traffic and belongs
upstream. If they do not, it is a tidiness question and belongs nowhere.
