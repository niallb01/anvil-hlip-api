"""Persistent predictive adaptation for Anvil-Scout v2 prototype.

This module is the first product-facing use of the Daedalus state seam:
it gives Anvil continuity.

What it does
------------
- assigns each scored lead an opaque lead_id (hash only, no raw PII stored)
- stores compact scoring episodes in state
- predicts lead quality probability from the rubric channels + evidence shape
- ingests later outcomes (won / lost / nurture)
- performs a bounded online logistic update
- tracks a small self-model: calls, outcomes, calibration error, policy version
- optionally calibrates the emitted channel scores while preserving the public
  JSON contract and the lead_score=sum(channel_scores) invariant

The public n8n schema is unchanged. Default CLI behavior is unchanged unless a
caller explicitly provides a StateProvider and asks for adaptive output.
"""

from __future__ import annotations

import copy
import hashlib
import json
import math
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Mapping, MutableMapping, Optional, Tuple

from anvil_scout.contracts import ScrapedInput
from anvil_scout.daedalus.outcomes import OutcomeLabel


# ---------------------------------------------------------------------------
# Constants: bounded model, bounded memory, stable feature surface
# ---------------------------------------------------------------------------

CHANNEL_MAX: Dict[str, int] = {
    "industry_fit": 20,
    "company_size_fit": 25,
    "decision_maker_seniority": 20,
    "budget_likelihood_score": 20,
    "growth_signals": 15,
}

FEATURE_NAMES: Tuple[str, ...] = (
    "industry_fit",
    "company_size_fit",
    "decision_maker_seniority",
    "budget_likelihood_score",
    "growth_signals",
    "lead_score",
    "confidence",
    "verified_density",
    "weak_density",
    "missing_density",
    "thin_scrape",
    "decision_maker",
)

MODEL_WEIGHT_FLOOR = -3.0
MODEL_WEIGHT_CEIL = 3.0
MODEL_BIAS_FLOOR = -4.0
MODEL_BIAS_CEIL = 4.0
DEFAULT_LEARNING_RATE = 0.18
MAX_EPISODES = 500
MAX_ADAPTIVE_DELTA = 12


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------

def _now_utc_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


def _sigmoid(x: float) -> float:
    # Stable enough for our bounded logits.
    if x >= 0:
        z = math.exp(-x)
        return 1.0 / (1.0 + z)
    z = math.exp(x)
    return z / (1.0 + z)


def _safe_int(v: Any, default: int = 0) -> int:
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


def _safe_float(v: Any, default: float = 0.0) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _bounded_round_probability(p: float) -> float:
    return round(_clamp(float(p), 0.01, 0.99), 4)


# ---------------------------------------------------------------------------
# Opaque identity: no PII in persisted state
# ---------------------------------------------------------------------------

def opaque_lead_id(inp: ScrapedInput) -> str:
    """Return a stable opaque identifier for a lead input.

    The digest is computed from the canonical input, but only the digest is
    ever stored. Raw contact fields and scraped content never enter state.
    """
    content_digest = hashlib.blake2b(
        (inp.website_content or "").encode("utf-8", errors="ignore"),
        digest_size=16,
    ).hexdigest()
    canonical = {
        "name": inp.name or "",
        "title": inp.title or "",
        "company": inp.company or "",
        "website_url": inp.website_url or "",
        "content_digest": content_digest,
    }
    blob = json.dumps(canonical, sort_keys=True, ensure_ascii=False).encode("utf-8")
    return "lead_" + hashlib.blake2b(blob, digest_size=12).hexdigest()


def input_fingerprint(inp: ScrapedInput) -> str:
    """Return a second opaque digest useful for audit grouping."""
    canonical = {
        "lead": opaque_lead_id(inp),
        "title_len": len(inp.title or ""),
        "url_len": len(inp.website_url or ""),
        "content_len": len(inp.website_content or ""),
    }
    blob = json.dumps(canonical, sort_keys=True).encode("utf-8")
    return "fp_" + hashlib.blake2b(blob, digest_size=10).hexdigest()


# ---------------------------------------------------------------------------
# State shape
# ---------------------------------------------------------------------------

