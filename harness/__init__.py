"""Research harness for local LLM inference experiments.

Nothing in this package writes prompt or completion text to disk. Only token
counts, timings and server-reported usage keys are recorded.
"""

__all__ = ["settings", "schema", "mtp_log", "server", "client", "run"]
