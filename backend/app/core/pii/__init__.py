"""PII Sanitizer / Restorer (D3 — full implementation).

Decision D4 (v0.5.6): scope is customer name / mobile / email only.
Master key from env PII_MASTER_KEY; quarterly rotation.

This is the D0 skeleton. Full impl + 95% coverage gate happens in D3.
See docs/spec/data_model.md §pii_maps and upgrade_plan.md §5.4.
"""

from .restorer import Restorer  # noqa: F401
from .sanitizer import Sanitizer  # noqa: F401
from .types import PIIBundle, PIIKind, PIIToken  # noqa: F401
