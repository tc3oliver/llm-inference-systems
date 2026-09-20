"""Timed inference client for the OpenAI-compatible and Anthropic-style endpoints.

Timing definitions, used consistently everywhere in this repository:

    ttft_s    request start to the first content delta on the stream
    decode_s  first content delta to the last one
    e2e_s     request start to the end of the stream
    prefill_s the server's own prompt_eval_duration, when it reports one;
              null otherwise, never silently equal to ttft_s
    decode_tps (completion_tokens - 1) / decode_s

Only token counts, timings and the server's own usage keys are returned. Prompt
and completion text never leave this function.
"""

from __future__ import annotations

import json
import time
from typing import Any, Iterable

import httpx

from . import settings as settings_mod

USAGE_CACHED_KEYS = (
    "cached_tokens",
    "prompt_cache_hit_tokens",
    "cache_read_input_tokens",
    "prompt_tokens_cached",
)
USAGE_PROMPT_KEYS = ("prompt_tokens", "input_tokens")
USAGE_COMPLETION_KEYS = ("completion_tokens", "output_tokens")

# Timings the server reports on its usage object, in seconds, mapped to the
# run record's own names. The server rounds these to two decimals, so they are
# coarser than the client-measured timings and never replace them.
SERVER_TIMING_KEYS = {
    "time_to_first_token": "ttft_s",
    "time_to_first_visible_token": "visible_ttft_s",
    "prompt_eval_duration": "prefill_s",
    "generation_duration": "generation_s",
    "generation_tokens_per_second": "decode_tps",
    "prompt_tokens_per_second": "prompt_tps",
    "total_time": "total_s",
    "model_load_duration": "model_load_s",
}

MTP_USAGE_PREFIX = "mtp_"
# Usage keys the server may carry for multi-token prediction, renamed to the
# run record's own names. Any other mtp_-prefixed key is carried through with
# its prefix stripped, so a new counter needs no change here.
MTP_USAGE_ALIASES = {
    "accepted_tokens": "accepted",
    "drafted_tokens": "drafted",
}

SHADOW_USAGE_PREFIX = "shadow_"


def _first(mapping: dict, keys: Iterable[str]) -> Any:
    for key in keys:
        value = mapping.get(key)
        if value is not None:
            return value
    return None


def _dig_usage(obj: Any) -> dict | None:
    """Find a usage object in a chunk.

    On the OpenAI path usage arrives on the final chunk, and only when the
    request set `stream_options.include_usage`. On the Anthropic path it is
    split: `message_start` carries the input counts inside `message`, and
    `message_delta` carries the output counts at the top level. Both shapes are
    merged across the stream by the caller.
    """
    if not isinstance(obj, dict):
        return None
    usage = obj.get("usage")
    if isinstance(usage, dict):
        return usage
    message = obj.get("message")
    if isinstance(message, dict) and isinstance(message.get("usage"), dict):
        return message["usage"]
    return None


def _merge_usage(into: dict, new: dict | None) -> dict:
    if new:
        for key, value in new.items():
            if value is not None:
                into[key] = value
    return into


def mtp_fields_from_usage(raw_usage: dict | None) -> dict:
    """The mtp_-prefixed usage keys, with the prefix stripped.

    Returns an empty dict when the server reports none, which is how a build
    without the instrumentation is distinguished from one reporting zeros.
    """
    if not raw_usage:
        return {}
    fields = {}
    for key, value in raw_usage.items():
        if not key.startswith(MTP_USAGE_PREFIX):
            continue
        name = key[len(MTP_USAGE_PREFIX):]
        fields[MTP_USAGE_ALIASES.get(name, name)] = value
    return fields


def shadow_fields_from_usage(raw_usage: dict | None) -> dict:
    """The shadow_-prefixed usage keys, with the prefix stripped.

    Returns an empty dict when the server reports none, which is how a build
    without the instrumentation is distinguished from one reporting zeros.
    """
    if not raw_usage:
        return {}
    fields = {}
    for key, value in raw_usage.items():
        if not key.startswith(SHADOW_USAGE_PREFIX):
            continue
        name = key[len(SHADOW_USAGE_PREFIX):]
        fields[name] = value
    return fields


