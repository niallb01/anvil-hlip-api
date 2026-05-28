"""5-channel scorer (TB-04, hardened at TB-09).

Implements the PDF rubric:

    industry_fit             0-20
    company_size_fit         0-25
    decision_maker_seniority 0-20
    budget_likelihood_score  0-20
    growth_signals           0-15
                             -----
    lead_score (sum)         0-100

Law II discipline: a channel with zero supporting evidence drops to zero
regardless of rubric_points. Implemented via `ChannelScore.final_score`
which gates on `evidence_count`. This makes the gate bug-resistant —
scorers can return any rubric_points value; if evidence_count is zero,
the emitted score is zero.

TB-09 context-modifier hardening:
  - Non-commercial gate: charity/foundation context caps industry_fit
    and budget_likelihood_score at 5
  - Commercial-subscription gate: currency only awards full budget
    points when paired with subscription markers (/month, /year, plan,
    tier, MRR, ARR, Enterprise) within 50 chars
  - Named-client check: social_proof boost requires named-entity client
    list (filters out "loved by thousands"-style B2C crowd language)

The rubric weights themselves remain UNCALIBRATED. They produce
directional scores honestly disclosed in CLI output (`Confidence=0.x —
uncalibrated`). Real calibration against partner outcome data is a
post-handoff concern.

This module is also responsible for two derived flags:
    budget_likelihood   "high" | "medium" | "low"   (categorical band)
    decision_maker      bool                         (seniority ≥ 15)
"""

from dataclasses import dataclass
from typing import Dict, List

from anvil_scout.contracts import ScrapedInput
from anvil_scout.core.detectors import Span
from anvil_scout.core.context import (
    is_non_commercial,
    has_commercial_subscription_context,
    has_named_b2b_clients,
)


# TB-18: enrichment-driven verification sets for industry_fit + growth_signals.
# Conservative selection: only categories that unambiguously indicate the
# relevant signal. Ambiguous values (seriesA, seed, bootstrapped, consumer)
# do NOT trigger boosts — they aren't negative signals, just not verifications.
_B2B_INDUSTRY_CLASSES = frozenset({
    "saas", "services", "marketplace", "platform", "enterprise", "b2b",
})
_GROWTH_FUNDING_STAGES = frozenset({
    "seriesB", "seriesC+", "ipo",
})


# ─── primitive ──────────────────────────────────────────────────────────────

@dataclass
class ChannelScore:
    """One scoring channel result, with Law-II gating built in."""

    name: str
    rubric_points: int   # what the rubric *would* assign before the gate
    evidence_count: int  # how many supporting spans / signals were observed

    @property
    def final_score(self) -> int:
        """Law II: zero evidence collapses score to zero."""
        if self.evidence_count <= 0:
            return 0
        return self.rubric_points


# ─── helpers ────────────────────────────────────────────────────────────────

def _count(spans: List[Span], kind: str, subtype: str = None) -> int:
    if subtype is None:
        return sum(1 for s in spans if s.kind == kind)
    return sum(1 for s in spans if s.kind == kind and s.subtype == subtype)


def _missing(spans: List[Span], subtype: str) -> bool:
    return any(s.kind == "missing" and s.subtype == subtype for s in spans)


def _present(spans: List[Span], subtype: str) -> bool:
    """A B2B category is 'present' if it is NOT in the missing list."""
    return not _missing(spans, subtype)


# ─── channel scorers ────────────────────────────────────────────────────────

