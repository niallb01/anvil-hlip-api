"""TB-16 — Multi-provider enrichment router tests.

Covers:

1. EnrichmentRouter.fetch_all  — dispatch + collection
2. EnrichmentRouter.fetch_all  — per-provider exception isolation
3. EnrichmentRouter.fetch_all  — provider_id auto-derivation
4. merge_results               — empty, all-unavailable, first-non-None rule,
                                 type guards, merged provider_id="merged"
5. set_providers / get_providers — multi-provider state
6. State coherence              — set_provider/set_providers mutual exclusion
7. CLI end-to-end with multi-provider — score lift visible; spans from
                                        every provider appear in V/W/M
8. Byte-identical regression    — default config still byte-identical to
                                  v0.1.0/TB-14A
"""

import hashlib
import json

import pytest

from anvil_scout.core.enrichment import (
    EnrichmentResult,
    EnrichmentProvider,
    StubProvider,
    get_provider,
    get_providers,
    set_provider,
    set_providers,
    reset_provider,
    enrichment_to_spans,
)
from anvil_scout.core.enrichment_router import (
    EnrichmentRouter,
    merge_results,
)


@pytest.fixture(autouse=True)
def _isolate_provider():
    reset_provider()
    yield
    reset_provider()


# ─── helpers ────────────────────────────────────────────────────────────────

class _StaticProvider:
    """Test helper — returns whatever is passed in at construction."""
    def __init__(self, result, *, provider_id=""):
        self._result = result
        if provider_id:
            self.provider_id = provider_id
    def fetch(self, company, website_url):
        return self._result


class _RaisingProvider:
    def __init__(self, *, provider_id="raising"):
        self.provider_id = provider_id
    def fetch(self, company, website_url):
        raise RuntimeError("kaboom")


class _GarbageProvider:
    """Returns the wrong type — tests defensive isinstance check."""
    def __init__(self, *, provider_id="garbage"):
        self.provider_id = provider_id
    def fetch(self, company, website_url):
        return {"employee_count": 85}   # dict, not EnrichmentResult


# ─── 1. Router dispatch + collection ────────────────────────────────────────

class TestRouterDispatch:

    def test_empty_providers_returns_empty_list(self):
        router = EnrichmentRouter(providers=[])
        assert router.fetch_all(company="x", website_url="") == []

    def test_single_provider_returns_one_result(self):
        p = _StaticProvider(
            EnrichmentResult(available=True, employee_count=85),
            provider_id="solo",
        )
        router = EnrichmentRouter(providers=[p])
        results = router.fetch_all(company="x", website_url="")
        assert len(results) == 1
        assert results[0].employee_count == 85
        assert results[0].provider_id == "solo"

    def test_multiple_providers_returns_all_in_order(self):
        p1 = _StaticProvider(
            EnrichmentResult(available=True, employee_count=85),
            provider_id="alpha",
        )
        p2 = _StaticProvider(
            EnrichmentResult(available=True, funding_stage="seriesB"),
            provider_id="beta",
        )
        p3 = _StaticProvider(
            EnrichmentResult(available=True, industry_class="saas"),
            provider_id="gamma",
        )
        router = EnrichmentRouter(providers=[p1, p2, p3])
        results = router.fetch_all(company="x", website_url="")
        assert len(results) == 3
        assert [r.provider_id for r in results] == ["alpha", "beta", "gamma"]


# ─── 2. Per-provider exception isolation ────────────────────────────────────

class TestRouterFailureIsolation:

    def test_raising_provider_becomes_unavailable_result(self):
        router = EnrichmentRouter(providers=[_RaisingProvider()])
        results = router.fetch_all(company="x", website_url="")
        assert len(results) == 1
        r = results[0]
        assert r.available is False
        assert "RuntimeError" in r.reason
        assert "raising" in r.reason
        assert r.provider_id == "raising"

    def test_one_failure_does_not_stop_others(self):
        good = _StaticProvider(
            EnrichmentResult(available=True, employee_count=85),
            provider_id="good",
        )
        router = EnrichmentRouter(providers=[_RaisingProvider(), good])
        results = router.fetch_all(company="x", website_url="")
        assert len(results) == 2
        assert results[0].available is False
        assert results[1].available is True
        assert results[1].employee_count == 85

    def test_garbage_return_type_handled_gracefully(self):
        router = EnrichmentRouter(providers=[_GarbageProvider()])
        results = router.fetch_all(company="x", website_url="")
        assert len(results) == 1
        assert results[0].available is False
        assert "non-EnrichmentResult" in results[0].reason


