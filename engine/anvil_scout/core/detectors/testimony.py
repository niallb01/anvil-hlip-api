"""Testimony detector — sentences that make a claim about the company.

We look for three structural shapes:

  1. Claim markers: "leading", "leader", "trusted by", "used by", "premier",
     "best-in-class", "award-winning", "industry-leading", etc.
  2. Social-proof openers: "trusted by", "used by", "chosen by", "powering"
     (often followed by named clients).
  3. First-person assertions: "we [verb] [adjective]" patterns where the
     adjective is evaluative.

Each match returns a Span covering the surrounding sentence so the classifier
can see the claim in context.
"""

import re
from typing import List

from anvil_scout.core.detectors import Span


# ─── patterns ───────────────────────────────────────────────────────────────

# Generic claim markers (evaluative adjectives + ranking phrases).
_CLAIM_MARKERS = re.compile(
    r"\b("
    r"leading|leader|market[- ]leader|market[- ]leading|"
    r"premier|industry[- ]leading|award[- ]winning|"
    r"innovative|cutting[- ]edge|next[- ]generation|"
    r"best[- ]in[- ]class|enterprise[- ]grade|"
    r"world[- ]class|top[- ]rated|"
    r"#\s*1|#1|number\s+one"
    r")\b",
    re.IGNORECASE,
)

# Social proof — "trusted by Globex" / "used by 200+ teams"
_SOCIAL_PROOF = re.compile(
    r"\b(trusted\s+by|used\s+by|chosen\s+by|powering|powers)\b",
    re.IGNORECASE,
)

# First-person + claim-shaped predicate.
# Matches things like "we are the leading", "we provide enterprise-grade",
# "our platform helps".
_FIRST_PERSON_CLAIM = re.compile(
    r"\b(we\s+(?:are|provide|offer|deliver|build|enable|power|help)|"
    r"our\s+(?:platform|product|solution|customers|team))\b",
    re.IGNORECASE,
)


# ─── sentence splitter ──────────────────────────────────────────────────────

# Reasonable English sentence terminator — keeps "Inc.", "U.K.", "$1.5M"
# intact by requiring whitespace+capital after the period.
_SENT_SPLIT = re.compile(r"(?<=[.!?])\s+(?=[A-Z])")


def _sentence_spans(text: str):
    """Yield (start, end, sentence_text) for each sentence."""
    pos = 0
    for sent in _SENT_SPLIT.split(text):
        if not sent.strip():
            pos += len(sent) + 1
            continue
        # find the actual position of this sentence in the original text
        start = text.find(sent, pos)
        if start < 0:
            start = pos
        end = start + len(sent)
        pos = end
        yield start, end, sent


# ─── detector ───────────────────────────────────────────────────────────────

def detect_testimony(text: str) -> List[Span]:
    """Return all testimony spans found in `text`."""
    out: List[Span] = []
    if not text:
        return out

    for start, end, sent in _sentence_spans(text):
        # Generic claim markers
        for m in _CLAIM_MARKERS.finditer(sent):
            out.append(Span(
                start=start + m.start(),
                end=start + m.end(),
                text=sent.strip(),
                kind="testimony",
                subtype="claim_marker",
                confidence=0.7,
            ))

        # Social proof
        for m in _SOCIAL_PROOF.finditer(sent):
            out.append(Span(
                start=start + m.start(),
                end=start + m.end(),
                text=sent.strip(),
                kind="testimony",
                subtype="social_proof",
                confidence=0.8,
            ))

        # First-person claim
        for m in _FIRST_PERSON_CLAIM.finditer(sent):
            out.append(Span(
                start=start + m.start(),
                end=start + m.end(),
                text=sent.strip(),
                kind="testimony",
                subtype="first_person",
                confidence=0.5,  # lower — could be neutral statement
            ))

    return out
