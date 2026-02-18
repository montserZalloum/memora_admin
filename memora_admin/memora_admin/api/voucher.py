"""Voucher batch generation and redemption API.

Provides the whitelisted generate_batch() entry point and the
generate_cards_job() background worker that creates all cards,
builds an encrypted export, and transitions the batch to Generated.

Also provides export_for_print() for decrypted CSV download,
void_batch() for bulk voiding, void_card() for single card voiding,
and preview_voucher() / redeem_voucher() for PIN-based redemption.
"""

import csv
import hmac as hmac_module
import io

import frappe
from frappe.utils import now as frappe_now

from memora_admin.memora_admin.api.products import get_grant_keys

from memora_admin.memora_admin.services.voucher.crypto import decrypt_data
from memora_admin.memora_admin.services.voucher.generator import (
	build_export_csv,
	compute_hmac,
	create_encrypted_export,
	generate_pin,
	reserve_serial_block,
)

MAX_BATCH_QUANTITY = 1000


@frappe.whitelist()
def generate_batch(batch_name: str) -> dict:
	"""Enqueue a background job to generate all cards for a batch.

	Validates that the batch is in Draft status and within quantity limits,
	then enqueues generate_cards_job for async execution.
	"""
	batch = frappe.get_doc("Memora Voucher Batch", batch_name)

	if batch.status != "Draft":
		frappe.throw(
			f"Batch must be in Draft status to generate cards. Current status: {batch.status}",
			frappe.ValidationError,
		)

	quantity = batch.quantity or 0
	if quantity <= 0:
		frappe.throw("Batch quantity must be greater than 0.", frappe.ValidationError)

	if quantity > MAX_BATCH_QUANTITY:
		frappe.throw(
			f"Batch quantity ({quantity}) exceeds maximum of {MAX_BATCH_QUANTITY}.",
			frappe.ValidationError,
		)

	hmac_secret = frappe.conf.get("voucher_hmac_secret")
	if not hmac_secret:
		frappe.throw(
			"voucher_hmac_secret is not configured in site_config.json. "
			"Please set it before generating cards.",
			frappe.ValidationError,
		)

	frappe.enqueue(
		"memora_admin.memora_admin.api.voucher.generate_cards_job",
		batch_name=batch_name,
		queue="default",
		timeout=600,
		job_name=f"voucher_gen_{batch_name}",
		enqueue_after_commit=True,
	)

	frappe.msgprint(
		f"Card generation has been queued for batch {batch_name}. "
		"You will be notified when it completes.",
		alert=True,
		indicator="blue",
	)

	return {"status": "enqueued"}


