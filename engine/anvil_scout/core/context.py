"""Context modifiers for B2B vs B2C / non-commercial discrimination (TB-09).

Three pure functions that the scorer optionally consults when the original
text is available. Each returns a boolean (or a check on a specific span).
The scorer applies the modifiers as gates / dampeners on top of raw span
counts.

These exist because TB-08 found that:
  - B2C consumer pricing (`£25`, `£899`) was inflating budget scores
  - Nonprofit/charity content was inflating industry_fit
  - Crowd-language testimony ("loved by thousands") was matching B2B
    named-client patterns

Adding context awareness keeps the detector layer simple (still pure regex)
while giving the scorer a sanity check on whether the matched signals
actually indicate B2B commerce.
"""

from __future__ import annotations

import re
from typing import Optional


# ─── non-commercial gate ───────────────────────────────────────────────────

# Multiple markers must be present to fire — single occurrence of e.g. "charity"
# could appear on a B2B page that mentions working with charities (JB-09-2).
_NON_COMMERCIAL_STRONG = (
    "registered charity",
    "non-profit",
    "nonprofit",
    "charitable foundation",
    "501(c)(3)",
    "501c3",
    "tax-exempt",
)
_NON_COMMERCIAL_WEAK = (
    "donate",
    "donation",
    "volunteer",
    "fundraise",
    "patron",
    "trustee",
    "grantmaking",
    "scholarship",
    "endowment",
    "philanthropy",
    "humanitarian",
)


def is_non_commercial(text: str) -> bool:
    """Detect non-commercial (charity/foundation) context.

    Requires EITHER one strong marker OR two weak markers — single weak
    marker (e.g. "donate" on a B2B blog) doesn't fire.
    """
    if not text:
        return False
    t = text.lower()
    if any(kw in t for kw in _NON_COMMERCIAL_STRONG):
        return True
    weak_hits = sum(1 for kw in _NON_COMMERCIAL_WEAK if kw in t)
    return weak_hits >= 2


# ─── commercial-subscription proximity ─────────────────────────────────────

# Word-bounded regex per marker — substring match risks false positives
# (e.g. "arr" matching "warranty"). Each marker is anchored to word
# boundaries (or appears with a slash prefix where the slash itself is the
# anchor).
#
# DELIBERATELY TIGHT: "starts at" and "billed" were trialled and dropped —
# both appear on D2C consumer pages ("starts at £899" for a bicycle) and
# would defeat the gate.
_SUBSCRIPTION_RE = re.compile(
    r"(?:"
    # Recurring-rate slash forms
    r"/month\b|/mo\b|/year\b|/yr\b"
    # Recurring-rate per-forms
    r"|\bper\s+month\b|\bper\s+year\b"
    # Recurring adverbs
    r"|\bmonthly\b|\bannually\b|\byearly\b"
    # B2B SaaS commerce vocabulary
    r"|\bsubscription\b|\bsubscribe\b|\bplan\b|\bplans\b|\btier\b|\btiers\b"
    # B2B-specific (acronyms / tiers)
    r"|\bMRR\b|\bARR\b|\benterprise\b|\bsaas\b"
    # B2B SaaS licensing
    r"|\blicense\b|\blicence\b|\bseat\b|\bseats\b"
    r")",
    re.IGNORECASE,
)

_PROXIMITY_WINDOW = 50   # characters either side of the currency span (JB-09-5)


def has_commercial_subscription_context(
    span_start: int,
    span_end: int,
    text: str,
) -> bool:
    """Check if a currency span sits near a subscription / B2B-commerce marker.

    Used to dampen budget scoring on consumer pricing like `£25` or `£899`
    that lack subscription context.
    """
    if not text:
        return False
    lo = max(0, span_start - _PROXIMITY_WINDOW)
    hi = min(len(text), span_end + _PROXIMITY_WINDOW)
    window = text[lo:hi]
    return bool(_SUBSCRIPTION_RE.search(window))


# ─── named-entity B2B client check ─────────────────────────────────────────

# Trigger phrases that often precede a client list.
_TRUST_TRIGGERS = re.compile(
    r"\b(?:trusted by|used by|chosen by|customers include|clients include|"
    r"works? with|powering|enterprise customers)\b",
    re.IGNORECASE,
)

# Words that indicate UNNAMED-CROWD rather than named-entity clients.
_CROWD_WORDS = (
    "thousands", "hundreds", "millions", "many", "countless",
    "everyone", "people", "users", "shoppers", "members",
    "fans", "drinkers", "readers",
)


def has_named_b2b_clients(text: str) -> bool:
    """Heuristic: are there named-entity B2B clients vs unnamed-crowd phrasing?

    A B2B page typically says "Trusted by Globex, Initech, and Hooli" — three
    Proper-Noun tokens after the trigger. A B2C page typically says
    "Loved by thousands of customers" — a crowd word after the trigger.
    """
    if not text:
        return False
    for match in _TRUST_TRIGGERS.finditer(text):
        # Look at the 80 chars following the trigger
        tail = text[match.end():match.end() + 80].lower()
        # If a crowd word appears soon after the trigger, treat as unnamed
        if any(kw in tail[:30] for kw in _CROWD_WORDS):
            continue
        # Look for capitalised tokens after the trigger (Proper Nouns)
        following = text[match.end():match.end() + 120]
        named_tokens = re.findall(r"\b[A-Z][a-zA-Z]{2,}\b", following)
        # Filter out common non-name tokens that happen to be capitalised
        filtered = [t for t in named_tokens if t.lower() not in (
            "the", "this", "these", "their", "they", "our",
        )]
        if len(filtered) >= 2:
            return True
    return False


__all__ = [
    "is_non_commercial",
    "has_commercial_subscription_context",
    "has_named_b2b_clients",
]

