"""Unit tests for analytics_exporter.watermark.

TDD: these tests were written before watermark.py was implemented.
Run without DB — pure unit tests using tempfiles.
"""

import json
import os
import tempfile

import pytest

from analytics_exporter.watermark import load_watermark, save_watermark

pytestmark = pytest.mark.unit


class TestLoadWatermark:
	def test_returns_none_when_file_absent(self):
		with tempfile.TemporaryDirectory() as tmpdir:
			path = os.path.join(tmpdir, ".watermark.json")
			result = load_watermark(path)
			assert result is None

	def test_returns_dict_when_file_exists(self):
		with tempfile.TemporaryDirectory() as tmpdir:
			path = os.path.join(tmpdir, ".watermark.json")
			data = {
				"practice_log": {
					"last_watermark": "2026-03-13T00:00:00",
					"last_row_count": 42,
				}
			}
			with open(path, "w") as f:
				json.dump(data, f)
			result = load_watermark(path)
			assert result == data

	def test_returns_correct_watermark_value(self):
		with tempfile.TemporaryDirectory() as tmpdir:
			path = os.path.join(tmpdir, ".watermark.json")
			data = {
				"practice_log": {
					"last_watermark": "2026-01-01T00:00:00",
					"last_row_count": 100,
					"last_export_at": "2026-01-01T01:00:00",
				}
			}
			with open(path, "w") as f:
				json.dump(data, f)
			result = load_watermark(path)
			assert result["practice_log"]["last_watermark"] == "2026-01-01T00:00:00"
			assert result["practice_log"]["last_row_count"] == 100

	def test_returns_none_for_empty_dir_path(self):
		with tempfile.TemporaryDirectory() as tmpdir:
			path = os.path.join(tmpdir, "subdir", ".watermark.json")
			result = load_watermark(path)
			assert result is None


class TestSaveWatermark:
	def test_saves_data_correctly(self):
		with tempfile.TemporaryDirectory() as tmpdir:
			path = os.path.join(tmpdir, ".watermark.json")
			data = {
				"practice_log": {
					"last_watermark": "2026-03-13T02:00:00",
					"last_row_count": 999,
				}
			}
			save_watermark(path, data)
			with open(path) as f:
				loaded = json.load(f)
			assert loaded == data

	def test_atomic_write_no_tmp_leftover(self):
		"""save_watermark must use os.replace() — no .tmp file remains after success."""
		with tempfile.TemporaryDirectory() as tmpdir:
			path = os.path.join(tmpdir, ".watermark.json")
			tmp_path = path + ".tmp"
			data = {"practice_log": {"last_watermark": "2026-03-13T00:00:00", "last_row_count": 1}}
			save_watermark(path, data)
			assert not os.path.exists(tmp_path)
			assert os.path.exists(path)

	def test_overwrites_existing_watermark(self):
		with tempfile.TemporaryDirectory() as tmpdir:
			path = os.path.join(tmpdir, ".watermark.json")
			old_data = {"practice_log": {"last_watermark": "2026-01-01T00:00:00", "last_row_count": 10}}
			save_watermark(path, old_data)
			new_data = {"practice_log": {"last_watermark": "2026-03-13T00:00:00", "last_row_count": 20}}
			save_watermark(path, new_data)
			result = load_watermark(path)
			assert result["practice_log"]["last_row_count"] == 20
			assert result["practice_log"]["last_watermark"] == "2026-03-13T00:00:00"

	def test_round_trip(self):
		with tempfile.TemporaryDirectory() as tmpdir:
			path = os.path.join(tmpdir, ".watermark.json")
			data = {
				"practice_log": {
					"last_watermark": "2026-03-12T02:30:00",
					"last_export_at": "2026-03-13T01:00:00",
					"last_row_count": 1234567,
				}
			}
			save_watermark(path, data)
			loaded = load_watermark(path)
			assert loaded == data