# ─── 3. Provider_id auto-derivation ─────────────────────────────────────────

class TestProviderIdDerivation:

    def test_provider_id_from_result_takes_priority(self):
        p = _StaticProvider(
            EnrichmentResult(available=True, employee_count=85, provider_id="from_result"),
            provider_id="from_provider",   # this should be overridden
        )
        router = EnrichmentRouter(providers=[p])
        r = router.fetch_all(company="x", website_url="")[0]
        # Result-level id takes priority over provider-attribute id
        assert r.provider_id == "from_result"

    def test_provider_id_from_provider_attribute_when_result_empty(self):
        p = _StaticProvider(
            EnrichmentResult(available=True, employee_count=85),
            provider_id="my_id",
        )
        router = EnrichmentRouter(providers=[p])
        r = router.fetch_all(company="x", website_url="")[0]
        assert r.provider_id == "my_id"

    def test_provider_id_fallback_to_class_name(self):
        class _Anonymous:
            def fetch(self, company, website_url):
                return EnrichmentResult(available=True, employee_count=85)
        router = EnrichmentRouter(providers=[_Anonymous()])
        r = router.fetch_all(company="x", website_url="")[0]
        assert r.provider_id == "_Anonymous"


# ─── 4. merge_results ───────────────────────────────────────────────────────

class TestMergeResults:

    def test_empty_returns_unavailable(self):
        m = merge_results([])
        assert m.available is False
        assert m.provider_id == "merged"

    def test_all_unavailable_returns_unavailable_with_first_reason(self):
        m = merge_results([
            EnrichmentResult(available=False, reason="rate limit", provider_id="a"),
            EnrichmentResult(available=False, reason="timeout", provider_id="b"),
        ])
        assert m.available is False
        assert "rate limit" in m.reason
        assert m.provider_id == "merged"

    def test_first_non_none_per_field_wins(self):
        m = merge_results([
            EnrichmentResult(available=True, employee_count=85, provider_id="alpha"),
            EnrichmentResult(available=True, employee_count=120, provider_id="beta"),
        ])
        assert m.available is True
        assert m.employee_count == 85   # alpha is first
        assert "employee_count=alpha" in m.reason

    def test_multi_source_disambiguation(self):
        """Different providers contribute different fields."""
        m = merge_results([
            EnrichmentResult(available=True, employee_count=85, provider_id="alpha"),
            EnrichmentResult(available=True, funding_stage="seriesB", provider_id="beta"),
            EnrichmentResult(available=True, industry_class="saas", provider_id="gamma"),
        ])
        assert m.employee_count == 85
        assert m.funding_stage == "seriesB"
        assert m.industry_class == "saas"
        assert "employee_count=alpha" in m.reason
        assert "funding_stage=beta" in m.reason
        assert "industry_class=gamma" in m.reason

    def test_merged_provider_id_is_merged(self):
        m = merge_results([
            EnrichmentResult(available=True, employee_count=85, provider_id="alpha"),
        ])
        assert m.provider_id == "merged"

    def test_type_guard_employee_count_non_int_skipped(self):
        bad = EnrichmentResult(available=True, provider_id="bad")
        object.__setattr__(bad, "employee_count", "lots")   # bypass typing
        good = EnrichmentResult(available=True, employee_count=85, provider_id="good")
        m = merge_results([bad, good])
        assert m.employee_count == 85
        assert "employee_count=good" in m.reason

    def test_type_guard_dm_confirmed_non_bool_skipped(self):
        bad = EnrichmentResult(available=True, provider_id="bad")
        object.__setattr__(bad, "decision_maker_confirmed", "yes")
        good = EnrichmentResult(available=True, decision_maker_confirmed=True, provider_id="good")
        m = merge_results([bad, good])
        assert m.decision_maker_confirmed is True
        assert "decision_maker_confirmed=good" in m.reason

    def test_all_available_but_all_fields_none_marks_as_such(self):
        m = merge_results([
            EnrichmentResult(available=True, provider_id="alpha"),
        ])
        assert m.available is True
        assert "all fields None" in m.reason


