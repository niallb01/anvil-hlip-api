"""Enrichment provider — the architectural seam for external data lookup.

Most B2B company data doesn't live on the website. Employee count, funding
stage, decision-maker confirmation — these come from LinkedIn / Clearbit /
Apollo APIs. Without enrichment, `company_size_fit` caps at 18 (we can
guess range but not confirm; see JB-04-2). With enrichment, partner can
push it to 25 and unlock real budget/seniority signals.

This module ships the SEAM, not the integration:

    EnrichmentProvider   — typing.Protocol (duck-typed)
    EnrichmentResult     — what providers return
    StubProvider         — v1 default: available=False
    get_provider()       — factory, returns the current provider
    set_provider(p)      — partner-facing: replace the default

Typical partner wiring (v1 pattern):

    from anvil_scout.core.enrichment import set_provider
    from my_company.clearbit_adapter import ClearbitProvider

    set_provider(ClearbitProvider(api_key="..."))

Note: `set_provider` is module-level mutable state. Acceptable for the
typical "load once per process" usage pattern. For multi-tenant async
flows, partner should swap to a per-context approach (e.g. contextvars).
"""

from dataclasses import dataclass, field
from typing import Optional, Protocol, runtime_checkable


# ─── data ───────────────────────────────────────────────────────────────────

@dataclass
class EnrichmentResult:
    """Standardised shape every provider returns.

    `available=False` means the provider has no data for this lead (offline,
    rate-limited, unknown company, etc). All payload fields are optional —
    a real provider may know only some of them.

    `provider_id` (TB-15): partner-supplied identifier surfaced as provenance
    in every enrichment-derived signal_evidence entry. Defaults to "" for
    backward-compatibility; callers may pass any short string ("apollo",
    "clearbit", "manual", etc.).
    """
    available: bool
    reason: str = ""

    employee_count: Optional[int] = None        # exact headcount if known
    funding_stage: Optional[str] = None         # seed | seriesA | seriesB | seriesC+ | ipo | bootstrapped
    industry_class: Optional[str] = None        # e.g. "saas", "services", "marketplace"
    decision_maker_confirmed: Optional[bool] = None  # this person has budget authority
    provider_id: str = ""                        # TB-15: provenance tag

    def short_summary(self) -> str:
        """One-line human-readable summary for the CLI rationale."""
        if not self.available:
            return f"unavailable ({self.reason})" if self.reason else "unavailable"
        parts = []
        if self.employee_count is not None:
            parts.append(f"employees={self.employee_count}")
        if self.funding_stage:
            parts.append(f"funding={self.funding_stage}")
        if self.industry_class:
            parts.append(f"industry={self.industry_class}")
        if self.decision_maker_confirmed is not None:
            parts.append(f"dm_confirmed={self.decision_maker_confirmed}")
        return ", ".join(parts) if parts else "available (no fields populated)"


# ─── interface ──────────────────────────────────────────────────────────────

@runtime_checkable
class EnrichmentProvider(Protocol):
    """Any object with `fetch(company, website_url) -> EnrichmentResult` qualifies."""

    def fetch(self, company: str, website_url: str) -> EnrichmentResult:
        ...


# ─── default stub ───────────────────────────────────────────────────────────

class StubProvider:
    """v1 default. Returns unavailable. Partner replaces via set_provider()."""

    def fetch(self, company: str, website_url: str) -> EnrichmentResult:
        return EnrichmentResult(
            available=False,
            reason="no provider configured (v1 stub)",
        )


# ─── factory (module-level singleton) ───────────────────────────────────────

_provider: Optional[EnrichmentProvider] = None
_providers: Optional[list] = None   # TB-16: multi-provider list


def get_provider() -> EnrichmentProvider:
    """Return the current SINGLE provider (TB-07 API, preserved for
    backward compatibility). Lazy-initialises to StubProvider on first call.

    If multi-provider mode is active (set_providers was called), this
    returns the FIRST configured provider — single-provider callers
    should prefer `get_providers()[0]` if they need the same semantics
    under both modes. Existing TB-07 / TB-15 code paths continue to work.
    """
    global _provider, _providers
    if _providers is not None and _providers:
        return _providers[0]
    if _provider is None:
        _provider = StubProvider()
    return _provider