def default_prediction_state() -> Dict[str, Any]:
    """Return the prediction-state bucket.

    This lives under state["prediction_state"]. It is deliberately compact,
    bounded, and PII-safe.
    """
    return {
        "model": {
            "bias": 0.0,
            "weights": {name: 0.0 for name in FEATURE_NAMES},
            "learning_rate": DEFAULT_LEARNING_RATE,
            "updates_seen": 0,
            "last_update_at": None,
        },
        "episodes": {},
        "episode_order": [],
        "outcomes": {},
        "tool_reliability": {
            "website_scrape": _empty_tool_bucket(),
            "enrichment": _empty_tool_bucket(),
        },
        "self_model": {
            "calls_seen": 0,
            "outcomes_seen": 0,
            "last_brier": None,
            "rolling_brier": None,
            "policy_version": "predictive-v0",
            "last_prediction_at": None,
            "last_outcome_at": None,
        },
    }


def _empty_tool_bucket() -> Dict[str, Any]:
    return {
        "calls_seen": 0,
        "available_count": 0,
        "yield_total": 0.0,
        "positive_outcomes": 0,
        "negative_outcomes": 0,
        "nurture_outcomes": 0,
        "utility_ema": None,
    }


def ensure_prediction_state(state: MutableMapping[str, Any]) -> MutableMapping[str, Any]:
    """Ensure state has a valid prediction_state bucket and return it."""
    if not isinstance(state.get("prediction_state"), dict):
        state["prediction_state"] = default_prediction_state()
    ps = state["prediction_state"]

    # Defensive shape repair without throwing away useful counters.
    default = default_prediction_state()
    for key, value in default.items():
        if key not in ps or not isinstance(ps.get(key), type(value)):
            ps[key] = copy.deepcopy(value)

    model = ps.setdefault("model", copy.deepcopy(default["model"]))
    model.setdefault("bias", 0.0)
    model.setdefault("weights", {})
    model.setdefault("learning_rate", DEFAULT_LEARNING_RATE)
    model.setdefault("updates_seen", 0)
    model.setdefault("last_update_at", None)
    weights = model.setdefault("weights", {})
    for name in FEATURE_NAMES:
        weights.setdefault(name, 0.0)

    tools = ps.setdefault("tool_reliability", {})
    for name in ("website_scrape", "enrichment"):
        if not isinstance(tools.get(name), dict):
            tools[name] = _empty_tool_bucket()
        else:
            bucket = tools[name]
            for k, v in _empty_tool_bucket().items():
                bucket.setdefault(k, v)

    sm = ps.setdefault("self_model", {})
    for k, v in default["self_model"].items():
        sm.setdefault(k, v)

    ps.setdefault("episodes", {})
    ps.setdefault("episode_order", [])
    ps.setdefault("outcomes", {})
    return ps


# ---------------------------------------------------------------------------
# Feature extraction + prediction
# ---------------------------------------------------------------------------

def features_from_payload(payload: Mapping[str, Any]) -> Dict[str, float]:
    """Extract bounded numeric features from the public JSON payload."""
    evidence = payload.get("signal_evidence", {}) or {}
    verified = evidence.get("verified", []) or []
    weak = evidence.get("weak", []) or []
    missing = evidence.get("missing", []) or []
    denom = max(1.0, float(len(verified) + len(weak) + len(missing)))

    features = {
        "industry_fit": _safe_int(payload.get("industry_fit")) / CHANNEL_MAX["industry_fit"],
        "company_size_fit": _safe_int(payload.get("company_size_fit")) / CHANNEL_MAX["company_size_fit"],
        "decision_maker_seniority": _safe_int(payload.get("decision_maker_seniority")) / CHANNEL_MAX["decision_maker_seniority"],
        "budget_likelihood_score": _safe_int(payload.get("budget_likelihood_score")) / CHANNEL_MAX["budget_likelihood_score"],
        "growth_signals": _safe_int(payload.get("growth_signals")) / CHANNEL_MAX["growth_signals"],
        "lead_score": _safe_int(payload.get("lead_score")) / 100.0,
        "confidence": _safe_float(evidence.get("confidence")),
        "verified_density": len(verified) / denom,
        "weak_density": len(weak) / denom,
        "missing_density": len(missing) / denom,
        "thin_scrape": 1.0 if bool(evidence.get("thin_scrape")) else 0.0,
        "decision_maker": 1.0 if bool(payload.get("decision_maker")) else 0.0,
    }
    return {k: round(_clamp(v, 0.0, 1.0), 6) for k, v in features.items()}


