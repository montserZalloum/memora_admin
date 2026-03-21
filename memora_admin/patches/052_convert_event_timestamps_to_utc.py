"""Convert Live Challenge Event timestamps from Asia/Amman to UTC.

Existing scheduled_start, exam_start_ts, and exam_end_ts values were stored
in Asia/Amman local time. This one-time migration converts them to UTC.
"""

import frappe


def execute():
	# Only convert rows that have a non-null scheduled_start.
	# CONVERT_TZ requires timezone tables loaded in MySQL;
	# fall back to fixed offset if they aren't.
	# Asia/Amman has been UTC+3 year-round since 2023 (no DST),
	# so a fixed -3h shift is correct for all existing data.
	has_tz_data = frappe.db.sql(
		"SELECT 1 FROM mysql.time_zone_name WHERE Name = 'Asia/Amman' LIMIT 1"
	)

	if has_tz_data:
		frappe.db.sql("""
			UPDATE `tabMemora Live Challenge Event`
			SET
				scheduled_start = CONVERT_TZ(scheduled_start, 'Asia/Amman', 'UTC'),
				exam_start_ts   = CONVERT_TZ(exam_start_ts, 'Asia/Amman', 'UTC'),
				exam_end_ts     = CONVERT_TZ(exam_end_ts, 'Asia/Amman', 'UTC')
			WHERE scheduled_start IS NOT NULL
		""")
	else:
		# Fallback: Asia/Amman is UTC+3 (no DST since 2023)
		frappe.db.sql("""
			UPDATE `tabMemora Live Challenge Event`
			SET
				scheduled_start = DATE_SUB(scheduled_start, INTERVAL 3 HOUR),
				exam_start_ts   = DATE_SUB(exam_start_ts, INTERVAL 3 HOUR),
				exam_end_ts     = DATE_SUB(exam_end_ts, INTERVAL 3 HOUR)
			WHERE scheduled_start IS NOT NULL
		""")

	frappe.db.commit()
