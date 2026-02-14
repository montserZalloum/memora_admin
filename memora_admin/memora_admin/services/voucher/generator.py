"""Voucher card generation utilities.

Provides PIN generation, HMAC computation, serial number reservation,
CSV export building, and encrypted export creation.
"""

import csv
import hashlib
import hmac as hmac_module
import io
import secrets

import frappe

from memora_admin.memora_admin.services.voucher.crypto import encrypt_data

# 30 characters: excludes ambiguous 0/O, 1/I/L
PIN_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"


def generate_pin(length: int = 12) -> str:
	"""Generate a cryptographically secure random PIN.

	Uses secrets.choice() for each character to ensure uniform distribution
	from a CSPRNG. Never uses random.choice().
	"""
	return "".join(secrets.choice(PIN_ALPHABET) for _ in range(length))


def compute_hmac(pin: str, secret: str) -> str:
	"""Compute HMAC-SHA256 hex digest of a PIN using the server-side secret.

	The resulting hex string is deterministic and suitable for WHERE clause lookups.
	"""
	return hmac_module.new(
		secret.encode("utf-8"),
		pin.encode("utf-8"),
		hashlib.sha256,
	).hexdigest()


def reserve_serial_block(count: int) -> list[str]:
	"""Reserve a contiguous block of serial numbers atomically.

	Uses tabSeries with FOR UPDATE to guarantee no gaps or collisions,
	even under concurrent generation jobs. Single lock acquisition for
	the entire block -- no per-card locking.

	Returns a list of formatted serial strings: VCH-000001, VCH-000002, etc.
	"""
	series_name = "VCH-SERIAL"

	# Single atomic operation: lock, read, update
	row = frappe.db.sql(
		"SELECT current FROM tabSeries WHERE name = %s FOR UPDATE",
		(series_name,),
		as_dict=True,
	)

	if row:
		start = row[0]["current"] + 1
		new_current = row[0]["current"] + count
		frappe.db.sql(
			"UPDATE tabSeries SET current = %s WHERE name = %s",
			(new_current, series_name),
		)
	else:
		start = 1
		new_current = count
		frappe.db.sql(
			"INSERT INTO tabSeries (name, current) VALUES (%s, %s)",
			(series_name, new_current),
		)

	return [f"VCH-{i:06d}" for i in range(start, start + count)]


def build_export_csv(cards_data: list[dict], product_names: str, face_value: str) -> bytes:
	"""Build a CSV file in memory from card data.

	Each dict in cards_data must have keys "serial_no" and "pin" (plaintext).
	Returns UTF-8 encoded bytes.
	"""
	output = io.StringIO()
	writer = csv.writer(output)
	writer.writerow(["serial_no", "pin", "product_names", "face_value"])
	for card in cards_data:
		writer.writerow([card["serial_no"], card["pin"], product_names, face_value])
	return output.getvalue().encode("utf-8")


def create_encrypted_export(csv_bytes: bytes, hmac_secret: str) -> bytes:
	"""Encrypt CSV bytes using Fernet for secure file storage.

	Returns encrypted bytes ready to be written to private/files/.
	"""
	return encrypt_data(csv_bytes, hmac_secret)
