"""Anvil-Pantheon-Floor — pathway audit (Packet 2).

Adapts DetMath v4.8 certificate_replay.py to Anvil. Where DetMath
replays a math-proof certificate by re-running the contest solver and
comparing digests, Anvil's pathway audit verifies that an emission
certificate's chain is intact end-to-end:

  - verify_structural(cert) -> AuditVerdict
    Structural-only verification: hash fields are valid, slot-fill
    discipline is consistent, substrate_outputs key/kind agreement.
    Does NOT regenerate substrate outputs.

  - verify_pathway(cert, replayer=None, prior_cert=None) -> AuditVerdict
    Full pathway verification. Always runs structural checks. If
    prior_cert is supplied, verifies the chain link. If a Replayer is
    supplied, re-runs the Replayer and compares the regenerated
    output_hash to the certificate's. If Replayer is None, the verdict
    is "structural_clean" (not "fully_replayed") — full replay requires
    the Clockwork Oracle which lands in Packet 10.

  - detect_chain_tampering(certs) -> list of broken indices
    Given a list of certificates in chain order, find every index where
    graph_hash does not match the prior content_hash. Useful for
    forensic review of a possibly-tampered log.

The two-tier verdict ("structural_clean" vs "fully_replayed") is
deliberate: it prevents the API from conflating "the receipt is
internally consistent" with "the receipt was end-to-end replayed
against the live solver."  Both are useful claims; they are not the
same claim.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Protocol, Tuple

from .receipts import validate_certificate
from .types import EmissionCertificate


# ─── Replayer protocol ────────────────────────────────────────────────────

class Replayer(Protocol):
    """The thing that knows how to regenerate substrate outputs and an
    emission from a (scout_output_hash, source_card_set_hash) pair.
    Implementation lands in Packet 10 (the Clockwork Oracle). Until
    then, callers pass replayer=None to verify_pathway and get the
    structural-clean verdict."""

    def replay(
        self,
        *,
        scout_output_hash: str,
        source_card_set_hash: str,
        template_choice: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Regenerate from canonical inputs. Returns a dict containing
        at least:
          substrate_outputs:  Dict[str, SubstrateOutput]  - same shape as cert
          slot_fills:         List[SlotFill]              - same shape as cert
          output_hash:        str                         - to compare
        """
        ...


# ─── Verdict ──────────────────────────────────────────────────────────────

AUDIT_VERDICT_KINDS = {
    "structural_clean",   # passed structural checks; no replay was attempted
    "fully_replayed",     # passed structural + Replayer regenerated identical certificate
    "broken",             # at least one violation found
    "pending",            # no Replayer available, full replay requested
}


@dataclass(frozen=True)
class AuditVerdict:
    """Result of a pathway audit. error_codes is a tuple of explicit
    violation strings (same vocabulary as validate_certificate)."""
    verdict_kind: str                       # one of AUDIT_VERDICT_KINDS
    certificate_id: str
    structural_clean: bool
    chain_link_clean: Optional[bool]        # None iff prior_cert was not provided
    replay_clean: Optional[bool]            # None iff Replayer was not provided
    error_codes: Tuple[str, ...] = field(default_factory=tuple)
    error_detail: Optional[str] = None


# ─── Structural verification ──────────────────────────────────────────────

def verify_structural(cert: EmissionCertificate) -> AuditVerdict:
    """Structural-only verification. Use this when you want a fast
    check without involving the Replayer."""
    structural_ok, errors = validate_certificate(cert)
    return AuditVerdict(
        verdict_kind="structural_clean" if structural_ok else "broken",
        certificate_id=cert.certificate_id,
        structural_clean=structural_ok,
        chain_link_clean=None,
        replay_clean=None,
        error_codes=tuple(errors),
    )


# ─── Full pathway verification ────────────────────────────────────────────

def verify_pathway(
    cert: EmissionCertificate,
    *,
    replayer: Optional[Replayer] = None,
    prior_cert: Optional[EmissionCertificate] = None,
) -> AuditVerdict:
    """End-to-end pathway audit. Always runs structural checks. If
    prior_cert is supplied, verifies the chain link. If replayer is
    supplied, regenerates and compares."""
    errors: List[str] = []

    # Structural check
    structural_ok, struct_errors = validate_certificate(cert)
    errors.extend(struct_errors)

    # Chain link check (if prior_cert provided)
    chain_link_clean: Optional[bool] = None
    if prior_cert is not None:
        expected = prior_cert.content_hash()
        if cert.graph_hash != expected:
            errors.append("graph_hash_broken")
            chain_link_clean = False
        else:
            chain_link_clean = True

    # Replay check (if replayer provided)
    replay_clean: Optional[bool] = None
    if replayer is not None:
        try:
            replay_result = replayer.replay(
                scout_output_hash=cert.scout_output_hash,
                source_card_set_hash=cert.source_card_set_hash,
                template_choice=cert.template_choice,
            )
            rebuilt_output_hash = replay_result.get("output_hash")
            if rebuilt_output_hash != cert.output_hash:
                errors.append("replay_output_hash_mismatch")
                replay_clean = False
            else:
                replay_clean = True
        except Exception as exc:
            errors.append("replay_exception")
            replay_clean = False
            return AuditVerdict(
                verdict_kind="broken",
                certificate_id=cert.certificate_id,
                structural_clean=structural_ok,
                chain_link_clean=chain_link_clean,
                replay_clean=replay_clean,
                error_codes=tuple(errors),
                error_detail=str(exc),
            )

    # Determine overall verdict
    if errors:
        verdict_kind = "broken"
    elif replayer is None:
        # Successful structural + (maybe) chain check, but no replay.
        # If the caller wanted full replay, they passed replayer=None and
        # the verdict explicitly tells them replay is pending.
        verdict_kind = "structural_clean"
    else:
        verdict_kind = "fully_replayed"

    return AuditVerdict(
        verdict_kind=verdict_kind,
        certificate_id=cert.certificate_id,
        structural_clean=structural_ok,
        chain_link_clean=chain_link_clean,
        replay_clean=replay_clean,
        error_codes=tuple(errors),
    )


# ─── Chain tampering detection ────────────────────────────────────────────

def detect_chain_tampering(certs: List[EmissionCertificate]) -> List[int]:
    """Given certificates in chain order, return the indices where the
    graph_hash does NOT match the prior content_hash. First receipt
    (index 0) must have graph_hash=""; otherwise it's flagged."""
    broken: List[int] = []
    if not certs:
        return broken
    if certs[0].graph_hash != "":
        broken.append(0)
    for i in range(1, len(certs)):
        if certs[i].graph_hash != certs[i - 1].content_hash():
            broken.append(i)
    return broken
