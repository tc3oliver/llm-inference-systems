"""Deterministic synthetic prompt and session generation.

    make_prompt(tokens, kind='code'|'prose', seed) -> (text, info)

The text is grown until its tokenized length is within a tolerance of the
target (default 3%). Length is measured with the tokenizer from the model
directory when transformers can load one; otherwise it falls back to a
4-characters-per-token approximation and sets `approx: True` in the info dict,
which callers should carry into their notes.

A session shape may also carry a shared `head` and mark a turn as a `reset`,
which together express a `/compact`-style discontinuity: the history is
replaced and the two streams still share the head.
`longest_common_token_prefix` measures how much of it actually survives
tokenization.

Nothing here is copied from a real prompt. Identifiers, sentences and structure
are assembled from the word lists below.
"""

from __future__ import annotations

import os
import pathlib
import random
from typing import Callable

DEFAULT_TOLERANCE = 0.03
CHARS_PER_TOKEN = 4.0

NOUNS = [
    "buffer", "scheduler", "cursor", "segment", "checkpoint", "token", "window",
    "shard", "lattice", "registry", "frame", "cache", "queue", "digest", "slice",
    "budget", "manifest", "sequence", "planner", "index", "record", "channel",
    "threshold", "partition", "observer", "gate", "quota", "pipeline", "snapshot",
]
VERBS = [
    "collect", "resolve", "flush", "admit", "evict", "merge", "score", "encode",
    "retire", "reserve", "compact", "promote", "rebalance", "drain", "seal",
    "replay", "trim", "stage", "settle", "dispatch", "annotate", "reconcile",
]
ADJS = [
    "warm", "cold", "paged", "dense", "sparse", "pinned", "idle", "partial",
    "nested", "linear", "bounded", "stale", "eager", "lazy", "shared", "local",
]
CONNECTORS = [
    "because", "so that", "while", "unless", "after", "before", "whenever",
    "provided that", "although", "once",
]
TYPES = ["int", "float", "str", "bytes", "bool", "list[int]", "dict[str, float]"]


# ----------------------------------------------------------------- tokenizer


def _tokenizer_candidates(model_dir: str | os.PathLike | None) -> list[pathlib.Path]:
    """The directory itself, then a subdirectory named by MODEL.

    A model directory may hold one model or be a parent holding several, one
    per model id, so both layouts are tried.
    """
    root = model_dir or os.environ.get("OMLX_MODEL_DIR")
    if not root:
        return []
    root = pathlib.Path(root).expanduser()
    candidates = [root]
    model = os.environ.get("MODEL")
    if model:
        candidates.insert(0, root / model)
    return [path for path in candidates if path.is_dir()]


def _load_tokenizer(model_dir: str | os.PathLike | None):
    candidates = _tokenizer_candidates(model_dir)
    if not candidates:
        return None
    try:
        from transformers import AutoTokenizer
    except Exception:
        return None
    for path in candidates:
        if not (path / "tokenizer.json").exists() and \
                not (path / "tokenizer_config.json").exists():
            continue
        try:
            return AutoTokenizer.from_pretrained(str(path), trust_remote_code=False)
        except Exception:
            continue
    return None


def token_counter(model_dir: str | os.PathLike | None = None) -> tuple[Callable[[str], int], bool]:
    """Return (count_fn, approx). approx is True when no tokenizer was found."""
    tokenizer = _load_tokenizer(model_dir)
    if tokenizer is None:
        return (lambda text: max(1, round(len(text) / CHARS_PER_TOKEN)), True)

    def count(text: str) -> int:
        return len(tokenizer.encode(text, add_special_tokens=False))

    return (count, False)


def token_encoder(model_dir: str | os.PathLike | None = None) -> tuple[Callable[[str], list], bool]:
    """Return (encode_fn, approx), the encoding counterpart of token_counter.

    The fallback splits the text into fixed CHARS_PER_TOKEN-sized runs. That is
    not a tokenization, and a prefix measured with it is an approximation; the
    caller carries `approx` onward exactly as make_prompt's callers do.
    """
    tokenizer = _load_tokenizer(model_dir)
    if tokenizer is None:
        step = int(CHARS_PER_TOKEN)
        return (lambda text: [text[i:i + step] for i in range(0, len(text), step)], True)

    def encode(text: str) -> list[int]:
        return tokenizer.encode(text, add_special_tokens=False)

    return (encode, False)


