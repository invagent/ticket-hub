"""Reverse the sanitizer: replace placeholders back with original PII values."""

import re
from typing import Protocol

from .types import PIIBundle

_PLACEHOLDER_RE = re.compile(r"\[(PERSON|PHONE|EMAIL)_\d+\]")


class _Decryptor(Protocol):
    def decrypt(self, ciphertext: bytes) -> bytes: ...


class Restorer:
    """Restore PII into LLM output. D0 reads bundle.mapping directly.

    In D3, accept an optional decryptor for AES-GCM ciphertexts.
    """

    def __init__(self, decryptor: _Decryptor | None = None) -> None:
        self._decryptor = decryptor

    def restore(self, sanitized_text: str, bundle: PIIBundle) -> str:
        if not sanitized_text:
            return sanitized_text

        def _swap(match: re.Match[str]) -> str:
            placeholder = match.group(0)
            payload = bundle.mapping.get(placeholder)
            if payload is None:
                return placeholder
            if self._decryptor is not None:
                return self._decryptor.decrypt(payload).decode()
            return payload.decode()

        return _PLACEHOLDER_RE.sub(_swap, sanitized_text)
