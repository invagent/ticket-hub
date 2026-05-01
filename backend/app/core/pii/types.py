"""PII data classes."""

from dataclasses import dataclass, field
from enum import StrEnum


class PIIKind(StrEnum):
    PERSON = "PERSON"
    PHONE = "PHONE"
    EMAIL = "EMAIL"


@dataclass(frozen=True, slots=True)
class PIIToken:
    """One opaque token replacing a real PII value."""

    kind: PIIKind
    index: int

    @property
    def placeholder(self) -> str:
        return f"[{self.kind.value}_{self.index}]"

    def __str__(self) -> str:
        return self.placeholder


@dataclass(slots=True)
class PIIBundle:
    """Sanitized text + map back to ciphertexts (encrypted plaintext)."""

    sanitized_text: str
    mapping: dict[str, bytes] = field(default_factory=dict)
    """placeholder -> AES-GCM ciphertext of original PII value."""
