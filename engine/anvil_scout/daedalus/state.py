"""State persistence — Protocol + default providers.

The StateProvider Protocol defines a minimal interface for persisting Daedalus
state across calls. Two implementations ship at TB-12:

    NullStateProvider     — default no-op. Returns empty state on read,
                            silently drops writes. Used by SNAPSHOT mode
                            and by tests that don't care about state.

    SQLiteStateProvider   — default real backend. SQLite file alongside
                            the package; WAL journal mode; transactional
                            writes; schema-versioned with migration framework.

Per JB-V2-13 (PII): raw scraped content is NEVER persisted. The state schema
contains only adapter weights, calibration counters, drift metrics, and
opaque identifiers. The `assert_no_pii_in_state` helper enforces this with
a runtime check that test_daedalus_state.py exercises.

Per JB-V2-11 (concurrency): SQLite uses WAL + BEGIN IMMEDIATE + 5s timeout.
Concurrent writers serialize; reads don't block writers in WAL mode.

Per JB-V2-12 (migration drift): state has a `schema_version` field; load
applies registered migrations in version order to bring v_n state up to
v_current. Migrations are append-only — they never remove fields without
a deprecation cycle.

Feynman: state is a small JSON dict (one row in a tiny SQLite table). Each
call to a state-aware part of v2 reads the dict, possibly modifies it
(in LEARNING mode), and writes it back. The Protocol means partner can
swap SQLite for their own database (Postgres, Redis, whatever) by writing
~30 lines of glue.

This module is internal-only. Nothing in cli.py imports it at TB-12.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import (
    Any,
    Callable,
    Dict,
    Iterator,
    List,
    Optional,
    Protocol,
    runtime_checkable,
)


# ============================================================
# State schema — versioned
# ============================================================

# Bump CURRENT_SCHEMA_VERSION when adding/removing top-level fields.
# Register a migration function in MIGRATIONS for each version bump.
CURRENT_SCHEMA_VERSION = 1


def _now_utc_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def initial_state() -> Dict[str, Any]:
    """Construct a fresh v1 state dict.

    All adaptive containers start empty. Future TBs (13+) populate them.

    PII discipline: this schema contains NO fields for raw text, names,
    emails, or other PII. If a future TB needs to remember anything about
    a specific lead, it must use an opaque identifier (hash of canonical
    input) and store the analysis output ONLY, never the raw input.
    """
    return {
        "schema_version": CURRENT_SCHEMA_VERSION,
        "created_at_utc": _now_utc_iso(),
        "last_updated_at_utc": _now_utc_iso(),
        "detector_state": {},       # TB-13/14: detector adapter weights + observation counters
        "channel_state": {},        # TB-15: channel-council shared evidence + weights
        "calibration_state": {},    # TB-17: per-channel confidence multipliers
        "drift_metrics": {},        # TB-18: self-audit drift signals
        "receipt_history": [],      # cross-TB receipt diffing log
    }


# ============================================================
# Migrations — append-only registry
# ============================================================

# Each migration takes a state dict at version N and returns it at version N+1.
# Migrations must be idempotent and PII-safe.

MIGRATIONS: Dict[int, Callable[[Dict[str, Any]], Dict[str, Any]]] = {}


def register_migration(from_version: int):
    """Decorator to register a state migration function.

    Usage (when bumping schema):
        @register_migration(from_version=1)
        def migrate_1_to_2(state: dict) -> dict:
            state["new_field"] = {}
            state["schema_version"] = 2
            return state
    """
    def decorator(fn: Callable[[Dict[str, Any]], Dict[str, Any]]):
        MIGRATIONS[from_version] = fn
        return fn
    return decorator


def migrate_state(state: Dict[str, Any]) -> Dict[str, Any]:
    """Apply migrations until state is at CURRENT_SCHEMA_VERSION.

    Strip-don't-raise: if a migration is missing, fall back to initial_state()
    with a note in `receipt_history`. Never crash on stale state.
    """
    if not isinstance(state, dict):
        return initial_state()
    version = state.get("schema_version", 0)
    while version < CURRENT_SCHEMA_VERSION:
        fn = MIGRATIONS.get(version)
        if fn is None:
            # No migration registered — restart from a clean v1 state,
            # log the abandonment in receipt_history of the new state.
            fresh = initial_state()
            fresh["receipt_history"].append({
                "event": "state_abandoned",
                "reason": f"no migration from v{version} to v{version + 1}",
                "at": _now_utc_iso(),
            })
            return fresh
        state = fn(state)
        new_version = state.get("schema_version", version + 1)
        if new_version <= version:
            # Migration failed to bump version — abandon to avoid infinite loop
            fresh = initial_state()
            fresh["receipt_history"].append({
                "event": "state_abandoned",
                "reason": f"migration from v{version} failed to bump version",
                "at": _now_utc_iso(),
            })
            return fresh
        version = new_version
    return state


# ============================================================
# PII guard — JB-V2-13
# ============================================================

# Field name patterns that suggest PII; reject if present at any depth.
_PII_FIELD_PATTERNS = (
    "website_content",      # raw scraped HTML/text
    "scraped_content",
    "raw_text",
    "raw_html",
    "email",                # contact email
    "phone",                # contact phone
    "first_name",
    "last_name",
    "full_name",
    "address",
    "ssn",
)


def assert_no_pii_in_state(state: Dict[str, Any]) -> None:
    """Raise ValueError if state contains any PII-suspect field name.

    This is a defensive runtime check. The test suite asserts that
    state written by the SQLite provider after sample-input runs never
    contains any of these field names.

    Note: an opaque hash like "lead_id_8a3f" is fine; a name like "John Doe"
    is not. The pattern check is on FIELD NAMES, not on values (we can't
    reliably detect PII in values without an NLP model).
    """
    def walk(obj: Any, path: str = "") -> None:
        if isinstance(obj, dict):
            for k, v in obj.items():
                k_low = str(k).lower()
                for pattern in _PII_FIELD_PATTERNS:
                    if pattern in k_low:
                        raise ValueError(
                            f"PII-suspect field {k!r} found in state at "
                            f"path {path or '<root>'}. JB-V2-13 violated."
                        )
                walk(v, f"{path}.{k}" if path else str(k))
        elif isinstance(obj, list):
            for i, item in enumerate(obj):
                walk(item, f"{path}[{i}]")
    walk(state)


# ============================================================
# StateProvider Protocol
# ============================================================

@runtime_checkable
class StateProvider(Protocol):
    """Minimal interface for state persistence.

    Implementations must:
      - return a state dict on `load()` (initial_state() if no state exists)
      - persist a state dict on `save()` atomically (transactional)
      - support `transaction()` as a context manager for compound writes
      - call `assert_no_pii_in_state` on save to enforce JB-V2-13

    Implementations should NOT:
      - retry indefinitely on failure (5s timeout is the budget)
      - silently corrupt state on partial writes
      - persist any field matching _PII_FIELD_PATTERNS
    """

    def load(self) -> Dict[str, Any]:
        """Return current state, or initial_state() if no state stored.
        Applies any pending migrations during load.
        """
        ...

    def save(self, state: Dict[str, Any]) -> None:
        """Persist state atomically. Bumps last_updated_at_utc.
        Raises ValueError if PII-suspect fields are present.
        """
        ...

    def transaction(self) -> Any:
        """Context manager for atomic compound writes. Exit-on-exception
        rolls back any pending changes."""
        ...


# ============================================================
# NullStateProvider — default no-op
# ============================================================

class NullStateProvider:
    """No-op StateProvider. load() always returns fresh initial_state();
    save() silently drops. Used by SNAPSHOT mode and by tests.

    This is the partner-facing default at TB-12: v0.1.0 contract is
    preserved because no state ever persists across calls.
    """

    def __init__(self) -> None:
        # In-memory state for the current call only — discarded on save.
        self._current = initial_state()

    def load(self) -> Dict[str, Any]:
        return initial_state()

    def save(self, state: Dict[str, Any]) -> None:
        # JB-V2-13 enforced even in null mode — catches mistakes in tests
        assert_no_pii_in_state(state)
        # ...then silently drop. No persistence.

    @contextmanager
    def transaction(self) -> Iterator[None]:
        # No-op context. Exists for Protocol compatibility.
        yield


# ============================================================
# SQLiteStateProvider — default real backend
# ============================================================

class SQLiteStateProvider:
    """SQLite-backed state persistence.

    Single-row table: anvil_state(id INTEGER PRIMARY KEY=1, json TEXT).
    Always upsert id=1. State is the entire dict serialized as JSON.

    WAL journal mode + 5s busy timeout + BEGIN IMMEDIATE for write txns.
    Concurrent writers serialize cleanly without corruption (JB-V2-11).

    Migration runs on every load (JB-V2-12) — schema_version drift is
    self-healing up to the registered migration set.

    PII discipline enforced on every save (JB-V2-13).

    Per JB-12-1: file lives at `state.db` relative to the package dir by
    default; pass `db_path` to override. Partner deployments that can't
    write to the package dir should pass an absolute path or swap to a
    different StateProvider implementation entirely.
    """

    _DDL = """
    CREATE TABLE IF NOT EXISTS anvil_state (
        id INTEGER PRIMARY KEY,
        json TEXT NOT NULL,
        updated_at TEXT NOT NULL
    );
    """

    def __init__(self, db_path: Optional[str] = None) -> None:
        if db_path is None:
            # Default: state.db alongside this module's package
            pkg_dir = Path(__file__).resolve().parent.parent  # anvil_scout/
            db_path = str(pkg_dir.parent / "state.db")        # alongside package
        self.db_path = db_path
        # Reentrant lock so transaction() can nest load() / save() from the
        # same thread. JB-12-7: avoids deadlock when compound writes happen
        # under a single transaction context.
        self._lock = threading.RLock()
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        # 5-second busy timeout; WAL for concurrent readers
        conn = sqlite3.connect(self.db_path, timeout=5.0, isolation_level=None)
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA synchronous=NORMAL;")
        return conn

    def _init_schema(self) -> None:
        with self._lock:
            conn = self._connect()
            try:
                conn.executescript(self._DDL)
            finally:
                conn.close()

    def load(self) -> Dict[str, Any]:
        with self._lock:
            conn = self._connect()
            try:
                cur = conn.execute(
                    "SELECT json FROM anvil_state WHERE id = 1"
                )
                row = cur.fetchone()
                if row is None:
                    return initial_state()
                try:
                    state = json.loads(row[0])
                except (json.JSONDecodeError, TypeError):
                    # Corrupted row — fall back to fresh state
                    return initial_state()
                # Migrate if needed
                return migrate_state(state)
            finally:
                conn.close()

    def save(self, state: Dict[str, Any]) -> None:
        # JB-V2-13: refuse to save PII
        assert_no_pii_in_state(state)
        # Bump timestamp
        state = dict(state)
        state["last_updated_at_utc"] = _now_utc_iso()
        payload = json.dumps(state, ensure_ascii=False)
        ts = state["last_updated_at_utc"]

        with self._lock:
            conn = self._connect()
            try:
                # BEGIN IMMEDIATE — acquire write lock now, avoid deadlock
                conn.execute("BEGIN IMMEDIATE")
                try:
                    conn.execute(
                        "INSERT OR REPLACE INTO anvil_state (id, json, updated_at) "
                        "VALUES (1, ?, ?)",
                        (payload, ts),
                    )
                    conn.execute("COMMIT")
                except Exception:
                    conn.execute("ROLLBACK")
                    raise
            finally:
                conn.close()

    @contextmanager
    def transaction(self) -> Iterator["SQLiteStateProvider"]:
        """Compound-write context. Holds the lock across multiple
        load/save operations.

        Usage:
            with provider.transaction():
                s = provider.load()
                s["foo"] = "bar"
                provider.save(s)
        """
        with self._lock:
            yield self


# ============================================================
# Public surface for the daedalus package
# ============================================================

__all__ = [
    "StateProvider",
    "NullStateProvider",
    "SQLiteStateProvider",
    "ExecutionMode",  # re-exported below for convenience
    "initial_state",
    "migrate_state",
    "register_migration",
    "MIGRATIONS",
    "CURRENT_SCHEMA_VERSION",
    "assert_no_pii_in_state",
]


# Convenience re-export so callers can `from anvil_scout.daedalus.state import ExecutionMode`
from anvil_scout.daedalus.modes import ExecutionMode  # noqa: E402
