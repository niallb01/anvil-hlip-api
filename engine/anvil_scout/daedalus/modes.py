"""Execution modes — explicit named modes for state-aware operations.

Per JB-V2-14 (adaptive surprises QA): adaptive behavior must be explicit,
never inferred. Two modes:

    SNAPSHOT  — read state, run pipeline, NEVER write state.
                Same input → same output. This is the partner-facing
                default: it preserves the v0.1.0 stateless-determinism
                contract exactly.

    LEARNING  — read state, run pipeline, write evolved state.
                Same input may produce different output AFTER a learning
                cycle elsewhere in the system. Opt-in via explicit setting.

The default mode is SNAPSHOT. Switching to LEARNING requires an explicit
flag — there is no auto-detection, no "if labels are available, switch
modes" magic. This makes adaptive behavior auditable.

Feynman: think of SNAPSHOT as taking a photo of the system — you can look
at it, but the photo doesn't change. LEARNING is letting the system grow
between photos. The partner picks which mode they want; the default is
the photo.

This module is internal infrastructure. Nothing in the partner-facing path
(cli.py, contracts.py, SCHEMA.json) imports from here at TB-12.
"""

from __future__ import annotations

from enum import Enum


class ExecutionMode(str, Enum):
    """The two execution modes.

    `str` mixin so values are JSON-serializable as their string form
    ("snapshot" / "learning") without custom encoder logic.
    """
    SNAPSHOT = "snapshot"
    LEARNING = "learning"

    @classmethod
    def default(cls) -> "ExecutionMode":
        """The default mode. SNAPSHOT preserves the v0.1.0 partner contract."""
        return cls.SNAPSHOT

    def allows_state_writes(self) -> bool:
        """SNAPSHOT mode never writes state; LEARNING mode may write state."""
        return self is ExecutionMode.LEARNING

    def is_partner_safe(self) -> bool:
        """Returns True if this mode is safe to expose to a partner that
        expects v0.1.0 deterministic behavior.

        SNAPSHOT mode is partner-safe — same input → same output.
        LEARNING mode is NOT partner-safe without explicit partner buy-in.
        """
        return self is ExecutionMode.SNAPSHOT


def parse_mode(s: str) -> ExecutionMode:
    """Parse a mode string. Case-insensitive. Defaults to SNAPSHOT on unknown.

    Raises ValueError only on explicitly invalid input, not on missing/None.
    The strip-don't-raise discipline: fall back to safe default on ambiguity.
    """
    if s is None or s == "":
        return ExecutionMode.default()
    s_low = s.strip().lower()
    if s_low == "snapshot":
        return ExecutionMode.SNAPSHOT
    if s_low == "learning":
        return ExecutionMode.LEARNING
    raise ValueError(
        f"unknown execution mode {s!r}; valid: 'snapshot', 'learning'"
    )
