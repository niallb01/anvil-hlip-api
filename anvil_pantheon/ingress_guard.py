"""Anvil-Pantheon-Floor — ingress guard (Packet 4).

Adapts DetMath v6.6 cheatwall.py for Anvil. Where DetMath Cheatwall
guards the ingress of contest prose against answer-leak phrases,
prompt-injection text, and missing critical operands, Anvil's ingress
guard performs the same discipline at the *post-Scout* boundary -- it
inspects the SourceBook produced by the scout_adapter before any
substrate organ sees it.

Two distinct mechanisms:

  - QUARANTINE  -- individual SourceCards whose span_text matches a
                   known-hostile pattern (prompt-injection markers,
                   answer-leak phrases). The card is removed from the
                   safe SourceBook but the rest of the book remains
                   usable. Recorded in QuarantineRecord with rule_id.

  - BLOCK       -- catastrophic Scout-output conditions where the
                   SourceBook as a whole cannot be safely consumed
                   (Scout's contract was structurally violated). The
                   verdict's clean=False and block_reasons names the
                   exact violation. Downstream packets must refuse.

Boundary discipline (NON_CLAIMS):
  * This module DOES NOT score or band the SourceBook. Hestia owns
    scoring (Packet 5). The guard only reports quarantine/block.
  * This module DOES NOT distinguish legitimate thin-scrape refusal
    from corruption. Thin scrapes with thin_scrape=True and consistent
    V/W/M counts are CLEAN here; the Oracle decides whether to emit
    a certified result or refuse. Only structural contradictions block.
  * This module DOES NOT "fix" cards -- it removes hostile ones, but
    never edits span_text. Adapter is the one place that touches text.

JB-P4-3 discipline: quarantine is per-card with explicit rule_id;
JB-P4-4 discipline: known attack patterns covered explicitly;
JB-P4-5 discipline: thin_scrape=True is CLEAN (legitimate refusal),
                    only structural contradictions block;
JB-P4-6 discipline: NON_CLAIMS comment lists what the guard does NOT do.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Pattern, Tuple

from .sourcebook import SourceBook
from .types import EvidenceKind, SourceCard


# ─── Pattern tables (named, traceable) ────────────────────────────────────

PROMPT_INJECTION_PATTERNS: Tuple[Tuple[str, str], ...] = (
    (r"\bignore\s+(?:all\s+)?(?:previous|prior|above)\s+(?:instructions|rules|prompts)\b",
     "prompt_injection.ignore_previous"),
    (r"\b(?:claude|chatgpt|gpt|model|ai|llm|assistant)\s+(?:must|should|will)\s+(?:output|answer|emit|score|grade|certify)\b",
     "prompt_injection.model_instruction"),
    (r"\breveal\s+(?:the\s+)?(?:system\s+prompt|hidden|secret|instructions)\b",
     "prompt_injection.reveal_hidden"),
    (r"(?:^|\s)(?:role|act)\s*:\s*(?:system|admin|root|developer)\b",
     "prompt_injection.role_override"),
    (r"\b(?:override|bypass|disable)\s+(?:the\s+)?(?:guard|filter|safety|check)\b",
     "prompt_injection.bypass_safety"),
    (r"<\|im_start\|>|<\|im_end\|>|<\|system\|>",
     "prompt_injection.chat_marker"),
)

ANSWER_LEAK_PATTERNS: Tuple[Tuple[str, str], ...] = (
    (r"\b(?:the\s+)?lead\s+band\s*(?:is|=|:)\s*(?:high|medium|low)\b",
     "answer_leak.lead_band"),
    (r"\b(?:score|grade|certify)\s+(?:this|us|me)\s+(?:at|as|with)\s+\d+\b",
     "answer_leak.score_directive"),
    (r"\b(?:gold|target|correct|expected)\s+(?:label|answer|certification|band|score)\s*(?:is|=|:)",
     "answer_leak.gold_marker"),
    (r"\b(?:predicted_quality|signal_density)\s*(?:is|=|:)\s*[01](?:\.\d+)?\b",
     "answer_leak.metric_directive"),
)

# Compiled once at module load for performance and to surface regex
# errors at import time rather than per-call.
_INJECTION_RX: Tuple[Tuple[Pattern[str], str], ...] = tuple(
    (re.compile(p, re.IGNORECASE), rid) for p, rid in PROMPT_INJECTION_PATTERNS
)
_LEAK_RX: Tuple[Tuple[Pattern[str], str], ...] = tuple(
    (re.compile(p, re.IGNORECASE), rid) for p, rid in ANSWER_LEAK_PATTERNS
)


CATASTROPHIC_BLOCK_REASONS = frozenset({
    "scout_internal_inconsistency.density_without_any_evidence",
    "scout_internal_inconsistency.verified_without_density",
    "scout_metadata_missing",
    "scout_metadata_malformed",
})


# ─── Verdict types ────────────────────────────────────────────────────────

@dataclass(frozen=True)
class QuarantineRecord:
    """One card was quarantined. rule_id identifies which pattern fired;
    excerpt is the matched substring for forensic review."""
    card_id: str
    rule_id: str
    excerpt: str


@dataclass(frozen=True)
class IngressVerdict:
    """Result of guard_ingress. clean=True iff no block reasons. The
    safe_book is the input SourceBook with quarantined cards removed
    (and scout_metadata preserved). Even when clean=True, individual
    cards may have been quarantined; check quarantine_records."""
    clean: bool
    quarantined_card_ids: Tuple[str, ...]
    quarantine_records: Tuple[QuarantineRecord, ...]
    block_reasons: Tuple[str, ...]
    safe_book: SourceBook


# ─── Pattern matching against a single card ───────────────────────────────

def _match_card(card: SourceCard) -> Optional[QuarantineRecord]:
    """Test a card's span_text against the pattern tables. Returns the
    first matching QuarantineRecord, or None if clean."""
    text = card.span_text or ""
    if not text:
        return None

    for rx, rid in _INJECTION_RX:
        m = rx.search(text)
        if m:
            return QuarantineRecord(
                card_id=card.card_id,
                rule_id=rid,
                excerpt=m.group(0),
            )

    for rx, rid in _LEAK_RX:
        m = rx.search(text)
        if m:
            return QuarantineRecord(
                card_id=card.card_id,
                rule_id=rid,
                excerpt=m.group(0),
            )

    return None


# ─── Structural / catastrophic checks ─────────────────────────────────────

def _check_scout_inconsistency(book: SourceBook) -> List[str]:
    """Return a list of catastrophic block reasons (empty if clean)."""
    reasons: List[str] = []
    meta = book.scout_metadata

    if not isinstance(meta, dict):
        reasons.append("scout_metadata_malformed")
        return reasons

    se = meta.get("signal_evidence", {}) or {}
    if not isinstance(se, dict):
        reasons.append("scout_metadata_malformed")
        return reasons

    sd = se.get("signal_density")
    has_any_evidence = len(book) > 0   # cards present in the book

    # Catastrophe 1: signal_density > 0 but NO V/W/M evidence at all.
    # Note: missing[] entries DO count as evidence (they're MISSING cards).
    # This block fires only when ALL three V/W/M lists were empty in Scout.
    if isinstance(sd, (int, float)) and sd > 0 and not has_any_evidence:
        reasons.append("scout_internal_inconsistency.density_without_any_evidence")

    # Catastrophe 2: at least one VERIFIED card present but signal_density
    # claimed to be exactly 0. Scout's contract: any V/W/M presence
    # implies density > 0.
    n_verified = len(book.by_evidence_kind(EvidenceKind.VERIFIED))
    if n_verified > 0 and isinstance(sd, (int, float)) and sd == 0:
        reasons.append("scout_internal_inconsistency.verified_without_density")

    return reasons


# ─── Public entry point ───────────────────────────────────────────────────

def guard_ingress(book: SourceBook) -> IngressVerdict:
    """Inspect a SourceBook for prompt-injection markers in span_text,
    answer-leak phrases, and catastrophic Scout-output inconsistencies.

    Returns an IngressVerdict with the safe SourceBook (quarantined
    cards removed) and a list of any block reasons. When block_reasons
    is non-empty, the safe_book is still returned (with quarantines
    applied) but callers must refuse to proceed -- the Scout output's
    structural contract was violated.
    """
    quarantine_records: List[QuarantineRecord] = []
    safe_cards: List[SourceCard] = []

    for card in book.all_cards():
        q = _match_card(card)
        if q is None:
            safe_cards.append(card)
        else:
            quarantine_records.append(q)

    # Build safe_book preserving scout_metadata
    safe_book = SourceBook(cards=safe_cards, scout_metadata=dict(book.scout_metadata))

    # Catastrophic checks run on the ORIGINAL book (not the post-
    # quarantine book) because quarantine removing all cards isn't a
    # structural inconsistency -- it just means we have to refuse.
    block_reasons = _check_scout_inconsistency(book)

    return IngressVerdict(
        clean=(not block_reasons),
        quarantined_card_ids=tuple(qr.card_id for qr in quarantine_records),
        quarantine_records=tuple(quarantine_records),
        block_reasons=tuple(block_reasons),
        safe_book=safe_book,
    )
