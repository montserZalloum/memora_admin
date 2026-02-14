"""Cryptographic utilities for voucher export encryption.

Uses HKDF-SHA256 to derive a Fernet key from the voucher HMAC secret,
providing authenticated encryption for exported CSV files containing PINs.
"""

import base64

from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

# Fixed, versioned derivation parameters (not secret)
HKDF_SALT = b"memora-voucher-export-v1"
HKDF_INFO = b"fernet-encryption-key"


def get_fernet_key(hmac_secret: str) -> bytes:
	"""Derive a 32-byte Fernet-compatible key from the HMAC secret via HKDF-SHA256.

	Returns base64url-encoded key bytes suitable for Fernet constructor.
	"""
	hkdf = HKDF(
		algorithm=hashes.SHA256(),
		length=32,
		salt=HKDF_SALT,
		info=HKDF_INFO,
	)
	raw_key = hkdf.derive(hmac_secret.encode("utf-8"))
	return base64.urlsafe_b64encode(raw_key)


def get_fernet(hmac_secret: str) -> Fernet:
	"""Return a Fernet instance using the HKDF-derived key."""
	return Fernet(get_fernet_key(hmac_secret))


def encrypt_data(data: bytes, hmac_secret: str) -> bytes:
	"""Encrypt raw bytes using Fernet with the HKDF-derived key."""
	return get_fernet(hmac_secret).encrypt(data)


def decrypt_data(encrypted: bytes, hmac_secret: str) -> bytes:
	"""Decrypt Fernet-encrypted bytes using the HKDF-derived key."""
	return get_fernet(hmac_secret).decrypt(encrypted)