@dataclass(frozen=True)
class PredictionReceipt:
    p_quality: float
    quality_score: int
    base_logit: float
    adaptive_delta: float
    model_updates_seen: int
    features: Dict[str, float]

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def predict_from_state(payload: Mapping[str, Any], state: MutableMapping[str, Any]) -> PredictionReceipt:
    """Predict lead quality probability using current adaptive state."""
    ps = ensure_prediction_state(state)
    model = ps["model"]
    features = features_from_payload(payload)

    lead_score = _safe_int(payload.get("lead_score"))
    # Baseline: rubric score translated into a probability-like logit.
    # 50 ≈ 0.5, 75 ≈ 0.80, 25 ≈ 0.20 before outcome learning.
    base_logit = (lead_score - 50.0) / 17.5
    weights = model.get("weights", {}) or {}
    adaptive_delta = _safe_float(model.get("bias"))
    for name, value in features.items():
        adaptive_delta += _safe_float(weights.get(name)) * value

    p = _bounded_round_probability(_sigmoid(base_logit + adaptive_delta))
    return PredictionReceipt(
        p_quality=p,
        quality_score=int(round(p * 100)),
        base_logit=round(base_logit, 6),
        adaptive_delta=round(adaptive_delta, 6),
        model_updates_seen=_safe_int(model.get("updates_seen")),
        features=features,
    )


# ---------------------------------------------------------------------------
# Episode memory + self-model
# ---------------------------------------------------------------------------

def _update_tool_bucket(bucket: MutableMapping[str, Any], *, available: bool, yield_value: float) -> None:
    bucket["calls_seen"] = _safe_int(bucket.get("calls_seen")) + 1
    if available:
        bucket["available_count"] = _safe_int(bucket.get("available_count")) + 1
    bucket["yield_total"] = round(_safe_float(bucket.get("yield_total")) + float(yield_value), 4)


def _trim_episodes(ps: MutableMapping[str, Any], max_episodes: int = MAX_EPISODES) -> None:
    episodes = ps.get("episodes", {})
    order = ps.get("episode_order", [])
    if not isinstance(episodes, dict) or not isinstance(order, list):
        return
    while len(order) > max_episodes:
        old = order.pop(0)
        episodes.pop(old, None)


def store_episode(
    state: MutableMapping[str, Any],
    *,
    lead_id: str,
    input_fp: str,
    payload: Mapping[str, Any],
    prediction: PredictionReceipt,
    text_chars: int,
    enrichment_available: bool,
    timestamp_utc: Optional[str] = None,
) -> Dict[str, Any]:
    """Persist one compact scoring episode and update the self-model.

    No raw text, contact name, company name, URL, or rationale is stored.
    """
    ps = ensure_prediction_state(state)
    ts = timestamp_utc or _now_utc_iso()
    evidence = payload.get("signal_evidence", {}) or {}
    tools_used = ["website_scrape"]
    if enrichment_available:
        tools_used.append("enrichment")

    episode = {
        "lead_id": lead_id,
        "input_fp": input_fp,
        "at": ts,
        "features": prediction.features,
        "scores": {k: _safe_int(payload.get(k)) for k in CHANNEL_MAX},
        "lead_score": _safe_int(payload.get("lead_score")),
        "confidence": _safe_float(evidence.get("confidence")),
        "prediction": {
            "p_quality": prediction.p_quality,
            "quality_score": prediction.quality_score,
            "model_updates_seen": prediction.model_updates_seen,
        },
        "tools_used": tools_used,
        "self_audit_flags": _self_audit_flags(payload, prediction, text_chars),
    }

    episodes = ps.setdefault("episodes", {})
    order = ps.setdefault("episode_order", [])
    if lead_id not in episodes:
        order.append(lead_id)
    episodes[lead_id] = episode
    _trim_episodes(ps)

    tools = ps.setdefault("tool_reliability", {})
    _update_tool_bucket(
        tools.setdefault("website_scrape", _empty_tool_bucket()),
        available=text_chars > 0,
        yield_value=min(1.0, max(0.0, text_chars / 2000.0)),
    )
    _update_tool_bucket(
        tools.setdefault("enrichment", _empty_tool_bucket()),
        available=enrichment_available,
        yield_value=1.0 if enrichment_available else 0.0,
    )

    sm = ps.setdefault("self_model", {})
    sm["calls_seen"] = _safe_int(sm.get("calls_seen")) + 1
    sm["last_prediction_at"] = ts
    return episode


