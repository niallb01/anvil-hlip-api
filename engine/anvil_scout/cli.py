"""Anvil-Scout CLI runner.

Reads ScrapedInput JSON from stdin, writes ScoredOutput JSON to stdout.

Usage
-----
    py -3.11 -m anvil_scout < input.json > output.json
    py -3.11 -m anvil_scout --help

Pipeline (5 stages + 2 boundary audits):
    Input adapter → Detectors → Classifier → Scorer (with context modifiers)
    → Law-0 emission wrapper → Schema validator → JSON output

Output discipline ("doesn't lie"):
    - Law-0 wrapper strips any verified/weak claim lacking a span pointer
    - Schema validator catches shape/type/range/enum drift at runtime
    - Both annotate the rationale on violation; the system never crashes
      (strip-don't-raise)

Enrichment is a partner-replaceable seam (see PARTNER_NOTES.md). The
default StubProvider returns `available=False`; the CLI surfaces the
status in the rationale.

v0.1.0-TB10 — first partner release. Rubric uncalibrated; confidence
hard-capped at 0.8 to disclose this honestly.
"""

from __future__ import annotations

import io
import json
import sys
import argparse
import importlib

from anvil_scout import (
    ScrapedInput,
    ScoredOutput,
    SignalEvidence,
    __version__,
)
from anvil_scout.core.input_adapter import prepare_text
from anvil_scout.core.detectors import run_all_detectors, hit_counts
from anvil_scout.core.classifier import classify_signals, compute_signal_density
from anvil_scout.core.scorer import (
    budget_likelihood_category,
    decision_maker_flag,
    score_all_channels,
)
from anvil_scout.core.law_zero import enforce_law_zero
from anvil_scout.core.schema_validator import validate_output
from anvil_scout.core.enrichment import get_provider


_HELP = """\
Anvil-Scout CLI {version}

Reads ScrapedInput JSON from stdin, writes ScoredOutput JSON to stdout.

Input shape:
  {{
    "name": "...",
    "title": "...",
    "company": "...",
    "website_url": "https://...",
    "website_content": "..."   (text or HTML; may be empty if URL provided)
  }}

Output shape: see SCHEMA.json in the repo root.

Examples (Windows):
  type input.json | py -3.11 -m anvil_scout > output.json
  py -3.11 -m anvil_scout --help

See README.md for the integration guide,
    PARTNER_NOTES.md for enrichment-provider wiring,
    LAWS.md for the design principles.
"""


