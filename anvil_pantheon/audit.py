"""Anvil-Pantheon-Floor — foundation audit primitives (Packet 1).

Adapts DetMath's foundation_audit.py pattern to Anvil. Declares what the
floor system MUST do (TASK_REQUIREMENTS), which modules are load-bearing
(FOUNDATION_COMPONENTS), and which are conformance/evidence scaffolding
not core (SCAFFOLDING_COMPONENTS). Provides run_foundation_audit() — a
structural check that all declared paths resolve.

Per the v0.37 doctrine and the manifold framework: declaring foundation
vs scaffolding explicitly is what stops a member from impersonating
substrate it doesn't own.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict


# ─── What the floor system must do ────────────────────────────────────────

TASK_REQUIREMENTS = (
    "ingest Scout V/W/M output and produce typed SourceCard set",
    "guard ingress: quarantine bad fields, block missing critical operands",
    "run substrate organs (Hestia/Vesta/Indra) over the SourceCard set",
    "compose certified emission via Cognitive Template Library",
    "certify each slot-fill as grounded/hedge/refused against SourceCards",
    "emit EmissionCertificate with hash-chained receipt",
    "support replay verification of the pathway end-to-end",
)


# ─── Foundation vs scaffolding ────────────────────────────────────────────

FOUNDATION_COMPONENTS = {
    "types":         "anvil_pantheon/types.py",
    "audit":         "anvil_pantheon/audit.py",
    "grounding":     "anvil_pantheon/grounding.py",
    "receipts":      "anvil_pantheon/receipts.py",
    "pathway_audit": "anvil_pantheon/pathway_audit.py",
    "sourcebook":    "anvil_pantheon/sourcebook.py",
    "scout_adapter": "anvil_pantheon/integration/scout_adapter.py",
    "ingress_guard": "anvil_pantheon/ingress_guard.py",
    "scrape_repair": "anvil_pantheon/scrape_repair.py",
    "hestia":        "anvil_pantheon/substrates/hestia.py",
    "vesta":         "anvil_pantheon/substrates/vesta.py",
    "indra":         "anvil_pantheon/substrates/indra.py",
    "mnemosyne":         "anvil_pantheon/services/mnemosyne.py",
    "hermes":            "anvil_pantheon/services/hermes.py",
    "template_library":  "anvil_pantheon/cognitive/template_library.py",
    "sales_email_v0_1":  "anvil_pantheon/cognitive/templates/sales_email_v0_1.py",
    "oracle":            "anvil_pantheon/oracle.py",
    "veritas":           "anvil_pantheon/veritas.py",
    "admission_gate":    "anvil_pantheon/admission_gate.py",
    "bounded_learning":  "anvil_pantheon/bounded_learning.py",
    "bridge_api":        "anvil_pantheon/bridge_api.py",
    "fastapi_shim":      "anvil_pantheon/fastapi_shim.py",
    "provenance":        "anvil_pantheon/provenance.py",
}

SCAFFOLDING_COMPONENTS = {
    "tests_p1_types":             "tests/test_p1_types.py",
    "tests_p1_audit":             "tests/test_p1_audit.py",
    "tests_p1_grounding":         "tests/test_p1_grounding.py",
    "tests_p2_receipts":          "tests/test_p2_receipts.py",
    "tests_p2_pathway_audit":     "tests/test_p2_pathway_audit.py",
    "tests_p3_sourcebook":        "tests/test_p3_sourcebook.py",
    "tests_p3_scout_adapter":     "tests/test_p3_scout_adapter.py",
    "tests_p4_ingress_guard":     "tests/test_p4_ingress_guard.py",
    "tests_p4_scrape_repair":     "tests/test_p4_scrape_repair.py",
    "tests_p5_hestia":            "tests/test_p5_hestia.py",
    "tests_p6_vesta":             "tests/test_p6_vesta.py",
    "tests_p7_indra":             "tests/test_p7_indra.py",
    "tests_p8_mnemosyne":         "tests/test_p8_mnemosyne.py",
    "tests_p8_hermes":            "tests/test_p8_hermes.py",
    "tests_p9_template_library":  "tests/test_p9_template_library.py",
    "tests_p9_sales_email":       "tests/test_p9_sales_email.py",
    "tests_p10_veritas":          "tests/test_p10_veritas.py",
    "tests_p10_oracle":           "tests/test_p10_oracle.py",
    "tests_p11_admission_gate":   "tests/test_p11_admission_gate.py",
    "tests_p12_bounded_learning": "tests/test_p12_bounded_learning.py",
    "tests_p13_bridge_api":       "tests/test_p13_bridge_api.py",
    "tests_p14_provenance":       "tests/test_p14_provenance.py",
}


# ─── Audit result type ────────────────────────────────────────────────────

@dataclass(frozen=True)
class FoundationAuditResult:
    """Structural check result. clean=True iff all declared component
    paths resolve. missing lists the unresolved paths; extras would list
    any unexpected anvil_pantheon/*.py not in FOUNDATION_COMPONENTS (a
    sentinel against organ-creep at the foundation layer)."""
    clean: bool
    components_checked: int
    missing: Dict[str, str]
    extras: Dict[str, str]
    requirements: tuple


# ─── The audit ────────────────────────────────────────────────────────────

def run_foundation_audit(root: str | Path = ".") -> FoundationAuditResult:
    """Verify that every path in FOUNDATION_COMPONENTS and
    SCAFFOLDING_COMPONENTS resolves under `root`, and that no
    unexpected files have been added to anvil_pantheon/ (extras)."""
    root = Path(root).resolve()
    missing: Dict[str, str] = {}
    for name, rel in {**FOUNDATION_COMPONENTS, **SCAFFOLDING_COMPONENTS}.items():
        if not (root / rel).exists():
            missing[name] = rel

    # Extras: any .py files in anvil_pantheon/ not in the foundation
    # manifest. Excludes __init__.py and __pycache__.
    extras: Dict[str, str] = {}
    pkg = root / "anvil_pantheon"
    if pkg.is_dir():
        declared = {Path(rel).name for rel in FOUNDATION_COMPONENTS.values()}
        for p in pkg.iterdir():
            if p.is_file() and p.suffix == ".py" and p.name != "__init__.py":
                if p.name not in declared:
                    extras[p.stem] = str(p.relative_to(root))

    return FoundationAuditResult(
        clean=(not missing and not extras),
        components_checked=len(FOUNDATION_COMPONENTS) + len(SCAFFOLDING_COMPONENTS),
        missing=missing,
        extras=extras,
        requirements=TASK_REQUIREMENTS,
    )
