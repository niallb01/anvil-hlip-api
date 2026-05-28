"""TB-18 — Enrichment consumption in industry_fit + growth_signals.

Closes Audit-2 finding A2-2 (partial enrichment consumption in scorer).
Two additional channels now consume enrichment fields:

  industry_fit:    consumes enrichment.industry_class as B2B verification
  growth_signals:  consumes enrichment.funding_stage as growth verification

Both are conservative additive boosts. Non-B2B industry_class values,
seriesA / seed / bootstrapped funding stages, and missing/unavailable
enrichment ALL leave existing behaviour unchanged.

The budget_likelihood_score channel is intentionally NOT extended in
TB-18; funding_stage → budget mapping is deferred.
"""

import json

import pytest

from anvil_scout.contracts import ScrapedInput
from anvil_scout.core.detectors import Span
from anvil_scout.core.enrichment import (
    EnrichmentResult,
    reset_provider,
    set_provider,
)
from anvil_scout.core.scorer import (
    score_industry_fit,
    score_growth_signals,
    score_all_channels,
    _B2B_INDUSTRY_CLASSES,
    _GROWTH_FUNDING_STAGES,
)


@pytest.fixture(autouse=True)
def _isolate_provider():
    reset_provider()
    yield
    reset_provider()


# ─── 1. Module constants documented and stable ────────────────────────────

class TestModuleConstants:

    def test_b2b_classes_includes_saas(self):
        assert "saas" in _B2B_INDUSTRY_CLASSES

    def test_b2b_classes_includes_services_marketplace_platform(self):
        for kind in ("services", "marketplace", "platform", "enterprise", "b2b"):
            assert kind in _B2B_INDUSTRY_CLASSES

    def test_b2b_classes_excludes_consumer(self):
        assert "consumer" not in _B2B_INDUSTRY_CLASSES
        assert "retail" not in _B2B_INDUSTRY_CLASSES
        assert "d2c" not in _B2B_INDUSTRY_CLASSES

    def test_growth_stages_includes_seriesB_and_above(self):
        for stage in ("seriesB", "seriesC+", "ipo"):
            assert stage in _GROWTH_FUNDING_STAGES

    def test_growth_stages_excludes_early_and_bootstrapped(self):
        assert "seed" not in _GROWTH_FUNDING_STAGES
        assert "seriesA" not in _GROWTH_FUNDING_STAGES
        assert "bootstrapped" not in _GROWTH_FUNDING_STAGES


# ─── 2. industry_fit verification boost ────────────────────────────────────

class TestIndustryFitVerification:

    def test_no_enrichment_preserves_tb04_behavior(self):
        """No enrichment arg → backward-compatible."""
        cs = score_industry_fit([], "")
        assert cs.final_score == 0

    def test_enrichment_saas_lifts_zero_to_five(self):
        """Empty text + enrichment confirming saas → rubric 5 (one verified evidence)."""
        enrichment = EnrichmentResult(available=True, industry_class="saas")
        cs = score_industry_fit([], "Some prose here.", enrichment)
        assert cs.final_score == 5

    def test_enrichment_marketplace_lifts(self):
        enrichment = EnrichmentResult(available=True, industry_class="marketplace")
        cs = score_industry_fit([], "Some prose here.", enrichment)
        assert cs.final_score == 5

    def test_enrichment_consumer_no_lift(self):
        """Non-B2B industry_class doesn't trigger boost."""
        enrichment = EnrichmentResult(available=True, industry_class="consumer")
        cs = score_industry_fit([], "Some prose here.", enrichment)
        assert cs.final_score == 0

    def test_enrichment_case_insensitive(self):
        """Provider returning 'SaaS' (mixed-case) should still match."""
        enrichment = EnrichmentResult(available=True, industry_class="SaaS")
        cs = score_industry_fit([], "Some prose here.", enrichment)
        assert cs.final_score == 5

    def test_unavailable_enrichment_no_lift(self):
        enrichment = EnrichmentResult(available=False, industry_class="saas")
        cs = score_industry_fit([], "Some prose here.", enrichment)
        assert cs.final_score == 0

    def test_text_signal_plus_enrichment_lifts_tier(self):
        """Existing text signal (evidence=1, rubric=5) + enrichment (+1) → rubric=10."""
        span = Span(start=0, end=10, text="100 customers",
                    kind="quantity", subtype="customer_count", confidence=0.9)
        enrichment = EnrichmentResult(available=True, industry_class="saas")
        cs = score_industry_fit([span], "100 customers", enrichment)
        assert cs.final_score == 10   # evidence=2 → tier 10

    def test_non_commercial_cap_still_applies(self):
        """JB-18-2 mitigation: non-commercial gate overrides enrichment-claimed B2B."""
        enrichment = EnrichmentResult(available=True, industry_class="saas")
        cs = score_industry_fit(
            [], "Our nonprofit foundation serves communities.", enrichment,
        )
        # Non-commercial gate caps at 5; enrichment can't override.
        assert cs.final_score <= 5

    def test_saas_strong_max_20_not_disturbed_by_enrichment(self):
        """Channel max should still cap at 20 even with enrichment boost."""
        # Build a saas-strong scenario: 2 uptime_sla spans + product
        spans = [
            Span(0, 10, "99.9% uptime", "quantity", "uptime_sla", 0.9),
            Span(20, 30, "99.99 SLA", "quantity", "uptime_sla", 0.9),
            Span(40, 50, "product", "missing", "product", 1.0),  # marker for _present
        ]
        # _present returns True when no missing span for that subtype;
        # passing a missing span actually makes _present false. Let me use
        # absence-of-missing pattern: no spans of kind=missing for product.
        spans = [
            Span(0, 10, "99.9% uptime", "quantity", "uptime_sla", 0.9),
            Span(20, 30, "99.99 SLA", "quantity", "uptime_sla", 0.9),
        ]
        enrichment = EnrichmentResult(available=True, industry_class="saas")
        cs = score_industry_fit(spans, "uptime sla text", enrichment)
        # Whatever the rubric ends up at, it must respect channel max (20).
        assert cs.final_score <= 20