def _emit_stub_output(prepared, inp: ScrapedInput, adapters=None) -> ScoredOutput:
    """Build the TB-05 output.

    Pipeline at this point:
      1. Input adapter prepared the text.
      2. Structural detectors produced spans.
      2a. (TB-14) Optional adapter filter — spans shorter than the
          per-detector min_span_length adapter are filtered out.
          With default adapters (or adapters=None) this is a no-op,
          preserving v0.1.0 behavior bit-identically (JB-14-1).
      3. Classifier produced VERIFIED / WEAK / MISSING lists.
      4. Scorer produced 5 channel scores with Law-II multiplicative gates.
      5. Law-0 emission wrapper audited the assembled output and stripped
         any claim lacking a backing span pointer.

    Schema fields populated:
      industry_fit, company_size_fit, decision_maker_seniority,
      budget_likelihood_score, growth_signals  (channel scores)
      lead_score                              (sum)
      budget_likelihood                       ("high"|"medium"|"low" band)
      decision_maker                          (bool, seniority >= 15)
      pain_points                             (empty — no fabrication)
      rationale                               (built free text)
      signal_evidence                         (from classifier, audited)

    Full JSON schema validation (TB-06) still pending.

    Args:
        prepared: PreparedText from input_adapter.
        inp: ScrapedInput from caller.
        adapters: Optional dict {detector_name -> DetectorAdapter}. When
            provided, spans are filtered by per-detector min_span_length
            BEFORE the classifier sees them. Default None → no filtering.
    """
    # Run detectors over cleaned text.
    spans = run_all_detectors(prepared.text)

    # TB-14: apply adapter filter if provided. Lazy-imported to keep
    # daedalus out of the partner-facing import path.
    if adapters is not None:
        try:
            from anvil_scout.daedalus.adapters import apply_adapters_to_spans
            spans = apply_adapters_to_spans(spans, adapters)
        except Exception:
            # Strip-don't-raise: adapter failure falls back to no filter.
            pass

    # ── TB-15/TB-16: fetch enrichment EARLY so its spans can flow through
    # the same classifier / Law-0 / scorer pipeline as detector spans. When
    # the default StubProvider is in use, all results report available=False
    # and enrichment_to_spans returns [] for each → byte-identical to
    # v0.1.0/TB14A.
    #
    # TB-16: dispatch to ALL configured providers via the router; collect
    # ALL results; produce one span set per result (audit trail preserves
    # per-provider provenance); merge results into a single enrichment
    # object for the scorer (first non-None per field wins).
    #
    # Strip-don't-raise: router-level + per-provider exception handling
    # already lives inside EnrichmentRouter.fetch_all; outer guard here is
    # belt-and-braces against any unexpected import-time failure.
    enrichment_results = []
    try:
        from anvil_scout.core.enrichment import get_providers
        from anvil_scout.core.enrichment_router import (
            EnrichmentRouter, merge_results,
        )
        router = EnrichmentRouter(providers=get_providers())
        enrichment_results = router.fetch_all(
            company=inp.company, website_url=inp.website_url,
        )
        enrichment = merge_results(enrichment_results)
    except Exception as _router_exc:
        from anvil_scout.core.enrichment import EnrichmentResult
        enrichment = EnrichmentResult(
            available=False,
            reason=f"router error: {type(_router_exc).__name__}",
        )

    try:
        from anvil_scout.core.enrichment import enrichment_to_spans
        enrichment_spans = []
        # TB-16: spans from EVERY result, not just merged.
        # Each enrichment_to_spans call preserves the source provider_id.
        for r in enrichment_results:
            enrichment_spans.extend(enrichment_to_spans(r))
    except Exception:
        enrichment_spans = []   # strip-don't-raise

    spans = list(spans) + list(enrichment_spans)

    counts = hit_counts(spans)

    # Classifier: spans → V/W/M.
    verified, weak, missing = classify_signals(spans)

    # Scorer: spans + title + enrichment → 5 channel scores with Law-II gates.
    # TB-09: prepared.text passed through for context modifiers.
    # TB-15: enrichment passed through for company_size_fit + seniority lifts.
    channel_scores = score_all_channels(spans, inp, prepared.text, enrichment)

    signal_density = compute_signal_density(
        thin_scrape=prepared.thin_scrape,
        verified_count=len(verified),
        weak_count=len(weak),
        missing_count=len(missing),
    )

    out = ScoredOutput()

    # ── populate channel scores (Law-II gated via .final_score) ──
    out.industry_fit = channel_scores["industry_fit"].final_score
    out.company_size_fit = channel_scores["company_size_fit"].final_score
    out.decision_maker_seniority = channel_scores["decision_maker_seniority"].final_score
    out.budget_likelihood_score = channel_scores["budget_likelihood_score"].final_score
    out.growth_signals = channel_scores["growth_signals"].final_score

    out.lead_score = (
        out.industry_fit
        + out.company_size_fit
        + out.decision_maker_seniority
        + out.budget_likelihood_score
        + out.growth_signals
    )

    # ── derived flags ──
    out.budget_likelihood = budget_likelihood_category(out.budget_likelihood_score)
    out.decision_maker = decision_maker_flag(out.decision_maker_seniority)

    # ── pain points: explicitly empty; no LLM, no fabrication ──
    out.pain_points = []

    # ── signal evidence ──
    out.signal_evidence = SignalEvidence(
        verified=verified,
        weak=weak,
        missing=missing,
        signal_density=signal_density,
        thin_scrape=prepared.thin_scrape,
    )

    # ── Law-0 emission wrapper: audit + strip ungrounded ──
    out, violations = enforce_law_zero(out, spans, prepared.thin_scrape)

    # Internal-only hint used by the predictive memory tail. It is not part
    # of ScoredOutput.to_dict(), so the public schema stays unchanged.
    try:
        out._enrichment_available = bool(enrichment.available)
    except Exception:
        pass

    # ── rationale: concise summary of pipeline + scores ──
    # Violations note is appended ONLY if violations > 0 (JB-05-2).
    rationale = (
        f"Anvil-Scout v0.1.0-TB10. Input adapter prepared {prepared.char_count} chars "
        f"(source_mode={prepared.source_mode}, "
        f"trafilatura_used={prepared.trafilatura_used}, "
        f"thin_scrape={prepared.thin_scrape}). "
        f"Detectors fired: testimony={counts['testimony']}, "
        f"quantity={counts['quantity']}, "
        f"causal={counts['causal']}, "
        f"missing={counts['missing']}. "
        f"Classifier emitted: verified={len(out.signal_evidence.verified)}, "
        f"weak={len(out.signal_evidence.weak)}, "
        f"missing={len(out.signal_evidence.missing)}. "
        f"Channel scores: industry_fit={out.industry_fit}, "
        f"company_size_fit={out.company_size_fit}, "
        f"decision_maker_seniority={out.decision_maker_seniority}, "
        f"budget_likelihood_score={out.budget_likelihood_score}, "
        f"growth_signals={out.growth_signals}. "
        f"lead_score={out.lead_score}/100. "
        f"Signal_density={out.signal_evidence.signal_density} (capped at 0.8 — structural ratio, NOT a probability — see predicted_quality). "
        f"Enrichment: {enrichment.short_summary()}. "
        "pain_points left empty (no LLM-narrative module in v1)."
    )
    if violations > 0:
        rationale += f" [Law-0: stripped {violations} ungrounded claim(s) at emission boundary]"
    out.rationale = rationale

    return out


