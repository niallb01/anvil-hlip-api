"""Quantity detector — numbers attached to meaningful nouns.

Subtypes captured:
    currency     — $499, £1.5M, €500K, USD 1000
    percentage   — 40%, 99.95%
    year         — "founded in 2019", "since 2015"
    headcount    — "10 engineers", "200 employees"
    customer_count — "200 customers", "over 1000 users"
    uptime_sla   — "99.95% uptime", "99.9% SLA"
    time_period  — "5 years", "12 months"

Each match returns a span at the exact match position.
"""

import re
from typing import List

from anvil_scout.core.detectors import Span


_PATTERNS = {
    "currency": re.compile(
        r"(?:\$|£|€|USD\s|GBP\s|EUR\s)\s?\d+(?:[.,]\d+)?\s?(?:k|K|m|M|bn|BN|million|billion)?\b"
        r"|"
        r"\b\d+(?:[.,]\d+)?\s?(?:k|K|m|M|bn|BN|million|billion)?\s?(?:USD|GBP|EUR|dollars|pounds|euros)\b",
    ),
    "percentage": re.compile(r"\b\d+(?:\.\d+)?\s*%(?!\s*(?:uptime|sla|availability))", re.IGNORECASE),
    "uptime_sla": re.compile(r"\b\d{2,3}(?:\.\d+)?\s*%\s*(?:uptime|sla|availability)\b", re.IGNORECASE),
    "year": re.compile(
        r"\b(?:since|founded|established|launched|started|incorporated|"
        r"founded\s+in|established\s+in)\s+(?:in\s+)?(\d{4})\b",
        re.IGNORECASE,
    ),
    "headcount": re.compile(
        r"\b(?:over\s+|more\s+than\s+)?\d+\+?\s+"
        r"(?:employees|engineers|team\s+members|people|staff|developers|"
        r"sales\s+reps|salespeople|designers)\b",
        re.IGNORECASE,
    ),
    "customer_count": re.compile(
        r"\b(?:over\s+|more\s+than\s+)?\d+\+?\s+"
        r"(?:customers|clients|users|companies|organi[sz]ations|teams|"
        r"businesses|brands)\b",
        re.IGNORECASE,
    ),
    "time_period": re.compile(
        r"\b\d+\+?\s+(?:years|months|weeks|days)\b",
        re.IGNORECASE,
    ),
}


# Confidence per subtype — currency/percentage are unambiguous; year is high
# when anchored by "founded in"; bare counts are mid because "5 minutes" or
# "10 features" could match if patterns were looser. We anchor patterns to
# specific nouns so confidence stays high.
_CONFIDENCE = {
    "currency":       0.95,
    "percentage":     0.95,
    "uptime_sla":     0.95,
    "year":           0.90,
    "headcount":      0.85,
    "customer_count": 0.85,
    "time_period":    0.70,
}


def detect_quantity(text: str) -> List[Span]:
    """Return all quantity spans found in `text`."""
    out: List[Span] = []
    if not text:
        return out

    for subtype, pat in _PATTERNS.items():
        for m in pat.finditer(text):
            out.append(Span(
                start=m.start(),
                end=m.end(),
                text=m.group(0),
                kind="quantity",
                subtype=subtype,
                confidence=_CONFIDENCE[subtype],
            ))

    return out
