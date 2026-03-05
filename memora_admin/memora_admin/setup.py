"""Setup module for Memora Admin app.

Runs after bench install-app to create required roles.
before_migrate blocks dangerous ALTER TABLE on the partitioned Memory State table.
after_migrate ensures database schema extensions (UUID polyfills, BINARY column
overrides, RANGE partitioning, composite indexes) survive Frappe migrations.
"""

import frappe


def after_install():
	"""Create custom roles and voucher schema extensions after app installation."""
	create_task_admin_role()
	_setup_voucher_schema()


def create_task_admin_role():
	"""Create Task Admin role for scheduled task operations.

	Grants:
	- Read/write access to Memora Task Run Log
	- Ability to trigger manual task runs (via API)
	- View task dashboard page
	"""
	if frappe.db.exists("Role", "Task Admin"):
		return  # Already exists

	role = frappe.get_doc(
		{
			"doctype": "Role",
			"role_name": "Task Admin",
			"desk_access": 1,
			"is_custom": 1,
		}
	)
	role.insert(ignore_permissions=True)
	frappe.db.commit()
	print("Created Task Admin role")


def before_migrate():
	"""Block Frappe schema sync from running ALTER TABLE on Memora Memory State.

	This hook runs BEFORE Frappe's model sync (which calls updatedb -> MariaDBTable.alter()).
	It monkey-patches frappe.db.updatedb to skip Memora Memory State entirely, preventing
	Frappe from adding/modifying/dropping columns on this RANGE-partitioned table.

	WHY: Memora Memory State is designed for 10+ billion rows with:
	- BIGINT PK (not Frappe's default varchar)
	- BINARY(16) item_id (managed via is_virtual + setup.py)
	- Composite PK (name, season_seq) for RANGE partitioning
	- Custom indexes (dedup + review query) managed by _ensure_memory_state_indexes()

	Any ALTER TABLE on a 10B-row partitioned table could lock it for hours.
	All schema changes MUST go through setup.py with proper safety checks.
	"""
	_guard_memory_state_schema()


def _guard_memory_state_schema():
	"""Monkey-patch frappe.db.updatedb to block schema sync on Memory State.

	Compares the current DB columns against the DocType JSON fields to detect
	if Frappe would attempt any column additions or modifications. If so,
	raises a loud error BEFORE any ALTER TABLE runs.

	Also patches updatedb to skip this table entirely during the sync phase.
	"""
	PROTECTED_TABLE = "Memora Memory State"

	# Check if the table exists yet (skip guard on fresh installs)
	tables = frappe.db.get_tables()
	if f"tab{PROTECTED_TABLE}" not in tables:
		return

	# Snapshot the original updatedb function
	original_updatedb = frappe.db.updatedb

	def guarded_updatedb(doctype, meta=None):
		if doctype == PROTECTED_TABLE:
			# Verify no unexpected schema changes are pending
			_verify_no_schema_drift(PROTECTED_TABLE)
			# Skip Frappe's ALTER TABLE entirely -- our after_migrate handles schema
			return
		return original_updatedb(doctype, meta)

	# Apply the monkey-patch
	frappe.db.updatedb = guarded_updatedb


def _verify_no_schema_drift(doctype: str):
	"""Check if the DocType JSON has fields that would cause Frappe to ALTER the table.

	Compares JSON-defined fields (excluding is_virtual) against actual DB columns.
	If a NEW non-virtual field is found that doesn't exist in the DB, it means
	someone added a field to the JSON without updating setup.py -- raise an error.
	"""
	meta = frappe.get_meta(doctype, cached=False)

	# Get fields Frappe would try to sync (excludes is_virtual)
	json_fields = {
		f.fieldname
		for f in meta.fields
		if not getattr(f, "is_virtual", False) and f.fieldtype not in frappe.model.no_value_fields
	}

	# Get actual DB columns
	db_columns = {col.name.lower() for col in frappe.db.get_table_columns_description(f"tab{doctype}")}

	# Standard Frappe columns that always exist
	standard_cols = {"name", "creation", "modified", "modified_by", "owner", "docstatus", "idx"}
	# Optional Frappe columns
	optional_cols = {"_user_tags", "_comments", "_assign", "_liked_by"}

	# Fields in JSON but not in DB = would trigger ADD COLUMN
	new_fields = json_fields - db_columns - standard_cols - optional_cols
	if new_fields:
		frappe.throw(
			f"BLOCKED: Memora Memory State is a 10B-row RANGE-partitioned table.\n"
			f"The following fields exist in the DocType JSON but not in the database: {new_fields}\n"
			f"Schema changes MUST be done manually via setup.py with proper safety checks.\n"
			f"Do NOT add fields to memora_memory_state.json without updating setup.py.\n"
			f"Remove the new fields from the JSON or add the columns via setup.py first.",
			title="Partitioned Table Protection",
		)


