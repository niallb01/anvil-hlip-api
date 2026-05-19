"""Missing-phrase detector — Law-0 absence detection.

Given the cleaned text, check whether each B2B signal category has ANY
keyword presence. If a category has zero keyword hits, emit a missing-span
for that category.

Categories were chosen by Pareto — the load-bearing five for B2B lead
scoring:
    pricing     — does the site mention pricing at all?
    hiring      — careers/jobs/we're-hiring language
    customers   — case studies, testimonials, named clients
    team_about  — founding year, team page, leadership
    product     — features, integrations, platform language

Missing-spans use start=-1, end=-1, text="" because absence has no
position in the text. Confidence is 1.0 — we are CERTAIN the keyword is
not present; this is direct observation, not inference.
"""

from typing import List

from anvil_scout.core.detectors import Span


# Keyword sets per category. Lowercase; matched against lowercased text.
CATEGORIES = {
    "pricing": [
        "pricing", "price", "/month", "/year", "per month", "per year",
        "plan", "tier", "free trial", "starting at", "starts at",
        "subscription", "$", "£", "€",
    ],
    "hiring": [
        "hiring", "careers", "career", "join us", "open positions",
        "open roles", "we're hiring", "we are hiring", "join our team",
        "job openings",
    ],
    "customers": [
        "customers", "clients", "case stud", "success stor",
        "testimonial", "trusted by", "used by", "customer logos",
    ],
    "team_about": [
        "founded", "founder", "team", "about us", "our team",
        "leadership", "headquartered", "based in", "since 19", "since 20",
    ],
    "product": [
        "features", "integrations", "platform", "product",
        "solution", "api", "capabilities", "how it works",
    ],
}


def detect_missing(text: str) -> List[Span]:
    """Return missing-spans for every category with zero keyword hits."""
    out: List[Span] = []
    if not text:
        # If text is empty, ALL categories are missing.
        for category in CATEGORIES:
            out.append(Span(
                start=-1, end=-1, text="",
                kind="missing", subtype=category, confidence=1.0,
            ))
        return out

    lo = text.lower()
    for category, keywords in CATEGORIES.items():
        if not any(kw in lo for kw in keywords):
            out.append(Span(
                start=-1, end=-1, text="",
                kind="missing", subtype=category, confidence=1.0,
            ))

    return out
