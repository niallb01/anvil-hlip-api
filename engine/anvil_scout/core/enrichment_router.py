"""TB-16 — Multi-provider enrichment router.

Composes multiple `EnrichmentProvider` instances. Each `fetch_all()` call
dispatches to every provider in order; per-provider exceptions are
isolated (one bad provider does not stop the others). The router's job is
DISPATCH + COLLECTION. Conflict resolution between providers (which
employee_count wins?) is handled separately by `merge_results()`, called
by the pipeline after fetch_all returns.

Design notes
------------
- **Provider order = priority order.** In `merge_results`, for each
  scorer-consumed field, the FIRST non-None value across available
  results wins. Partners control trust by ordering the list.
- **Audit trail.** Every result carries a `provider_id` (derived from
  `type(p).__name__` if the provider/result didn't set one). The merged
  result's `reason` records which provider won each field.
- **Failure isolation.** A provider that raises an exception becomes
  `EnrichmentResult(available=False, reason="provider error (<id>): <type>")`.
  Other providers continue. This is the strip-don't-raise Agentic
  Corollary applied at the provider boundary.
- **Spans from EVERY provider.** The cli.py pipeline calls
  `enrichment_to_spans()` on EVERY result, not just the merged one, so
  the V/W/M audit trail shows EVERY provider's contribution with its
  provider_id provenance. The scorer only consumes the merged result's
  values — but the audit trail is complete.

Feynman: imagine three different intelligence services each handing you
a folder on the same target. The router collects all three folders.
Merge picks the freshest/most-trusted answer per question. The display
shows ALL folders side-by-side so the partner can see why each answer
was chosen — and where the services disagreed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, List, Optional

from anvil_scout.core.enrichment import (
    EnrichmentProvider,
    EnrichmentResult,
)


# Fields that the scorer can consume. Other EnrichmentResult fields
# (reason, provider_id, available) are not in this set because they're
# metadata, not signals.
_MERGE_FIELDS = (
    "employee_count",
    "funding_stage",
    "industry_class",
    "decision_maker_confirmed",
)


def _provider_id_for(provider: Any) -> str:
    """Derive a provider_id from a Provider instance.

    Prefers an explicit `provider_id` attribute (str), then class name,
    then "unknown". The fallback ensures every result is identifiable
    even from third-party providers that didn't follow the convention.
    """
    pid = getattr(provider, "provider_id", None)
    if isinstance(pid, str) and pid:
        return pid
    cls = type(provider).__name__
    return cls or "unknown"


def _ensure_provider_id(result: EnrichmentResult, provider: Any) -> None:
    """If the result didn't carry a provider_id, populate from the provider."""
    if not getattr(result, "provider_id", ""):
        result.provider_id = _provider_id_for(provider)


@dataclass
class EnrichmentRouter:
    """Multi-provider dispatch + collection (TB-16).

    The router does NOT merge. It returns all results unchanged (modulo
    provider_id back-fill and exception → unavailable conversion).
    Merging is the pipeline's concern; see `merge_results()`.
    """
    providers: List[EnrichmentProvider] = field(default_factory=list)

    def fetch_all(
        self,
        *,
        company: str,
        website_url: str,
    ) -> List[EnrichmentResult]:
        """Call each provider; return all results in provider order.

        Per-provider exceptions are converted to
        `EnrichmentResult(available=False, reason="provider error (...)").`
        A provider returning a non-EnrichmentResult is similarly handled.

        Every returned result is guaranteed to have a non-empty
        `provider_id` for audit traceability.
        """
        results: List[EnrichmentResult] = []
        for p in self.providers:
            pid = _provider_id_for(p)
            try:
                r = p.fetch(company=company, website_url=website_url)
            except Exception as e:
                r = EnrichmentResult(
                    available=False,
                    reason=f"provider error ({pid}): {type(e).__name__}",
                    provider_id=pid,
                )
                results.append(r)
                continue

            if not isinstance(r, EnrichmentResult):
                r = EnrichmentResult(
                    available=False,
                    reason=(
                        f"provider ({pid}) returned non-EnrichmentResult: "
                        f"{type(r).__name__}"
                    ),
                    provider_id=pid,
                )
                results.append(r)
                continue

            _ensure_provider_id(r, p)
            results.append(r)
        return results


def merge_results(results: List[EnrichmentResult]) -> EnrichmentResult:
    """Merge multiple EnrichmentResults into one for the scorer.

    Rule: for each scorer-consumed field (employee_count, funding_stage,
    industry_class, decision_maker_confirmed), the FIRST non-None value
    across results-in-order wins. Order = trust order.

    Returns:
        - If no available results: EnrichmentResult(available=False)
          with the first available-False reason for diagnostics.
        - Else: EnrichmentResult(available=True, provider_id="merged",
          fields populated per the rule, reason="merged: field=src, ...").

    The merged result has `provider_id="merged"` to distinguish it from
    single-source results. The per-field attribution is in `reason`.
    """
    if not results:
        return EnrichmentResult(
            available=False,
            reason="no providers configured",
            provider_id="merged",
        )

    available = [r for r in results if getattr(r, "available", False)]

    if not available:
        # Surface the first reason for diagnostic purposes (audit trail).
        first_reason = ""
        for r in results:
            if getattr(r, "reason", ""):
                first_reason = r.reason
                break
        return EnrichmentResult(
            available=False,
            reason=first_reason or "all providers unavailable",
            provider_id="merged",
        )

    merged = EnrichmentResult(available=True, provider_id="merged")
    attributions: List[str] = []

    for fname in _MERGE_FIELDS:
        for r in available:
            v = getattr(r, fname, None)
            if v is None:
                continue
            # Type guard — non-int employee_count, non-bool dm_confirmed
            # are not "None" but they're invalid; downstream scorer
            # type-guards too, but it's cheaper to skip them here.
            if fname == "employee_count" and not isinstance(v, int):
                continue
            if fname == "decision_maker_confirmed" and not isinstance(v, bool):
                continue
            if fname in ("funding_stage", "industry_class") and not (
                isinstance(v, str) and v
            ):
                continue
            setattr(merged, fname, v)
            pid = getattr(r, "provider_id", "") or "unknown"
            attributions.append(f"{fname}={pid}")
            break  # first non-None wins

    if attributions:
        merged.reason = "merged: " + ", ".join(attributions)
    else:
        merged.reason = "merged: all fields None across available providers"

    return merged


__all__ = [
    "EnrichmentRouter",
    "merge_results",
]