def run_once(
    raw_in: str,
    *,
    state_provider=None,
    mode=None,
    adaptive_output: bool = False,
) -> str:
    """Pure function: JSON string in, JSON string out. Used by tests.

    Optional TB-13 observability tail:
        state_provider:  A daedalus.state.StateProvider implementation, or None.
                         Default None → no state path → bit-identical to v0.1.0.
        mode:            A daedalus.modes.ExecutionMode, or None.
                         Default None → treated as SNAPSHOT → no state writes.
        adaptive_output: True applies outcome-trained calibration to the
                         emitted channel scores. Default False preserves
                         v0.1.0 byte-identical output.

    Observability and episode memory run ONLY when state_provider is non-None
    AND mode is LEARNING. SNAPSHOT mode and default config skip writes.
    """
    try:
        d = json.loads(raw_in)
    except json.JSONDecodeError as e:
        return json.dumps({"error": f"invalid JSON input: {e}"})

    if not isinstance(d, dict):
        return json.dumps({"error": "input must be a JSON object"})

    inp = ScrapedInput.from_dict(d)
    prepared = prepare_text(
        website_content=inp.website_content,
        website_url=inp.website_url,
        fetch_if_empty=False,  # TB-07 default: no implicit network calls
    )

    # TB-14: load adapters when a state provider is given. SNAPSHOT mode
    # WITH a state provider reads but never writes adapters (freeze
    # invariant — JB-14-7). Default config (state_provider=None) skips
    # this entirely → bit-identical v0.1.0 behavior.
    adapters = None
    state_snapshot = None
    if state_provider is not None:
        try:
            from anvil_scout.daedalus.adapters import adapters_from_state
            state_snapshot = state_provider.load()
            adapters = adapters_from_state(state_snapshot)
        except Exception:
            # Strip-don't-raise: adapter load failure → no filter.
            adapters = None

    out = _emit_stub_output(prepared, inp, adapters=adapters)

    # TB-06: runtime schema validation against SCHEMA.json. Strip-don't-raise.
    payload = out.to_dict()

    # ── TB-17A: predicted_quality from the existing TB-P2 logistic ──
    # Sourced from daedalus/predictive.py:predict_from_state which:
    #   - with no state / no outcomes → sigmoid((lead_score - 50)/17.5)
    #     (a soft transform of the rubric: 0.85 @ 80, 0.50 @ 50, 0.15 @ 20)
    #   - with outcomes recorded (TB-P2 LEARNING mode) → calibrated probability
    # Lazy import keeps daedalus off the default partner-facing import path.
    # Strip-don't-raise: any failure leaves the dataclass default 0.5.
    pq_updates_seen = 0
    try:
        from anvil_scout.daedalus.predictive import predict_from_state
        state_for_pred = state_snapshot if state_snapshot is not None else {}
        receipt = predict_from_state(payload, state_for_pred)
        payload["predicted_quality"] = round(float(receipt.p_quality), 4)
        pq_updates_seen = int(receipt.model_updates_seen)
    except Exception:
        payload.setdefault("predicted_quality", 0.5)

    # Annotate rationale with a brief predicted_quality note. Whether the
    # model is calibrated (outcomes seen > 0) or rubric-derived is part of
    # the honest disclosure.
    pq_value = payload.get("predicted_quality", 0.5)
    if pq_updates_seen > 0:
        pq_note = (
            f" predicted_quality={pq_value:.2f} "
            f"(calibrated against {pq_updates_seen} outcome update(s))."
        )
    else:
        pq_note = (
            f" predicted_quality={pq_value:.2f} "
            f"(rubric-derived; no outcome calibration yet)."
        )
    payload["rationale"] = (payload.get("rationale", "") or "") + pq_note

    # TB-P2: optional predictive calibration. This is explicit and opt-in;
    # default output remains byte-identical to v0.1.0/TB-14A.
    if adaptive_output and state_provider is not None:
        try:
            from anvil_scout.daedalus.predictive import apply_adaptive_output
            if state_snapshot is None:
                state_snapshot = state_provider.load()
            payload, _adaptive_receipt = apply_adaptive_output(payload, state_snapshot)
        except Exception:
            pass  # strip-don't-raise: adaptive calibration cannot break JSON output

    is_valid, errors = validate_output(payload)
    if not is_valid:
        # Surface in rationale; emit anyway. The schema violation is auditable
        # but the system stays robust.
        note = f" [Schema-violation: {'; '.join(errors[:3])}{'…' if len(errors) > 3 else ''}]"
        payload["rationale"] = payload.get("rationale", "") + note

    # TB-13 observability tail — runs ONLY in LEARNING mode with a real
    # state provider. Default config skips this entirely.
    # Strip-don't-raise: any exception here is swallowed; partner output
    # is never affected.
    if state_provider is not None and mode is not None:
        try:
            from anvil_scout.daedalus.modes import ExecutionMode as _Mode
            if isinstance(mode, _Mode) and mode.allows_state_writes():
                _run_observability_tail(prepared.text, out, payload, state_provider)
                _run_prediction_tail(inp, prepared, out, payload, state_provider)
        except Exception:
            pass  # strip-don't-raise

    return json.dumps(payload, indent=2, ensure_ascii=False)