def set_provider(provider: EnrichmentProvider) -> None:
    """Partner integration point (TB-07): replace the default StubProvider
    with ONE provider.

    Example:
        from anvil_scout.core.enrichment import set_provider
        from my_company.clearbit_adapter import ClearbitProvider
        set_provider(ClearbitProvider(api_key="..."))

    For multiple providers, use `set_providers([...])` (TB-16).
    set_provider and set_providers are mutually exclusive — calling one
    clears the other.
    """
    global _provider, _providers
    _provider = provider
    _providers = None


def reset_provider() -> None:
    """Test helper: restore the default StubProvider. Safe to call anytime.
    Clears BOTH single- and multi-provider state."""
    global _provider, _providers
    _provider = StubProvider()
    _providers = None


# ─── TB-16: multi-provider API ──────────────────────────────────────────────

def set_providers(providers: list) -> None:
    """TB-16: set multiple providers. The router calls them in order;
    merge picks first-non-None per field. List order = trust order.

    Example:
        from anvil_scout.core.enrichment import set_providers
        set_providers([ApolloProvider(), ClearbitProvider(), DNSProvider()])

    set_providers and set_provider are mutually exclusive — calling one
    clears the other.
    """
    global _provider, _providers
    _providers = list(providers)
    _provider = None


def get_providers() -> list:
    """TB-16: return the current provider list.

    - If multi-provider mode is active: returns that list.
    - If single-provider mode is active: returns [that_provider].
    - If neither: lazy-inits to [StubProvider()] and returns that.
    """
    global _provider, _providers
    if _providers is not None:
        return list(_providers)
    if _provider is None:
        _provider = StubProvider()
    return [_provider]


__all__ = [
    "EnrichmentResult",
    "EnrichmentProvider",
    "StubProvider",
    "get_provider",
    "set_provider",
    "reset_provider",
    "set_providers",
    "get_providers",
    "enrichment_to_spans",
]


# ─── TB-15: enrichment → spans adapter ──────────────────────────────────────

def enrichment_to_spans(
    result: "EnrichmentResult",
    provider_id: str = "",
) -> list:
    """Convert an EnrichmentResult to a list of synthetic Spans (TB-15).

    Each non-None payload field becomes one Span with:
        kind     = "enrichment"
        subtype  = field name ("employee_count" / "funding_stage" /
                   "industry_class" / "decision_maker_confirmed")
        text     = "provider=<id>; <field>=<value>"   (provenance trail)
        start    = 0
        end      = 0           (synthetic — no position in input text)
        confidence = 1.0       (provider-supplied facts; provider trust
                                is a separate concern, tracked by provider_id)

    When result is None or result.available is False, returns [].
    The classifier treats kind="enrichment" as VERIFIED (TB-15);
    Law-0 wrapper accepts enrichment/* prefixes (TB-15);
    scorers consume the structured EnrichmentResult directly,
    so this adapter exists primarily for the V/W/M audit trail and
    rationale display.

    The effective provider_id is: argument > result.provider_id > "unknown".
    Surfacing it in span.text gives partners a per-claim provenance trail
    (Law-0 / B.1 axes from the manifold framework).
    """
    # Lazy import to keep enrichment.py free of detector-module dependency
    from anvil_scout.core.detectors import Span

    if result is None or not getattr(result, "available", False):
        return []

    eff_id = provider_id or getattr(result, "provider_id", "") or "unknown"

    spans = []

    # employee_count
    ec = getattr(result, "employee_count", None)
    if isinstance(ec, int):
        spans.append(Span(
            start=0, end=0,
            text=f"provider={eff_id}; employee_count={ec}",
            kind="enrichment", subtype="employee_count",
            confidence=1.0,
        ))

    # funding_stage
    fs = getattr(result, "funding_stage", None)
    if isinstance(fs, str) and fs:
        spans.append(Span(
            start=0, end=0,
            text=f"provider={eff_id}; funding_stage={fs}",
            kind="enrichment", subtype="funding_stage",
            confidence=1.0,
        ))

    # industry_class
    ic = getattr(result, "industry_class", None)
    if isinstance(ic, str) and ic:
        spans.append(Span(
            start=0, end=0,
            text=f"provider={eff_id}; industry_class={ic}",
            kind="enrichment", subtype="industry_class",
            confidence=1.0,
        ))

    # decision_maker_confirmed
    dm = getattr(result, "decision_maker_confirmed", None)
    if isinstance(dm, bool):
        spans.append(Span(
            start=0, end=0,
            text=f"provider={eff_id}; decision_maker_confirmed={dm}",
            kind="enrichment", subtype="decision_maker_confirmed",
            confidence=1.0,
        ))

    return spans
