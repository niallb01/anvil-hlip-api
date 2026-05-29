"""Anvil-Pantheon-Floor — sales_email_v0_1 template.

Challenger Selling email template. Four slots anchored to SourceCards.
Critical slot (quantitative_proof) refuses the whole template if not
satisfied — you cannot send a data-backed email without data.

SLOTS:

  - contact_name        (CRITICAL)
      Requires: SignalKind.TESTIMONY, subtype=name, METADATA card
      Rationale: personalisation requires a real name.

  - quantitative_proof  (CRITICAL)
      Requires: SignalKind.ENRICHMENT or QUANTITY, min VERIFIED
      Rationale: Challenger opening must reference a specific fact.

  - causal_connector    (not critical)
      Requires: SignalKind.CAUSAL, min WEAK
      Rationale: the tension point — what the fact implies.

  - testimony_hook      (not critical)
      Requires: SignalKind.TESTIMONY, min VERIFIED
      Rationale: optional social proof or first-person grounding.
"""

from __future__ import annotations

from ..template_library import SlotSpec, Template
from ...types import EvidenceKind, SignalKind


CONTACT_NAME_SLOT = SlotSpec(
    slot_name="contact_name",
    acceptable_signal_kinds=(SignalKind.TESTIMONY,),
    min_evidence_kind=EvidenceKind.VERIFIED,
    critical=False,
    hedge_template="{card_text}",
    refusal_text="there",
)

QUANTITATIVE_PROOF_SLOT = SlotSpec(
    slot_name="quantitative_proof",
    acceptable_signal_kinds=(SignalKind.QUANTITY, SignalKind.ENRICHMENT),
    min_evidence_kind=EvidenceKind.VERIFIED,
    critical=True,
    hedge_template="around {card_text}",
    refusal_text="",
)

CAUSAL_CONNECTOR_SLOT = SlotSpec(
    slot_name="causal_connector",
    acceptable_signal_kinds=(SignalKind.CAUSAL,),
    min_evidence_kind=EvidenceKind.WEAK,
    critical=False,
    hedge_template="seemingly {card_text}",
    refusal_text="",
)

TESTIMONY_HOOK_SLOT = SlotSpec(
    slot_name="testimony_hook",
    acceptable_signal_kinds=(SignalKind.TESTIMONY,),
    min_evidence_kind=EvidenceKind.VERIFIED,
    critical=False,
    hedge_template="and customers note {card_text}",
    refusal_text="",
)


_BODY = (
    "{contact_name},\n\n"
    "I noticed {quantitative_proof}. "
    "{causal_connector}\n\n"
    "{testimony_hook} "
    "Most sales leaders find that as this scales, their team spends "
    "more time on manual research than actual selling.\n\n"
    "How are you currently ensuring your reps focus on the right "
    "prospects at this stage?\n\n"
    "Best,"
)


SALES_EMAIL_V0_1 = Template(
    template_id="sales_email",
    version="v0.1",
    slot_specs=(
        CONTACT_NAME_SLOT,
        QUANTITATIVE_PROOF_SLOT,
        CAUSAL_CONNECTOR_SLOT,
        TESTIMONY_HOOK_SLOT,
    ),
    body=_BODY,
)
