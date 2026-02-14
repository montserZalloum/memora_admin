"""Voucher batch generation API.

Provides the whitelisted generate_batch() entry point and the
generate_cards_job() background worker that creates all cards,
builds an encrypted export, and transitions the batch to Generated.
"""

import frappe
from frappe.utils import now as frappe_now

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
			frappe.db.get_value("Memora Product Grant", grant.product_grant, "grant_label")
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
