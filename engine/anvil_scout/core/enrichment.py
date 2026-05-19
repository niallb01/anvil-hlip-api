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
    """
    available: bool
    reason: str = ""

    employee_count: Optional[int] = None        # exact headcount if known
    funding_stage: Optional[str] = None         # seed | seriesA | seriesB | seriesC+ | ipo | bootstrapped
    industry_class: Optional[str] = None        # e.g. "saas", "services", "marketplace"
    decision_maker_confirmed: Optional[bool] = None  # this person has budget authority

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


def get_provider() -> EnrichmentProvider:
    """Return the current provider. Lazy-initialises to StubProvider on first call."""
    global _provider
    if _provider is None:
        _provider = StubProvider()
    return _provider


def set_provider(provider: EnrichmentProvider) -> None:
    """Partner integration point: replace the default StubProvider.

    Example:
        from anvil_scout.core.enrichment import set_provider
        from my_company.clearbit_adapter import ClearbitProvider
        set_provider(ClearbitProvider(api_key="..."))
    """
    global _provider
    _provider = provider


def reset_provider() -> None:
    """Test helper: restore the default StubProvider. Safe to call anytime."""
    global _provider
    _provider = StubProvider()


__all__ = [
    "EnrichmentResult",
    "EnrichmentProvider",
    "StubProvider",
    "get_provider",
    "set_provider",
    "reset_provider",
]
