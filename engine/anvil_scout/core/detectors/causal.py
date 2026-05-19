"""Causal detector — cause-effect language.

Finds connector words/phrases that indicate causation. These are the
load-bearing linguistic signals for growth claims ("X led to 40% growth"),
value claims ("Y enables teams to ship faster"), and product claims
("Z drives revenue").

Each match returns a Span covering the surrounding sentence so the
classifier can read the cause and the effect together.
"""

import re
from typing import List

from anvil_scout.core.detectors import Span


_CAUSAL_MARKERS = re.compile(
    r"\b("
    r"because|"
    r"led\s+to|leading\s+to|"
    r"results?\s+in|resulting\s+in|"
    r"drives?|driving|"
    r"enables?|enabling|"
    r"allows?|allowing|allowed\s+(?:them|us|customers|teams)|"
    r"powers?|powering|"
    r"so\s+that|"
    r"thanks\s+to|due\s+to|owing\s+to|"
    r"helps?|helping|helped|"
    r"achieves?|achieving|achieved|"
    r"unlocks?|unlocked"
    r")\b",
    re.IGNORECASE,
)


# Sentence splitter (same heuristic as testimony.py).
_SENT_SPLIT = re.compile(r"(?<=[.!?])\s+(?=[A-Z])")


def _sentence_spans(text: str):
    pos = 0
    for sent in _SENT_SPLIT.split(text):
        if not sent.strip():
            pos += len(sent) + 1
            continue
        start = text.find(sent, pos)
        if start < 0:
            start = pos
        end = start + len(sent)
        pos = end
        yield start, end, sent


def detect_causal(text: str) -> List[Span]:
    """Return all causal spans found in `text`."""
    out: List[Span] = []
    if not text:
        return out

    for start, _end, sent in _sentence_spans(text):
        for m in _CAUSAL_MARKERS.finditer(sent):
            out.append(Span(
                start=start + m.start(),
                end=start + m.end(),
                text=sent.strip(),
                kind="causal",
                subtype="connector",
                confidence=0.7,
            ))

    return out
