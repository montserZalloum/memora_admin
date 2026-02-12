# Copyright (c) 2026, corex and contributors
# For license information, please see license.txt

import re

import frappe
from frappe.model.document import Document

MOBILE_PATTERN = re.compile(r"^\d{9,15}$")


class MemoraPlayerProfile(Document):
	__new_password = None

	def __setup__(self):
		# Prevent Frappe's _save_passwords() from storing the password as
		# Fernet-encrypted (encrypted=1). We handle hashing manually via
		# update_password() which stores PBKDF2-SHA256 (encrypted=0).
		# check_password() queries encrypted=0 ONLY, so without this flag
		# password verification would always fail.
		self.flags.ignore_save_passwords = ["password"]

	def validate(self):
		# 1. Capture raw password before Frappe can touch it
		if self.password and not self.is_dummy_password(self.password):
			self.__new_password = self.password
			self.password = ""

		# 2. Password policy: minimum 8 characters
		if self.__new_password and len(self.__new_password) < 8:
			frappe.throw("Password must be at least 8 characters", frappe.ValidationError)

		# 3. Phone normalization: strip non-digits, validate length
		if self.mobile:
			self.mobile = self._normalize_mobile(self.mobile)

		# 4. Mobile mandatory for new documents only
		#    (existing records may not have mobile yet)
		if self.is_new() and not self.mobile:
			frappe.throw("Mobile number is required for new players", frappe.ValidationError)

	def after_insert(self):
		"""Hash password and create wallet for new players."""
		self._hash_password()
		self._create_player_wallet()

	def on_update(self):
		"""Hash password on every save (guarded by __new_password check)."""
		self._hash_password()

	def _hash_password(self):
		"""Store password as PBKDF2-SHA256 hash in __Auth table (encrypted=0)."""
		if self.__new_password:
			from frappe.utils.password import update_password

			update_password(
				self.name,
				self.__new_password,
				doctype="Memora Player Profile",
				fieldname="password",
			)
			self.__new_password = None

	@staticmethod
	def _normalize_mobile(mobile: str) -> str:
		"""Strip non-digit characters and validate 9-15 digit length."""
		cleaned = re.sub(r"[^\d]", "", mobile)
		if not MOBILE_PATTERN.match(cleaned):
			frappe.throw("Mobile number must be 9-15 digits", frappe.ValidationError)
		return cleaned

	def _create_player_wallet(self):
		"""Create a Player Wallet record for this player."""
		# Check if wallet already exists (safety check)
		existing = frappe.db.get_value("Memora Player Wallet", {"player": self.name}, "name")
		if existing:
			return

		# Create new wallet
		wallet = frappe.get_doc(
			{
				"doctype": "Memora Player Wallet",
				"player": self.name,
				"total_xp": 0,
				"current_streak": 0,
				"dirty_flag": 0,
				"status": "Active",
				"total_lessons": 0,
				"total_time_min": 0,
			}
		)
		wallet.insert(ignore_permissions=True)
		frappe.msgprint(f"Created wallet {wallet.name} for player {self.name}")