# ─── 5. set_providers / get_providers ───────────────────────────────────────

class TestMultiProviderState:

    def test_set_providers_then_get_providers(self):
        p1 = _StaticProvider(EnrichmentResult(available=True), provider_id="p1")
        p2 = _StaticProvider(EnrichmentResult(available=True), provider_id="p2")
        set_providers([p1, p2])
        result = get_providers()
        assert len(result) == 2
        assert result[0] is p1
        assert result[1] is p2

    def test_get_providers_default_is_single_stub(self):
        result = get_providers()
        assert len(result) == 1
        assert isinstance(result[0], StubProvider)

    def test_get_providers_after_set_provider_returns_single(self):
        p = _StaticProvider(EnrichmentResult(available=True), provider_id="solo")
        set_provider(p)
        result = get_providers()
        assert len(result) == 1
        assert result[0] is p


# ─── 6. State mutual exclusion (set_provider vs set_providers) ──────────────

class TestStateMutualExclusion:

    def test_set_provider_after_set_providers_clears_providers(self):
        p1 = _StaticProvider(EnrichmentResult(available=True), provider_id="p1")
        p2 = _StaticProvider(EnrichmentResult(available=True), provider_id="p2")
        set_providers([p1, p2])
        single = _StaticProvider(EnrichmentResult(available=True), provider_id="single")
        set_provider(single)
        result = get_providers()
        assert len(result) == 1
        assert result[0] is single

    def test_set_providers_after_set_provider_clears_provider(self):
        single = _StaticProvider(EnrichmentResult(available=True), provider_id="single")
        set_provider(single)
        p1 = _StaticProvider(EnrichmentResult(available=True), provider_id="p1")
        p2 = _StaticProvider(EnrichmentResult(available=True), provider_id="p2")
        set_providers([p1, p2])
        result = get_providers()
        assert len(result) == 2

    def test_reset_provider_clears_both(self):
        p1 = _StaticProvider(EnrichmentResult(available=True), provider_id="p1")
        set_providers([p1])
        reset_provider()
        result = get_providers()
        assert len(result) == 1
        assert isinstance(result[0], StubProvider)

    def test_get_provider_returns_first_when_multi(self):
        """Backward compat: get_provider() with multi-provider state
        returns the first in the list."""
        p1 = _StaticProvider(EnrichmentResult(available=True), provider_id="p1")
        p2 = _StaticProvider(EnrichmentResult(available=True), provider_id="p2")
        set_providers([p1, p2])
        assert get_provider() is p1


# ─── 7. End-to-end CLI integration with multi-provider ──────────────────────