def _run_observability_tail(
    prepared_text: str,
    out_after_law0,
    payload: dict,
    state_provider,
) -> None:
    """Internal: derive detector receipts from a pipeline trace and persist.

    Called only when state_provider is non-None AND mode is LEARNING.
    Re-runs the detector + classifier path on the same text to capture
    pre-Law-0 lists, then compares to post-Law-0 lists from the output.

    This re-run is cheap (regex + classifier, no I/O) and avoids changing
    the main pipeline's data flow.

    Strip-don't-raise: caller wraps this in try/except.
    """
    from anvil_scout.daedalus.observability import (
        observe_detectors,
        aggregate_into_state,
    )

    # Re-derive pre-strip classified lists (cheap — pure functions).
    spans = run_all_detectors(prepared_text)
    verified_pre, weak_pre, missing_pre = classify_signals(spans)

    # Post-Law-0 lists are what's in the emitted SignalEvidence.
    verified_post = list(out_after_law0.signal_evidence.verified)
    weak_post = list(out_after_law0.signal_evidence.weak)
    missing_post = list(out_after_law0.signal_evidence.missing)

    receipts = observe_detectors(
        text=prepared_text,
        spans=spans,
        verified_pre=verified_pre,
        weak_pre=weak_pre,
        missing_pre=missing_pre,
        verified_post=verified_post,
        weak_post=weak_post,
        missing_post=missing_post,
    )

    # TB-14: compute coherence flags from receipts BEFORE aggregating —
    # the per-call strip rate is what drives adapter updates. After
    # aggregation the rolling counters reflect history; the per-call
    # signal is more responsive to immediate over-firing.
    from anvil_scout.daedalus.coherence import (
        DEFAULT_HIGH_STRIP_RATE,
        FLAG_HIGH_STRIP_RATE,
    )
    from anvil_scout.daedalus.adapters import (
        adapters_from_state,
        adapters_to_state,
        update_all_adapters,
    )

    flagged_detectors = [
        r.detector_name
        for r in receipts
        if r.spans_classified > 0
        and r.strip_rate() > DEFAULT_HIGH_STRIP_RATE
    ]

    with state_provider.transaction():
        state = state_provider.load()
        aggregate_into_state(state, receipts)
        # TB-14: pull adapters, apply update rule, persist back.
        # In LEARNING mode (the gate is checked by the caller in run_once),
        # this is where Law III T1 actually fires.
        current_adapters = adapters_from_state(state)
        updated_adapters = update_all_adapters(current_adapters, flagged_detectors)
        adapters_to_state(state, updated_adapters)
        state_provider.save(state)