# ------------------------------------------------------------------ content


def _prose_sentence(rng: random.Random) -> str:
    return (
        f"The {rng.choice(ADJS)} {rng.choice(NOUNS)} will {rng.choice(VERBS)} "
        f"every {rng.choice(ADJS)} {rng.choice(NOUNS)} {rng.choice(CONNECTORS)} "
        f"the {rng.choice(NOUNS)} stays below its {rng.choice(NOUNS)}."
    )


def _prose_paragraph(rng: random.Random, sentences: int = 5) -> str:
    return " ".join(_prose_sentence(rng) for _ in range(sentences))


def _code_block(rng: random.Random, index: int) -> str:
    name = f"{rng.choice(VERBS)}_{rng.choice(NOUNS)}_{index}"
    arg_a, arg_b = rng.choice(NOUNS), rng.choice(NOUNS)
    type_a, type_b = rng.choice(TYPES), rng.choice(TYPES)
    field = rng.choice(NOUNS)
    limit = rng.randint(2, 512)
    return (
        f"def {name}({arg_a}: {type_a}, {arg_b}: {type_b}) -> {type_b}:\n"
        f'    """{_prose_sentence(rng)}"""\n'
        f"    {field} = 0\n"
        f"    for step, item in enumerate({arg_a}):\n"
        f"        if step % {limit} == 0:\n"
        f"            {field} += len(str(item))\n"
        f"        else:\n"
        f"            {field} -= 1\n"
        f"    return {arg_b} if {field} > {limit} else {arg_b}\n"
    )


def _chunk(kind: str, rng: random.Random, index: int) -> str:
    if kind == "code":
        return _code_block(rng, index) + "\n"
    if kind == "prose":
        return _prose_paragraph(rng) + "\n\n"
    raise ValueError(f"unknown kind: {kind!r}")


# ------------------------------------------------------------- token prefix


def longest_common_token_prefix(left, right) -> int:
    """How many leading tokens two token sequences share.

    This is the quantity a compaction experiment reports. When a session's
    history is replaced, the canonical state that survives is exactly the
    prefix the new token stream still agrees on, and that has to be measured
    rather than assumed: two texts sharing a character prefix need not share a
    token prefix, because a tokenizer merges across the point where they
    diverge and the last token of the shared region can differ.
    """
    shared = 0
    for left_token, right_token in zip(left, right):
        if left_token != right_token:
            break
        shared += 1
    return shared


def turn_prefix_overlap(left_text: str, right_text: str,
                        encode_fn: Callable[[str], list] | None = None,
                        model_dir: str | os.PathLike | None = None,
                        approx: bool | None = None) -> dict:
    """The shared leading tokens of two turns, as {tokens, approx}.

    `approx` is True when no tokenizer was available and the fallback encoder
    stood in for one, in which case the count is an estimate and the caller
    carries that onward.
    """
    if encode_fn is None:
        encode_fn, detected_approx = token_encoder(model_dir)
        approx = detected_approx if approx is None else approx
    return {
        "tokens": longest_common_token_prefix(encode_fn(left_text),
                                              encode_fn(right_text)),
        "approx": bool(approx),
    }


# ------------------------------------------------------------------- public


def make_prompt(tokens: int, kind: str = "prose", seed: int = 0,
                model_dir: str | os.PathLike | None = None,
                tolerance: float = DEFAULT_TOLERANCE,
                count_fn: Callable[[str], int] | None = None,
                approx: bool | None = None) -> tuple[str, dict]:
    """Generate text whose tokenized length is within `tolerance` of `tokens`.

    Returns (text, info) where info carries target_tokens, actual_tokens, kind,
    seed and approx.
    """
    if tokens < 1:
        raise ValueError("tokens must be at least 1")
    if count_fn is None:
        count_fn, detected_approx = token_counter(model_dir)
        approx = detected_approx if approx is None else approx
    approx = bool(approx)

    rng = random.Random(seed)
    parts: list[str] = []
    index = 0
    text = ""
    count = 0
    low = tokens * (1 - tolerance)
    high = tokens * (1 + tolerance)

    # Grow in chunks until we are at or above the target.
    while count < low:
        parts.append(_chunk(kind, rng, index))
        index += 1
        text = "".join(parts)
        count = count_fn(text)
        if index > 100000:  # pragma: no cover - defensive
            break

    # Then trim word by word from the end until we land inside the band.
    if count > high:
        words = text.split(" ")
        while len(words) > 1 and count > high:
            drop = max(1, int((count - tokens) / 2))
            words = words[:-drop]
            text = " ".join(words)
            count = count_fn(text)

    # A trim can overshoot downward; pad with single words to climb back.
    guard = 0
    while count < low and guard < 10000:
        text += " " + rng.choice(NOUNS)
        count = count_fn(text)
        guard += 1

    return text, {
        "target_tokens": tokens,
        "actual_tokens": count,
        "kind": kind,
        "seed": seed,
        "approx": approx,
        "tolerance": tolerance,
    }


