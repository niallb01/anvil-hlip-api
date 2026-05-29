"""Anvil-Pantheon-Floor — Cognitive Template Library (Packet 9).

The Cognitive Template Library is the composition layer that turns
SourceBook evidence into a candidate emission. Templates are reusable
patterns with named slots; each slot is filled from a SourceCard (or
REFUSED if no card meets the threshold). The output is a list of
SlotFill records + the rendered text, ready for the Oracle (Packet 10)
to certify and emit.

CORE DISCIPLINE: trace-to-card-or-refuse. There is no LLM-narrative
synthesis at floor. Every GROUNDED slot's span_text comes verbatim from
a SourceCard's span_text. HEDGE slots use a pre-declared hedge_template
keyed to a real card. REFUSED slots have no card and no text -- they
fall back to the template's refusal_text (non-critical) or refuse the
whole template (critical).

NON_CLAIMS (the cognitive-layer discipline):
  - Does NOT compute substrate outputs (Hestia/Vesta/Indra own those)
  - Does NOT decide whether to emit -- the Oracle (P10) does
  - Does NOT invent text -- GROUNDED slots are byte-for-byte from cards
  - Templates are PURE functions: same SourceBook -> same SlotFills

Floor scope:
  - SlotSpec: declarative schema for a slot (what kinds of evidence
    qualify, min evidence kind, critical flag, hedge/refusal text)
  - Template: tuple of SlotSpecs + body string with {slot_name}
    placeholders
  - TemplateRegistry: module-load-time immutable registry
  - fill_template(template, sourcebook) -> TemplateFillResult: the
    pure function that produces SlotFills + rendered text (or
    template-level refusal record)
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from ..sourcebook import SourceBook
from ..types import (
    CertificationStatus,
    EvidenceKind,
    SignalKind,
    SlotFill,
    SourceCard,
)


# ─── Schema types ─────────────────────────────────────────────────────────

@dataclass(frozen=True)
class SlotSpec:
    """Declarative schema for one slot in a Template.

      slot_name: identifier used in the template body as {slot_name}
      acceptable_signal_kinds: ordered preference list -- matching tries
        each in order
      min_evidence_kind: VERIFIED or WEAK. Cards below this threshold
        are ignored; if no card meets the threshold, slot is REFUSED.
      critical: if True, REFUSAL of this slot refuses the WHOLE template
      hedge_template: text used when only WEAK evidence available; must
        contain {card_text} placeholder which will be substituted with
        the card's span_text
      refusal_text: text used when slot is REFUSED and slot is not
        critical (substitutes the {slot_name} placeholder)
    """
    slot_name: str
    acceptable_signal_kinds: Tuple[SignalKind, ...]
    min_evidence_kind: EvidenceKind
    critical: bool
    hedge_template: str
    refusal_text: str


@dataclass(frozen=True)
class Template:
    """A reusable emission pattern with named slots."""
    template_id: str
    version: str
    slot_specs: Tuple[SlotSpec, ...]
    body: str

    def slot_names(self) -> Tuple[str, ...]:
        return tuple(s.slot_name for s in self.slot_specs)


@dataclass(frozen=True)
class TemplateFillResult:
    """Output of fill_template:
      - rendered_text: the final text (with all slot substitutions),
        OR empty string if template_refused=True
      - slot_fills: list of SlotFill records, one per slot
      - template_refused: True iff any critical slot was REFUSED
      - refusal_reasons: tuple of refusal reasons (one per critical
        slot that refused; empty if not refused)
    """
    rendered_text: str
    slot_fills: Tuple[SlotFill, ...]
    template_refused: bool
    refusal_reasons: Tuple[str, ...]


# ─── Card-matching logic ─────────────────────────────────────────────────

# Evidence ordering: VERIFIED > WEAK > MISSING (descending strength).
_EVIDENCE_STRENGTH = {
    EvidenceKind.VERIFIED: 2,
    EvidenceKind.WEAK: 1,
    EvidenceKind.MISSING: 0,
}


def _evidence_meets_threshold(card_kind: EvidenceKind, min_kind: EvidenceKind) -> bool:
    """Returns True iff card's evidence strength is >= min threshold."""
    return _EVIDENCE_STRENGTH[card_kind] >= _EVIDENCE_STRENGTH[min_kind]


def _find_best_card(
    sourcebook: SourceBook,
    spec: SlotSpec,
) -> Optional[SourceCard]:
    """Find the best card matching the slot's spec. Tries each acceptable
    signal_kind in declared order; within a signal_kind, prefers VERIFIED
    over WEAK. Returns None if no card meets the threshold.

    Tie-break: within a signal_kind+evidence_kind, picks the card with
    the lexicographically smallest card_id (deterministic; matches
    SourceBook.all_cards() canonical order)."""
    for sig_kind in spec.acceptable_signal_kinds:
        candidates = sourcebook.by_signal_kind(sig_kind)
        # Prefer VERIFIED over WEAK
        verified = [c for c in candidates if c.evidence_kind == EvidenceKind.VERIFIED]
        weak = [c for c in candidates if c.evidence_kind == EvidenceKind.WEAK]

        if _evidence_meets_threshold(EvidenceKind.VERIFIED, spec.min_evidence_kind) and verified:
            return verified[0]  # already sorted by card_id from .by_signal_kind
        if _evidence_meets_threshold(EvidenceKind.WEAK, spec.min_evidence_kind) and weak:
            return weak[0]
    return None


