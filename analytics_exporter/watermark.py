"""Watermark state management for incremental exports.

Watermark file format: JSON at {analytics_output_path}/.watermark.json
{
  "practice_log": {
    "last_watermark": "<ISO datetime>",
    "last_export_at": "<ISO datetime>",
    "last_row_count": <int>
  }
}

Writes are atomic via .tmp file + os.replace() to prevent corruption on interruption.
"""

import json
import os


def load_watermark(watermark_path: str) -> dict | None:
	"""Load watermark state from JSON file.

	Returns None if the file does not exist.
	"""
	if not os.path.exists(watermark_path):
		return None
	with open(watermark_path) as f:
		return json.load(f)


def save_watermark(watermark_path: str, data: dict) -> None:
	"""Save watermark state atomically via .tmp rename.

	Writes to {watermark_path}.tmp then calls os.replace() so a crash during
	the write cannot leave a partial/corrupt watermark file.
	"""
	tmp_path = watermark_path + ".tmp"
	with open(tmp_path, "w") as f:
		json.dump(data, f, indent=2)
	os.replace(tmp_path, watermark_path)