def _self_audit_flags(payload: Mapping[str, Any], prediction: PredictionReceipt, text_chars: int) -> List[str]:
    flags: List[str] = []
    evidence = payload.get("signal_evidence", {}) or {}
    lead_score = _safe_int(payload.get("lead_score"))
    confidence = _safe_float(evidence.get("confidence"))
    thin = bool(evidence.get("thin_scrape"))

    if thin and lead_score >= 60:
        flags.append("thin_scrape_high_score")
    if confidence < 0.3 and lead_score >= 50:
        flags.append("low_confidence_high_score")
    if prediction.model_updates_seen > 0:
        raw_prob_score = prediction.quality_score
        if abs(raw_prob_score - lead_score) >= 25:
            flags.append("model_rubric_disagreement")
    if text_chars < 1:
        flags.append("empty_scrape")
    return flags


# ---------------------------------------------------------------------------
# Outcome adaptation
# ---------------------------------------------------------------------------

def _target_for_label(label: OutcomeLabel | str) -> float:
    label = OutcomeLabel(label)
    if label is OutcomeLabel.WON:
        return 1.0
    if label is OutcomeLabel.LOST:
        return 0.0
    return 0.5


def _update_tool_outcomes(ps: MutableMapping[str, Any], episode: Mapping[str, Any], target: float, error: float) -> None:
    tools = ps.setdefault("tool_reliability", {})
    for name in episode.get("tools_used", []) or []:
        bucket = tools.setdefault(name, _empty_tool_bucket())
        if target >= 0.75:
            bucket["positive_outcomes"] = _safe_int(bucket.get("positive_outcomes")) + 1
        elif target <= 0.25:
            bucket["negative_outcomes"] = _safe_int(bucket.get("negative_outcomes")) + 1
        else:
            bucket["nurture_outcomes"] = _safe_int(bucket.get("nurture_outcomes")) + 1
        current = bucket.get("utility_ema")
        signal = abs(float(error))
        if current is None:
            bucket["utility_ema"] = round(signal, 4)
        else:
            bucket["utility_ema"] = round(0.8 * _safe_float(current) + 0.2 * signal, 4)