def _run_prediction_tail(
    inp: ScrapedInput,
    prepared,
    out_after_law0,
    payload: dict,
    state_provider,
) -> None:
    """Internal: persist one predictive episode in LEARNING mode.

    Stores only opaque hashes, bounded numeric features, compact score fields,
    and prediction receipts. Raw website text and contact data do not enter
    state. Caller wraps this in strip-don't-raise.
    """
    from anvil_scout.daedalus.predictive import remember_prediction_for_input

    remember_prediction_for_input(
        state_provider,
        inp=inp,
        payload=payload,
        text_chars=getattr(prepared, "char_count", 0),
        enrichment_available=bool(getattr(out_after_law0, "_enrichment_available", False)),
    )


def main(argv=None) -> int:
    argv = argv if argv is not None else sys.argv[1:]

    if argv and argv[0] in ("-h", "--help"):
        sys.stdout.write(_HELP.format(version=__version__))
        sys.stdout.write(
            "\nState-aware prototype options:\n"
            "  --mode snapshot|learning       Read/write behavior for internal state.\n"
            "  --state-db PATH                SQLite state file for memory.\n"
            "  --adaptive-output              Apply outcome-trained calibration to output.\n"
            "  --record-outcome won|lost|nurture  Record feedback for the input or --lead-id.\n"
            "  --lead-id ID                   Opaque lead id for outcome recording.\n"
            "  --self-model                   Print compact predictive self-model JSON.\n"
        )
        return 0

    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--mode", choices=("snapshot", "learning"), default=None)
    parser.add_argument("--state-db", default=None)
    parser.add_argument("--adaptive-output", action="store_true")
    parser.add_argument("--record-outcome", choices=("won", "lost", "nurture"), default=None)
    parser.add_argument("--lead-id", default=None)
    parser.add_argument("--self-model", action="store_true")
    try:
        args = parser.parse_args(argv)
    except SystemExit:
        sys.stderr.write("error: invalid arguments. See --help.\n")
        return 2

    # Force UTF-8 on stdin/stdout for Windows safety (JB-01-2).
    try:
        sys.stdin.reconfigure(encoding="utf-8")     # type: ignore[attr-defined]
        sys.stdout.reconfigure(encoding="utf-8")    # type: ignore[attr-defined]
    except Exception:
        pass  # older python or non-TTY — best effort

    needs_state = bool(args.state_db or args.mode or args.adaptive_output
                       or args.record_outcome or args.self_model)
    provider = None
    mode = None
    if needs_state:
        state_mod = importlib.import_module("anvil_scout.daedalus.state")
        modes_mod = importlib.import_module("anvil_scout.daedalus.modes")
        provider = state_mod.SQLiteStateProvider(args.state_db)
        mode = modes_mod.parse_mode(args.mode or "snapshot")

    if args.self_model:
        from anvil_scout.daedalus.predictive import summarize_self_model
        state = provider.load() if provider is not None else {}
        sys.stdout.write(json.dumps(summarize_self_model(state), indent=2, ensure_ascii=False))
        sys.stdout.write("\n")
        return 0

    raw = sys.stdin.read()

    if args.record_outcome:
        from anvil_scout.daedalus.predictive import opaque_lead_id, record_outcome
        if provider is None:
            state_mod = importlib.import_module("anvil_scout.daedalus.state")
            provider = state_mod.SQLiteStateProvider(args.state_db)

        lead_id = args.lead_id
        if not lead_id:
            if not raw.strip():
                sys.stderr.write("error: outcome recording needs --lead-id or input JSON on stdin.\n")
                return 1
            try:
                d = json.loads(raw)
            except json.JSONDecodeError as e:
                sys.stderr.write(f"error: invalid JSON input: {e}\n")
                return 1
            if not isinstance(d, dict):
                sys.stderr.write("error: input must be a JSON object.\n")
                return 1
            lead_id = opaque_lead_id(ScrapedInput.from_dict(d))

        receipt = record_outcome(provider, lead_id=lead_id, label=args.record_outcome)
        sys.stdout.write(json.dumps(receipt, indent=2, ensure_ascii=False))
        sys.stdout.write("\n")
        return 0

    if not raw.strip():
        sys.stderr.write("error: no input on stdin. See --help.\n")
        return 1

    sys.stdout.write(run_once(
        raw,
        state_provider=provider,
        mode=mode,
        adaptive_output=bool(args.adaptive_output),
    ))
    sys.stdout.write("\n")
    return 0

if __name__ == "__main__":
    sys.exit(main())
