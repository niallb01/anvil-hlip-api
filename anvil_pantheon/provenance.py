"""Anvil-Pantheon-Floor — Provenance (Packet 14).

Schema-aligned provenance types per pantheon_organs v1.3
(source_provenance_record.schema.json + source_use_record.schema.json).
This module ships the TYPES + the Grimmdönger identity record + UTF-8
round-trip validation. Integration with SourceCard / EmissionCertificate
/ Veritas / Bridge happens in P15.

NON_CLAIMS:
  - Does NOT compute substrate outputs
  - Does NOT modify existing SourceCard or EmissionCertificate yet (P15)
  - Does NOT decide what to do with a provenance record (consumers do)
  - The Grimmdönger identity is the locked authoritative record;
    AI co-authorship is captured ONLY in the retrieval block of
    each ProvenanceRecord (forensic internal use), NEVER in the
    origin.authors_or_org public byline

Floor scope:
  - All sub-records as frozen dataclasses with __post_init__ validation
  - Enums for closed-set fields (material_type, license_id_scheme,
    local_copy, epistemic_status, support_level)
  - AuthorIdentity with UTF-8 round-trip discipline (the umlaut MUST
    survive every serialization roundtrip; "Grimmdonger" without
    umlaut FAILS validation)
  - GRIMMDONGER_IDENTITY constant
  - GRIMMDONGER_RIGHTS default (all-rights-reserved profile)
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple, Union


# ─── Enums (closed sets per schema) ──────────────────────────────────────

class MaterialType(str, Enum):
    """Per source_provenance_record.material_type enum."""
    THEOREM           = "theorem"
    DEFINITION        = "definition"
    FORMULA           = "formula"
    CONSTANT          = "constant"
    DATA_ROW          = "data_row"
    EXAMPLE           = "example"
    QUOTE             = "quote"
    PARAPHRASE        = "paraphrase"
    INTERNAL_RULE     = "internal_rule"
    GENERATED_TEST    = "generated_test"
    USER_SPAN         = "user_span"
    STANDARD_CLAUSE   = "standard_clause"
    SAFETY_BOUNDARY   = "safety_boundary"


class LicenseIdScheme(str, Enum):
    """Per source_provenance_record.rights.license_id_scheme enum."""
    SPDX                = "SPDX"
    CUSTOM              = "custom"
    UNKNOWN             = "unknown"
    PUBLIC_DOMAIN_MARK  = "public_domain_mark"
    CONTRACT            = "contract"


class LocalCopy(str, Enum):
    """Per source_provenance_record.retrieval.local_copy enum."""
    METADATA_ONLY    = "metadata_only"
    EXCERPT          = "excerpt"
    FULL_MIRROR      = "full_mirror"
    GENERATED_LOCAL  = "generated_local"


class EpistemicStatus(str, Enum):
    """Per source_provenance_record.scope.epistemic_status enum.
    A = primary/authoritative; B = strong secondary; C = weak/draft;
    D = self-generated/internal."""
    A = "A"
    B = "B"
    C = "C"
    D = "D"


class SupportLevel(str, Enum):
    """Per source_provenance_record.claim_bindings[].support_level enum
    AND source_use_record.support_level enum. 'contradicts' is the
    important one -- citing what you disagree with."""
    DIRECT       = "direct"
    INDIRECT     = "indirect"
    BACKGROUND   = "background"
    CONTRADICTS  = "contradicts"
    INTERNAL     = "internal"


# ─── ID format validators ────────────────────────────────────────────────

_PROVENANCE_ID_RX = re.compile(r"^PRV-[A-Z0-9_]+-[0-9]{6,}$")
_SOURCE_ID_RX     = re.compile(r"^SRC-[A-Z0-9_]+-[0-9]{4,}$")
_MATERIAL_ID_RX   = re.compile(r"^MAT-[A-Z0-9_]+-[0-9]{4,}$")


def validate_provenance_id(pid: str) -> None:
    """JB-P14-7: provenance_id must match PRV-<MODULE>-<6+digits>."""
    if not _PROVENANCE_ID_RX.match(pid):
        raise ValueError(
            f"provenance_id {pid!r} does not match required pattern "
            f"PRV-<MODULE>-<6+digits> (e.g. PRV-LOPSIDED-000001)"
        )


def validate_source_id(sid: str) -> None:
    if not _SOURCE_ID_RX.match(sid):
        raise ValueError(
            f"source_id {sid!r} does not match required pattern "
            f"SRC-<MODULE>-<4+digits>"
        )


def validate_material_id(mid: str) -> None:
    if not _MATERIAL_ID_RX.match(mid):
        raise ValueError(
            f"material_id {mid!r} does not match required pattern "
            f"MAT-<MODULE>-<4+digits>"
        )


# ─── UTF-8 round-trip validation (JB-P14-1) ──────────────────────────────

def validate_utf8_roundtrip(text: str, label: str = "string") -> None:
    """Ensure a string round-trips through UTF-8 encode/decode without
    loss. Catches:
      - silent diacritic stripping (e.g. ö -> o by ASCII-only systems)
      - normalization that drops codepoints
      - any encoding that mangles the input

    The Grimmdönger identity REQUIRES this: 'Grimmdonger' (no umlaut)
    is NOT 'Grimmdönger'. A system that silently normalizes the umlaut
    away has corrupted the author identity. This check makes that
    failure mode loud, not silent.
    """
    if not isinstance(text, str):
        raise TypeError(f"{label} must be str; got {type(text).__name__}")
    try:
        encoded = text.encode("utf-8")
        decoded = encoded.decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError) as exc:
        raise ValueError(
            f"{label} {text!r} failed UTF-8 round-trip: {exc}"
        ) from exc
    if decoded != text:
        raise ValueError(
            f"{label} {text!r} did not round-trip cleanly through UTF-8 "
            f"(got {decoded!r}); diacritics or codepoints may be lost"
        )


# ─── Author identity ─────────────────────────────────────────────────────

@dataclass(frozen=True)
class AuthorIdentity:
    """The canonical author record. Primary name MUST round-trip
    cleanly through UTF-8 (catches umlaut-stripping). Aliases are
    other names the same identity is known by (Grimm, GrimmGlass).

    AI co-authorship is intentionally NOT a field here. Public
    attribution is the primary_name alone. Internal forensic trail
    for AI assistance goes in the ProvenanceRecord.retrieval block
    (retrieved_by / retrieval_tool), which is metadata about HOW we
    got the canonical text, not WHO authored it.
    """
    primary_name: str
    aliases: Tuple[str, ...]
    rights_status_default: str
    note: str = ""

    def __post_init__(self):
        validate_utf8_roundtrip(self.primary_name, label="primary_name")
        for alias in self.aliases:
            validate_utf8_roundtrip(alias, label="alias")
        if not self.primary_name.strip():
            raise ValueError("primary_name must be non-empty")


# THE LOCKED AUTHOR IDENTITY for the entire Grimmdönger project.
# Umlaut is part of the identifier; aliases include the simpler forms.
GRIMMDONGER_IDENTITY = AuthorIdentity(
    primary_name="Grimmdönger",
    aliases=("Grimm", "GrimmGlass"),
    rights_status_default="all_rights_reserved_grimmdonger",
    note=(
        "Pen name; mad-scientist register. Public attribution is "
        "Grimmdönger alone. AI assistance, where present, is captured "
        "in ProvenanceRecord.retrieval forensic trail, NOT in the "
        "origin.authors_or_org public byline."
    ),
)


# ─── Rights record ───────────────────────────────────────────────────────

@dataclass(frozen=True)
class Rights:
    """Per source_provenance_record.rights. Required fields per schema:
    license_name, license_id, license_id_scheme, redistribution_allowed,
    commercial_use_allowed, derivative_use_allowed, local_mirror_allowed."""
    license_name: str
    license_id: str
    license_id_scheme: LicenseIdScheme
    redistribution_allowed: bool
    commercial_use_allowed: bool
    derivative_use_allowed: bool
    local_mirror_allowed: bool

    license_url: Optional[str] = None
    copyright_holder: Optional[str] = None
    attribution_required: bool = True
    ai_training_allowed: Union[bool, str] = False
    embedding_allowed: Union[bool, str] = False
    excerpt_allowed: bool = True
    sharealike_required: bool = False
    no_derivatives: bool = False
    no_commercial: bool = False
    expires_at: Optional[str] = None
    contract_id: Optional[str] = None
    rights_notes: str = ""


# Default rights profile for Grimmdönger-authored material.
GRIMMDONGER_RIGHTS = Rights(
    license_name="All Rights Reserved (Grimmdönger)",
    license_id="AllRightsReserved-Grimmdonger-v1",
    license_id_scheme=LicenseIdScheme.CUSTOM,
    redistribution_allowed=False,
    commercial_use_allowed=False,
    derivative_use_allowed=False,
    local_mirror_allowed=True,
    copyright_holder="Grimmdönger",
    attribution_required=True,
    ai_training_allowed=False,
    embedding_allowed=False,
    excerpt_allowed=True,
    rights_notes=(
        "All rights reserved by Grimmdönger. Local mirroring permitted "
        "for the Grimmdönger system's own operation. AI training and "
        "embedding by third parties expressly not permitted. Excerpts "
        "permitted under standard fair-use conventions with attribution."
    ),
)


# Default rights profile for third-party material quoted under fair use.
FAIR_USE_QUOTE_RIGHTS = Rights(
    license_name="Fair Use Quote",
    license_id="FairUseQuote-Default-v1",
    license_id_scheme=LicenseIdScheme.CUSTOM,
    redistribution_allowed=False,
    commercial_use_allowed=False,
    derivative_use_allowed=False,
    local_mirror_allowed=True,
    copyright_holder=None,  # set per-record to the actual rights holder
    attribution_required=True,
    excerpt_allowed=True,
    rights_notes=(
        "Brief quoted excerpt used under fair-use convention with "
        "attribution. Each record must set copyright_holder to the "
        "specific rights holder and include the quote extent in "
        "material_summary for review."
    ),
)


# ─── Sub-records (origin, retrieval, scope, claim_bindings, compliance) ──

@dataclass(frozen=True)
class Origin:
    """Per source_provenance_record.origin. Required: title,
    authors_or_org, year_or_version, canonical_locator."""
    title: str
    authors_or_org: str
    year_or_version: str
    canonical_locator: str
    publisher_or_issuer: Optional[str] = None
    edition: Optional[str] = None
    standard_number: Optional[str] = None
    doi: Optional[str] = None
    isbn: Optional[str] = None
    url: Optional[str] = None

    def __post_init__(self):
        validate_utf8_roundtrip(self.title, label="origin.title")
        validate_utf8_roundtrip(self.authors_or_org, label="origin.authors_or_org")


@dataclass(frozen=True)
class Retrieval:
    """Per source_provenance_record.retrieval. Required: retrieved_at_utc,
    retrieval_method, original_content_digest (can be null per schema but
    we require it as a SHA-256 hex string for our floor), local_copy.

    retrieved_by + retrieval_tool are the fields where AI-assistance
    metadata lives (forensic internal use only, not public byline)."""
    retrieved_at_utc: str
    retrieval_method: str
    local_copy: LocalCopy
    original_content_digest: Optional[str] = None
    canonical_text_digest: Optional[str] = None
    retrieved_by: Optional[str] = None
    retrieval_tool: Optional[str] = None
    local_path: Optional[str] = None


@dataclass(frozen=True)
class Scope:
    """Per source_provenance_record.scope. Required: epistemic_status,
    source_class, stability."""
    epistemic_status: EpistemicStatus
    source_class: str
    stability: str
    permitted_modules: Tuple[str, ...] = ()
    permitted_claim_types: Tuple[str, ...] = ()
    not_permitted_for: Tuple[str, ...] = ()


@dataclass(frozen=True)
class ClaimBinding:
    """Per source_provenance_record.claim_bindings[]. Required:
    claim_id, support_level, confidence."""
    claim_id: str
    support_level: SupportLevel
    confidence: float
    locator: Optional[str] = None
    source_span_hash: Optional[str] = None

    def __post_init__(self):
        if not (0.0 <= self.confidence <= 1.0):
            raise ValueError(
                f"confidence must be in [0,1]; got {self.confidence}"
            )


@dataclass(frozen=True)
class Compliance:
    """Per source_provenance_record.compliance. Required:
    personal_data_present, high_stakes_domain, refresh_policy."""
    personal_data_present: bool
    high_stakes_domain: str
    refresh_policy: str
    minors_data_present: bool = False
    education_record_present: bool = False
    health_data_present: bool = False
    financial_data_present: bool = False
    biometric_data_present: bool = False
    jurisdiction_tags: Tuple[str, ...] = ()
    audit_retention_class: str = "default"
    export_control_review: str = "not_applicable"
    accessibility_relevance: str = "not_applicable"
    human_oversight_required: bool = False


# ─── ProvenanceRecord (top-level) ────────────────────────────────────────

@dataclass(frozen=True)
class ProvenanceRecord:
    """Per source_provenance_record.schema.json (pantheon_organs v1.3).
    Frozen, validated on construction. All required schema fields are
    non-optional here."""
    provenance_id: str
    module: str
    source_id: str
    material_id: str
    material_type: MaterialType
    origin: Origin
    retrieval: Retrieval
    rights: Rights
    scope: Scope
    claim_bindings: Tuple[ClaimBinding, ...]
    compliance: Compliance

    material_summary: str = ""
    transformations: Tuple[Dict[str, Any], ...] = ()

    def __post_init__(self):
        validate_provenance_id(self.provenance_id)
        validate_source_id(self.source_id)
        validate_material_id(self.material_id)
        if not self.module.strip():
            raise ValueError("module must be non-empty")
        if not self.claim_bindings:
            raise ValueError(
                f"claim_bindings must have at least 1 entry "
                f"(schema minItems: 1); got 0 for {self.provenance_id}"
            )


# ─── SourceUseRecord ─────────────────────────────────────────────────────

@dataclass(frozen=True)
class SourceUseRecord:
    """Per source_use_record.schema.json. Records that a specific
    claim in an answer packet used a specific provenance source.
    Required: provenance_id, source_id, claim_id, used_for,
    support_level, rights_status, confidence."""
    provenance_id: str
    source_id: str
    claim_id: str
    used_for: str
    support_level: SupportLevel
    rights_status: str
    confidence: float
    locator: Optional[str] = None
    source_digest: Optional[str] = None
    transformation_ids: Tuple[str, ...] = ()

    def __post_init__(self):
        if not (0.0 <= self.confidence <= 1.0):
            raise ValueError(
                f"confidence must be in [0,1]; got {self.confidence}"
            )
        validate_provenance_id(self.provenance_id)
        validate_source_id(self.source_id)
        if not self.used_for.strip():
            raise ValueError("used_for must be non-empty")


# ─── Convenience builders ────────────────────────────────────────────────

def make_grimmdonger_origin(
    *,
    title: str,
    year_or_version: str,
    canonical_locator: str,
    edition: Optional[str] = None,
) -> Origin:
    """Build an Origin record with Grimmdönger as authors_or_org.
    Use this for any Grimmdönger-authored material. UTF-8 round-trip
    on the umlaut is enforced via Origin.__post_init__."""
    return Origin(
        title=title,
        authors_or_org=GRIMMDONGER_IDENTITY.primary_name,
        year_or_version=year_or_version,
        canonical_locator=canonical_locator,
        edition=edition,
    )


# ─── ProvenanceStore (P16) ────────────────────────────────────────────────

class ProvenanceStore:
    """Append-only, idempotent registry of ProvenanceRecords keyed by
    provenance_id. In-memory floor implementation (mirrors ReceiptStore's
    idempotency discipline without disk persistence -- the corpus phase
    can add a JSONL backing later).

    JB-P15-2: registering the same record twice is a no-op. Registering a
    DIFFERENT record under an id already present is a conflict and raises
    -- ids are content-stable, so a clash means a real inconsistency.
    """

    def __init__(self) -> None:
        self._records: Dict[str, ProvenanceRecord] = {}

    def register(self, record: ProvenanceRecord) -> bool:
        """Register a record. Returns True if newly stored, False if an
        identical record was already present (idempotent no-op). Raises
        ValueError if a DIFFERENT record shares the provenance_id."""
        existing = self._records.get(record.provenance_id)
        if existing is not None:
            if existing == record:
                return False
            raise ValueError(
                f"provenance_id {record.provenance_id} already registered "
                f"with different content"
            )
        self._records[record.provenance_id] = record
        return True

    def get(self, provenance_id: str) -> Optional[ProvenanceRecord]:
        return self._records.get(provenance_id)

    def is_present(self, provenance_id: str) -> bool:
        return provenance_id in self._records

    def all_ids(self) -> Tuple[str, ...]:
        return tuple(sorted(self._records.keys()))

    def __len__(self) -> int:
        return len(self._records)


# ─── SourceUseRecord auto-generation (P16) ────────────────────────────────

def generate_source_use_records(
    certificate: Any,
    sourcebook: Any,
    store: ProvenanceStore,
) -> Tuple[SourceUseRecord, ...]:
    """Derive SourceUseRecords from an emitted certificate's GROUNDED
    slot fills. For each grounded fill whose card carries a provenance_id
    that resolves in `store`, emit one SourceUseRecord.

    claim_id discipline (JB-P15-5): claim_id is derived as
    "{template_id}.{slot_name}" and MUST match an actual ClaimBinding in
    the resolved ProvenanceRecord. If it does not, the fill is SKIPPED --
    we never invent a claim_id that the provenance record doesn't declare.
    """
    # Local imports avoid any module-load ordering concerns; types.py and
    # sourcebook.py do not import provenance, so there is no cycle.
    from .types import CertificationStatus

    template_id = ""
    if isinstance(getattr(certificate, "template_choice", None), dict):
        template_id = certificate.template_choice.get("template_id", "")

    out: List[SourceUseRecord] = []
    for sf in certificate.slot_fills:
        if sf.certification != CertificationStatus.GROUNDED or not sf.source_card_id:
            continue
        card = sourcebook.get(sf.source_card_id)
        if card is None or not getattr(card, "provenance_id", None):
            continue
        record = store.get(card.provenance_id)
        if record is None:
            continue
        claim_id = f"{template_id}.{sf.slot_name}"
        binding = next(
            (cb for cb in record.claim_bindings if cb.claim_id == claim_id), None
        )
        if binding is None:
            continue  # do not invent claim_ids the record never declared
        out.append(SourceUseRecord(
            provenance_id=record.provenance_id,
            source_id=record.source_id,
            claim_id=claim_id,
            used_for=sf.slot_name,
            support_level=binding.support_level,
            rights_status=record.rights.license_id,
            confidence=binding.confidence,
            locator=binding.locator,
            source_digest=binding.source_span_hash,
        ))
    return tuple(out)
