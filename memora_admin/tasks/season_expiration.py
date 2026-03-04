"""Daily job: expire Available/Allocated cards linked to ended or unpublished seasons.

A card's season is determined by the chain: Card -> Batch -> Batch Grant ->
Product Grant -> Academic Plan -> Season. Cards are expired if ANY of their
batch's grants link to a season that has ended (end_date < today) or is
unpublished (is_published = 0).

Only non-terminal statuses (Available, Allocated) are affected. Redeemed and
Void cards are never modified -- they are terminal states.

Runs daily at 01:05 (cron: 5 1 * * *).
"""

import frappe


def expire_season_cards():
	"""Expire Available/Allocated cards linked to ended or unpublished seasons."""
	today = frappe.utils.today()

	# Step 1: Find batches with at least one grant linked to an ended/unpublished season
	expired_batches = frappe.db.sql(
		"""
		SELECT DISTINCT b.name as batch_name
		FROM `tabMemora Voucher Batch` b
		JOIN `tabMemora Voucher Batch Grant` bg ON bg.parent = b.name
		JOIN `tabMemora Product Grant` pg ON bg.product_grant = pg.name
		JOIN `tabMemora Academic Plan` ap ON pg.plan = ap.name
		JOIN `tabMemora Season` s ON ap.season = s.name
		WHERE b.status IN ('Generated', 'Active')
			AND (s.end_date < %s OR s.is_published = 0)
		""",
		(today,),
		as_dict=True,
	)

	# Step 2: Early return if nothing to process
	if not expired_batches:
		frappe.logger().info("Season expiration: No batches with ended/unpublished seasons")
		return

	total_expired = 0
	batches_processed = 0

	# Step 3-5: Process each batch independently
	for row in expired_batches:
		batch_name = row.batch_name
		try:
			frappe.db.sql(
				"""
				UPDATE `tabMemora Voucher Card`
				SET status = 'Expired', void_reason = 'Season Ended',
					modified = NOW(), modified_by = 'Administrator'
				WHERE batch = %s AND status IN ('Available', 'Allocated')
				""",
				(batch_name,),
			)

			affected = frappe.db.sql("SELECT ROW_COUNT()")[0][0]
			if affected:
				total_expired += affected
				frappe.logger().info(f"Season expiration: {affected} card(s) expired in batch {batch_name}")

			# Recount batch counters and check auto-close condition
			from memora_admin.memora_admin.services.voucher.batch_utils import recount_and_maybe_close

			recount_result = recount_and_maybe_close(batch_name)
			if recount_result["closed"]:
				frappe.logger().info(f"Batch {batch_name} auto-closed during expiration")

			batches_processed += 1

		except Exception:
			frappe.log_error(title=f"Season expiration failed for batch {batch_name}")

	# Step 6: Commit if any cards were expired
	if total_expired:
		frappe.db.commit()

	# Step 7: Log final summary
	frappe.logger().info(
		f"Season expiration complete: {total_expired} card(s) expired "
		f"across {batches_processed} batch(es)"
	)
