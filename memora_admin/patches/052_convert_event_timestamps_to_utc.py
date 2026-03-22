"""Convert Live Challenge Event timestamps from Asia/Amman to UTC.

Existing scheduled_start, exam_start_ts, and exam_end_ts values were stored
in Asia/Amman local time. This one-time migration converts them to UTC.

Already applied manually on 2026-03-22. This patch is a no-op now;
_compute_timestamps() handles UTC conversion for all new/edited events.
"""


def execute():
	pass