def _certification_for(card: SourceCard, spec: SlotSpec) -> CertificationStatus:
    """A card matching at VERIFIED evidence is GROUNDED; matching at
    WEAK is HEDGE."""
    if card.evidence_kind == EvidenceKind.VERIFIED:
        return CertificationStatus.GROUNDED
    if card.evidence_kind == EvidenceKind.WEAK:
        return CertificationStatus.HEDGE
    # Defensive: anything else is treated as refusal
    return CertificationStatus.REFUSED


def _span_text_for(card: SourceCard, spec: SlotSpec, cert: CertificationStatus) -> str:
    """The literal text that fills the placeholder. GROUNDED returns
    the card's span_text verbatim (no synthesis); HEDGE substitutes
    the card's span_text into the spec's hedge_template."""
    if cert == CertificationStatus.GROUNDED:
        return card.span_text
    if cert == CertificationStatus.HEDGE:
        return spec.hedge_template.replace("{card_text}", card.span_text)
    return ""


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# ─── Slot filling ─────────────────────────────────────────────────────────

def fill_slot(
    spec: SlotSpec,
    sourcebook: SourceBook,
) -> Tuple[str, SlotFill]:
    """Fill one slot from the sourcebook. Returns (text, SlotFill).

    - GROUNDED: text is card.span_text verbatim
    - HEDGE:    text is hedge_template with {card_text} substituted
    - REFUSED:  text is spec.refusal_text (caller handles critical flag)
    """
    card = _find_best_card(sourcebook, spec)

    if card is None:
        # No card meets threshold -> REFUSED
        fill = SlotFill(
            slot_name=spec.slot_name,
            source_card_id=None,
            span_text_hash=None,
            certification=CertificationStatus.REFUSED,
        )
        return spec.refusal_text, fill

    cert = _certification_for(card, spec)
    text = _span_text_for(card, spec, cert)
    fill = SlotFill(
        slot_name=spec.slot_name,
        source_card_id=card.card_id,
        span_text_hash=_sha256_text(text),
        certification=cert,
    )
    return text, fill


def fill_template(
    template: Template,
    sourcebook: SourceBook,
) -> TemplateFillResult:
    """Fill all slots in the template from the sourcebook. Returns
    TemplateFillResult.

    If any critical slot is REFUSED, template_refused=True and
    rendered_text="" -- the template as a whole declines to emit.
    Otherwise rendered_text is the body with each {slot_name}
    substituted by the slot's filled text.

    JB-P9-5 discipline: REFUSED non-critical slots substitute the
    refusal_text (no {placeholder} leaks); REFUSED critical slots
    abort the whole template.
    """
    fills: List[SlotFill] = []
    texts: Dict[str, str] = {}
    refusal_reasons: List[str] = []

    for spec in template.slot_specs:
        text, fill = fill_slot(spec, sourcebook)
        fills.append(fill)
        texts[spec.slot_name] = text

        if fill.certification == CertificationStatus.REFUSED and spec.critical:
            refusal_reasons.append(
                f"critical_slot_refused:{spec.slot_name}"
            )

    if refusal_reasons:
        return TemplateFillResult(
            rendered_text="",
            slot_fills=tuple(fills),
            template_refused=True,
            refusal_reasons=tuple(refusal_reasons),
        )

    # Render: substitute each {slot_name} in body
    rendered = template.body
    for slot_name, slot_text in texts.items():
        rendered = rendered.replace("{" + slot_name + "}", slot_text)

    return TemplateFillResult(
        rendered_text=rendered,
        slot_fills=tuple(fills),
        template_refused=False,
        refusal_reasons=(),
    )


# ─── Template registry ────────────────────────────────────────────────────

class TemplateRegistry:
    """Immutable registry of templates. Built at module load time from
    a frozen tuple of declared templates. No add() method -- templates
    cannot be hot-loaded (JB-P9-4 discipline)."""

    def __init__(self, templates: Tuple[Template, ...]):
        self._by_id: Dict[str, Template] = {}
        for t in templates:
            key = f"{t.template_id}@{t.version}"
            if key in self._by_id:
                raise ValueError(f"duplicate template registration: {key}")
            self._by_id[key] = t
        # Freeze
        self._frozen_keys = tuple(sorted(self._by_id.keys()))

    def get(self, template_id: str, version: str) -> Template:
        key = f"{template_id}@{version}"
        if key not in self._by_id:
            raise KeyError(f"no template registered for {key}")
        return self._by_id[key]

    def has(self, template_id: str, version: str) -> bool:
        return f"{template_id}@{version}" in self._by_id

    def all_keys(self) -> Tuple[str, ...]:
        return self._frozen_keys

    def __len__(self) -> int:
        return len(self._by_id)


# The canonical module-level registry is built in __init__.py from the
# templates package; this module provides only the schema + plumbing.
