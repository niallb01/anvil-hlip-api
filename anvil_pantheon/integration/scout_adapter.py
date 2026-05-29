"""Anvil-Pantheon-Floor — Scout adapter (Packet 3).

Reads anvil_scout's JSON output and emits a SourceBook of typed
SourceCards. Every V/W/M span becomes one content-addressed SourceCard;
the non-V/W/M parts of Scout's output (channel scores, signal_density,
predicted_quality, thin_scrape, etc.) are preserved verbatim in
SourceBook.scout_metadata so the round-trip from Scout output -> adapter
output -> reconstructed Scout output is information-preserving (the
Packet 3 admission gate).

Scout output schema (as of TB-19, observed in production):

  {
    "lead_score": int,
    "industry_fit": int, "company_size_fit": int,
    "decision_maker_seniority": int, "budget_likelihood_score": int,
    "growth_signals": int,
    "predicted_quality": float,
    "rationale": str,
    "pain_points": list,
    "budget_likelihood": str,
    "decision_maker": bool,
    "signal_evidence": {
        "verified": ["kind/subtype: text", ...],
        "weak":     ["kind/subtype: text", ...],
        "missing":  ["category category absent from page", ...],
        "signal_density": float,
        "thin_scrape": bool,
    },
  }

Two span formats observed:
  detector style:  "{kind}/{subtype}: {text}"
                   kinds: causal, quantity, testimony
                   subtypes: connector, currency, customer_count,
                             percentage, uptime_sla, year, claim_marker,
                             first_person, social_proof, etc.
  missing style:   "{category} category absent from page"
                   categories: customers, hiring, pricing, product, team_about

The adapter is strict about Scout's schema: unknown detector kinds raise
(schema drift should be loud, not silent). signal_evidence top-level may
be absent (treated as empty), but its sub-keys, if present, must be the
right shape.
"""

from __future__ import annotations

from typing import Any, Dict, List, Tuple

from ..sourcebook import SourceBook
from ..types import (
    EvidenceKind,
    SignalKind,
    SourceCard,
    SourceCardKind,
    infer_signal_kind,
)


_MISSING_SUFFIX = " category absent from page"

# Top-level Scout fields preserved in scout_metadata (everything except
# signal_evidence's V/W/M lists, which become SourceCards).
_METADATA_TOP_LEVEL_FIELDS = (
    "lead_score",
    "industry_fit", "company_size_fit",
    "decision_maker_seniority", "budget_likelihood_score",
    "growth_signals",
    "predicted_quality", "rationale",
    "pain_points", "budget_likelihood", "decision_maker",
)
# Fields within signal_evidence that are NOT lists of spans (these flow
# into scout_metadata under a "signal_evidence" sub-dict).
_METADATA_SIGNAL_EVIDENCE_FIELDS = ("signal_density", "thin_scrape")


def adapt_scout_output(scout_output: Dict[str, Any]) -> SourceBook:
    """Convert Scout's JSON output to a SourceBook with scout_metadata
    attached as an attribute. The returned SourceBook carries enough
    information (cards + metadata) to reconstruct the original
    scout_output exactly via reconstruct_scout_output.

    Raises:
        ValueError: if the Scout output schema is malformed (missing
            required fields, unparseable span format, unknown detector
            kind).
    """
    if not isinstance(scout_output, dict):
        raise ValueError(
            f"scout_output must be a dict, got {type(scout_output).__name__}"
        )

    sig_ev = scout_output.get("signal_evidence", {}) or {}

    # If signal_evidence is present, its V/W/M keys must be lists.
    for required_key in ("verified", "weak", "missing"):
        if required_key in sig_ev and not isinstance(sig_ev[required_key], list):
            raise ValueError(
                f"scout_output.signal_evidence.{required_key} must be a list, "
                f"got {type(sig_ev[required_key]).__name__}"
            )

    # Build scout_metadata: top-level fields + non-list signal_evidence
    # fields, preserving everything the SourceBook does NOT carry as cards.
    metadata: Dict[str, Any] = {}
    for f in _METADATA_TOP_LEVEL_FIELDS:
        if f in scout_output:
            metadata[f] = scout_output[f]
    se_meta: Dict[str, Any] = {}
    for f in _METADATA_SIGNAL_EVIDENCE_FIELDS:
        if f in sig_ev:
            se_meta[f] = sig_ev[f]
    if se_meta:
        metadata["signal_evidence"] = se_meta

    book = SourceBook(scout_metadata=metadata)

    # Add contact metadata as METADATA SourceCards
    for meta_key, signal_kind in (
        ("name", SignalKind.TESTIMONY),
        ("title", SignalKind.QUANTITY),
        ("company", SignalKind.CAUSAL),
    ):
        val = scout_output.get(meta_key, "")
        if val:
            book.add(SourceCard.make(
                kind=SourceCardKind.METADATA,
                evidence_kind=EvidenceKind.VERIFIED,
                span_text=str(val),
                signal_kind=signal_kind,
                subtype=meta_key,
            ))

    # Process each evidence kind in turn
    for raw_span in sig_ev.get("verified", []):
        book.add(_card_from_span(raw_span, EvidenceKind.VERIFIED))
    for raw_span in sig_ev.get("weak", []):
        book.add(_card_from_span(raw_span, EvidenceKind.WEAK))
    for raw_span in sig_ev.get("missing", []):
        book.add(_card_from_span(raw_span, EvidenceKind.MISSING))

    return book