def apply_outcome_to_state(
    state: MutableMapping[str, Any],
    *,
    lead_id: str,
    label: OutcomeLabel | str,
    timestamp_utc: Optional[str] = None,
) -> Dict[str, Any]:
    """Apply one outcome label to state and return a receipt.

    Idempotent for the same lead_id + label: if the outcome already exists,
    no second gradient update is applied.
    """
    ps = ensure_prediction_state(state)
    label = OutcomeLabel(label)
    ts = timestamp_utc or _now_utc_iso()
    outcomes = ps.setdefault("outcomes", {})
    existing = outcomes.get(lead_id)
    if isinstance(existing, dict) and existing.get("label") == label.value:
        return {
            "lead_id": lead_id,
            "label": label.value,
            "status": "already_recorded",
            "update_applied": False,
            "model_updates_seen": _safe_int(ps["model"].get("updates_seen")),
        }

    episode = (ps.get("episodes", {}) or {}).get(lead_id)
    if not isinstance(episode, dict):
        # Store the outcome, but do not update model without emission features.
        outcomes[lead_id] = {
            "label": label.value,
            "target": _target_for_label(label),
            "at": ts,
            "status": "recorded_without_episode",
        }
        ps.setdefault("self_model", {})["last_outcome_at"] = ts
        return {
            "lead_id": lead_id,
            "label": label.value,
            "status": "recorded_without_episode",
            "update_applied": False,
            "model_updates_seen": _safe_int(ps["model"].get("updates_seen")),
        }

    target = _target_for_label(label)
    p_before = _safe_float((episode.get("prediction", {}) or {}).get("p_quality"), 0.5)
    features = episode.get("features", {}) or {}
    error = target - p_before
    brier = round((target - p_before) ** 2, 6)

    model = ps.setdefault("model", {})
    lr = _safe_float(model.get("learning_rate"), DEFAULT_LEARNING_RATE)
    model["bias"] = round(_clamp(_safe_float(model.get("bias")) + lr * error, MODEL_BIAS_FLOOR, MODEL_BIAS_CEIL), 6)
    weights = model.setdefault("weights", {})
    for name in FEATURE_NAMES:
        value = _safe_float(features.get(name))
        weights[name] = round(
            _clamp(_safe_float(weights.get(name)) + lr * error * value, MODEL_WEIGHT_FLOOR, MODEL_WEIGHT_CEIL),
            6,
        )
    model["updates_seen"] = _safe_int(model.get("updates_seen")) + 1
    model["last_update_at"] = ts

    outcomes[lead_id] = {
        "label": label.value,
        "target": target,
        "at": ts,
        "p_quality_at_emission": p_before,
        "lead_score_at_emission": _safe_int(episode.get("lead_score")),
        "brier": brier,
        "status": "applied",
    }

    sm = ps.setdefault("self_model", {})
    sm["outcomes_seen"] = _safe_int(sm.get("outcomes_seen")) + 1
    sm["last_brier"] = brier
    prev = sm.get("rolling_brier")
    sm["rolling_brier"] = brier if prev is None else round(0.9 * _safe_float(prev) + 0.1 * brier, 6)
    sm["last_outcome_at"] = ts
    sm["policy_version"] = f"predictive-v{model['updates_seen']}"

    _update_tool_outcomes(ps, episode, target, error)

    return {
        "lead_id": lead_id,
        "label": label.value,
        "target": target,
        "status": "applied",
        "update_applied": True,
        "p_quality_at_emission": p_before,
        "brier": brier,
        "model_updates_seen": model["updates_seen"],
    }


def record_outcome(
    state_provider: Any,
    *,
    lead_id: str,
    label: OutcomeLabel | str,
) -> Dict[str, Any]:
    """Load state, apply an outcome, save state, return receipt."""
    with state_provider.transaction():
        state = state_provider.load()
        receipt = apply_outcome_to_state(state, lead_id=lead_id, label=label)
        state_provider.save(state)
    return receipt


# ---------------------------------------------------------------------------
# Optional adaptive output calibration
# ---------------------------------------------------------------------------