# ─── 3. growth_signals funding-stage boost ─────────────────────────────────

class TestGrowthSignalsVerification:

    def _missing_growth_spans(self):
        """Helper: explicit missing spans for hiring and team_about so the
        TB-04 'absence-of-missing means present' quirk doesn't muddy the
        baseline. Forces _present(hiring)=False, _present(team_about)=False
        so growth_signals starts at rubric=0 with no other evidence."""
        return [
            Span(start=-1, end=-1, text="", kind="missing",
                 subtype="hiring", confidence=1.0),
            Span(start=-1, end=-1, text="", kind="missing",
                 subtype="team_about", confidence=1.0),
        ]

    def test_no_enrichment_preserves_tb04_behavior(self):
        """No enrichment + explicit missing growth signals → rubric=0."""
        cs = score_growth_signals(self._missing_growth_spans())
        assert cs.final_score == 0

    def test_no_enrichment_with_empty_spans_keeps_existing_quirk(self):
        """Pre-TB-18 quirk preserved: spans=[] → hiring 'present' → rubric=5."""
        cs = score_growth_signals([])
        assert cs.final_score == 5

    def test_enrichment_seriesB_lifts_zero_to_five(self):
        """Explicit-missing baseline=0 + seriesB enrichment → rubric=5."""
        enrichment = EnrichmentResult(available=True, funding_stage="seriesB")
        cs = score_growth_signals(self._missing_growth_spans(), enrichment)
        assert cs.final_score == 5

    def test_enrichment_seriesC_plus_lifts(self):
        enrichment = EnrichmentResult(available=True, funding_stage="seriesC+")
        cs = score_growth_signals(self._missing_growth_spans(), enrichment)
        assert cs.final_score == 5

    def test_enrichment_ipo_lifts(self):
        enrichment = EnrichmentResult(available=True, funding_stage="ipo")
        cs = score_growth_signals(self._missing_growth_spans(), enrichment)
        assert cs.final_score == 5

    def test_enrichment_seriesA_no_lift(self):
        """seriesA is too early to count as growth verification."""
        enrichment = EnrichmentResult(available=True, funding_stage="seriesA")
        cs = score_growth_signals(self._missing_growth_spans(), enrichment)
        assert cs.final_score == 0

    def test_enrichment_seed_no_lift(self):
        enrichment = EnrichmentResult(available=True, funding_stage="seed")
        cs = score_growth_signals(self._missing_growth_spans(), enrichment)
        assert cs.final_score == 0

    def test_enrichment_bootstrapped_no_lift_no_penalty(self):
        """Bootstrapped doesn't verify growth-stage funding, but also doesn't reduce."""
        enrichment = EnrichmentResult(available=True, funding_stage="bootstrapped")
        cs = score_growth_signals(self._missing_growth_spans(), enrichment)
        assert cs.final_score == 0

    def test_text_signal_plus_enrichment_adds_5(self):
        """Existing 'spans=[]' quirk: rubric=5 baseline; seriesB lift +5 → 10."""
        spans = []
        enrichment = EnrichmentResult(available=True, funding_stage="seriesB")
        cs = score_growth_signals(spans, enrichment)
        assert cs.final_score == 10

    def test_max_capped_at_15(self):
        """Full text growth signal at 15 + enrichment seriesB shouldn't exceed 15."""
        causal_spans = [
            Span(0, 5, "because", "causal", "connector", 0.9),
            Span(10, 15, "therefore", "causal", "connector", 0.9),
        ]
        enrichment = EnrichmentResult(available=True, funding_stage="seriesB")
        cs = score_growth_signals(causal_spans, enrichment)
        assert cs.final_score == 15   # capped at channel max

    def test_unavailable_no_lift(self):
        enrichment = EnrichmentResult(available=False, funding_stage="seriesB")
        cs = score_growth_signals(self._missing_growth_spans(), enrichment)
        assert cs.final_score == 0