class TestCLIMultiProvider:

    PAYLOAD = json.dumps({
        "name": "Jane",
        "title": "Senior Engineer",
        "company": "Acme",
        "website_url": "",
        "website_content": "Acme is a B2B SaaS platform. Founded in 2019. " * 8,
    })

    def test_two_providers_contribute_different_fields(self):
        """Apollo gives employee_count, Clearbit gives dm_confirmed.
        Both lifts should fire."""
        from anvil_scout.cli import run_once

        apollo = _StaticProvider(
            EnrichmentResult(available=True, employee_count=85),
            provider_id="apollo",
        )
        clearbit = _StaticProvider(
            EnrichmentResult(available=True, decision_maker_confirmed=True),
            provider_id="clearbit",
        )
        set_providers([apollo, clearbit])
        out = json.loads(run_once(self.PAYLOAD))
        assert out["company_size_fit"] == 25
        assert out["decision_maker_seniority"] == 20

        # Both providers' contributions visible in V/W/M
        verified = out["signal_evidence"]["verified"]
        assert any("provider=apollo" in v and "employee_count" in v for v in verified)
        assert any("provider=clearbit" in v and "decision_maker_confirmed" in v for v in verified)

    def test_one_provider_failing_does_not_break_pipeline(self):
        from anvil_scout.cli import run_once

        good = _StaticProvider(
            EnrichmentResult(available=True, employee_count=85),
            provider_id="good",
        )
        set_providers([_RaisingProvider(), good])
        out = json.loads(run_once(self.PAYLOAD))
        # Good provider's lift still happens
        assert out["company_size_fit"] == 25
        # And good provider's claim is in verified
        verified = out["signal_evidence"]["verified"]
        assert any("provider=good" in v for v in verified)

    def test_two_providers_disagree_first_wins_but_both_visible(self):
        """Apollo says 85, Clearbit says 120. Scorer uses 85 (first).
        Both claims appear in V/W/M for audit."""
        from anvil_scout.cli import run_once

        apollo = _StaticProvider(
            EnrichmentResult(available=True, employee_count=85),
            provider_id="apollo",
        )
        clearbit = _StaticProvider(
            EnrichmentResult(available=True, employee_count=120),
            provider_id="clearbit",
        )
        set_providers([apollo, clearbit])
        out = json.loads(run_once(self.PAYLOAD))
        # First-non-None: apollo wins, employee_count=85 → in range → 25
        assert out["company_size_fit"] == 25

        # Both providers' employee_count claims are visible (orthogonal
        # channel disagreement, NOT smoothed)
        verified = out["signal_evidence"]["verified"]
        assert any("provider=apollo" in v and "employee_count=85" in v for v in verified)
        assert any("provider=clearbit" in v and "employee_count=120" in v for v in verified)

    def test_clearbit_first_in_order_changes_priority(self):
        """Swap the order: Clearbit's 120 wins → out of range → fallback."""
        from anvil_scout.cli import run_once

        apollo = _StaticProvider(
            EnrichmentResult(available=True, employee_count=85),
            provider_id="apollo",
        )
        clearbit = _StaticProvider(
            EnrichmentResult(available=True, employee_count=120),
            provider_id="clearbit",
        )
        set_providers([clearbit, apollo])   # clearbit FIRST now
        out = json.loads(run_once(self.PAYLOAD))
        # 120 is in range → 25 lift still applies
        assert out["company_size_fit"] == 25

    def test_all_providers_unavailable_no_lift(self):
        from anvil_scout.cli import run_once

        a = _StaticProvider(EnrichmentResult(available=False, reason="a-down"))
        b = _StaticProvider(EnrichmentResult(available=False, reason="b-down"))
        set_providers([a, b])
        out = json.loads(run_once(self.PAYLOAD))
        # No enrichment lift; falls back to title-derived seniority
        assert out["decision_maker_seniority"] == 15


# ─── 8. Conservative-extension regression (load-bearing) ────────────────────

class TestConservativeExtensionRegression:
    """The load-bearing TB-16 invariant: default-config (StubProvider only,
    no set_providers call) output is byte-identical to v0.1.0/TB-14A on
    every fixture."""

    def test_sample_output_byte_identical_with_default(self):
        from anvil_scout.cli import run_once
        with open("data/sample_input.json") as f:
            raw = f.read()
        out_str = run_once(raw)
        h = hashlib.md5((out_str + "\n").encode("utf-8")).hexdigest()
        assert h == "058e975f76daf08bea568732b68b22cd", (
            f"Sample output MD5 drifted under default config. "
            f"Expected 058e975f76daf08bea568732b68b22cd, got {h}. "
            f"TB-16 broke the conservative-extension invariant."
        )

    def test_explicit_stub_provider_via_set_providers_also_byte_identical(self):
        """Even when set_providers is explicitly called with [StubProvider],
        output should be byte-identical to default config."""
        from anvil_scout.cli import run_once
        set_providers([StubProvider()])
        with open("data/sample_input.json") as f:
            raw = f.read()
        out_str = run_once(raw)
        h = hashlib.md5((out_str + "\n").encode("utf-8")).hexdigest()
        assert h == "058e975f76daf08bea568732b68b22cd"