def generate_cards_job(batch_name: str) -> None:
	"""Background job: generate all cards, create encrypted export, transition batch.

	Steps:
	1. Load batch configuration
	2. Reserve a contiguous serial number block
	3. Generate PINs + HMAC hashes for each card
	4. Bulk-insert all cards in a single operation
	5. Build and encrypt the CSV export file
	6. Attach the encrypted file to the batch
	7. Update batch counters and transition to Generated
	8. Commit all changes atomically

	On any failure, rolls back everything and notifies the user.
	"""
	try:
		# --- 1. Load batch config ---
		batch = frappe.get_doc("Memora Voucher Batch", batch_name)
		quantity = batch.quantity
		pin_length = int(batch.pin_length)
		face_value = str(batch.face_value or "0")
		hmac_secret = frappe.conf.get("voucher_hmac_secret")

		# Build product names string from batch grants
		product_names = ", ".join(
			frappe.db.get_value("Memora Product Grant", grant.product_grant, "item_code")
			or grant.product_grant
			for grant in batch.batch_grants
		)

		# --- 2. Reserve serial block ---
		serials = reserve_serial_block(quantity)

		# --- 3. Generate PINs and HMACs ---
		timestamp = frappe_now()
		user = frappe.session.user

		# Collect bulk_insert rows and plaintext data for export
		insert_rows = []
		export_data = []

		for i, serial in enumerate(serials):
			pin = generate_pin(pin_length)
			pin_hmac = compute_hmac(pin, hmac_secret)

			insert_rows.append((
				serial,        # name (document PK = serial_no)
				serial,        # serial_no
				pin_hmac,      # HMAC-SHA256 hash
				batch_name,    # batch link
				"Available",   # status
				user,          # owner
				timestamp,     # creation
				timestamp,     # modified
				user,          # modified_by
				0,             # docstatus
			))

			export_data.append({"serial_no": serial, "pin": pin})

			# Report progress every 100 cards
			if (i + 1) % 100 == 0 or (i + 1) == quantity:
				frappe.publish_progress(
					percent=int(((i + 1) / quantity) * 70),  # 0-70% for generation
					title="Generating Cards",
					description=f"Generated {i + 1} of {quantity} cards",
				)

		# --- 4. Bulk insert all cards ---
		fields = [
			"name", "serial_no", "pin_hmac", "batch", "status",
			"owner", "creation", "modified", "modified_by", "docstatus",
		]

		frappe.db.bulk_insert(
			"Memora Voucher Card",
			fields,
			insert_rows,
			chunk_size=10_000,  # All cards in one chunk (max 1000)
		)

		frappe.publish_progress(
			percent=80,
			title="Generating Cards",
			description="Cards inserted, building export...",
		)

		# --- 5. Build and encrypt CSV export ---
		csv_bytes = build_export_csv(export_data, product_names, face_value)
		encrypted_bytes = create_encrypted_export(csv_bytes, hmac_secret)

		frappe.publish_progress(
			percent=90,
			title="Generating Cards",
			description="Saving encrypted export file...",
		)

		# --- 6. Attach encrypted file to batch ---
		file_doc = frappe.get_doc({
			"doctype": "File",
			"file_name": f"{batch_name}_cards.enc",
			"attached_to_doctype": "Memora Voucher Batch",
			"attached_to_name": batch_name,
			"content": encrypted_bytes,
			"is_private": 1,
		})
		file_doc.save(ignore_permissions=True)

		# --- 7. Update batch ---
		frappe.db.set_value(
			"Memora Voucher Batch",
			batch_name,
			{
				"encrypted_file_url": file_doc.file_url,
				"generated_count": quantity,
				"status": "Generated",
			},
			update_modified=True,
		)

		# --- 8. Commit ---
		frappe.db.commit()

		frappe.publish_progress(
			percent=100,
			title="Generating Cards",
			description="Generation complete!",
		)

		frappe.publish_realtime(
			"batch_generation_complete",
			{"batch_name": batch_name, "count": quantity},
			after_commit=True,
		)

	except Exception:
		frappe.db.rollback()
		frappe.log_error(title=f"Voucher generation failed: {batch_name}")
		frappe.publish_realtime(
			"batch_generation_failed",
			{"batch_name": batch_name, "error": frappe.get_traceback(with_context=True)},
			after_commit=True,
		)
		raise


