"""Setup module for Memora Admin app.

Runs after bench install-app to create required roles.
after_migrate ensures database schema extensions (UUID polyfills, BINARY column
overrides, RANGE partitioning, composite indexes) survive Frappe migrations.
"""

import frappe


def after_install():
	"""Create custom roles after app installation."""
	create_task_admin_role()


def create_task_admin_role():
	"""Create Task Admin role for scheduled task operations.

	Grants:
	- Read/write access to Memora Task Run Log
	- Ability to trigger manual task runs (via API)
	- View task dashboard page
	"""
	if frappe.db.exists("Role", "Task Admin"):
		return  # Already exists

	role = frappe.get_doc({
		"doctype": "Role",
		"role_name": "Task Admin",
		"desk_access": 1,
		"is_custom": 1,
	})
	role.insert(ignore_permissions=True)
	frappe.db.commit()
	print("Created Task Admin role")


def after_migrate():
	"""Ensure Memory State schema extensions survive Frappe migrations.

	Operations (all idempotent):
	1. UUID_TO_BIN / BIN_TO_UUID polyfill stored functions
	2. name column override from varchar to BIGINT + sequence creation
	3. item_id column override from varchar to BINARY(16)
	4. RANGE partitioning by season_seq with composite PK
	5. Unique + composite indexes for query performance
	"""
	try:
		_ensure_uuid_polyfill_functions()
	except Exception as e:
		print(f"[after_migrate] UUID polyfill setup failed: {e}")

	try:
		_ensure_name_bigint_column()
	except Exception as e:
		print(f"[after_migrate] name BIGINT override failed: {e}")

	try:
		_ensure_item_id_binary_column()
	except Exception as e:
		print(f"[after_migrate] item_id BINARY override failed: {e}")

	try:
		_ensure_memory_state_partitioning()
	except Exception as e:
		print(f"[after_migrate] Partitioning setup failed: {e}")


def _ensure_uuid_polyfill_functions():
	"""Create UUID_TO_BIN and BIN_TO_UUID polyfill stored functions.

	MariaDB 10.6 lacks these (MySQL 8.0 only). Uses UNHEX/HEX under the hood.
	Idempotent: checks INFORMATION_SCHEMA before creating.
	"""
	result = frappe.db.sql("""
		SELECT ROUTINE_NAME FROM INFORMATION_SCHEMA.ROUTINES
		WHERE ROUTINE_SCHEMA = DATABASE()
		AND ROUTINE_NAME = 'UUID_TO_BIN'
	""")
	if result:
		return  # Already exists

	# UUID_TO_BIN: single-expression function
	frappe.db.sql_ddl("DROP FUNCTION IF EXISTS UUID_TO_BIN")
	frappe.db.sql_ddl("""
		CREATE FUNCTION UUID_TO_BIN(uuid CHAR(36))
		RETURNS BINARY(16) DETERMINISTIC NO SQL
		RETURN UNHEX(REPLACE(uuid, '-', ''))
	""")

	# BIN_TO_UUID: multi-statement function with BEGIN/END block.
	# frappe.db.sql_ddl() sends this as a single command via the MariaDB
	# programmatic connector (PyMySQL). BEGIN/END blocks do NOT require
	# DELIMITER changes when sent programmatically (DELIMITER is a mysql
	# CLI feature only).
	frappe.db.sql_ddl("DROP FUNCTION IF EXISTS BIN_TO_UUID")
	frappe.db.sql_ddl("""
		CREATE FUNCTION BIN_TO_UUID(b BINARY(16))
		RETURNS CHAR(36) DETERMINISTIC NO SQL
		BEGIN
			DECLARE hexStr CHAR(32);
			SET hexStr = HEX(b);
			RETURN LOWER(CONCAT(
				SUBSTR(hexStr, 1, 8), '-',
				SUBSTR(hexStr, 9, 4), '-',
				SUBSTR(hexStr, 13, 4), '-',
				SUBSTR(hexStr, 17, 4), '-',
				SUBSTR(hexStr, 21)
			));
		END
	""")
	print("[after_migrate] Created UUID_TO_BIN and BIN_TO_UUID polyfill functions")


