"""Input adapter — first stage of the Anvil-Scout pipeline.

Two real-world callers:

1. **Partner's n8n pipeline** — passes `website_content` as a string already
   scraped by an upstream node. We may or may not need to clean HTML.

2. **Standalone use** — passes only `website_url`. We fetch and clean via
   Trafilatura.

Output is `PreparedText` with a thin-scrape flag enforcing Law 0: under 500
chars of cleaned text caps downstream confidence at 0.2.
"""

import re
from dataclasses import dataclass
from typing import Optional


# Hard threshold from LAWS.md (Law 0).
THIN_SCRAPE_THRESHOLD = 500


# Trafilatura is optional at import time. The structural fallback (regex strip)
# is enough for unit tests and for content that arrives already cleaned. Only
# the URL-fetch path actually requires Trafilatura.
try:
    import trafilatura  # type: ignore
    _HAS_TRAFILATURA = True
except ImportError:
    trafilatura = None  # type: ignore
    _HAS_TRAFILATURA = False


@dataclass
class PreparedText:
    """Output of the input adapter. Internal type; not part of public schema."""

    text: str
    source_url: str
    source_mode: str          # "content" | "url" | "empty"
    char_count: int
    thin_scrape: bool
    trafilatura_used: bool    # transparency: did we run trafilatura on this?


def _looks_like_html(s: str) -> bool:
    """Heuristic: does the input string contain HTML structure?

    True if any of: doctype, <html>, <body>, OR 3+ closing angle brackets
    combined with at least one opening angle bracket. False on plain text or
    short snippets.
    """
    if not s:
        return False
    lo = s.lower()
    if "<html" in lo or "<body" in lo or "<!doctype" in lo:
        return True
    if "<" not in s:
        return False
    closes = s.count(">")
    return closes >= 3


_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")


def _strip_html_fallback(html: str) -> str:
    """Minimal HTML stripper when Trafilatura is unavailable.

    Removes tags via regex and collapses whitespace. Not as good as
    Trafilatura's boilerplate removal — but deterministic, dependency-free,
    and good enough for unit tests and for already-near-clean content.
    """
    text = _TAG_RE.sub(" ", html)
    text = _WS_RE.sub(" ", text)
    return text.strip()


def _clean_html(html: str) -> tuple[str, bool]:
    """Run Trafilatura if available; otherwise minimal regex fallback.

    Returns (cleaned_text, trafilatura_used).
    """
    if _HAS_TRAFILATURA:
        extracted = trafilatura.extract(
            html,
            include_comments=False,
            include_tables=True,
            no_fallback=False,
        )
        if extracted:
            return extracted.strip(), True
        # Trafilatura returned None — fall through to regex
    return _strip_html_fallback(html), False


def prepare_text(
    website_content: str = "",
    website_url: str = "",
    fetch_if_empty: bool = False,
) -> PreparedText:
    """Produce cleaned text for downstream stages.

    Parameters
    ----------
    website_content : str
        Raw or cleaned website text. If provided and non-empty, this is used.
    website_url : str
        URL the content came from. Used for fetching if `website_content` is
        empty and `fetch_if_empty=True`. Also stored on the result for audit.
    fetch_if_empty : bool
        If True and `website_content` is empty, attempt to fetch the URL via
        Trafilatura. Requires the optional Trafilatura install.

    Returns
    -------
    PreparedText
    """
    trafilatura_used = False

    if website_content:
        if _looks_like_html(website_content):
            text, trafilatura_used = _clean_html(website_content)
        else:
            text = website_content.strip()
        mode = "content"

    elif website_url and fetch_if_empty:
        if not _HAS_TRAFILATURA:
            # Honest failure rather than silent garbage.
            raise RuntimeError(
                "URL fetch requested but trafilatura is not installed. "
                "Run install.bat (which installs from requirements.txt)."
            )
        downloaded = trafilatura.fetch_url(website_url)
        if downloaded:
            text, trafilatura_used = _clean_html(downloaded)
        else:
            text = ""
        mode = "url"

    else:
        text = ""
        mode = "empty"

    char_count = len(text)
    thin = char_count < THIN_SCRAPE_THRESHOLD

    return PreparedText(
        text=text,
        source_url=website_url,
        source_mode=mode,
        char_count=char_count,
        thin_scrape=thin,
        trafilatura_used=trafilatura_used,
    )
