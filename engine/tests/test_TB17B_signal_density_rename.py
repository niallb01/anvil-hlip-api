"""TB-17B — confidence → signal_density rename invariants.

Locks the rename in code so a future regression that re-introduces the
old name fails loudly. Covers:

1. Schema shape — required + properties under new name
2. Output shape — signal_density present, confidence absent on every fixture
3. Numeric identity — value previously under confidence equals new signal_density
4. classifier.compute_signal_density present; compute_confidence absent
5. FEATURE_NAMES contains signal_density, not confidence
6. State migration — legacy weights["confidence"] preserved as
   weights["signal_density"] when state loaded
7. Metacognition flag rename — low_signal_density_high_score, not
   low_confidence_high_score
"""

import json
import os
import subprocess

import pytest

from anvil_scout.contracts import ScoredOutput, SignalEvidence


# ─── 1. Schema shape ───────────────────────────────────────────────────────

class TestSchemaShape:

    def _load_schema(self):
        repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with open(os.path.join(repo_root, "SCHEMA.json")) as f:
            return json.load(f)

    def test_signal_evidence_required_has_signal_density_not_confidence(self):
        schema = self._load_schema()
        se = schema["properties"]["signal_evidence"]
        assert "signal_density" in se["required"]
        assert "confidence" not in se["required"]

    def test_signal_evidence_properties_has_signal_density_not_confidence(self):
        schema = self._load_schema()
        se_props = schema["properties"]["signal_evidence"]["properties"]
        assert "signal_density" in se_props
        assert "confidence" not in se_props

    def test_signal_density_property_shape(self):
        schema = self._load_schema()
        prop = schema["properties"]["signal_evidence"]["properties"]["signal_density"]
        assert prop["type"] == "number"
        assert prop["minimum"] == 0.0
        assert prop["maximum"] == 1.0


# ─── 2. Dataclass shape ────────────────────────────────────────────────────

class TestDataclassShape:

    def test_signal_evidence_has_signal_density_field(self):
        se = SignalEvidence()
        assert hasattr(se, "signal_density")
        assert se.signal_density == 0.0

    def test_signal_evidence_no_confidence_field(self):
        se = SignalEvidence()
        assert not hasattr(se, "confidence"), (
            "SignalEvidence still has a 'confidence' attribute — rename incomplete"
        )

    def test_signal_evidence_kwarg_signal_density_accepted(self):
        se = SignalEvidence(signal_density=0.7)
        assert se.signal_density == 0.7

    def test_signal_evidence_kwarg_confidence_rejected(self):
        with pytest.raises(TypeError):
            SignalEvidence(confidence=0.5)  # type: ignore


# ─── 3. End-to-end output shape ────────────────────────────────────────────

class TestEndToEndOutput:

    def test_sample_output_has_signal_density_not_confidence(self):
        from anvil_scout.cli import run_once
        with open("data/sample_input.json") as f:
            out = json.loads(run_once(f.read()))
        se = out["signal_evidence"]
        assert "signal_density" in se
        assert "confidence" not in se

    def test_signal_density_value_matches_documented_baseline_for_sample(self):
        """The sample input's signal_density should be 0.8 (the value
        that the field formerly known as confidence produced)."""
        from anvil_scout.cli import run_once
        with open("data/sample_input.json") as f:
            out = json.loads(run_once(f.read()))
        assert out["signal_evidence"]["signal_density"] == 0.8

    def test_all_fixtures_have_signal_density_not_confidence(self):
        """Across every fixture, the rename is complete in the output."""
        fixture_dirs = [
            "data",
            "data/e2e_fixtures",
            "data/hostile_fixtures",
        ]
        any_checked = False
        for d in fixture_dirs:
            if not os.path.isdir(d): continue
            for fn in sorted(os.listdir(d)):
                if not fn.endswith(".json"): continue
                path = os.path.join(d, fn)
                with open(path) as f:
                    raw = f.read()
                out_raw = subprocess.run(
                    ["python", "-m", "anvil_scout"],
                    input=raw, capture_output=True, text=True,
                ).stdout
                out = json.loads(out_raw)
                se = out["signal_evidence"]
                assert "signal_density" in se, f"{fn} missing signal_density"
                assert "confidence" not in se, f"{fn} still has confidence"
                any_checked = True
        assert any_checked, "no fixtures were tested"


