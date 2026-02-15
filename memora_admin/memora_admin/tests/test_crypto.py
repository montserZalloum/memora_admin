"""Unit tests for voucher crypto service functions.

Tests Fernet encrypt/decrypt roundtrip from
memora_admin.services.voucher.crypto.
"""

import unittest

from cryptography.fernet import InvalidToken

from memora_admin.memora_admin.services.voucher.crypto import decrypt_data, encrypt_data


class TestCrypto(unittest.TestCase):
	pass