def score_industry_fit(spans: List[Span], text: str = "", enrichment=None) -> ChannelScore:
    """B2B SaaS/tech orientation, from observable text signals only.

    TB-09 hardening:
      - non-commercial context caps the score at 5
      - currency only counts toward `saas_strong` when paired with a B2B
        subscription marker (otherwise it's likely consumer pricing)
      - social_proof only boosts the rubric if the page actually names B2B
        clients (not generic crowd language)

    TB-18 enrichment integration:
      - When enrichment provides industry_class in {saas, services,
        marketplace, platform, enterprise, b2b}, it counts as one
        additional piece of evidence — flowing through the existing
        tier logic, naturally lifting rubric in the standard way.
      - Non-commercial cap STILL APPLIES at 5 (a charity-page with
        enrichment claiming "saas" is more likely a provider error
        than a real signal).
      - industry_class outside the B2B set has no effect (consumer/retail/
        d2c don't reduce the score; they just don't verify B2B).
    """
    # TB-09: split currency into commercial vs bare; only commercial counts here.
    currency_spans = [s for s in spans if s.kind == "quantity" and s.subtype == "currency"]
    if text:
        commercial_currency_n = sum(
            1 for s in currency_spans
            if has_commercial_subscription_context(s.start, s.end, text)
        )
    else:
        commercial_currency_n = len(currency_spans)

    saas_strong = _count(spans, "quantity", "uptime_sla") + commercial_currency_n
    b2b_signals = _count(spans, "quantity", "customer_count")
    social_proof = _count(spans, "testimony", "social_proof")

    # TB-09: dampen social_proof if no named B2B clients in the text
    if text and social_proof > 0 and not has_named_b2b_clients(text):
        social_proof = 0   # crowd-language testimony doesn't count for industry_fit

    evidence = saas_strong + b2b_signals + social_proof

    # TB-18: enrichment-confirmed B2B industry class counts as one additional
    # piece of evidence. Provider returns lowercase canonical forms per the
    # EnrichmentResult docstring; we still .lower() defensively.
    enrichment_b2b_verified = False
    if enrichment is not None and getattr(enrichment, "available", False):
        ic = getattr(enrichment, "industry_class", None)
        if isinstance(ic, str) and ic.lower() in _B2B_INDUSTRY_CLASSES:
            enrichment_b2b_verified = True
            evidence += 1

    if saas_strong >= 2 and _present(spans, "product"):
        rubric = 20    # SaaS-strong: priced + enterprise feature + product page
    elif saas_strong >= 1 and (b2b_signals + social_proof) >= 1:
        rubric = 15    # B2B SaaS likely
    elif evidence >= 2:
        rubric = 10    # B2B-leaning, mixed (TB-18: enrichment can push us here)
    elif evidence >= 1:
        rubric = 5     # weak signal (TB-18: enrichment alone lands here)
    else:
        rubric = 0

    # TB-09: non-commercial gate hard-caps industry_fit at 5
    # (TB-18: this gate still applies even if enrichment claims "saas" —
    # respect the strongest negative signal)
    if text and is_non_commercial(text):
        rubric = min(rubric, 5)

    return ChannelScore("industry_fit", rubric, evidence)


def score_company_size_fit(spans: List[Span], enrichment=None) -> ChannelScore:
    """Mid-market range (20-200 employees) — heuristic, not number-parsed.

    TB-15: when enrichment is available and provides an in-range
    employee_count, the rubric lifts to 25 (the previously-documented
    ceiling). Out-of-range values, non-int values, and unavailable
    enrichment all fall back to the existing TB-04 heuristic unchanged.
    """
    headcount = _count(spans, "quantity", "headcount")
    customer_count = _count(spans, "quantity", "customer_count")
    hiring = _present(spans, "hiring")
    team_about = _present(spans, "team_about")

    evidence = headcount + customer_count + int(hiring) + int(team_about)

    # TB-15: enrichment-confirmed in-range employee count → rubric 25
    if enrichment is not None and getattr(enrichment, "available", False):
        ec = getattr(enrichment, "employee_count", None)
        if isinstance(ec, int) and 20 <= ec <= 200:
            # Enrichment span itself counts as one piece of evidence so the
            # Law-II multiplicative gate is satisfied even if website-derived
            # evidence is otherwise sparse.
            return ChannelScore("company_size_fit", 25, evidence + 1)

    # Existing TB-04 heuristic (unchanged):
    # Note: hard 25 (confirmed 20-200) would require parsing the number out
    # of headcount spans. Deferred to TB-09. Until then ceiling is 18.
    if headcount >= 1 and customer_count >= 1:
        rubric = 18    # multiple size signals — suggests in-range
    elif headcount >= 1 or customer_count >= 1:
        rubric = 10    # single quantitative signal
    elif hiring and team_about:
        rubric = 10    # hiring page + team page — likely 20+ employees
    elif hiring or team_about:
        rubric = 5     # one structural signal
    else:
        rubric = 0

    return ChannelScore("company_size_fit", rubric, evidence)