# ─── 4. classifier API rename ──────────────────────────────────────────────

class TestClassifierAPI:

    def test_compute_signal_density_is_exported(self):
        from anvil_scout.core.classifier import compute_signal_density
        assert callable(compute_signal_density)

    def test_compute_confidence_is_not_exported(self):
        import anvil_scout.core.classifier as cl
        assert not hasattr(cl, "compute_confidence"), (
            "compute_confidence still exists in classifier module"
        )
        # __all__ should not list it either
        assert "compute_confidence" not in getattr(cl, "__all__", [])
        assert "compute_signal_density" in cl.__all__

    def test_math_unchanged_post_rename(self):
        """The renamed function computes IDENTICAL values to what the
        old function did."""
        from anvil_scout.core.classifier import compute_signal_density
        # Spot-check the documented behaviour preserved from compute_confidence.
        # Math:  clamp(0.2 + 0.6*(v + 0.5*w)/total, 0.2, 0.8)
        # thin_scrape forces 0.2
        assert compute_signal_density(True, 50, 0, 0) == 0.2
        # No spans at all -> 0.3
        assert compute_signal_density(False, 0, 0, 0) == 0.3
        # All verified -> 0.8 (ceiling)
        c = compute_signal_density(False, 10, 0, 0)
        assert c == 0.8
        # Mixed: 2 verified + 1 weak + 2 missing -> total=5, weighted=2.5/5=0.5
        # -> 0.2 + 0.3 = 0.5
        c = compute_signal_density(False, 2, 1, 2)
        assert c == 0.5


# ─── 5. FEATURE_NAMES rename ───────────────────────────────────────────────

class TestFeatureNamesRename:

    def test_feature_names_has_signal_density(self):
        from anvil_scout.daedalus.predictive import FEATURE_NAMES
        assert "signal_density" in FEATURE_NAMES

    def test_feature_names_has_no_confidence(self):
        from anvil_scout.daedalus.predictive import FEATURE_NAMES
        assert "confidence" not in FEATURE_NAMES


# ─── 6. State migration (load-bearing) ────────────────────────────────────

