"""VAPID key pair generation for Web Push."""

import base64

from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from py_vapid import Vapid


def generate_vapid_keypair() -> tuple[str, str]:
	"""Generate an ECDSA P-256 key pair for VAPID, base64url-encoded (no padding).

	Returns:
		(public_key_b64url, private_key_b64url)
	"""
	vapid = Vapid()
	vapid.generate_keys()

	raw_pub = vapid.public_key.public_bytes(Encoding.X962, PublicFormat.UncompressedPoint)
	public_key_b64 = base64.urlsafe_b64encode(raw_pub).rstrip(b"=").decode()

	raw_priv = vapid.private_key.private_numbers().private_value.to_bytes(32, "big")
	private_key_b64 = base64.urlsafe_b64encode(raw_priv).rstrip(b"=").decode()

	return public_key_b64, private_key_b64