# Title parser — ordered highest → lowest seniority.
_CSUITE = ("ceo", "cto", "cfo", "coo", "cmo", "cro", "cio", "chief")
_VPDIR = ("vp ", " vp", "vp,", "vice president", "director", "head of", "head, ", "head:")
_SENIOR = ("senior", "principal", "staff", "lead ")
_MANAGER = ("manager",)
_IC = ("engineer", "analyst", "specialist", "associate", "developer", "designer",
       "consultant", "researcher", "scientist")


def score_decision_maker_seniority(title: str, enrichment=None) -> ChannelScore:
    """Parse the lead's title; seniority drives scoring per PDF rubric.

    TB-15: when enrichment confirms decision_maker_confirmed=True, the
    rubric is upgraded to 20 if the title-derived score was lower. False
    or None has no effect (we never downgrade from title). The enrichment
    span itself counts as one piece of evidence in addition to the title.
    """
    t = (title or "").lower().strip()
    if not t:
        # No title at all — only an enrichment confirmation can lift here.
        if enrichment is not None and getattr(enrichment, "available", False):
            dm = getattr(enrichment, "decision_maker_confirmed", None)
            if dm is True:
                return ChannelScore("decision_maker_seniority", 20, 1)
        return ChannelScore("decision_maker_seniority", 0, 0)

    # Title-derived rubric (existing TB-04 logic, ordered highest→lowest)
    if any(kw in t for kw in _CSUITE):
        rubric, ev = 20, 1
    elif any(kw in t for kw in _VPDIR):
        rubric, ev = 20, 1
    elif any(kw in t for kw in _SENIOR):
        rubric, ev = 15, 1
    elif any(kw in t for kw in _MANAGER):
        rubric, ev = 10, 1
    elif any(kw in t for kw in _IC):
        rubric, ev = 5, 1
    else:
        # Title given but no keyword match — minimal evidence, low score.
        rubric, ev = 5, 1

    # TB-15: enrichment-confirmed decision-maker upgrades sub-20 rubric to 20.
    # Confirmation never downgrades a title that already says C-suite/VP/Dir.
    if enrichment is not None and getattr(enrichment, "available", False):
        dm = getattr(enrichment, "decision_maker_confirmed", None)
        if dm is True:
            if rubric < 20:
                rubric = 20
            ev += 1   # enrichment span counts as an additional piece of evidence

    return ChannelScore("decision_maker_seniority", rubric, ev)


def score_budget_likelihood(
    spans: List[Span], text: str = ""
) -> ChannelScore:
    """Commercial maturity signals.

    TB-09 hardening: currency only contributes full points if a subscription
    marker (`/month`, `/year`, `plan`, `tier`, `MRR`, `Enterprise`, ...) sits
    within 50 chars of the currency span. Non-commercial context caps at 5.
    """
    currency_spans = [s for s in spans if s.kind == "quantity" and s.subtype == "currency"]
    # TB-09: split currency spans into commercial-context vs bare consumer pricing
    if text:
        commercial_currency = [
            s for s in currency_spans
            if has_commercial_subscription_context(s.start, s.end, text)
        ]
    else:
        # No text supplied → backward-compatible behaviour: treat all currency as commercial
        commercial_currency = currency_spans

    bare_currency = len(currency_spans) - len(commercial_currency)
    currency = len(commercial_currency)

    uptime = _count(spans, "quantity", "uptime_sla")
    customer_count = _count(spans, "quantity", "customer_count")
    pricing_present = _present(spans, "pricing")

    # Evidence still counts bare consumer pricing (it's information), but the
    # rubric only fully rewards commercial-context currency.
    evidence = currency + bare_currency + uptime + customer_count + int(pricing_present)

    if currency >= 1 and uptime >= 1:
        rubric = 20    # priced + enterprise tier signals
    elif currency >= 1:
        rubric = 15    # has visible commercial pricing
    elif customer_count >= 1 and pricing_present:
        rubric = 15    # revenue-generating, commercial intent
    elif customer_count >= 1 or uptime >= 1:
        rubric = 10    # commercial but unclear
    elif bare_currency >= 1:
        rubric = 5     # bare consumer-style pricing without B2B context
    elif pricing_present:
        rubric = 5
    else:
        rubric = 0

    # TB-09: non-commercial gate hard-caps budget at 5
    if text and is_non_commercial(text):
        rubric = min(rubric, 5)

    return ChannelScore("budget_likelihood_score", rubric, evidence)