def after_migrate():
	"""Ensure Memory State schema extensions survive Frappe migrations.

	Operations (all idempotent):
	1. UUID_TO_BIN / BIN_TO_UUID polyfill stored functions
	2. name column override from varchar to BIGINT + sequence creation
	3. item_id column override from varchar to BINARY(16)
	4. RANGE partitioning by season_seq with composite PK
	5. Unique + composite indexes for query performance
	6. Voucher schema extensions (custom fields, composite indexes)
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

	try:
		_ensure_fsrs_state_columns()
	except Exception as e:
		print(f"[after_migrate] FSRS state columns setup failed: {e}")

	# Voucher schema extensions
	try:
		_setup_voucher_schema()
	except Exception as e:
		print(f"[after_migrate] Voucher schema setup failed: {e}")

	# Composite indexes on hot tables (PERF-03)
	try:
		_ensure_hot_table_indexes()
	except Exception as e:
		print(f"[after_migrate] Hot table indexes setup failed: {e}")

	# Practice Log raw SQL table
	try:
		_ensure_practice_log_table()
	except Exception as e:
		print(f"[after_migrate] Practice Log table setup failed: {e}")

	# Practice query indexes (keep after Practice Log table creation)
	try:
		_ensure_practice_query_indexes()
	except Exception as e:
		print(f"[after_migrate] Practice query indexes setup failed: {e}")


def _ensure_uuid_polyfill_functions():
	"""Create UUID_TO_BIN and BIN_TO_UUID polyfill stored functions.

	MariaDB 10.6 lacks these (MySQL 8.0 only). Uses UNHEX/HEX under the hood.
	Idempotent: checks INFORMATION_SCHEMA before creating.
	"""
	result = frappe.db.sql("""
		SELECT ROUTINE_NAME FROM INFORMATION_SCHEMA.ROUTINES
		WHERE ROUTINE_SCHEMA = DATABASE()
		AND ROUTINE_NAME IN ('UUID_TO_BIN', 'BIN_TO_UUID')
	""")
	if len(result) == 2:
		return  # Both exist

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
	row_count = frappe.db.sql("SELECT COUNT(*) FROM `tabMemora Memory State`")[0][0]
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
	"""Ensure item_id BINARY(16) column exists. Never ALTER if it already exists.

	SCHEMA REFERENCE -- tabMemora Memory State:
	=============================================
	This is a RANGE-partitioned table designed for 10+ billion rows.
	ALL schema changes MUST go through this file (setup.py), never via JSON + bench migrate.

	Columns (actual DB, not JSON):
	  name          BIGINT NOT NULL          -- PK, auto-generated via Frappe sequence
	  season_seq    INT(11) NOT NULL         -- Partition key (RANGE partitioned)
	  subject       VARCHAR(140)             -- Link to Memora Subject
	  player        VARCHAR(140)             -- Link to Memora Player Profile
	  item_id       BINARY(16) NOT NULL      -- UUID stored as binary (is_virtual in JSON)
	  stage_id      VARCHAR(140)             -- Lesson stage identifier
	  lesson        VARCHAR(140)             -- Link to Memora Lesson
	  stability     DECIMAL(21,9) DEFAULT 0  -- FSRS stability score
	  difficulty    DECIMAL(21,9) DEFAULT 0  -- FSRS difficulty score
	  next_review   DATE                     -- Next scheduled review date
	  creation      DATETIME(6)              -- Frappe standard
	  modified      DATETIME(6)              -- Frappe standard
	  modified_by   VARCHAR(140)             -- Frappe standard
	  owner         VARCHAR(140)             -- Frappe standard
	  docstatus     INT(1) DEFAULT 0         -- Frappe standard
	  idx           INT(8) DEFAULT 0         -- Frappe standard

	Primary Key: (name, season_seq)  -- composite PK required for RANGE partitioning

	Indexes:
	  PRIMARY KEY                          (name, season_seq)
	  UNIQUE idx_player_item_season        (player, item_id, season_seq)  -- dedup
	  INDEX  idx_review_query              (player, subject, next_review, season_seq)  -- review queries

	Partitioning: RANGE(season_seq)
	  p_season_1   VALUES LESS THAN (2)
	  p_season_N   VALUES LESS THAN (N+1)   -- created dynamically by MemoraSeason.after_insert
	  p_future     VALUES LESS THAN MAXVALUE

	SAFETY RULES:
	  1. NEVER use Frappe ORM on this table. Always use frappe.db.sql().
	  2. EVERY query MUST include season_seq in WHERE for partition pruning.
	  3. EVERY query touching item_id MUST use UUID_TO_BIN() / BIN_TO_UUID().
	  4. NEVER add fields to memora_memory_state.json without updating this file.
	  5. NEVER run ALTER TABLE directly in production.
	  6. New indexes must be added here with IF NOT EXISTS / idempotent checks.

	item_id is marked is_virtual in the DocType JSON so Frappe skips all DB
	operations for it (no CREATE, no ALTER, no MODIFY). We manage the actual
	column ourselves:
	- Missing -> CREATE as BINARY(16) (fresh install / new server)
	- Exists as BINARY(16) -> do nothing
	- Exists as wrong type -> warn loudly, never ALTER (unsafe on large tables)
	"""
	result = frappe.db.sql("""
		SELECT COLUMN_TYPE FROM INFORMATION_SCHEMA.COLUMNS
		WHERE TABLE_SCHEMA = DATABASE()
		AND TABLE_NAME = 'tabMemora Memory State'
		AND COLUMN_NAME = 'item_id'
	""")

	if not result:
		# Column doesn't exist — fresh install or new server
		frappe.db.sql_ddl("""
			ALTER TABLE `tabMemora Memory State`
			ADD COLUMN `item_id` BINARY(16) NOT NULL
		""")
		print("[after_migrate] Created item_id BINARY(16) column")
		return

	column_type = str(result[0][0]).lower()
	if "binary" in column_type:
		return  # Already correct

	# Column exists but wrong type — NEVER alter, warn for manual fix
	print(
		f"[after_migrate] WARNING: item_id is {column_type}, expected binary(16)."
		" Manual intervention required. DO NOT alter on large partitioned tables."
	)


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
		frappe.db.sql_ddl("DROP INDEX `player_subject_next_review_index` ON `tabMemora Memory State`")
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

	Query-to-index mapping:
	  _lookup_memory_state       -> idx_player_item_season (exact match)
	  get_review_overview        -> idx_review_query (player, season_seq prefix + range on next_review)
	  get_due_items              -> idx_review_query (player, subject, next_review range, season_seq)
	  submit_reviews (lookup)    -> idx_player_item_season (exact match)
	  submit_reviews (remaining) -> idx_review_query (count query)
	  _update_memory_state       -> PRIMARY KEY (name, season_seq)
	  get_memory_mastery         -> idx_player_item_season (player prefix + partition pruning on season_seq)

	NOTE: No dedicated mastery index. At 10B rows, an index including the high-churn
	`stability` column would cause write amplification on every review. The mastery query
	uses partition pruning (season_seq) + idx_player_item_season (player prefix) to scan
	a player's items within a single partition (<25K rows typical), which is fast enough.
	If mastery reads become a bottleneck, use Redis counters (memora:stats:*) instead.
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

	# Cleanup: drop idx_mastery if it exists from a previous migration.
	# A covering index on high-churn `stability` causes write amplification at scale.
	_existing_all = frappe.db.sql("""
		SELECT DISTINCT INDEX_NAME FROM INFORMATION_SCHEMA.STATISTICS
		WHERE TABLE_SCHEMA = DATABASE()
		AND TABLE_NAME = 'tabMemora Memory State'
		AND INDEX_NAME = 'idx_mastery'
	""")
	if _existing_all:
		frappe.db.sql_ddl("ALTER TABLE `tabMemora Memory State` DROP INDEX idx_mastery")
		print("[after_migrate] Dropped idx_mastery (write amplification risk at scale)")


def _ensure_fsrs_state_columns():
	"""Ensure state, step, last_review columns exist on tabMemora Memory State.

	These 3 columns store the full FSRS card state, enabling intervals to grow
	correctly across review sessions. Added here (not via JSON) because this is
	a 10B-row RANGE-partitioned table managed exclusively by setup.py.

	Idempotent: checks INFORMATION_SCHEMA before adding each column.
	Instant operation: MariaDB InnoDB ADD COLUMN with DEFAULT NULL is metadata-only.
	"""
	# Check which of the 3 columns already exist
	existing = frappe.db.sql("""
		SELECT COLUMN_NAME FROM INFORMATION_SCHEMA.COLUMNS
		WHERE TABLE_SCHEMA = DATABASE()
		AND TABLE_NAME = 'tabMemora Memory State'
		AND COLUMN_NAME IN ('state', 'step', 'last_review')
	""")
	existing_cols = {row[0] for row in existing}

	if "state" not in existing_cols:
		frappe.db.sql_ddl("""
			ALTER TABLE `tabMemora Memory State`
			ADD COLUMN `state` TINYINT DEFAULT NULL
		""")
		print("[after_migrate] Created state TINYINT column on tabMemora Memory State")

	if "step" not in existing_cols:
		frappe.db.sql_ddl("""
			ALTER TABLE `tabMemora Memory State`
			ADD COLUMN `step` TINYINT DEFAULT NULL
		""")
		print("[after_migrate] Created step TINYINT column on tabMemora Memory State")

	if "last_review" not in existing_cols:
		frappe.db.sql_ddl("""
			ALTER TABLE `tabMemora Memory State`
			ADD COLUMN `last_review` DATETIME(6) DEFAULT NULL
		""")
		print("[after_migrate] Created last_review DATETIME(6) column on tabMemora Memory State")


def _setup_voucher_schema():
	"""Set up voucher-related schema extensions. Idempotent."""
	from memora_admin.memora_admin.custom.customer_fields import add_customer_voucher_fields
	from memora_admin.memora_admin.custom.invoice_fields import add_voucher_invoice_fields

	# NOTE: voucher_hmac_secret must be manually added to site_config.json before Phase 34.
	# Generate with: python3 -c 'import secrets; print(secrets.token_hex(32))'
	# Add to site_config.json: {"voucher_hmac_secret": "<generated_hex>"}
	# See SEC-06 requirement.

	add_customer_voucher_fields()
	print("[after_migrate] Customer voucher fields ensured")

	add_voucher_invoice_fields()
	print("[after_migrate] Voucher invoice Link fields ensured")

	_ensure_voucher_service_item()
	_ensure_voucher_card_indexes()


def _ensure_voucher_service_item():
	"""Create the MEMORA-VOUCHER-CARD service Item if it doesn't exist.

	This Item is used as the line item on Sales Invoices and Credit Notes
	for voucher card transactions. It's a non-stock, sales-only service item.

	Wrapped in try/except because it depends on ERPNext's Item DocType
	being available (ERPNext must be installed).
	"""
	try:
		if frappe.db.exists("Item", "MEMORA-VOUCHER-CARD"):
			return

		item = frappe.get_doc(
			{
				"doctype": "Item",
				"item_code": "MEMORA-VOUCHER-CARD",
				"item_name": "Memora Voucher Card",
				"item_group": "Services",
				"stock_uom": "Nos",
				"is_stock_item": 0,
				"is_sales_item": 1,
				"include_item_in_manufacturing": 0,
				"description": "Memora educational voucher card",
			}
		)
		item.insert(ignore_permissions=True)
		frappe.db.commit()
		print("[after_migrate] Created MEMORA-VOUCHER-CARD service item")
	except Exception as e:
		print(f"[after_migrate] Voucher service item creation skipped: {e}")


def _ensure_voucher_card_indexes():
	"""Create composite index on Voucher Card (batch, status) for allocation queries."""
	# Skip if table doesn't exist yet (fresh install before bench migrate)
	tables = frappe.db.get_tables()
	if "tabMemora Voucher Card" not in tables:
		return

	existing = frappe.db.sql("""
		SELECT DISTINCT INDEX_NAME FROM INFORMATION_SCHEMA.STATISTICS
		WHERE TABLE_SCHEMA = DATABASE()
		AND TABLE_NAME = 'tabMemora Voucher Card'
		AND INDEX_NAME = 'idx_batch_status'
	""")

	if not existing:
		frappe.db.sql_ddl("""
			ALTER TABLE `tabMemora Voucher Card`
			ADD INDEX idx_batch_status (batch, status)
		""")
		print("[after_migrate] Created INDEX idx_batch_status on tabMemora Voucher Card")


def _ensure_practice_log_table():
	"""Create tabMemora Practice Log raw SQL table for practice session results.

	This is NOT a Frappe DocType — it's a raw SQL table managed via setup.py,
	following the Memory State precedent for high-volume tables (~500M rows).

	Idempotent: uses CREATE TABLE IF NOT EXISTS. Existing installs get any
	additional indexes via _ensure_practice_query_indexes().
	"""
	frappe.db.sql_ddl("""
		CREATE TABLE IF NOT EXISTS `tabMemora Practice Log` (
			`id` BIGINT AUTO_INCREMENT,
			`player_id` VARCHAR(140) NOT NULL,
			`item_id` VARCHAR(36) NOT NULL,
			`first_seen_at` DATETIME NOT NULL,
			`last_seen_at` DATETIME NOT NULL,
			`last_result` ENUM('Correct', 'Incorrect') NOT NULL,
			`attempt_count` INT UNSIGNED NOT NULL DEFAULT 1,
			`correct_count` INT UNSIGNED NOT NULL DEFAULT 0,
			PRIMARY KEY (`id`),
			UNIQUE KEY `uq_player_item` (`player_id`, `item_id`),
			KEY `idx_item_id` (`item_id`),
			KEY `idx_player_seen_item` (`player_id`, `last_seen_at`, `item_id`)
		) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
	""")


def _ensure_hot_table_indexes():
	"""Create composite indexes on high-traffic tables (PERF-03).

	These indexes eliminate index intersection overhead on hot query paths:
	- idx_lesson_subject: hierarchy builds, stage JOINs
	- idx_event_creation: FSRS processor cutoff query (every 1 min)
	- idx_player_subject: every progress lookup (sync.py, FastAPI)
	- idx_player_access: every access check (access_sync.py)
	- idx_status_creation: reporting/reconciliation time-range queries (PERF-20)

	idx_batch_status on Voucher Card is handled separately by _ensure_voucher_card_indexes().
	"""
	tables = frappe.db.get_tables()

	indexes = [
		("tabMemora Lesson", "idx_lesson_subject", "(subject)"),
		("tabMemora Interaction Log", "idx_event_creation", "(event_type, creation)"),
		("tabMemora Structure Progress", "idx_player_subject", "(player, subject)"),
		("tabMemora Player Subscription", "idx_player_access", "(player, access_key)"),
		("tabMemora Subscription Transaction", "idx_status_creation", "(status, creation)"),
	]

	for table, index_name, columns in indexes:
		if table not in tables:
			continue

		existing = frappe.db.sql(
			"""
			SELECT 1 FROM INFORMATION_SCHEMA.STATISTICS
			WHERE TABLE_SCHEMA = DATABASE()
			AND TABLE_NAME = %s
			AND INDEX_NAME = %s
			LIMIT 1
		""",
			(table, index_name),
		)

		if not existing:
			frappe.db.sql_ddl(f"""
				CREATE INDEX `{index_name}` ON `{table}` {columns}
			""")
			print(f"[after_migrate] Created INDEX {index_name} on {table}")


def _ensure_practice_query_indexes():
	"""Backfill composite indexes that keep practice selection queries fast.

	Practice now excludes "seen in current session" items server-side using
	Practice Log.last_seen_at. These indexes keep both the Review Item scope
	filter and the Practice Log time-window lookup index-backed.
	"""
	tables = set(frappe.db.get_tables())

	indexes = [
		("tabMemora Review Item", "idx_practice_scope", "(subject, topic, lesson)"),
		(
			"tabMemora Practice Log",
			"idx_player_seen_item",
			"(player_id, last_seen_at, item_id)",
		),
	]

	for table, index_name, columns in indexes:
		if table not in tables:
			continue

		existing = frappe.db.sql(
			"""
			SELECT 1 FROM INFORMATION_SCHEMA.STATISTICS
			WHERE TABLE_SCHEMA = DATABASE()
			AND TABLE_NAME = %s
			AND INDEX_NAME = %s
			LIMIT 1
		""",
			(table, index_name),
		)

		if existing:
			continue

		frappe.db.sql_ddl(f"""
			CREATE INDEX `{index_name}` ON `{table}` {columns}
		""")
		print(f"[after_migrate] Created INDEX {index_name} on {table}")
