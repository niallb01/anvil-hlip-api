"""Anvil-Pantheon-Floor — sales_email_v0_1 template (Packet 9).

The first concrete cognitive template. Narrow MVP for B2B sales-email
generation. Three slots, each anchored to a specific signal_kind from
the SourceBook. Critical slot (quantitative_proof) refuses the whole
template if not satisfied -- you cannot send a "data-backed" sales
email without data.

SLOTS:

  - quantitative_proof  (CRITICAL)
      Requires: SignalKind.QUANTITY, min VERIFIED
      Rationale: a B2B email without at least one verified number is
      vibes. Refuse rather than emit a vague pitch.

  - testimony_hook      (not critical)
      Requires: SignalKind.TESTIMONY, min VERIFIED
      Rationale: optional social-proof line; refusal_text omits the
      hook gracefully rather than refusing the email.

  - causal_connector    (not critical)
      Requires: SignalKind.CAUSAL, min WEAK
      Rationale: a soft connecting clause; WEAK-evidence acceptance
      means a hedge phrasing is used when only weak evidence is present.

BODY:
  Three-paragraph email with three placeholders. When all slots fill
  GROUNDED, you get a tight evidence-anchored pitch. When testimony or
  causal slots refuse, the refusal_text fills inline (graceful
  degradation). When quantitative_proof refuses, the whole template
  refuses (the Oracle moves to a different template or refuses
  entirely).
"""

from __future__ import annotations

from ..template_library import SlotSpec, Template
from ...types import EvidenceKind, SignalKind


# ─── Slot specs ───────────────────────────────────────────────────────────

QUANTITATIVE_PROOF_SLOT = SlotSpec(
    slot_name="quantitative_proof",
    acceptable_signal_kinds=(SignalKind.ENRICHMENT, SignalKind.QUANTITY),
    min_evidence_kind=EvidenceKind.VERIFIED,
    critical=True,
    hedge_template="around {card_text}",
    refusal_text="",  # not used (critical -> template refuses)
)

TESTIMONY_HOOK_SLOT = SlotSpec(
    slot_name="testimony_hook",
    acceptable_signal_kinds=(SignalKind.TESTIMONY,),
    min_evidence_kind=EvidenceKind.VERIFIED,
    critical=False,
    hedge_template="and customers note {card_text}",
    refusal_text="",  # graceful: just omit the hook
)

CAUSAL_CONNECTOR_SLOT = SlotSpec(
    slot_name="causal_connector",
    acceptable_signal_kinds=(SignalKind.CAUSAL,),
    min_evidence_kind=EvidenceKind.WEAK,
    critical=False,
    hedge_template="seemingly {card_text}",
    refusal_text="",
)


# ─── Template body ────────────────────────────────────────────────────────

# Three sentences with placeholders. After substitution:
#  - GROUNDED quantitative_proof: "I noticed your team is doing 99.99% uptime"
#  - GROUNDED testimony_hook:     "Trusted by Snowflake and Datadog stood out"
#  - HEDGE causal_connector:      "seemingly enables faster decisions"
#  - REFUSED non-critical:        substitutes "" -> empty inline gap

_BODY = (
    "Hi -- I came across your team and noticed {quantitative_proof}. "
    "{testimony_hook} {causal_connector} "
    "Would you have 15 minutes this week to discuss how we approach "
    "similar problems?"
)


# ─── The exported Template ────────────────────────────────────────────────

SALES_EMAIL_V0_1 = Template(
    template_id="sales_email",
    version="v0.1",
    slot_specs=(
        QUANTITATIVE_PROOF_SLOT,
        TESTIMONY_HOOK_SLOT,
        CAUSAL_CONNECTOR_SLOT,
    ),
    body=_BODY,
)
