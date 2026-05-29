"""Anvil-Pantheon-Floor — Veritas verifier (Packet 10).

Veritas is the post-emission verifier. After the Oracle composes a
candidate emission (via fill_template), Veritas runs structural checks
that prove the emission is consistent with the SourceBook it claims to
trace from. It is the last line of defense before a certificate ships.

NON_CLAIMS (the verifier discipline):
  - Does NOT compute substrate outputs
  - Does NOT decide emit/refuse (Oracle's job)
  - Does NOT modify the rendered text or slot fills
  - Only VERIFIES: returns a verdict, leaves remediation to caller

Floor checks (4 named violation codes):
  - "placeholder_leak"
      Rendered text contains an unresolved {slot_name} placeholder.
  - "grounded_card_id_invalid"
      A GROUNDED SlotFill's source_card_id does not exist in the
      SourceBook. (REFUSED fills with source_card_id=None are skipped.)
  - "span_text_hash_mismatch"
      A GROUNDED SlotFill's span_text_hash does not equal the SHA-256
      of the card's actual span_text. (Catches tampered hashes.)
  - "emission_empty"
      Rendered text is empty when at least one slot was non-REFUSED.

The output is a single string: "clean" for pass, or a JSON-encoded dict
of {violation_code: [details]}. This matches the
EmissionCertificate.veritas_pass field schema from Packet 1.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Tuple

from .sourcebook import SourceBook
from .types import CertificationStatus, SlotFill


# ─── Constants ────────────────────────────────────────────────────────────

# Pattern for detecting unresolved template placeholders. Slot names
# follow the SlotSpec convention (snake_case identifier).
PLACEHOLDER_REGEX = re.compile(r"\{[a-z_][a-z0-9_]*\}")

VERITAS_PASS_CLEAN = "clean"

# All known violation codes (closed set; used by tests + audit)
VIOLATION_CODES: Tuple[str, ...] = (
    "placeholder_leak",
    "grounded_card_id_invalid",
    "span_text_hash_mismatch",
    "emission_empty",
    "provenance_id_invalid",
)


# ─── Verdict ──────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class VeritasVerdict:
    """Structured verdict. Use .to_certificate_field() to render the
    string that goes into EmissionCertificate.veritas_pass."""
    clean: bool
    violations: Dict[str, Tuple[str, ...]] = field(default_factory=dict)

    def to_certificate_field(self) -> str:
        """Render as the string that goes into the certificate's
        veritas_pass field: 'clean' or JSON-encoded violations dict."""
        if self.clean:
            return VERITAS_PASS_CLEAN
        # Canonical JSON: sorted keys, no whitespace -- deterministic
        # across runs so receipt hashing is stable.
        payload = {k: list(v) for k, v in sorted(self.violations.items())}
        return json.dumps(payload, sort_keys=True, separators=(",", ":"))


# ─── Verification entry point ─────────────────────────────────────────────

def verify(
    rendered_text: str,
    slot_fills: Tuple[SlotFill, ...],
    sourcebook: SourceBook,
    provenance_store: Any = None,
) -> VeritasVerdict:
    """Run all post-emission checks. Returns VeritasVerdict.

    Discipline: Veritas does not raise on violations -- it collects
    them. The Oracle decides whether to ship a certificate with
    violations recorded (refused) or to retry composition.

    P16: if a GROUNDED fill's card carries a provenance_id, Veritas
    validates its format (always) and, when `provenance_store` is
    provided, that it resolves there. Cards with no provenance_id are
    untouched (ACF backward-compat)."""
    violations: Dict[str, List[str]] = {}

    # ─── Check 1: placeholder leak ─────────────────────────────────────
    leaks = PLACEHOLDER_REGEX.findall(rendered_text)
    if leaks:
        # Dedupe while preserving order
        seen: List[str] = []
        for leak in leaks:
            if leak not in seen:
                seen.append(leak)
        violations["placeholder_leak"] = seen

    # ─── Check 2: grounded card_id invalid ─────────────────────────────
    dangling: List[str] = []
    for fill in slot_fills:
        if fill.certification != CertificationStatus.GROUNDED:
            continue  # REFUSED and HEDGE have different invariants
        if fill.source_card_id is None or fill.source_card_id not in sourcebook:
            dangling.append(f"{fill.slot_name}:{fill.source_card_id!r}")
    if dangling:
        violations["grounded_card_id_invalid"] = dangling

    # ─── Check 3: span_text_hash mismatch ──────────────────────────────
    mismatches: List[str] = []
    for fill in slot_fills:
        if fill.certification != CertificationStatus.GROUNDED:
            continue
        if fill.source_card_id is None or fill.source_card_id not in sourcebook:
            continue  # already caught above
        card = sourcebook.get(fill.source_card_id)
        if card is None:
            continue
        expected = hashlib.sha256(card.span_text.encode("utf-8")).hexdigest()
        if fill.span_text_hash != expected:
            mismatches.append(
                f"{fill.slot_name}:expected={expected[:16]}…,got={(fill.span_text_hash or '')[:16]}…"
            )
    if mismatches:
        violations["span_text_hash_mismatch"] = mismatches

    # ─── Check 4: emission empty when at least one slot non-REFUSED ────
    any_non_refused = any(
        f.certification != CertificationStatus.REFUSED for f in slot_fills
    )
    if any_non_refused and not rendered_text.strip():
        violations["emission_empty"] = (
            f"rendered_text_empty_but_{len(slot_fills)}_slots_filled",
        )

    # ─── Check 5: provenance_id invalid (P16) ──────────────────────────
    # Runs only for GROUNDED fills whose card carries a provenance_id.
    # (a) format discipline always; (b) resolution only if a store given.
    from .provenance import validate_provenance_id
    prov_bad: List[str] = []
    for fill in slot_fills:
        if fill.certification != CertificationStatus.GROUNDED or fill.source_card_id is None:
            continue
        if fill.source_card_id not in sourcebook:
            continue  # already caught by Check 2
        card = sourcebook.get(fill.source_card_id)
        pid = getattr(card, "provenance_id", None) if card is not None else None
        if pid is None:
            continue
        try:
            validate_provenance_id(pid)
        except Exception:
            prov_bad.append(f"{fill.slot_name}:{pid!r}:bad_format")
            continue
        if provenance_store is not None and not provenance_store.is_present(pid):
            prov_bad.append(f"{fill.slot_name}:{pid!r}:unresolved")
    if prov_bad:
        violations["provenance_id_invalid"] = prov_bad

    if violations:
        return VeritasVerdict(
            clean=False,
            violations={k: tuple(v) for k, v in violations.items()},
        )
    return VeritasVerdict(clean=True, violations={})