def _ensure_name_bigint_column():
	"""Override name column from varchar(140) to BIGINT and create sequence.

	Frappe's autoname="autoincrement" creates BIGINT + sequence for NEW tables,
	but doesn't alter existing tables. Since this table was originally created with
	format-based autoname (varchar PK), we must explicitly convert it.

	The sequence is used by Frappe's ORM to generate autoincrement names via
	frappe.db.get_next_val() instead of relying on column-level AUTO_INCREMENT.
	"""
	result = frappe.db.sql("""
		SELECT COLUMN_TYPE FROM INFORMATION_SCHEMA.COLUMNS
		WHERE TABLE_SCHEMA = DATABASE()
		AND TABLE_NAME = 'tabMemora Memory State'
		AND COLUMN_NAME = 'name'
	""")
	if not result:
		return

	column_type = result[0][0]
	if isinstance(column_type, bytes):
		column_type = column_type.decode()

	if column_type == "bigint(20)":
		# Already correct, just ensure sequence exists
		frappe.db.create_sequence("Memora Memory State", check_not_exists=True)
		return

	# Table must be empty for varchar->bigint conversion (we truncate during partitioning)
	row_count = frappe.db.sql(
		"SELECT COUNT(*) FROM `tabMemora Memory State`"
	)[0][0]
	if row_count > 0:
		print("[after_migrate] WARNING: Cannot convert name to BIGINT - table has data. Skipping.")
		return

	# Check if table is partitioned (must handle PK differently)
	partitions = frappe.db.sql("""
		SELECT PARTITION_NAME FROM INFORMATION_SCHEMA.PARTITIONS
		WHERE TABLE_SCHEMA = DATABASE()
		AND TABLE_NAME = 'tabMemora Memory State'
		AND PARTITION_NAME IS NOT NULL
		LIMIT 1
	""")

	if partitions:
		# Partitioned table: drop partitioning, alter column, re-partition
		frappe.db.sql_ddl("ALTER TABLE `tabMemora Memory State` REMOVE PARTITIONING")
		frappe.db.sql_ddl("""
			ALTER TABLE `tabMemora Memory State`
			DROP PRIMARY KEY,
			MODIFY COLUMN `name` BIGINT NOT NULL,
			ADD PRIMARY KEY (name, season_seq)
		""")
		frappe.db.sql_ddl("""
			ALTER TABLE `tabMemora Memory State`
			PARTITION BY RANGE (season_seq) (
				PARTITION p_season_1 VALUES LESS THAN (2),
				PARTITION p_future VALUES LESS THAN MAXVALUE
			)
		""")
	else:
		# Non-partitioned: simpler alter
		frappe.db.sql_ddl("""
			ALTER TABLE `tabMemora Memory State`
			DROP PRIMARY KEY,
			MODIFY COLUMN `name` BIGINT NOT NULL,
			ADD PRIMARY KEY (name)
		""")

	# Create Frappe sequence for autoincrement name generation
	frappe.db.create_sequence("Memora Memory State", check_not_exists=True)
	print("[after_migrate] Changed name column to BIGINT and created sequence")


def _ensure_item_id_binary_column():
	"""Override item_id column from varchar (Frappe default for Data) to BINARY(16).

	Frappe's DocType JSON does not support BINARY column type, so item_id is
	defined as fieldtype Data. This function overrides the actual column type
	after Frappe creates/modifies it.
	"""
	result = frappe.db.sql("""
		SELECT COLUMN_TYPE FROM INFORMATION_SCHEMA.COLUMNS
		WHERE TABLE_SCHEMA = DATABASE()
		AND TABLE_NAME = 'tabMemora Memory State'
		AND COLUMN_NAME = 'item_id'
	""")
	if not result:
		return  # Column doesn't exist yet (first migrate may not have created it)

	column_type = result[0][0]
	if column_type == b"binary(16)" or column_type == "binary(16)":
		return  # Already correct

	frappe.db.sql_ddl("""
		ALTER TABLE `tabMemora Memory State`
		MODIFY COLUMN `item_id` BINARY(16) NOT NULL
	""")
	print(f"[after_migrate] Changed item_id column from {column_type} to BINARY(16)")


