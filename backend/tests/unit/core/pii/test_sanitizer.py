"""PII Sanitizer tests — D0 baseline.

Coverage gate: app/core/pii/* ≥ 95%. Add to this file (or test_restorer.py)
whenever new PII paths land. Decision D4: scope = name + mobile + email only.
"""

from app.core.pii import PIIBundle, PIIKind, Restorer, Sanitizer


def test_empty_text_returns_empty_bundle() -> None:
    bundle = Sanitizer().sanitize("")
    assert bundle.sanitized_text == ""
    assert bundle.mapping == {}


def test_none_input_treated_as_empty() -> None:
    # Pydantic doesn't enforce non-None at this layer; defensive.
    bundle = Sanitizer().sanitize(None)  # type: ignore[arg-type]
    assert bundle.sanitized_text == ""


def test_phone_number_basic() -> None:
    bundle = Sanitizer().sanitize("call 13800138000 today")
    assert "[PHONE_1]" in bundle.sanitized_text
    assert "13800138000" not in bundle.sanitized_text
    assert bundle.mapping["[PHONE_1]"] == b"13800138000"


def test_phone_with_country_code() -> None:
    bundle = Sanitizer().sanitize("intl: +8613800138000 ok")
    assert "[PHONE_1]" in bundle.sanitized_text
    assert "13800138000" not in bundle.sanitized_text


def test_phone_does_not_match_landline() -> None:
    """11-digit landline starting with 0 should NOT be flagged."""
    bundle = Sanitizer().sanitize("dial 02012345678 for support")
    assert bundle.sanitized_text == "dial 02012345678 for support"


def test_phone_does_not_match_invalid_first_digit() -> None:
    """1[3-9] only — 12345678901 must not match."""
    bundle = Sanitizer().sanitize("ref 12345678901 elsewhere")
    assert "[PHONE_1]" not in bundle.sanitized_text


def test_phone_dedup_within_bundle() -> None:
    bundle = Sanitizer().sanitize("call 13800138000 or 13800138000")
    assert bundle.sanitized_text.count("[PHONE_1]") == 2
    assert "[PHONE_2]" not in bundle.sanitized_text
    assert len(bundle.mapping) == 1


def test_two_distinct_phones_get_distinct_indices() -> None:
    bundle = Sanitizer().sanitize("13800138000 and 13911112222")
    assert "[PHONE_1]" in bundle.sanitized_text
    assert "[PHONE_2]" in bundle.sanitized_text
    assert bundle.mapping["[PHONE_1]"] != bundle.mapping["[PHONE_2]"]


def test_email_basic() -> None:
    bundle = Sanitizer().sanitize("write to alice@example.com please")
    assert "[EMAIL_1]" in bundle.sanitized_text
    assert "alice@example.com" not in bundle.sanitized_text


def test_email_with_dots_and_plus() -> None:
    bundle = Sanitizer().sanitize("alice.smith+work@sub.example.co.uk")
    assert "[EMAIL_1]" in bundle.sanitized_text


def test_email_dedup() -> None:
    bundle = Sanitizer().sanitize("a@b.com again a@b.com")
    assert bundle.sanitized_text.count("[EMAIL_1]") == 2
    assert "[EMAIL_2]" not in bundle.sanitized_text


def test_phone_and_email_coexist() -> None:
    bundle = Sanitizer().sanitize("contact 13800138000 / x@y.com")
    assert "[PHONE_1]" in bundle.sanitized_text
    assert "[EMAIL_1]" in bundle.sanitized_text


def test_extra_names_replaces_persons() -> None:
    bundle = Sanitizer().sanitize("张三 reports an issue", extra_names=["张三"])
    assert "[PERSON_1]" in bundle.sanitized_text
    assert "张三" not in bundle.sanitized_text
    assert bundle.mapping["[PERSON_1]"] == "张三".encode()


def test_extra_names_skips_short_inputs() -> None:
    """Single-char names are noise; require >=2 chars."""
    bundle = Sanitizer().sanitize("A reports", extra_names=["A"])
    assert "PERSON" not in bundle.sanitized_text


def test_extra_names_skips_empty() -> None:
    bundle = Sanitizer().sanitize("", extra_names=["", "张三"])
    assert bundle.sanitized_text == ""


def test_extra_names_longest_first() -> None:
    """If two names overlap, the longer should match first."""
    bundle = Sanitizer().sanitize("张三丰 and 张三", extra_names=["张三", "张三丰"])
    # 张三丰 must replace before 张三 to avoid corrupting the longer one
    assert "张三丰" not in bundle.sanitized_text


def test_extra_names_no_match_in_text_skipped() -> None:
    """Names not present in text should not appear in mapping."""
    bundle = Sanitizer().sanitize("hello world", extra_names=["李四"])
    assert all(not k.startswith("[PERSON") for k in bundle.mapping)


def test_pii_token_str_renders_placeholder() -> None:
    from app.core.pii.types import PIIToken

    tok = PIIToken(kind=PIIKind.PHONE, index=3)
    assert str(tok) == "[PHONE_3]"
    assert tok.placeholder == "[PHONE_3]"


def test_restorer_round_trip() -> None:
    san = Sanitizer()
    bundle = san.sanitize("call 13800138000 or x@y.com")
    sanitized = bundle.sanitized_text
    assert "13800138000" not in sanitized
    restored = Restorer().restore(sanitized + " end", bundle)
    assert "13800138000" in restored
    assert "x@y.com" in restored
    assert restored.endswith(" end")


def test_restorer_unknown_placeholder_passthrough() -> None:
    """Unknown placeholder should remain (don't crash, don't fabricate)."""
    bundle = PIIBundle(sanitized_text="[PHONE_99] x", mapping={})
    out = Restorer().restore("[PHONE_99] x", bundle)
    assert out == "[PHONE_99] x"


def test_restorer_empty_passthrough() -> None:
    assert Restorer().restore("", PIIBundle(sanitized_text="")) == ""


class _FakeEncryptor:
    def encrypt(self, b: bytes) -> bytes:
        return b"enc:" + b


class _FakeDecryptor:
    def decrypt(self, b: bytes) -> bytes:
        assert b.startswith(b"enc:")
        return b[4:]


def test_sanitizer_with_encryptor_stores_ciphertext() -> None:
    bundle = Sanitizer(encryptor=_FakeEncryptor()).sanitize(
        "call 13800138000 张三", extra_names=["张三"]
    )
    assert bundle.mapping["[PHONE_1]"] == b"enc:13800138000"
    assert bundle.mapping["[PERSON_1]"] == b"enc:" + "张三".encode()


def test_restorer_with_decryptor_round_trip() -> None:
    san = Sanitizer(encryptor=_FakeEncryptor())
    bundle = san.sanitize("ping 13800138000 ok")
    out = Restorer(decryptor=_FakeDecryptor()).restore(bundle.sanitized_text, bundle)
    assert "13800138000" in out
