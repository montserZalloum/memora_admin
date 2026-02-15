"""Unit tests for voucher crypto service functions.

Tests Fernet encrypt/decrypt roundtrip from
memora_admin.services.voucher.crypto.
"""

import unittest

from cryptography.fernet import InvalidToken

from memora_admin.memora_admin.services.voucher.crypto import decrypt_data, encrypt_data


class TestCrypto(unittest.TestCase):
	"""Test Fernet encryption/decryption roundtrip and error handling."""

	def test_encrypt_decrypt_roundtrip(self):
		"""FR-016: Encrypt and decrypt data preserves original plaintext."""
		plaintext = b"serial_no,pin\nVCH-000001,ABCDEF123456"
		secret = "test-secret"

		encrypted = encrypt_data(plaintext, secret)
		decrypted = decrypt_data(encrypted, secret)

		self.assertEqual(decrypted, plaintext, "Decrypted data should match original plaintext")

	def test_ciphertext_differs_from_plaintext(self):
		"""FR-017: Encrypted data differs from original plaintext."""
		plaintext = b"serial_no,pin\nVCH-000001,ABCDEF123456"
		secret = "test-secret"

		encrypted = encrypt_data(plaintext, secret)

		self.assertNotEqual(
			encrypted,
			plaintext,
			"Ciphertext should differ from plaintext",
		)

	def test_wrong_secret_raises_error(self):
		"""FR-018: Wrong secret raises InvalidToken on decryption."""
		plaintext = b"serial_no,pin\nVCH-000001,ABCDEF123456"
		secret = "test-secret"
		wrong_secret = "wrong-secret"

		encrypted = encrypt_data(plaintext, secret)

		with self.assertRaises(InvalidToken):
			decrypt_data(encrypted, wrong_secret)