class TestStateMigration:
    """Legacy partner state files trained under weights['confidence']
    must come out with weights['signal_density'] preserving the value.
    Without this migration, any prior training would silently re-initialise."""

    def _legacy_state(self, confidence_weight: float = 0.42):
        return {
            "prediction_state": {
                "model": {
                    "bias": 0.15,
                    "weights": {
                        "industry_fit": 0.31,
                        "company_size_fit": 0.22,
                        "decision_maker_seniority": 0.18,
                        "budget_likelihood_score": 0.27,
                        "growth_signals": 0.14,
                        "lead_score": 0.5,
                        "confidence": confidence_weight,    # ← legacy key
                        "verified_density": 0.09,
                        "weak_density": -0.05,
                        "missing_density": -0.13,
                        "thin_scrape": -0.21,
                        "decision_maker": 0.19,
                    },
                    "learning_rate": 0.1,
                    "updates_seen": 47,
                    "last_update_at": "2026-05-26T10:00:00",
                },
                "episodes": {},
                "episode_order": [],
                "outcomes": {},
                "tool_reliability": {
                    "website_scrape": {
                        "calls_seen": 10, "available_count": 8,
                        "yield_total": 1.0,
                        "positive_outcomes": 2, "negative_outcomes": 1,
                        "nurture_outcomes": 0, "utility_ema": None,
                    },
                    "enrichment": {
                        "calls_seen": 0, "available_count": 0,
                        "yield_total": 0.0,
                        "positive_outcomes": 0, "negative_outcomes": 0,
                        "nurture_outcomes": 0, "utility_ema": None,
                    },
                },
                "self_model": {
                    "calls_seen": 47, "outcomes_seen": 3,
                    "last_brier": 0.17, "rolling_brier": 0.21,
                    "policy_version": "predictive-v0",
                    "last_prediction_at": "2026-05-26T10:00:00",
                    "last_outcome_at": "2026-05-26T10:00:00",
                },
            },
        }

    def test_legacy_confidence_weight_renamed_to_signal_density(self):
        from anvil_scout.daedalus.predictive import ensure_prediction_state
        state = self._legacy_state(confidence_weight=0.42)
        ps = ensure_prediction_state(state)
        weights = ps["model"]["weights"]
        assert weights["signal_density"] == 0.42, (
            f"learned weight not preserved across rename: {weights.get('signal_density')!r}"
        )
        assert "confidence" not in weights, "legacy key not removed"

    def test_migration_preserves_other_weights(self):
        from anvil_scout.daedalus.predictive import ensure_prediction_state
        state = self._legacy_state()
        ps = ensure_prediction_state(state)
        weights = ps["model"]["weights"]
        # Every non-confidence weight survives unchanged
        assert weights["industry_fit"] == 0.31
        assert weights["company_size_fit"] == 0.22
        assert weights["lead_score"] == 0.5
        assert weights["decision_maker"] == 0.19

    def test_migration_preserves_training_counters(self):
        from anvil_scout.daedalus.predictive import ensure_prediction_state
        state = self._legacy_state()
        ps = ensure_prediction_state(state)
        assert ps["model"]["bias"] == 0.15
        assert ps["model"]["updates_seen"] == 47

    def test_migration_idempotent(self):
        """Running ensure_prediction_state twice doesn't corrupt the state."""
        from anvil_scout.daedalus.predictive import ensure_prediction_state
        state = self._legacy_state()
        ps1 = ensure_prediction_state(state)
        first_weight = ps1["model"]["weights"]["signal_density"]
        ps2 = ensure_prediction_state(state)
        second_weight = ps2["model"]["weights"]["signal_density"]
        assert first_weight == second_weight == 0.42

    def test_fresh_state_initialises_signal_density_not_confidence(self):
        """A brand-new state (no legacy migration involved) gets
        signal_density seeded fresh and never gets a confidence key."""
        from anvil_scout.daedalus.predictive import ensure_prediction_state
        ps = ensure_prediction_state({})
        weights = ps["model"]["weights"]
        assert "signal_density" in weights
        assert weights["signal_density"] == 0.0
        assert "confidence" not in weights

    def test_non_numeric_legacy_value_handled_gracefully(self):
        """A corrupted state file with weights['confidence']='bad' should
        not crash; the new key takes 0.0 and the old key is removed."""
        from anvil_scout.daedalus.predictive import ensure_prediction_state
        state = {
            "prediction_state": {
                "model": {"weights": {"confidence": "bad"}},
            },
        }
        ps = ensure_prediction_state(state)
        weights = ps["model"]["weights"]
        assert "confidence" not in weights
        assert weights["signal_density"] == 0.0


# ─── 7. Metacognition flag rename ─────────────────────────────────────────

class TestMetacognitionFlagRename:

    def test_low_signal_density_high_score_flag_emitted(self):
        """When signal_density is low but lead_score is high, the renamed
        flag must fire (not the old 'low_confidence_high_score')."""
        from anvil_scout.daedalus.predictive import (
            _self_audit_flags, PredictionReceipt,
        )
        payload = {
            "lead_score": 75,
            "signal_evidence": {
                "signal_density": 0.25, "thin_scrape": False,
                "verified": [], "weak": [], "missing": [],
            },
        }
        receipt = PredictionReceipt(
            p_quality=0.5, quality_score=50,
            base_logit=0.0, adaptive_delta=0.0,
            model_updates_seen=0, features={},
        )
        flags = _self_audit_flags(payload, receipt, text_chars=1000)
        assert "low_signal_density_high_score" in flags
        assert "low_confidence_high_score" not in flags