@frappe.whitelist()
def export_for_print(batch_name: str):
	"""Download decrypted CSV of card PINs for physical printing.

	Restricted to System Manager role. Logs every export in the
	batch's export_log child table for audit purposes.
	"""
	if "System Manager" not in frappe.get_roles():
		frappe.throw("Only System Manager can export", frappe.PermissionError)

	batch = frappe.get_doc("Memora Voucher Batch", batch_name)

	if not batch.encrypted_file_url:
		frappe.throw("No export file available for this batch.")

	# Read the encrypted file from disk
	# file_url is typically "/private/files/filename.enc"
	file_path = frappe.get_site_path(batch.encrypted_file_url.lstrip("/"))
	with open(file_path, "rb") as f:
		encrypted_bytes = f.read()

	hmac_secret = frappe.conf.get("voucher_hmac_secret")
	if not hmac_secret:
		frappe.throw(
			"voucher_hmac_secret is not configured in site_config.json.",
			frappe.ValidationError,
		)

	csv_bytes = decrypt_data(encrypted_bytes, hmac_secret)

	# Filter CSV to only include Available cards
	available_serials = set(
		row[0]
		for row in frappe.db.sql(
			"SELECT serial_no FROM `tabMemora Voucher Card` WHERE batch = %s AND status = 'Available'",
			(batch_name,),
		)
	)

	csv_text = csv_bytes.decode("utf-8")
	reader = csv.DictReader(io.StringIO(csv_text))
	filtered_rows = [row for row in reader if row["serial_no"] in available_serials]

	if not filtered_rows:
		frappe.throw("No available cards to export for this batch.")

	# Rebuild CSV with same format
	output = io.StringIO()
	writer = csv.DictWriter(output, fieldnames=["serial_no", "pin", "product_names", "face_value"])
	writer.writeheader()
	writer.writerows(filtered_rows)
	csv_bytes = output.getvalue().encode("utf-8")

	# Log the export in the child table
	batch.append("export_log", {
		"exported_by": frappe.session.user,
		"exported_at": frappe.utils.now(),
		"card_count": len(filtered_rows),
	})
	batch.save(ignore_permissions=True)
	frappe.db.commit()

	# Serve CSV as file download
	frappe.local.response.filename = f"{batch_name}_pins.csv"
	frappe.local.response.filecontent = csv_bytes
	frappe.local.response.type = "download"


@frappe.whitelist()
def void_batch(batch_name: str, void_reason: str) -> dict:
	"""Void all non-terminal cards in a batch and close it.

	Sets all Available/Allocated cards to Void via direct SQL for performance,
	deletes the encrypted export file, and transitions the batch to Closed.
	Requires a void_reason.
	"""
	if not (void_reason or "").strip():
		frappe.throw("Void reason is required.", frappe.ValidationError)

	void_reason = void_reason.strip()

	batch = frappe.get_doc("Memora Voucher Batch", batch_name)

	if batch.status == "Draft":
		frappe.throw("Cannot void a Draft batch -- no cards exist.", frappe.ValidationError)

	if batch.status == "Closed":
		frappe.throw("Batch is already Closed.", frappe.ValidationError)

	# Void all non-terminal cards via direct SQL (fast for up to 1000 cards)
	frappe.db.sql("""
		UPDATE `tabMemora Voucher Card`
		SET status = 'Void', void_reason = %s, modified = NOW(), modified_by = %s
		WHERE batch = %s AND status IN ('Available', 'Allocated')
	""", (void_reason, frappe.session.user, batch_name))

	voided_count = frappe.db.count("Memora Voucher Card", {"batch": batch_name, "status": "Void"})

	# Delete encrypted export file if it exists
	if batch.encrypted_file_url:
		file_name = frappe.db.get_value(
			"File",
			{
				"file_url": batch.encrypted_file_url,
				"attached_to_doctype": "Memora Voucher Batch",
				"attached_to_name": batch_name,
			},
			"name",
		)
		if file_name:
			frappe.delete_doc("File", file_name, ignore_permissions=True)
		batch.encrypted_file_url = ""

	# Update batch status
	batch.voided_count = voided_count
	batch.status = "Closed"
	batch.void_reason = void_reason
	batch.save(ignore_permissions=True)
	frappe.db.commit()

	return {"voided_count": voided_count, "status": "Closed"}


@frappe.whitelist()
def void_card(card_name: str, void_reason: str) -> dict:
	"""Void a single Available or Allocated card.

	Updates the card status to Void and increments the parent batch's
	voided_count. Requires a void_reason.
	"""
	if not (void_reason or "").strip():
		frappe.throw("Void reason is required.", frappe.ValidationError)

	void_reason = void_reason.strip()

	card = frappe.get_doc("Memora Voucher Card", card_name)

	if card.status not in ("Available", "Allocated"):
		frappe.throw(
			f"Cannot void card with status '{card.status}'. "
			f"Only Available or Allocated cards can be voided.",
			frappe.ValidationError,
		)

	card.status = "Void"
	card.void_reason = void_reason
	card.save(ignore_permissions=True)

	# Update parent batch counters and check auto-close
	from memora_admin.memora_admin.services.voucher.batch_utils import recount_and_maybe_close
	recount_and_maybe_close(card.batch)
	frappe.db.commit()

	return {"status": "Void", "card": card_name}