def apply_adaptive_output(
    payload: Mapping[str, Any],
    state: MutableMapping[str, Any],
    *,
    max_delta: int = MAX_ADAPTIVE_DELTA,
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """Return a payload with bounded outcome-calibrated channel scores.

    The public JSON shape is unchanged. The channel scores stay within their
    rubric maxima and lead_score remains the exact sum of the channels.

    No change is made until at least one outcome update has trained the model.
    """
    ps = ensure_prediction_state(state)
    if _safe_int((ps.get("model", {}) or {}).get("updates_seen")) <= 0:
        return dict(payload), {"applied": False, "reason": "no_outcome_updates"}

    prediction = predict_from_state(payload, state)
    current = _safe_int(payload.get("lead_score"))
    target = prediction.quality_score
    raw_delta = target - current
    if raw_delta == 0:
        return dict(payload), {
            "applied": False,
            "reason": "already_calibrated",
            "p_quality": prediction.p_quality,
        }

    bounded_delta = int(max(-max_delta, min(max_delta, raw_delta)))
    adjusted = copy.deepcopy(dict(payload))
    applied_delta = _redistribute_delta(adjusted, bounded_delta, ps)
    adjusted["lead_score"] = sum(_safe_int(adjusted.get(k)) for k in CHANNEL_MAX)

    # Keep derived fields aligned with score channels.
    adjusted["budget_likelihood"] = _budget_likelihood_category(_safe_int(adjusted.get("budget_likelihood_score")))
    adjusted["decision_maker"] = _safe_int(adjusted.get("decision_maker_seniority")) >= 15

    if applied_delta != 0:
        note = (
            f" Adaptive prediction p_quality={prediction.p_quality:.2f}; "
            f"calibrated score delta={applied_delta:+d} after "
            f"{prediction.model_updates_seen} outcome update(s)."
        )
        adjusted["rationale"] = (adjusted.get("rationale", "") or "") + note

    return adjusted, {
        "applied": applied_delta != 0,
        "requested_delta": bounded_delta,
        "applied_delta": applied_delta,
        "p_quality": prediction.p_quality,
        "quality_score": prediction.quality_score,
        "model_updates_seen": prediction.model_updates_seen,
    }


def _budget_likelihood_category(budget_score: int) -> str:
    if budget_score >= 15:
        return "high"
    if budget_score >= 8:
        return "medium"
    return "low"


def _redistribute_delta(adjusted: MutableMapping[str, Any], delta: int, ps: Mapping[str, Any]) -> int:
    if delta == 0:
        return 0

    weights = ((ps.get("model", {}) or {}).get("weights", {}) or {})
    channels = list(CHANNEL_MAX.keys())
    if delta > 0:
        # Prefer evidence-backed channels with positive learned weight, then capacity.
        channels.sort(
            key=lambda k: (
                _safe_float(weights.get(k)),
                CHANNEL_MAX[k] - _safe_int(adjusted.get(k)),
                _safe_int(adjusted.get(k)) > 0,
            ),
            reverse=True,
        )
    else:
        # Prefer channels with negative learned weight, then channels carrying points.
        channels.sort(
            key=lambda k: (
                -_safe_float(weights.get(k)),
                _safe_int(adjusted.get(k)),
            ),
            reverse=True,
        )

    remaining = abs(delta)
    sign = 1 if delta > 0 else -1
    applied = 0
    while remaining > 0:
        moved_this_round = False
        for key in channels:
            cur = _safe_int(adjusted.get(key))
            if sign > 0:
                # Do not invent a channel from zero evidence. Positive calibration
                # can only lift channels that already had some support.
                if cur <= 0 or cur >= CHANNEL_MAX[key]:
                    continue
                adjusted[key] = cur + 1
            else:
                if cur <= 0:
                    continue
                adjusted[key] = cur - 1
            remaining -= 1
            applied += sign
            moved_this_round = True
            if remaining <= 0:
                break
        if not moved_this_round:
            break
    return applied


# ---------------------------------------------------------------------------
# Provider-level helpers used by CLI
# ---------------------------------------------------------------------------

def remember_prediction_for_input(
    state_provider: Any,
    *,
    inp: ScrapedInput,
    payload: Mapping[str, Any],
    text_chars: int,
    enrichment_available: bool,
) -> Dict[str, Any]:
    """Load state, persist episode, save state, return a compact receipt."""
    lead_id = opaque_lead_id(inp)
    fp = input_fingerprint(inp)
    with state_provider.transaction():
        state = state_provider.load()
        prediction = predict_from_state(payload, state)
        episode = store_episode(
            state,
            lead_id=lead_id,
            input_fp=fp,
            payload=payload,
            prediction=prediction,
            text_chars=text_chars,
            enrichment_available=enrichment_available,
        )
        state_provider.save(state)
    return {
        "lead_id": lead_id,
        "prediction": episode["prediction"],
        "self_audit_flags": episode.get("self_audit_flags", []),
    }


def summarize_self_model(state: MutableMapping[str, Any]) -> Dict[str, Any]:
    ps = ensure_prediction_state(state)
    model = ps.get("model", {}) or {}
    sm = ps.get("self_model", {}) or {}
    return {
        "calls_seen": _safe_int(sm.get("calls_seen")),
        "outcomes_seen": _safe_int(sm.get("outcomes_seen")),
        "model_updates_seen": _safe_int(model.get("updates_seen")),
        "rolling_brier": sm.get("rolling_brier"),
        "last_brier": sm.get("last_brier"),
        "policy_version": sm.get("policy_version"),
        "episodes_stored": len(ps.get("episodes", {}) or {}),
        "tool_reliability": ps.get("tool_reliability", {}),
    }


__all__ = [
    "CHANNEL_MAX",
    "FEATURE_NAMES",
    "PredictionReceipt",
    "opaque_lead_id",
    "input_fingerprint",
    "default_prediction_state",
    "ensure_prediction_state",
    "features_from_payload",
    "predict_from_state",
    "store_episode",
    "apply_outcome_to_state",
    "record_outcome",
    "apply_adaptive_output",
    "remember_prediction_for_input",
    "summarize_self_model",
]
