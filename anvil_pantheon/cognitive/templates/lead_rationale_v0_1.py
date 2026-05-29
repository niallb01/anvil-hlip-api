"""Anvil-Pantheon-Floor — lead_rationale_v0_1 template.

Generates a 3-sentence sales rep briefing from Scout's verified signals.
Same slot discipline as sales_email_v0_1: grounded in SourceCards only,
no invention, certifiable.

SLOTS:

  - company_signal    (CRITICAL)
      Requires: SignalKind.CAUSAL, min VERIFIED
      Rationale: what the company does — must come from a real causal
      signal on their website, not inferred.

  - scale_signal      (CRITICAL)
      Requires: SignalKind.QUANTITY, min VERIFIED
      Rationale: the strongest quantitative fact — employee count,
      revenue, customer count. Refuses if no verified number exists.

  - testimony_signal  (not critical)
      Requires: SignalKind.TESTIMONY, min WEAK
      Rationale: optional social proof or first-person statement.
      Omitted gracefully if not available.

BODY:
  Three sentences for a sales rep about to make a call.
  Sentence 1: what the company does (causal signal).
  Sentence 2: strongest quantitative fact (scale signal).
  Sentence 3: optional testimony or omitted cleanly.

  Every word traces to a SourceCard or a template constant.
  No adjectives, no invented state, no hallucination possible.
"""

from __future__ import annotations

from ..template_library import SlotSpec, Template
from ...types import EvidenceKind, SignalKind


COMPANY_SIGNAL_SLOT = SlotSpec(
    slot_name="company_signal",
    acceptable_signal_kinds=(SignalKind.CAUSAL,),
    min_evidence_kind=EvidenceKind.VERIFIED,
    critical=True,
    hedge_template="seemingly {card_text}",
    refusal_text="",
)

SCALE_SIGNAL_SLOT = SlotSpec(
    slot_name="scale_signal",
    acceptable_signal_kinds=(SignalKind.ENRICHMENT, SignalKind.QUANTITY),
    min_evidence_kind=EvidenceKind.VERIFIED,
    critical=True,
    hedge_template="around {card_text}",
    refusal_text="",
)

TESTIMONY_SIGNAL_SLOT = SlotSpec(
    slot_name="testimony_signal",
    acceptable_signal_kinds=(SignalKind.TESTIMONY,),
    min_evidence_kind=EvidenceKind.WEAK,
    critical=False,
    hedge_template="and customers note {card_text}",
    refusal_text="",
)


_BODY = (
    "{company_signal} "
    "The strongest verified signal is {scale_signal}. "
    "{testimony_signal}"
)


LEAD_RATIONALE_V0_1 = Template(
    template_id="lead_rationale",
    version="v0.1",
    slot_specs=(
        COMPANY_SIGNAL_SLOT,
        SCALE_SIGNAL_SLOT,
        TESTIMONY_SIGNAL_SLOT,
    ),
    body=_BODY,
)
