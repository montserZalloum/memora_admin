"""Unit tests for live sync executor edge cases."""

from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import MagicMock, patch

import pyarrow as pa
import pyarrow.parquet as pq

from archive_executor.config import Config
from archive_executor.live_sync import _process_pending_live_jobs
from archive_executor.schemas import load_archive_type, load_sync_type


def _make_config(base_dir: str) -> Config:
	return Config(
		db_host="127.0.0.1",
		db_port=3306,
		db_user="test",
		db_password="test",
		db_name="test",
		archive_output_path=str(Path(base_dir) / "archive") + "/",
		schema_registry_path=str(Path(__file__).resolve().parents[2] / "archive_schemas"),
		log_path=str(Path(base_dir) / "logs") + "/",
		lock_file=str(Path(base_dir) / "archive.lock"),
		chunk_size=1000,
		stuck_timeout_hours=1,
		ssh_host="",
		ssh_user="",
		ssh_key_path="",
		ssh_port=22,
		ssh_timeout=300,
		remote_archive_path="",
		remote_live_path="",
		analytics_cmd_path="",
		duckdb_path="",
		live_output_path=str(Path(base_dir) / "live") + "/",
		live_lock_file=str(Path(base_dir) / "live.lock"),
		sync_state_path=str(Path(base_dir) / "sync_state") + "/",
		sync_output_path=str(Path(base_dir) / "sync_output") + "/",
		sync_overlap_seconds=300,
		sync_remote_path="",
		purge_grace_days=7,
		snapshot_output_path=str(Path(base_dir) / "snapshots") + "/",
		remote_snapshot_path="",
	)


def test_practice_log_live_schema_filters_orphan_review_items():
	registry_path = str(Path(__file__).resolve().parents[2] / "archive_schemas")
	schema = load_sync_type(registry_path, "practice_log_live", "v1")
	query = schema["fact_sql"]["full_snapshot"]

	assert "INNER JOIN `tabMemora Review Item`" in query
	assert "pl.`item_id` = ri.`item_id`" in query


def test_practice_log_archive_schema_filters_orphan_review_items():
	registry_path = str(Path(__file__).resolve().parents[2] / "archive_schemas")
	schema = load_archive_type(registry_path, "practice_log", "v1")

	for query in (schema["fact_sql"]["filtered"], schema["fact_sql"]["full_snapshot"]):
		assert "INNER JOIN `tabMemora Review Item`" in query
		assert "pl.`item_id` = ri.`item_id`" in query


def test_process_pending_live_jobs_publishes_empty_snapshot():
	"""An empty live dataset must still be published so analytics replaces
	any stale non-empty snapshot with the correct empty state."""
	with TemporaryDirectory() as tmpdir:
		config = _make_config(tmpdir)
		log = MagicMock()
		staging_dir = Path(config.live_output_path) / ".staging" / "LSYNC-00001"
		staging_dir.mkdir(parents=True, exist_ok=True)
		fact_path = staging_dir / "fact_practice_log_live.parquet"
		pq.write_table(pa.table({"player_id": pa.array([], type=pa.string())}), fact_path)

		job = {
			"name": "LSYNC-00001",
			"sync_type": "practice_log_live",
			"schema_version": "v1",
			"job_meta": json.dumps({"source_table": "tabMemora Practice Log"}),
			"retry_count": 0,
		}
		schema = {
			"sync_type": "practice_log_live",
			"source_table": "tabMemora Practice Log",
			"fact_columns": ["player_id"],
			"schema_snapshot": {"columns": [{"name": "player_id", "type": "VARCHAR(140)"}]},
			"dimensions": [],
		}

		fact_size = fact_path.stat().st_size

		with patch("archive_executor.live_sync._get_jobs_by_status", return_value=[job]), patch(
			"archive_executor.live_sync._claim_job", return_value=True
		), patch(
			"archive_executor.live_sync._is_source_paused", return_value=False
		), patch(
			"archive_executor.live_sync.load_sync_type", return_value=schema
		), patch(
			"archive_executor.live_sync._get_completed_archive_ranges", return_value=[]
		), patch(
			"archive_executor.live_sync.export_fact_data",
			return_value=(str(fact_path), 0, {}),
		), patch(
			"archive_executor.live_sync.validate_file",
			return_value={
				"valid": True,
				"filename": "fact_practice_log_live.parquet",
				"row_count": 0,
				"checksum": "sha256:test",
				"size_bytes": fact_size,
			},
		), patch(
			"archive_executor.live_sync._update_stage"
		), patch(
			"archive_executor.live_sync._mark_completed_empty"
		) as mock_mark_empty, patch(
			"archive_executor.live_sync._mark_exported"
		) as mock_mark_exported:
			processed = _process_pending_live_jobs(config, log)

		assert processed == 1
		# Empty dataset is published and marked Exported (not completed_empty)
		# so the transfer phase sends the empty state to analytics.
		mock_mark_empty.assert_not_called()
		mock_mark_exported.assert_called_once()
		assert mock_mark_exported.call_args.args[2] == 0  # row_count

		# staging_dir was swapped to final_dir
		final_dir = Path(config.live_output_path) / "LSYNC-00001"
		assert final_dir.exists()
		assert not staging_dir.exists()

		# manifest was written
		manifest_path = final_dir / "manifest.json"
		assert manifest_path.exists()
		manifest = json.loads(manifest_path.read_text())
		assert manifest["files"][0]["row_count"] == 0

		log.info.assert_any_call(
			"live_job_exported_empty",
			job="LSYNC-00001",
			reason="fact_has_0_rows_after_exclusions",
			duration_s=mock_mark_exported.call_args.args[6],
		)
