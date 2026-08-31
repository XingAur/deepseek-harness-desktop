from __future__ import annotations

import base64
import os
import unittest
from unittest import mock

from app.manager_credential_crypto import (
    AesGcmCredentialCipher,
    CredentialDecryptError,
    CredentialEncryptionUnavailable,
    credential_aad,
)


class ManagerCredentialCryptoTests(unittest.TestCase):
    def setUp(self) -> None:
        self.master_key = base64.urlsafe_b64encode(b"k" * 32).decode("ascii")
        self.aad = credential_aad(
            scope_type="local",
            scope_key="default",
            provider="model",
            profile_key="demo",
            field="api_key",
        )

    def test_aes_gcm_cipher_never_returns_plaintext_and_requires_valid_master_key(self) -> None:
        with mock.patch.dict(
            os.environ,
            {"HARNESS_MANAGER_CREDENTIAL_MASTER_KEY": self.master_key},
            clear=False,
        ):
            cipher = AesGcmCredentialCipher.from_environment()
            encrypted = cipher.encrypt("SENTINEL_SECRET", aad=self.aad)

        self.assertTrue(encrypted.startswith("aesgcm.v1."))
        self.assertNotIn("SENTINEL_SECRET", encrypted)
        self.assertEqual("SENTINEL_SECRET", cipher.decrypt(encrypted, aad=self.aad))

    def test_encryption_uses_a_fresh_nonce(self) -> None:
        with mock.patch.dict(
            os.environ,
            {"HARNESS_MANAGER_CREDENTIAL_MASTER_KEY": self.master_key},
            clear=False,
        ):
            cipher = AesGcmCredentialCipher.from_environment()
            first = cipher.encrypt("same-value", aad=self.aad)
            second = cipher.encrypt("same-value", aad=self.aad)

        self.assertNotEqual(first, second)

    def test_credential_aad_is_unambiguous_for_adversarial_path_segments(self) -> None:
        first_aad = credential_aad(
            scope_type="a/b",
            scope_key="c",
            provider="model",
            profile_key="demo",
            field="api_key",
        )
        second_aad = credential_aad(
            scope_type="a",
            scope_key="b/c",
            provider="model",
            profile_key="demo",
            field="api_key",
        )
        with mock.patch.dict(
            os.environ,
            {"HARNESS_MANAGER_CREDENTIAL_MASTER_KEY": self.master_key},
            clear=False,
        ):
            cipher = AesGcmCredentialCipher.from_environment()
            first_encrypted = cipher.encrypt("first", aad=first_aad)
            second_encrypted = cipher.encrypt("second", aad=second_aad)

        self.assertNotEqual(first_aad, second_aad)
        self.assertEqual("first", cipher.decrypt(first_encrypted, aad=first_aad))
        self.assertEqual("second", cipher.decrypt(second_encrypted, aad=second_aad))
        with self.assertRaisesRegex(CredentialDecryptError, "^credential_decrypt_failed$"):
            cipher.decrypt(first_encrypted, aad=second_aad)

    def test_missing_or_invalid_master_key_is_unavailable(self) -> None:
        standard_not_urlsafe = base64.b64encode(b"\xfb" * 32).decode("ascii")
        for value in (
            "",
            "not-base64",
            base64.urlsafe_b64encode(b"short").decode("ascii"),
            standard_not_urlsafe,
        ):
            with self.subTest(value=value):
                with mock.patch.dict(
                    os.environ,
                    {"HARNESS_MANAGER_CREDENTIAL_MASTER_KEY": value},
                    clear=False,
                ):
                    with self.assertRaisesRegex(
                        CredentialEncryptionUnavailable,
                        "^encryption_unavailable$",
                    ):
                        AesGcmCredentialCipher.from_environment()

    def test_malformed_tampered_or_wrong_aad_ciphertext_fails_closed(self) -> None:
        with mock.patch.dict(
            os.environ,
            {"HARNESS_MANAGER_CREDENTIAL_MASTER_KEY": self.master_key},
            clear=False,
        ):
            cipher = AesGcmCredentialCipher.from_environment()
            encrypted = cipher.encrypt("SENTINEL_SECRET", aad=self.aad)

        payload = encrypted.rsplit(".", 1)[1]
        tampered = "aesgcm.v1." + payload[:-1] + ("A" if payload[-1] != "A" else "B")
        for value, aad in (
            ("invalid", self.aad),
            ("aesgcm.v1.not-base64", self.aad),
            (tampered, self.aad),
            (encrypted, b"local/default/model/other/api_key"),
        ):
            with self.subTest(value=value, aad=aad):
                with self.assertRaisesRegex(CredentialDecryptError, "^credential_decrypt_failed$"):
                    cipher.decrypt(value, aad=aad)


if __name__ == "__main__":
    unittest.main()
