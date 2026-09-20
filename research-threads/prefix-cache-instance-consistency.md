# Research candidate — two prefix-cache instances for one served model

**Status: recorded, not investigated, not fixed, no upstream claim.** It is kept
out of EXP-003's argument deliberately: EXP-003's own failure was an integration
bug on top of this, and mixing the two would make neither legible.

## What was observed

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

## Why it is not being chased now

It needs its own reproduction before it can be called anything. Specifically:
whether the second instance is an artifact of this particular engine
composition, whether both instances ever serve the *same* prefix in production
traffic, and where the flag divergence originates — `SchedulerConfig`
defaults to `False` and the settings layer resolves `auto` to *enabled when the
SSD cache is on*, which is a plausible source but was not traced.

None of that is EXP-003's question, and EXP-003 does not need it answered: the
experiment's own fix is to bind the recovery job to the instance that served
the request, which is correct whether or not a second instance should exist.

## What would promote it

A minimal reproduction that does not involve any EXP-003 code: start the
server, issue two ordinary requests, and show either that both instances serve
prefixes for the same conversation, or that they do not. If they do, the flag
disagreement is a live correctness question for ordinary traffic and belongs
upstream. If they do not, it is a tidiness question and belongs nowhere.
