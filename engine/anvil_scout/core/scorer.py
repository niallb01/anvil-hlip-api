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

def score_industry_fit(spans: List[Span], text: str = "") -> ChannelScore:
    """B2B SaaS/tech orientation, from observable text signals only.

    TB-09 hardening:
      - non-commercial context caps the score at 5
      - currency only counts toward `saas_strong` when paired with a B2B
        subscription marker (otherwise it's likely consumer pricing)
      - social_proof only boosts the rubric if the page actually names B2B
        clients (not generic crowd language)
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

    if saas_strong >= 2 and _present(spans, "product"):
        rubric = 20    # SaaS-strong: priced + enterprise feature + product page
    elif saas_strong >= 1 and (b2b_signals + social_proof) >= 1:
        rubric = 15    # B2B SaaS likely
    elif evidence >= 2:
        rubric = 10    # B2B-leaning, mixed
    elif evidence >= 1:
        rubric = 5     # weak signal
    else:
        rubric = 0

    # TB-09: non-commercial gate hard-caps industry_fit at 5
    if text and is_non_commercial(text):
        rubric = min(rubric, 5)

    return ChannelScore("industry_fit", rubric, evidence)


def score_company_size_fit(spans: List[Span]) -> ChannelScore:
    """Mid-market range (20-200 employees) — heuristic, not number-parsed."""
    headcount = _count(spans, "quantity", "headcount")
    customer_count = _count(spans, "quantity", "customer_count")
    hiring = _present(spans, "hiring")
    team_about = _present(spans, "team_about")

    evidence = headcount + customer_count + int(hiring) + int(team_about)

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


def score_decision_maker_seniority(title: str) -> ChannelScore:
    """Parse the lead's title; seniority drives scoring per PDF rubric."""
    t = (title or "").lower().strip()
    if not t:
        return ChannelScore("decision_maker_seniority", 0, 0)

    # Order matters: highest seniority first (JB-04-3).
    if any(kw in t for kw in _CSUITE):
        return ChannelScore("decision_maker_seniority", 20, 1)
    if any(kw in t for kw in _VPDIR):
        return ChannelScore("decision_maker_seniority", 20, 1)
    if any(kw in t for kw in _SENIOR):
        return ChannelScore("decision_maker_seniority", 15, 1)
    if any(kw in t for kw in _MANAGER):
        return ChannelScore("decision_maker_seniority", 10, 1)
    if any(kw in t for kw in _IC):
        return ChannelScore("decision_maker_seniority", 5, 1)

    # Title given but no keyword match — minimal evidence, low score.
    return ChannelScore("decision_maker_seniority", 5, 1)


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


def score_growth_signals(spans: List[Span]) -> ChannelScore:
    """Cause-effect language + hiring + customer-count signals."""
    causal = _count(spans, "causal")
    hiring = _present(spans, "hiring")
    customer_count = _count(spans, "quantity", "customer_count")

    evidence = causal + int(hiring) + customer_count

    if causal >= 2 and (hiring or customer_count >= 1):
        rubric = 15    # multiple growth signals
    elif causal >= 1 or (hiring and customer_count >= 1):
        rubric = 10    # 1-2 signals
    elif hiring or customer_count >= 1:
        rubric = 5     # weak
    else:
        rubric = 0

    return ChannelScore("growth_signals", rubric, evidence)


# ─── aggregator ─────────────────────────────────────────────────────────────

def score_all_channels(
    spans: List[Span], inp: ScrapedInput, text: str = ""
) -> Dict[str, ChannelScore]:
    """Run all 5 channels. Returns a dict keyed by schema field name.

    TB-09: `text` is passed to industry_fit and budget_likelihood so they
    can apply the non-commercial gate, commercial-subscription gate, and
    named-client check. When `text` is empty (legacy callers), the scorers
    behave as TB-04 originally — backward-compatible.
    """
    return {
        "industry_fit": score_industry_fit(spans, text),
        "company_size_fit": score_company_size_fit(spans),
        "decision_maker_seniority": score_decision_maker_seniority(inp.title),
        "budget_likelihood_score": score_budget_likelihood(spans, text),
        "growth_signals": score_growth_signals(spans),
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