def score_growth_signals(spans: List[Span], enrichment=None) -> ChannelScore:
    """Cause-effect language + hiring + customer-count signals.

    TB-18 enrichment integration:
      - When enrichment provides funding_stage in {seriesB, seriesC+, ipo},
        it counts as one additional piece of evidence AND lifts rubric
        by 5 (capped at 15).
      - seriesA / seed / bootstrapped are NOT treated as growth signals —
        too ambiguous (seriesA can mean very small early company,
        bootstrapped can mean strong organic growth OR stagnation).
    """
    causal = _count(spans, "causal")
    hiring = _present(spans, "hiring")
    customer_count = _count(spans, "quantity", "customer_count")

    evidence = causal + int(hiring) + customer_count

    # TB-18: enrichment-confirmed growth-stage funding counts as evidence
    enrichment_growth_verified = False
    if enrichment is not None and getattr(enrichment, "available", False):
        fs = getattr(enrichment, "funding_stage", None)
        if isinstance(fs, str) and fs in _GROWTH_FUNDING_STAGES:
            enrichment_growth_verified = True
            evidence += 1

    if causal >= 2 and (hiring or customer_count >= 1):
        rubric = 15    # multiple growth signals
    elif causal >= 1 or (hiring and customer_count >= 1):
        rubric = 10    # 1-2 signals
    elif hiring or customer_count >= 1:
        rubric = 5     # weak
    else:
        rubric = 0

    # TB-18: enrichment funding-stage growth boost (+5, capped at channel max 15)
    if enrichment_growth_verified:
        rubric = min(15, rubric + 5)

    return ChannelScore("growth_signals", rubric, evidence)


# ─── aggregator ─────────────────────────────────────────────────────────────

def score_all_channels(
    spans: List[Span], inp: ScrapedInput, text: str = "", enrichment=None,
) -> Dict[str, ChannelScore]:
    """Run all 5 channels. Returns a dict keyed by schema field name.

    TB-09: `text` is passed to industry_fit and budget_likelihood so they
    can apply the non-commercial gate, commercial-subscription gate, and
    named-client check. When `text` is empty (legacy callers), the scorers
    behave as TB-04 originally — backward-compatible.

    TB-15: `enrichment` is an optional EnrichmentResult passed to the two
    channels that consume it (company_size_fit and decision_maker_seniority).
    When `enrichment` is None or `enrichment.available` is False, all
    scorers behave bit-identically to TB-09 — backward-compatible.

    TB-18: enrichment now also forwards to industry_fit (consumes
    industry_class as B2B verification) and growth_signals (consumes
    funding_stage as growth verification). budget_likelihood_score
    remains unchanged in this packet — funding_stage→budget mapping
    requires more design care and is deferred.
    """
    return {
        "industry_fit": score_industry_fit(spans, text, enrichment),
        "company_size_fit": score_company_size_fit(spans, enrichment),
        "decision_maker_seniority": score_decision_maker_seniority(inp.title, enrichment),
        "budget_likelihood_score": score_budget_likelihood(spans, text),
        "growth_signals": score_growth_signals(spans, enrichment),
    }


# ─── derived flags ──────────────────────────────────────────────────────────

def budget_likelihood_category(budget_score: int) -> str:
    """Categorical band per PDF rubric."""
    if budget_score >= 15:
        return "high"
    if budget_score >= 8:
        return "medium"
    return "low"


def decision_maker_flag(seniority_score: int) -> bool:
    """True if the lead has meaningful budget influence (manager+)."""
    return seniority_score >= 15


__all__ = [
    "ChannelScore",
    "score_industry_fit",
    "score_company_size_fit",
    "score_decision_maker_seniority",
    "score_budget_likelihood",
    "score_growth_signals",
    "score_all_channels",
    "budget_likelihood_category",
    "decision_maker_flag",
]