def _ensure_memory_state_partitioning():
	"""Set up RANGE partitioning by season_seq on Memora Memory State.

	Steps (all idempotent):
	1. Check if already partitioned
	2. If not: truncate table, drop old index, alter PK to composite, partition, add indexes
	"""
	# Check if already partitioned
	partitions = frappe.db.sql("""
		SELECT PARTITION_NAME FROM INFORMATION_SCHEMA.PARTITIONS
		WHERE TABLE_SCHEMA = DATABASE()
		AND TABLE_NAME = 'tabMemora Memory State'
		AND PARTITION_NAME IS NOT NULL
		LIMIT 1
	""")
	if partitions:
		# Already partitioned -- ensure indexes exist (they may have been dropped by Frappe migrate)
		_ensure_memory_state_indexes()
		return

	print("[after_migrate] Setting up RANGE partitioning on tabMemora Memory State...")

	# Truncate table (no production data -- system is new per roadmap decision)
	frappe.db.sql_ddl("TRUNCATE TABLE `tabMemora Memory State`")

	# Drop old composite index if it exists (from Phase 25)
	try:
		frappe.db.sql_ddl(
			"DROP INDEX `player_subject_next_review_index` ON `tabMemora Memory State`"
		)
		print("[after_migrate] Dropped old player_subject_next_review_index")
	except Exception:
		pass  # Index may not exist

	# Alter PK to composite (name, season_seq) -- required for RANGE partitioning
	frappe.db.sql_ddl("""
		ALTER TABLE `tabMemora Memory State`
		DROP PRIMARY KEY,
		ADD PRIMARY KEY (name, season_seq)
	""")
	print("[after_migrate] Changed PK to composite (name, season_seq)")

	# Apply RANGE partitioning by season_seq
	frappe.db.sql_ddl("""
		ALTER TABLE `tabMemora Memory State`
		PARTITION BY RANGE (season_seq) (
			PARTITION p_season_1 VALUES LESS THAN (2),
			PARTITION p_future VALUES LESS THAN MAXVALUE
		)
	""")
	print("[after_migrate] Applied RANGE partitioning (p_season_1, p_future)")

	# Add indexes
	_ensure_memory_state_indexes()


def _ensure_memory_state_indexes():
	"""Create unique and composite indexes on Memora Memory State.

	Indexes:
	- idx_player_item_season: UNIQUE(player, item_id, season_seq) -- prevents duplicates
	- idx_review_query: INDEX(player, subject, next_review, season_seq) -- review queries
	"""
	# Check which indexes already exist
	existing = frappe.db.sql("""
		SELECT DISTINCT INDEX_NAME FROM INFORMATION_SCHEMA.STATISTICS
		WHERE TABLE_SCHEMA = DATABASE()
		AND TABLE_NAME = 'tabMemora Memory State'
		AND INDEX_NAME IN ('idx_player_item_season', 'idx_review_query')
	""")
	existing_names = {row[0] for row in existing}

	if "idx_player_item_season" not in existing_names:
		frappe.db.sql_ddl("""
			ALTER TABLE `tabMemora Memory State`
			ADD UNIQUE INDEX idx_player_item_season (player, item_id, season_seq)
		""")
		print("[after_migrate] Created UNIQUE INDEX idx_player_item_season")

	if "idx_review_query" not in existing_names:
		frappe.db.sql_ddl("""
			ALTER TABLE `tabMemora Memory State`
			ADD INDEX idx_review_query (player, subject, next_review, season_seq)
		""")
		print("[after_migrate] Created INDEX idx_review_query")