def server_timing_from_usage(raw_usage: dict | None) -> dict:
    """The server's own timings, renamed. Absent fields stay null."""
    usage = raw_usage or {}
    return {
        name: usage.get(key) for key, name in SERVER_TIMING_KEYS.items()
    }


def _openai_delta(chunk: dict) -> str | None:
    for choice in chunk.get("choices") or ():
        delta = choice.get("delta") or {}
        text = delta.get("content")
        if text:
            return text
        message = choice.get("message") or {}
        if message.get("content"):
            return message["content"]
    return None


def _anthropic_delta(chunk: dict) -> str | None:
    if chunk.get("type") == "content_block_delta":
        delta = chunk.get("delta") or {}
        return delta.get("text") or delta.get("partial_json") or None
    if chunk.get("type") == "content_block_start":
        block = chunk.get("content_block") or {}
        return block.get("text") or None
    return None


def _messages_to_anthropic(messages: list[dict]) -> tuple[list[dict], str | None]:
    system = None
    converted = []
    for message in messages:
        if message.get("role") == "system":
            system = message.get("content")
            continue
        converted.append(message)
    return converted, system


def build_request(messages: list[dict], max_tokens: int, model: str,
                  endpoint: str, stream: bool, extra_body: dict | None):
    """The path and JSON body for one request. Kept separate so tests can read it."""
    extra_body = dict(extra_body or {})
    if endpoint == "anthropic":
        converted, system = _messages_to_anthropic(messages)
        body = {"model": model, "messages": converted, "max_tokens": max_tokens,
                "stream": stream}
        if system is not None:
            body["system"] = system
        body.update(extra_body)
        return "/v1/messages", body
    body = {"model": model, "messages": messages, "max_tokens": max_tokens,
            "stream": stream}
    body.update(extra_body)
    if stream:
        # A streaming response carries no usage at all unless the request asks
        # for it, and a run with no usage has no token counts and no draft
        # counters. This is set after the caller's own body so it cannot be
        # switched off by accident; other stream_options keys are kept.
        options = dict(body.get("stream_options") or {})
        options["include_usage"] = True
        body["stream_options"] = options
    return "/v1/chat/completions", body


def _iter_sse(response: httpx.Response) -> Iterable[dict]:
    for raw in response.iter_lines():
        line = raw.strip()
        if not line or not line.startswith("data:"):
            continue
        payload = line[len("data:"):].strip()
        if payload == "[DONE]":
            return
        try:
            yield json.loads(payload)
        except json.JSONDecodeError:
            continue


