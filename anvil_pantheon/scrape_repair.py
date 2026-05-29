"""Anvil-Pantheon-Floor — scrape repair (Packet 4).

Adapts DetMath v6.5 adversarial_brownian.py for Anvil. Where DetMath's
Brownian harness applies adversarial damage to math prose for hardening
the parser, Anvil's scrape_repair runs the inverse direction: it cleans
known noise patterns out of Firecrawl scrape output before Scout sees
it. The discipline matches DetMath's: "repair only adjacent to noise
markers, never invent missing facts."

Per DetMath v6.5 doctrine: 'The Brownian repair layer is intentionally
narrow: it repairs ingress damage around math cue words and must not
invent missing numbers, operators, or answers.' Anvil's equivalent:
the scrape repair layer repairs paywall remnants, navigation noise,
cookie banners, HTML entity remnants, and excessive whitespace -- and
nothing else. It NEVER fabricates content that wasn't in the input.

The NON_INVENTION discipline is structural: every repair rule is a
PATTERN -> REPLACEMENT pair where the replacement is either empty (the
rule removes noise) or a documented short normalization (single space
for &nbsp;, etc.). No rule produces meaningful new content.

Floor scope:
  - HTML entities (most common in Firecrawl output)
  - Cookie banner remnants
  - Paywall-subscribe prompts
  - Navigation breadcrumbs (heuristic, line-anchored)
  - Copyright footer boilerplate
  - Excessive whitespace collapse

JB-P4-1 discipline: rules are narrow + line-anchored where reasonable;
JB-P4-2 discipline: NEVER invents content -- tests assert every
                    character in the output traces to either an input
                    character or a documented short normalization.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import List, Pattern, Tuple


# ─── Repair rule table ────────────────────────────────────────────────────

# Each rule: (rule_name, category, regex, replacement)
# Order matters: HTML entities first (so we can subsequently match cleaned
# text), then noise removals, then whitespace normalization last.
REPAIR_RULES: Tuple[Tuple[str, str, str, str], ...] = (
    # ─── HTML entity normalization (replacement is documented) ───
    ("html_nbsp",        "html_entity",   r"&nbsp;",            " "),
    ("html_amp",         "html_entity",   r"&amp;",             "&"),
    ("html_lt",          "html_entity",   r"&lt;",              "<"),
    ("html_gt",          "html_entity",   r"&gt;",              ">"),
    ("html_quot",        "html_entity",   r"&quot;",            '"'),
    ("html_apos",        "html_entity",   r"&#39;|&apos;",      "'"),

    # ─── Noise removal (replacement is empty) ───
    ("cookie_banner",
     "noise",
     r"(?:We|This\s+site|We're)\s+use[s]?\s+cookies[^.!?\n]*[.!?]",
     ""),
    ("paywall_subscribe",
     "noise",
     r"Subscribe\s+to\s+(?:continue\s+reading|continue|read|access)\s+(?:this\s+|the\s+(?:full\s+)?)?(?:article|story|content|post)",
     ""),
    ("paywall_for_subscribers",
     "noise",
     r"This\s+(?:article|content|story|post)\s+is\s+(?:reserved\s+|exclusively\s+)?for\s+(?:our\s+)?subscribers",
     ""),
    ("login_required",
     "noise",
     r"(?:Please\s+)?(?:log\s*in|sign\s*in)\s+(?:to\s+(?:continue|read|view))",
     ""),

    # ─── Navigation noise (line-anchored to reduce false-positives) ───
    ("breadcrumb_line",
     "nav_noise",
     r"^\s*(?:Home|Main)\s*(?:[›>/»])\s*(?:[^›>/»\n]+\s*[›>/»]\s*)*[^\n]*$",
     ""),

    # ─── Footer boilerplate ───
    ("copyright_footer",
     "footer",
     r"©\s*\d{4}[^!?\n]{0,100}?(?:All\s+rights\s+reserved\.?|Inc\.?(?!\w)|LLC\.?(?!\w))",
     ""),

    # ─── Whitespace normalization (runs LAST so it cleans up after others) ───
    ("multiple_newlines", "whitespace", r"\n{4,}",                "\n\n"),
    ("multiple_spaces",   "whitespace", r"[ \t]{3,}",             "  "),
)


# Compile once at module load
_COMPILED_RULES: Tuple[Tuple[str, str, Pattern[str], str], ...] = tuple(
    (name, cat, re.compile(pat, re.MULTILINE), repl)
    for name, cat, pat, repl in REPAIR_RULES
)


# ─── Result types ─────────────────────────────────────────────────────────

@dataclass(frozen=True)
class RepairEntry:
    """One repair rule fired against one match. start/end are offsets in
    the input AT THE TIME the rule fired (which may differ from the
    original input if earlier rules already modified offsets)."""
    rule_name: str
    category: str
    matched_text: str
    replacement: str
    start: int
    end: int


@dataclass(frozen=True)
class RepairResult:
    """The cleaned text plus a forensic log of every rule firing.
    cleaned is what callers consume; repairs is what forensic review
    examines."""
    cleaned: str
    repairs: Tuple[RepairEntry, ...]
    bytes_in: int
    bytes_out: int

    def categories_fired(self) -> Tuple[str, ...]:
        """Distinct categories of repairs applied, in insertion order."""
        seen: List[str] = []
        for r in self.repairs:
            if r.category not in seen:
                seen.append(r.category)
        return tuple(seen)


# ─── Public entry point ───────────────────────────────────────────────────

def repair_scrape(raw: str) -> RepairResult:
    """Apply repair rules in declared order. Returns a RepairResult
    with the cleaned text and a forensic log.

    NON_INVENTION discipline: every replacement is either empty (removal)
    or a documented short normalization listed in REPAIR_RULES. The
    output contains no content that wasn't in the input modulo these
    documented replacements.

    Raises ValueError if `raw` is not a string.
    """
    if not isinstance(raw, str):
        raise ValueError(f"raw must be a string, got {type(raw).__name__}")

    bytes_in = len(raw.encode("utf-8"))
    text = raw
    log: List[RepairEntry] = []

    for name, category, rx, repl in _COMPILED_RULES:
        # Collect all matches (with offsets in the CURRENT text) before
        # applying the substitution. This lets us log each match
        # individually with its position.
        matches = list(rx.finditer(text))
        for m in matches:
            log.append(RepairEntry(
                rule_name=name,
                category=category,
                matched_text=m.group(0),
                replacement=repl,
                start=m.start(),
                end=m.end(),
            ))
        if matches:
            text = rx.sub(repl, text)

    bytes_out = len(text.encode("utf-8"))

    return RepairResult(
        cleaned=text,
        repairs=tuple(log),
        bytes_in=bytes_in,
        bytes_out=bytes_out,
    )


# ─── Discipline check (callable from tests) ────────────────────────────────

def all_replacements_are_documented() -> bool:
    """Returns True iff every rule's replacement is either empty
    (removal) or in the documented short-normalization set. This is a
    structural invariant: if you add a rule whose replacement contains
    meaningful new content, this check should be updated consciously
    (the same per-packet discipline as foundation_audit)."""
    documented_replacements = {
        "",        # removal
        " ",       # &nbsp; -> space
        "&", "<", ">", '"', "'",   # HTML entity normalizations
        "\n\n",    # collapse 4+ newlines to paragraph break
        "  ",      # collapse 3+ spaces to two spaces
    }
    for name, cat, pat, repl in REPAIR_RULES:
        if repl not in documented_replacements:
            return False
    return True