# ─── 4. End-to-end CLI behaviour ───────────────────────────────────────────

class TestCLIIntegration:

    def _sample_raw(self):
        with open("data/sample_input.json") as f:
            return f.read()

    def test_default_stub_provider_no_change(self):
        """Without a real provider, behaviour matches pre-TB-18 baseline."""
        from anvil_scout.cli import run_once
        out = json.loads(run_once(self._sample_raw()))
        # Sample input scores lead=80 / industry_fit=20 / growth_signals=10 by default
        assert out["lead_score"] == 80
        assert out["industry_fit"] == 20
        assert out["growth_signals"] == 10

    def test_apollo_provider_lifts_growth(self):
        """An Apollo-style provider with funding_stage=seriesB lifts growth.
        Sample baseline growth_signals=10; seriesB lift +5 → 15."""
        from anvil_scout.cli import run_once

        class ApolloProvider:
            def fetch(self, company, website_url):
                return EnrichmentResult(
                    available=True,
                    industry_class="saas",
                    funding_stage="seriesB",
                    provider_id="apollo",
                )

        set_provider(ApolloProvider())
        try:
            out = json.loads(run_once(self._sample_raw()))
        finally:
            reset_provider()

        # industry_fit is already at 20 max in the sample; can't lift further.
        assert out["industry_fit"] == 20
        # growth_signals at baseline=10; seriesB lift +5 → 15.
        assert out["growth_signals"] == 15

    def test_enrichment_provenance_in_signal_evidence(self):
        """Enrichment spans for industry_class + funding_stage flow through V/W/M."""
        from anvil_scout.cli import run_once

        class RichProvider:
            def fetch(self, company, website_url):
                return EnrichmentResult(
                    available=True,
                    industry_class="saas",
                    funding_stage="seriesB",
                    provider_id="apollo",
                )

        set_provider(RichProvider())
        try:
            out = json.loads(run_once(self._sample_raw()))
        finally:
            reset_provider()

        verified = out["signal_evidence"]["verified"]
        assert any("enrichment/industry_class" in v for v in verified)
        assert any("enrichment/funding_stage" in v for v in verified)


# ─── 5. score_all_channels signature backward-compat ──────────────────────

class TestAggregatorBackwardCompat:

    def test_score_all_channels_no_enrichment_works(self):
        from anvil_scout.contracts import ScrapedInput
        inp = ScrapedInput(name="x", title="t", company="c",
                           website_url="", website_content="")
        scores = score_all_channels([], inp, "")
        assert "industry_fit" in scores
        assert "growth_signals" in scores

    def test_score_all_channels_with_enrichment_forwards_to_industry_and_growth(self):
        from anvil_scout.contracts import ScrapedInput
        inp = ScrapedInput(name="x", title="t", company="c",
                           website_url="", website_content="")
        enrichment = EnrichmentResult(
            available=True,
            industry_class="saas",
            funding_stage="seriesB",
        )
        scores = score_all_channels([], inp, "Some prose here.", enrichment)
        # industry_fit: enrichment-only → 5 (one evidence)
        assert scores["industry_fit"].final_score == 5
        # growth_signals: empty spans (hiring "present" via absence-of-missing
        # gives baseline 5) + seriesB (+5) → 10
        assert scores["growth_signals"].final_score == 10