def run_chat(messages: list[dict], max_tokens: int, endpoint: str = "openai",
             stream: bool = True, extra_body: dict | None = None,
             cfg: settings_mod.Settings | None = None, model: str | None = None,
             timeout_s: float = 600.0, capture_text: bool = False) -> dict:
    """Issue one request and return the timing and usage fields of a run record.

    With `capture_text`, the completion is also returned under `output_text`.
    That is the model's answer to a synthetic prompt; the prompt itself is never
    returned and never written anywhere.
    """
    cfg = cfg or settings_mod.load()
    model = model or cfg.model
    if not model:
        raise ValueError("no model id: set MODEL or pass model=")
    path, body = build_request(messages, max_tokens, model, endpoint, stream, extra_body)
    url = f"{cfg.url}{path}"
    headers = cfg.headers()

    raw_usage: dict = {}
    first_delta_at: float | None = None
    last_delta_at: float | None = None
    text_chars = 0
    delta_count = 0
    pieces: list[str] = []

    started = time.monotonic()
    with httpx.Client(timeout=timeout_s) as client:
        if not stream:
            response = client.post(url, json=body, headers=headers)
            response.raise_for_status()
            payload = response.json()
            ended = time.monotonic()
            _merge_usage(raw_usage, _dig_usage(payload))
            e2e = ended - started
            text = _non_stream_text(payload, endpoint) if capture_text else None
            return _assemble(raw_usage, endpoint, ttft=None, decode=None, e2e=e2e,
                             text_chars=len(text) if text else None,
                             delta_count=None, streamed=False, output_text=text)

        with client.stream("POST", url, json=body, headers=headers) as response:
            response.raise_for_status()
            for chunk in _iter_sse(response):
                _merge_usage(raw_usage, _dig_usage(chunk))
                delta = (_anthropic_delta(chunk) if endpoint == "anthropic"
                         else _openai_delta(chunk))
                if delta:
                    now = time.monotonic()
                    if first_delta_at is None:
                        first_delta_at = now
                    last_delta_at = now
                    text_chars += len(delta)
                    delta_count += 1
                    if capture_text:
                        pieces.append(delta)
        ended = time.monotonic()

    ttft = first_delta_at - started if first_delta_at is not None else None
    decode = (last_delta_at - first_delta_at
              if first_delta_at is not None and last_delta_at is not None else None)
    return _assemble(raw_usage, endpoint, ttft=ttft, decode=decode, e2e=ended - started,
                     text_chars=text_chars, delta_count=delta_count, streamed=True,
                     output_text="".join(pieces) if capture_text else None)


def _non_stream_text(payload: dict, endpoint: str) -> str | None:
    if endpoint == "anthropic":
        blocks = payload.get("content") or []
        return "".join(b.get("text", "") for b in blocks if isinstance(b, dict)) or None
    for choice in payload.get("choices") or ():
        message = choice.get("message") or {}
        if message.get("content"):
            return message["content"]
    return None


def _assemble(raw_usage: dict, endpoint: str, ttft, decode, e2e,
              text_chars, delta_count, streamed: bool,
              output_text: str | None = None) -> dict:
    prompt_tokens = _first(raw_usage, USAGE_PROMPT_KEYS)
    completion_tokens = _first(raw_usage, USAGE_COMPLETION_KEYS)
    cached_tokens = _first(raw_usage, USAGE_CACHED_KEYS)
    if cached_tokens is None:
        details = raw_usage.get("prompt_tokens_details")
        if isinstance(details, dict):
            cached_tokens = _first(details, USAGE_CACHED_KEYS)

    server_timing = server_timing_from_usage(raw_usage)

    uncached = None
    if prompt_tokens is not None and cached_tokens is not None:
        uncached = max(int(prompt_tokens) - int(cached_tokens), 0)
    elif prompt_tokens is not None and cached_tokens is None:
        uncached = int(prompt_tokens)

    output_tokens = completion_tokens
    if output_tokens is None and delta_count:
        output_tokens = None  # a delta is not a token; leave it unmeasured

    decode_tps = None
    if output_tokens is not None and decode and output_tokens > 1 and decode > 0:
        decode_tps = (int(output_tokens) - 1) / decode

    return {
        "endpoint": endpoint,
        "prompt_tokens": int(prompt_tokens) if prompt_tokens is not None else None,
        "cached_tokens": int(cached_tokens) if cached_tokens is not None else None,
        "uncached_suffix_tokens": uncached,
        "output_tokens": int(output_tokens) if output_tokens is not None else None,
        "ttft_s": ttft,
        "prefill_s": server_timing["prefill_s"],
        "decode_s": decode,
        "e2e_s": e2e,
        "decode_tps": decode_tps,
        "raw_usage": dict(raw_usage) or None,
        "server_timing": server_timing,
        "includes_model_load": bool(server_timing["model_load_s"]),
        "mtp_from_usage": mtp_fields_from_usage(raw_usage),
        "shadow_from_usage": shadow_fields_from_usage(raw_usage),
        "output_text": output_text,
        "streamed": streamed,
        "output_chars": text_chars,
        "delta_count": delta_count,
    }