def reconstruct_scout_output(book: SourceBook) -> Dict[str, Any]:
    """Reverse of adapt_scout_output. Given a SourceBook (with its
    scout_metadata attribute), reconstruct the original scout_output
    dict. The reconstruction is exact iff adapt_scout_output was called
    on the original (this is the floor admission gate)."""
    out: Dict[str, Any] = {}
    meta = book.scout_metadata

    # Top-level fields go back where they came from
    for f in _METADATA_TOP_LEVEL_FIELDS:
        if f in meta:
            out[f] = meta[f]

    # Reconstruct signal_evidence: V/W/M lists from cards + non-list
    # fields from metadata.
    sig_ev: Dict[str, Any] = {"verified": [], "weak": [], "missing": []}
    for card in book.all_cards():
        raw = _span_from_card(card)
        if card.evidence_kind == EvidenceKind.VERIFIED:
            sig_ev["verified"].append(raw)
        elif card.evidence_kind == EvidenceKind.WEAK:
            sig_ev["weak"].append(raw)
        elif card.evidence_kind == EvidenceKind.MISSING:
            sig_ev["missing"].append(raw)

    # Non-list fields under signal_evidence
    se_meta = meta.get("signal_evidence", {})
    for f in _METADATA_SIGNAL_EVIDENCE_FIELDS:
        if f in se_meta:
            sig_ev[f] = se_meta[f]

    out["signal_evidence"] = sig_ev
    return out


# ─── Per-span parsing ─────────────────────────────────────────────────────

def _card_from_span(raw_span: str, evidence_kind: EvidenceKind) -> SourceCard:
    """Parse one raw span string and build a SourceCard. Handles both
    detector style and missing style."""
    if not isinstance(raw_span, str):
        raise ValueError(
            f"scout span must be a string, got {type(raw_span).__name__}"
        )

    signal_kind, subtype, span_text = _parse_span(raw_span, evidence_kind)

    import re
    clean_text = span_text
    # Remove corroboration annotations
    clean_text = re.sub(r'\s*\(corroborated by adjacent quantity\)', '', clean_text)
    clean_text = re.sub(r'\s*\(no supporting quantity\)', '', clean_text)
    # Remove Apollo provider prefix
    clean_text = re.sub(r'provider=\w+;\s*', '', clean_text)
    # Convert key=value enrichment format to readable text
    clean_text = re.sub(r'employee_count=(\d+)', r'\1 employees', clean_text)
    clean_text = re.sub(r'industry_class=([\w\s&]+)', r'\1 industry', clean_text)
    clean_text = re.sub(r'decision_maker_confirmed=\w+', '', clean_text)
    # Remove trailing truncation — catches "clarity and c" style cuts
    clean_text = re.sub(r'\s+\w{1,4}$', '', clean_text)
    # Also catch truncation before last word if sentence feels incomplete
    clean_text = re.sub(r'\s+(and|or|with|for|the|a|an|to|of|in|at|by)\s*$', '', clean_text, flags=re.IGNORECASE)
    clean_text = re.sub(r'….*$', '', clean_text)
    clean_text = re.sub(r'\.\.\.$', '', clean_text)
    clean_text = clean_text.strip().rstrip('.,')

    return SourceCard.make(
        kind=SourceCardKind.SPAN,
        evidence_kind=evidence_kind,
        span_text=clean_text,
        signal_kind=signal_kind,
        subtype=subtype,
    )


def _parse_span(raw_span: str, evidence_kind: EvidenceKind) -> Tuple[SignalKind, str, str]:
    """Returns (signal_kind, subtype, span_text). Raises ValueError on
    unparseable inputs (schema drift)."""
    # Detector style: "kind/subtype: text"
    if "/" in raw_span and ":" in raw_span:
        prefix, _, text = raw_span.partition(":")
        detector_kind, _, subtype = prefix.partition("/")
        # infer_signal_kind raises on unknown kinds -- propagate as the
        # schema-drift signal (JB-P3-6: schema drift must be loud)
        signal_kind = infer_signal_kind(detector_kind.strip())
        return signal_kind, subtype.strip() or "", text.strip()

    # Missing style: "category category absent from page"
    if evidence_kind == EvidenceKind.MISSING and raw_span.endswith(_MISSING_SUFFIX):
        # The category is the leading word(s) before " category absent..."
        category = raw_span[: -len(_MISSING_SUFFIX)].strip()
        return SignalKind.MISSING_PHRASE, category, raw_span

    raise ValueError(
        f"unparseable scout span (evidence_kind={evidence_kind.value}): {raw_span!r}"
    )


def _span_from_card(card: SourceCard) -> str:
    """Reverse of _parse_span: reconstruct the raw Scout span string from
    a SourceCard. Used for round-trip reconstruction."""
    if card.signal_kind == SignalKind.MISSING_PHRASE:
        # Missing style: span_text already holds the full original
        return card.span_text

    # Detector style: kind/subtype: text
    kind = card.signal_kind.value if card.signal_kind else ""
    subtype = card.subtype or ""
    return f"{kind}/{subtype}: {card.span_text}"
