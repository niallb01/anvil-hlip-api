"""Anvil-Pantheon-Floor — cognitive layer (Packet 9+).

The cognitive layer composes substrate evidence into emissions via
templates. Per Packet 9 floor: trace-to-card-or-refuse discipline; no
LLM-narrative synthesis. The module-level CANONICAL_REGISTRY is built
at import time from the templates package; immutable.
"""

from .template_library import TemplateRegistry
from .templates.sales_email_v0_1 import SALES_EMAIL_V0_1
from .templates.lead_rationale_v0_1 import LEAD_RATIONALE_V0_1


# Module-load-time registry. Add new templates by appending to this
# tuple; the registry is built once and frozen.
CANONICAL_REGISTRY = TemplateRegistry(templates=(
    SALES_EMAIL_V0_1,
    LEAD_RATIONALE_V0_1,
))
