"""Runtime schema validation — formal output-contract enforcement.

SCHEMA.json has been the OUTPUT CONTRACT since TB-00. Until now nothing
runtime-validated emissions against it. TB-06 closes that loop.

Validation philosophy mirrors Law-0:
    - Validate, never crash.
    - On failure: report violations in the rationale string, emit anyway.
    - Single point of truth: SCHEMA.json on disk at the repo root.

Complements Law-0 (TB-05):
    - Law-0 audits CONTENT  (no claim without backing).
    - Schema audits SHAPE   (no missing fields, no type errors).
    - Together they form the complete output contract.

Graceful degradation: if `jsonschema` is somehow not installed, the
validator becomes a no-op and reports the absence; the pipeline does
not crash.
"""

from __future__ import annotations

import json
import os
from typing import Dict, List, Tuple

try:
    import jsonschema   # type: ignore
    _HAS_JSONSCHEMA = True
except ImportError:
    jsonschema = None   # type: ignore
    _HAS_JSONSCHEMA = False


# Module-level cache: read SCHEMA.json once per process.
_SCHEMA: dict | None = None


def _schema_path() -> str:
    """Resolve SCHEMA.json from this file's location: <repo>/SCHEMA.json.

    Layout:
        <repo>/
            SCHEMA.json
            anvil_scout/
                core/
                    schema_validator.py   <-- this file
    """
    here = os.path.dirname(os.path.abspath(__file__))         # .../anvil_scout/core
    parent = os.path.dirname(here)                              # .../anvil_scout
    repo = os.path.dirname(parent)                              # .../<repo>
    return os.path.join(repo, "SCHEMA.json")


def load_schema() -> dict:
    """Lazy-load + cache SCHEMA.json. Used by validate_output()."""
    global _SCHEMA
    if _SCHEMA is None:
        with open(_schema_path(), "r", encoding="utf-8") as f:
            _SCHEMA = json.load(f)
    return _SCHEMA


# ─── main API ───────────────────────────────────────────────────────────────

def validate_output(payload: dict) -> Tuple[bool, List[str]]:
    """Validate `payload` against SCHEMA.json.

    Returns
    -------
    (is_valid, errors)
        is_valid : True iff payload conforms.
        errors   : list of human-readable error strings (empty if valid).
    """
    if not _HAS_JSONSCHEMA:
        # Graceful degradation — validator absent.
        return True, ["jsonschema not installed; runtime validation skipped"]

    schema = load_schema()
    validator = jsonschema.Draft7Validator(schema)
    errors: List[str] = []
    for err in validator.iter_errors(payload):
        path = ".".join(str(p) for p in err.absolute_path) or "<root>"
        errors.append(f"{path}: {err.message}")

    return (len(errors) == 0), errors


# ─── defensive repair (optional helper, not used by default CLI path) ──────

_REQUIRED_DEFAULTS_TOP = {
    "lead_score": 0,
    "industry_fit": 0,
    "company_size_fit": 0,
    "decision_maker_seniority": 0,
    "budget_likelihood_score": 0,
    "growth_signals": 0,
    "pain_points": [],
    "budget_likelihood": "low",
    "decision_maker": False,
    "rationale": "",
    "signal_evidence": {},
}

_REQUIRED_DEFAULTS_SE = {
    "verified": [],
    "weak": [],
    "missing": [],
    "signal_density": 0.0,
    "thin_scrape": False,
}


def validate_or_repair(payload: dict) -> Tuple[dict, List[str]]:
    """Validate, and conservatively repair, missing required fields.

    Repairs only ADD missing required fields with safe defaults; does NOT
    invent content. Used by callers that need a guaranteed-valid output
    even when upstream is degraded.

    Returns (possibly_repaired_payload, errors_observed).
    """
    is_valid, errors = validate_output(payload)
    if is_valid:
        return payload, []

    notes: List[str] = list(errors)

    # Top-level missing fields
    for key, default in _REQUIRED_DEFAULTS_TOP.items():
        if key not in payload:
            payload[key] = (
                default.copy() if isinstance(default, (list, dict)) else default
            )
            notes.append(f"repaired missing top-level field: {key}")

    # signal_evidence sub-fields
    se = payload.get("signal_evidence")
    if isinstance(se, dict):
        for key, default in _REQUIRED_DEFAULTS_SE.items():
            if key not in se:
                se[key] = (
                    default.copy() if isinstance(default, (list, dict)) else default
                )
                notes.append(f"repaired missing signal_evidence.{key}")
    else:
        payload["signal_evidence"] = {**_REQUIRED_DEFAULTS_SE}
        notes.append("repaired non-object signal_evidence")

    return payload, notes


__all__ = ["validate_output", "validate_or_repair", "load_schema"]
