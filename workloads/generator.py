"""Deterministic synthetic prompt and session generation.

    make_prompt(tokens, kind='code'|'prose', seed) -> (text, info)

The text is grown until its tokenized length is within a tolerance of the
target (default 3%). Length is measured with the tokenizer from the model
directory when transformers can load one; otherwise it falls back to a
4-characters-per-token approximation and sets `approx: True` in the info dict,
which callers should carry into their notes.

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


def make_session(shape_yaml: str | os.PathLike | dict, seed: int = 0,
                 model_dir: str | os.PathLike | None = None,
                 count_fn: Callable[[str], int] | None = None,
                 approx: bool | None = None) -> list[dict]:
    """Build the turns of a session from a shape file.

    A shape is a list of turns, each with add_tokens, kind, output_max_tokens
    and idle_s. Each returned turn carries the generated `text` for its new
    tokens, so a caller can append it to a growing conversation.
    """
    shape = shape_yaml if isinstance(shape_yaml, dict) else load_shape(shape_yaml)
    turns = shape.get("turns") or []
    if count_fn is None:
        count_fn, detected_approx = token_counter(model_dir)
        approx = detected_approx if approx is None else approx

    built = []
    for position, turn in enumerate(turns):
        add_tokens = int(turn.get("add_tokens", 0))
        kind = turn.get("kind", "prose")
        text, info = make_prompt(
            add_tokens, kind=kind, seed=seed * 1000 + position,
            count_fn=count_fn, approx=approx,
        )
        built.append({
            "index": position,
            "text": text,
            "add_tokens": add_tokens,
            "actual_tokens": info["actual_tokens"],
            "kind": kind,
            "output_max_tokens": int(turn.get("output_max_tokens", 128)),
            "idle_s": float(turn.get("idle_s", 0.0)),
            "approx": info["approx"],
        })
    return built