# ---------------------------------------------------------------------------
# Redemption API
# ---------------------------------------------------------------------------

# Map error codes to Redemption Log status values (must match Select options)
_ERROR_TO_LOG_STATUS = {
	"INVALID_PIN": "Invalid PIN",
	"NOT_ALLOCATED": "Not Allocated",
	"ALREADY_REDEEMED": "Already Redeemed",
	"EXPIRED": "Expired",
	"VOID": "Void",
	"BATCH_INACTIVE": "Batch Inactive",
	"SEASON_INACTIVE": "Season Inactive",
	"ALL_GRANTS_OWNED": "All Grants Owned",
	"GRANT_NOT_IN_BATCH": "Grant Not In Batch",
	"ALREADY_OWNED": "Already Owned",
}

# Map card statuses to error codes for non-Allocated cards
_CARD_STATUS_ERRORS = {
	"Available": "NOT_ALLOCATED",
	"Redeemed": "ALREADY_REDEEMED",
	"Void": "VOID",
	"Expired": "EXPIRED",
}


def _log_attempt(
	player_id,
	pin_masked,
	card,
	library,
	batch,
	requested_grant,
	status,
	failure_reason,
	ip_address,
):
	"""Create an immutable Voucher Redemption Log entry.

	Args:
		player_id: Memora Player Profile name.
		pin_masked: Last 4 chars of pin_hmac (will be prefixed with ****).
		card: Voucher Card name or None.
		library: Customer link or None.
		batch: Voucher Batch name or None.
		requested_grant: Product Grant name or None.
		status: Log status value matching DocType Select options
			("Success", "Invalid PIN", "Already Redeemed", etc.).
		failure_reason: Error code string for failures, empty string for success.
		ip_address: Client IP address.
	"""
	frappe.get_doc({
		"doctype": "Memora Voucher Redemption Log",
		"player": player_id,
		"pin_masked": f"****{pin_masked}",
		"card": card,
		"library": library,
		"batch": batch,
		"requested_grant": requested_grant,
		"status": status,
		"failure_reason": failure_reason,
		"ip_address": ip_address,
		"timestamp": frappe.utils.now(),
	}).insert(ignore_permissions=True)


def _check_season_active(player_id):
	"""Check if the player's plan season is published and not ended.

	Returns True if season is active or no season is configured.
	Returns False if season exists but is inactive or ended.
	"""
	try:
		plan_id = frappe.get_value("Memora Player Profile", player_id, "plan")
		if not plan_id:
			return True  # No plan -- skip season check

		season_id = frappe.get_value("Memora Academic Plan", plan_id, "season")
		if not season_id:
			return True  # No season -- skip check

		season_data = frappe.db.get_value(
			"Memora Season", season_id, ["is_published", "end_date"], as_dict=True
		)
		if not season_data:
			return True  # Season record missing -- skip check

		if not season_data.is_published:
			return False

		if season_data.end_date and season_data.end_date < frappe.utils.today():
			return False

		return True
	except Exception:
		frappe.logger().warning(f"Season check failed for player {player_id}, allowing by default")
		return True


