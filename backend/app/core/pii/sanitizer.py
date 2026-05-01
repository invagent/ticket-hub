"""PII sanitizer skeleton.

D0: regex for phone + email; person-name detection deferred to D3 where it
will use customer_identities dictionary lookup (decision D4).

Coverage gate: this module + restorer must hit >= 95% line coverage
before merging to main (enforced by `make pii-cov`).
"""

import re
from collections.abc import Iterable
from typing import Protocol

from .types import PIIBundle, PIIKind, PIIToken


class _Encryptor(Protocol):
    def encrypt(self, plaintext: bytes) -> bytes: ...


# E.164-ish + China mobile (1[3-9]\d{9}); avoids matching arbitrary 11-digit ids
_PHONE_RE = re.compile(r"(?<!\d)(?:\+?86)?1[3-9]\d{9}(?!\d)")
_EMAIL_RE = re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b")


class Sanitizer:
    """Replace PII with stable placeholders within a single bundle.

    Design notes (will expand in D3):
      - Each unique value gets a stable index per bundle (PERSON_1, PHONE_1, ...).
      - Cross-bundle stability not guaranteed (deliberate; reduces correlation).
      - `customer_identities` dictionary lookup will augment regex for names.
    """

    def __init__(self, encryptor: _Encryptor | None = None) -> None:
        # encryptor: object with .encrypt(bytes) -> bytes; injected in D3
        self._encryptor = encryptor

    def sanitize(self, text: str, *, extra_names: Iterable[str] = ()) -> PIIBundle:
        if not text:
            return PIIBundle(sanitized_text=text or "")

        seen: dict[tuple[PIIKind, str], PIIToken] = {}
        mapping: dict[str, bytes] = {}

        def _swap(match: re.Match[str], kind: PIIKind) -> str:
            value = match.group(0)
            key = (kind, value)
            if key not in seen:
                seen[key] = PIIToken(kind=kind, index=len([k for k in seen if k[0] == kind]) + 1)
                if self._encryptor is not None:
                    mapping[seen[key].placeholder] = self._encryptor.encrypt(value.encode())
                else:  # D0 placeholder: store plaintext bytes; D3 mandates encryptor
                    mapping[seen[key].placeholder] = value.encode()
            return seen[key].placeholder

        sanitized = _PHONE_RE.sub(lambda m: _swap(m, PIIKind.PHONE), text)
        sanitized = _EMAIL_RE.sub(lambda m: _swap(m, PIIKind.EMAIL), sanitized)

        # extra_names: explicit list (D3 will pull from customer_identities)
        for name in sorted(set(extra_names), key=len, reverse=True):
            if not name or len(name) < 2:
                continue
            kind = PIIKind.PERSON
            key = (kind, name)
            if key not in seen and name in sanitized:
                seen[key] = PIIToken(kind=kind, index=len([k for k in seen if k[0] == kind]) + 1)
                if self._encryptor is not None:
                    mapping[seen[key].placeholder] = self._encryptor.encrypt(name.encode())
                else:
                    mapping[seen[key].placeholder] = name.encode()
                sanitized = sanitized.replace(name, seen[key].placeholder)

        return PIIBundle(sanitized_text=sanitized, mapping=mapping)
