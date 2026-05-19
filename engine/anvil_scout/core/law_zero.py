"""Law-0 emission wrapper — bounded cognition enforced at the output boundary.

Every internal module (detectors, classifier, scorer) is *designed* to
respect Law 0 (no claim without backing evidence). This module provides
the boundary check: it does not trust upstream discipline. It audits the
final ScoredOutput against the underlying spans and strips any claim
that lacks a backing span pointer.

This is the "doesn't lie" architectural guarantee. Even if a downstream
bug or adversarial input fabricates content, the wrapper catches it
before emission.

Strip-don't-raise: in production, surfacing a downstream-fabrication bug
as a 500 error makes the system fragile. Surfacing it as "we removed N
ungrounded claims" (in the rationale string only — never the schema)
makes the system robust and auditable.
"""

from typing import List, Tuple

from anvil_scout.contracts import ScoredOutput
from anvil_scout.core.detectors import Span


# Verified/weak strings begin with "<kind>/<subtype>:" per classifier._describe().
# Any entry whose prefix is not in this set is by definition not backed by a
# real detector span — strip.
_VALID_VW_PREFIXES = frozenset({
    # quantity subtypes
    "quantity/currency",
    "quantity/percentage",
    "quantity/uptime_sla",
    "quantity/year",
    "quantity/headcount",
    "quantity/customer_count",
    "quantity/time_period",
    # testimony subtypes
    "testimony/claim_marker",
    "testimony/social_proof",
    "testimony/first_person",
    # causal subtypes
    "causal/connector",
})

_VALID_MISSING_SUBTYPES = frozenset({
    "pricing", "hiring", "customers", "team_about", "product",
})

_BUDGET_CATEGORIES = frozenset({"high", "medium", "low"})

# Channel rubric ranges per PDF.
_CHANNEL_MAX = {
    "industry_fit": 20,
    "company_size_fit": 25,
    "decision_maker_seniority": 20,
    "budget_likelihood_score": 20,
    "growth_signals": 15,
}


# ─── helpers ────────────────────────────────────────────────────────────────

def _vw_prefix(entry: str) -> str:
    """Extract the 'kind/subtype' prefix from a verified/weak string."""
    if ":" not in entry:
        return ""
    return entry.split(":", 1)[0].strip()


def _has_supporting_span(entry: str, spans: List[Span]) -> bool:
    """A verified/weak entry is backed iff its prefix matches an actual span."""
    prefix = _vw_prefix(entry)
    if prefix not in _VALID_VW_PREFIXES:
        return False
    kind, subtype = prefix.split("/", 1)
    return any(s.kind == kind and s.subtype == subtype for s in spans)


def _is_valid_missing(entry: str) -> bool:
    """A missing entry is valid iff it references a known B2B category."""
    # Expected format: "<subtype> category absent from page"
    if " category absent" not in entry:
        return False
    head = entry.split(" ", 1)[0].strip()
    return head in _VALID_MISSING_SUBTYPES


def _clamp(value, lo, hi):
    return max(lo, min(hi, value))


# ─── main entry ─────────────────────────────────────────────────────────────

def enforce_law_zero(
    scored: ScoredOutput,
    spans: List[Span],
    thin_scrape: bool,
) -> Tuple[ScoredOutput, int]:
    """Audit and clean the ScoredOutput. Return (clean_output, violations).

    The violations count is for rationale transparency; the schema shape is
    never altered. Pass-through cost on a clean output is one O(n) sweep
    over each list plus a few scalar checks.
    """
    violations = 0
    se = scored.signal_evidence

    # 1. verified — every entry must trace back to a real span
    kept_v = [v for v in se.verified if _has_supporting_span(v, spans)]
    violations += len(se.verified) - len(kept_v)
    se.verified = kept_v

    # 2. weak — same check
    kept_w = [w for w in se.weak if _has_supporting_span(w, spans)]
    violations += len(se.weak) - len(kept_w)
    se.weak = kept_w

    # 3. missing — must reference a known B2B subtype
    kept_m = [m for m in se.missing if _is_valid_missing(m)]
    violations += len(se.missing) - len(kept_m)
    se.missing = kept_m

    # 4. pain_points must be [] (v1 contract — no LLM-narrative)
    if scored.pain_points:
        violations += len(scored.pain_points)
        scored.pain_points = []

    # 5. confidence bounds + thin-scrape hard floor
    if not (0.0 <= se.confidence <= 1.0):
        se.confidence = round(_clamp(se.confidence, 0.0, 1.0), 2)
        violations += 1
    if thin_scrape and se.confidence > 0.2:
        se.confidence = 0.2
        violations += 1

    # 6. budget_likelihood enum
    if scored.budget_likelihood not in _BUDGET_CATEGORIES:
        scored.budget_likelihood = "low"
        violations += 1

    # 7. decision_maker must be a bool
    if not isinstance(scored.decision_maker, bool):
        scored.decision_maker = False
        violations += 1

    # 8. channel scores in rubric range (clamp BEFORE recomputing lead_score)
    for name, ceiling in _CHANNEL_MAX.items():
        v = getattr(scored, name)
        if not isinstance(v, int):
            try:
                v = int(v)
            except (TypeError, ValueError):
                v = 0
            setattr(scored, name, v)
            violations += 1
        clamped = _clamp(v, 0, ceiling)
        if clamped != v:
            setattr(scored, name, clamped)
            violations += 1

    # 9. lead_score must equal sum of clamped channel scores
    expected = (
        scored.industry_fit
        + scored.company_size_fit
        + scored.decision_maker_seniority
        + scored.budget_likelihood_score
        + scored.growth_signals
    )
    if scored.lead_score != expected:
        scored.lead_score = expected
        violations += 1

    return scored, violations


__all__ = ["enforce_law_zero"]