@frappe.whitelist(allow_guest=False)
def preview_voucher(pin_hmac: str, player_id: str) -> dict:
	"""Preview what a voucher card unlocks (read-only, no state change).

	Validates card status, batch status, and season. Returns available
	grants filtering out already-owned ones.

	Args:
		pin_hmac: HMAC-SHA256 hex digest of the PIN.
		player_id: Memora Player Profile name.

	Returns:
		Dict with ``face_value`` and ``grants`` list, or ``{"error": "..."}``
		with a machine-readable error code.
	"""
	# 1. Look up card by HMAC (read-only -- no FOR UPDATE)
	cards = frappe.db.sql(
		"SELECT name, status, batch, pin_hmac FROM `tabMemora Voucher Card` WHERE pin_hmac = %s LIMIT 1",
		(pin_hmac,),
		as_dict=True,
	)

	if not cards:
		return {"error": "INVALID_PIN"}

	card = cards[0]

	# 2. Timing-safe HMAC verification (REDEEM-09)
	if not hmac_module.compare_digest(card.pin_hmac, pin_hmac):
		return {"error": "INVALID_PIN"}

	# 3. Card status validation
	if card.status in _CARD_STATUS_ERRORS:
		return {"error": _CARD_STATUS_ERRORS[card.status]}

	if card.status != "Allocated":
		return {"error": "INVALID_PIN"}  # Unknown status

	# 4. Batch must be Active
	batch_status = frappe.db.get_value("Memora Voucher Batch", card.batch, "status")
	if batch_status != "Active":
		return {"error": "BATCH_INACTIVE"}

	# 5. Season validation via player's plan
	if not _check_season_active(player_id):
		return {"error": "SEASON_INACTIVE"}

	# 6. Build available grants (filter out already-owned)
	batch = frappe.get_doc("Memora Voucher Batch", card.batch)
	available_grants = []

	for bg in batch.batch_grants:
		grant_keys = get_grant_keys(bg.product_grant)
		all_owned = all(
			frappe.db.exists(
				"Memora Player Subscription", {"player": player_id, "access_key": key}
			)
			for key in grant_keys
		)
		if not all_owned:
			display_name = (
				frappe.db.get_value("Memora Product Grant", bg.product_grant, "item_code")
				or bg.product_grant
			)
			available_grants.append({
				"grant_id": bg.product_grant,
				"name": display_name,
			})

	if not available_grants:
		return {"error": "ALL_GRANTS_OWNED"}

	return {
		"face_value": str(batch.face_value or "0"),
		"grants": available_grants,
	}