def load_shape(path: str | os.PathLike) -> dict:
    import yaml

    return yaml.safe_load(pathlib.Path(path).read_text())


def _head_seed(seed: int) -> int:
    """A seed no turn position can take.

    Turn seeds are `seed * 1000 + position` with position at least 0, so
    negating the session's base keeps the head's text out of every turn's own
    stream however many turns a shape has.
    """
    return -(seed * 1000 + 1)


def make_session(shape_yaml: str | os.PathLike | dict, seed: int = 0,
                 model_dir: str | os.PathLike | None = None,
                 count_fn: Callable[[str], int] | None = None,
                 approx: bool | None = None) -> list[dict]:
    """Build the turns of a session from a shape file.

    A shape is a list of turns, each with add_tokens, kind, output_max_tokens
    and idle_s. Each returned turn carries the generated `text` for its new
    tokens, so a caller can append it to a growing conversation.

    Two optional keys describe a session that does not only grow. A shape-level
    `head: {tokens, kind}` generates one fixed block and places it at the front
    of the first turn and of every turn marked `reset: true`; a turn marked
    `reset: true` starts a new conversation instead of appending to the one
    before it. Together those are what a `/compact` looks like from the
    server's side — the history is replaced, and what survives is the head the
    two streams still share. A shape using neither key behaves exactly as
    before, and `reset` is False and `head_tokens` null on every turn it
    produces.
    """
    shape = shape_yaml if isinstance(shape_yaml, dict) else load_shape(shape_yaml)
    turns = shape.get("turns") or []
    if count_fn is None:
        count_fn, detected_approx = token_counter(model_dir)
        approx = detected_approx if approx is None else approx

    head_spec = shape.get("head") or {}
    head_text = ""
    head_tokens = None
    if head_spec.get("tokens"):
        head_body, head_info = make_prompt(
            int(head_spec["tokens"]), kind=head_spec.get("kind", "prose"),
            seed=_head_seed(seed), count_fn=count_fn, approx=approx,
        )
        head_text = head_body + "\n\n"
        head_tokens = head_info["actual_tokens"]

    built = []
    for position, turn in enumerate(turns):
        add_tokens = int(turn.get("add_tokens", 0))
        kind = turn.get("kind", "prose")
        text, info = make_prompt(
            add_tokens, kind=kind, seed=seed * 1000 + position,
            count_fn=count_fn, approx=approx,
        )
        reset = bool(turn.get("reset", False))
        # The head opens the session and opens it again after every reset, so
        # the streams on either side of a reset share it.
        carries_head = bool(head_text) and (position == 0 or reset)
        built.append({
            "index": position,
            "text": (head_text + text) if carries_head else text,
            "add_tokens": add_tokens,
            "actual_tokens": info["actual_tokens"],
            "kind": kind,
            "output_max_tokens": int(turn.get("output_max_tokens", 128)),
            "idle_s": float(turn.get("idle_s", 0.0)),
            "approx": info["approx"],
            "reset": reset,
            "head_tokens": head_tokens if carries_head else None,
        })
    return built


def session_prompts(turns: list[dict]) -> list[str]:
    """The user-side text each turn of a built session presents.

    The runner appends every turn to a growing message list and empties that
    list at a turn marked `reset`. This is the same accumulation over text
    alone, so a shape's prefix properties can be checked without a server.
    Concatenation stands in for the chat template, which inserts the same role
    markers at the same places on both sides of a reset.
    """
    prompts = []
    accumulated: list[str] = []
    for turn in turns:
        if turn.get("reset"):
            accumulated = []
        accumulated.append(turn["text"])
        prompts.append("".join(accumulated))
    return prompts