@frappe.whitelist(allow_guest=False)
def redeem_voucher(
	pin_hmac: str,
	player_id: str,
	product_grant_id: str,
	ip_address: str = "",
) -> dict:
	"""Redeem a voucher card for a specific product grant.

	Uses SELECT FOR UPDATE for atomic card state transition. Creates a
	Subscription Transaction via two-step save which triggers the existing
	Phase 23 pipeline (_handle_approval -> Player Subscriptions + Redis SADD).

	Args:
		pin_hmac: HMAC-SHA256 hex digest of the PIN.
		player_id: Memora Player Profile name.
		product_grant_id: Chosen Product Grant from the batch.
		ip_address: Client IP for audit logging.

	Returns:
		``{"status": "success", "transaction_id": "..."}`` on success,
		or ``{"error": "ERROR_CODE"}`` on failure.
	"""
	# 1. Lock card row with SELECT FOR UPDATE
	cards = frappe.db.sql(
		"SELECT name, status, batch, pin_hmac FROM `tabMemora Voucher Card` "
		"WHERE pin_hmac = %s FOR UPDATE",
		(pin_hmac,),
		as_dict=True,
	)

	if not cards:
		_log_attempt(
			player_id, pin_hmac[-4:], None, None, None,
			product_grant_id, "Invalid PIN", "INVALID_PIN", ip_address,
		)
		return {"error": "INVALID_PIN"}

	card = cards[0]

	# 2. Timing-safe HMAC verification (REDEEM-09)
	if not hmac_module.compare_digest(card.pin_hmac, pin_hmac):
		_log_attempt(
			player_id, pin_hmac[-4:], None, None, None,
			product_grant_id, "Invalid PIN", "INVALID_PIN", ip_address,
		)
		return {"error": "INVALID_PIN"}

	# 3. Card status must be Allocated
	if card.status != "Allocated":
		error_code = _CARD_STATUS_ERRORS.get(card.status, "INVALID_PIN")
		log_status = _ERROR_TO_LOG_STATUS.get(error_code, "Error")
		_log_attempt(
			player_id, pin_hmac[-4:], card.name, None, card.batch,
			product_grant_id, log_status, error_code, ip_address,
		)
		return {"error": error_code}

	# 4. Batch must be Active
	batch_status = frappe.db.get_value("Memora Voucher Batch", card.batch, "status")
	if batch_status != "Active":
		_log_attempt(
			player_id, pin_hmac[-4:], card.name, None, card.batch,
			product_grant_id, "Batch Inactive", "BATCH_INACTIVE", ip_address,
		)
		return {"error": "BATCH_INACTIVE"}

	# 5. Season validation via player's plan
	if not _check_season_active(player_id):
		_log_attempt(
			player_id, pin_hmac[-4:], card.name, None, card.batch,
			product_grant_id, "Season Inactive", "SEASON_INACTIVE", ip_address,
		)
		return {"error": "SEASON_INACTIVE"}

	# 6. Validate grant belongs to batch
	valid_grants = frappe.get_all(
		"Memora Voucher Batch Grant",
		filters={"parent": card.batch},
		pluck="product_grant",
	)
	if product_grant_id not in valid_grants:
		_log_attempt(
			player_id, pin_hmac[-4:], card.name, None, card.batch,
			product_grant_id, "Grant Not In Batch", "GRANT_NOT_IN_BATCH", ip_address,
		)
		return {"error": "GRANT_NOT_IN_BATCH"}

	# 7. Check ALREADY_OWNED (does NOT consume card)
	grant_keys = get_grant_keys(product_grant_id)
	all_owned = all(
		frappe.db.exists(
			"Memora Player Subscription", {"player": player_id, "access_key": key}
		)
		for key in grant_keys
	)
	if all_owned:
		_log_attempt(
			player_id, pin_hmac[-4:], card.name, None, card.batch,
			product_grant_id, "Already Owned", "ALREADY_OWNED", ip_address,
		)
		return {"error": "ALREADY_OWNED"}

	# 8. Mark card as Redeemed
	frappe.db.set_value("Memora Voucher Card", card.name, {
		"status": "Redeemed",
		"redeemed_by": player_id,
		"redeemed_at": frappe.utils.now(),
		"redeemed_grant": product_grant_id,
	})

	# 9. Get card's library for audit log
	library = frappe.db.get_value("Memora Voucher Card", card.name, "library")

	# 10. Get face_value from batch
	face_value = frappe.db.get_value("Memora Voucher Batch", card.batch, "face_value") or 0

	# 11. Create Subscription Transaction with TWO-STEP SAVE
	# Step 1: Insert with Pending Approval (triggers after_insert hook)
	trx = frappe.get_doc({
		"doctype": "Memora Subscription Transaction",
		"player": player_id,
		"payment_method": "Voucher",
		"status": "Pending Approval",
		"related_grant": product_grant_id,
		"amount_paid": face_value,
		"transaction_id": card.name,
	})
	trx.insert(ignore_permissions=True)

	# Step 2: Change status to Completed and save
	# This triggers on_update -> _handle_approval() which creates
	# Player Subscriptions and syncs access to Redis
	trx.status = "Completed"
	trx.save(ignore_permissions=True)

	# 12. Link transaction back to card
	frappe.db.set_value("Memora Voucher Card", card.name, "subscription_transaction", trx.name)

	# 13. Log success
	_log_attempt(
		player_id, pin_hmac[-4:], card.name, library, card.batch,
		product_grant_id, "Success", "", ip_address,
	)

	# 14. Update batch counters and check auto-close
	from memora_admin.memora_admin.services.voucher.batch_utils import recount_and_maybe_close
	recount_and_maybe_close(card.batch)

	frappe.db.commit()

	return {"status": "success", "transaction_id": trx.name}
