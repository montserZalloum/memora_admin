"""YAML schema registry loader for dimensions and archive types."""

import os

import yaml


def load_dimension_schema(registry_path: str, entity: str, version: str) -> dict:
	"""Load a dimension schema YAML file.

	Args:
		registry_path: Root path to the schema registry directory.
		entity: Dimension entity name (e.g., "player").
		version: Schema version (e.g., "v1").

	Returns:
		Parsed dict from the YAML file.

	Raises:
		FileNotFoundError: If the schema file does not exist.
	"""
	file_path = os.path.join(registry_path, "dimensions", f"{entity}.{version}.yaml")
	if not os.path.isfile(file_path):
		raise FileNotFoundError(f"Dimension schema not found: {file_path}")
	with open(file_path, "r") as f:
		return yaml.safe_load(f)


def load_archive_type(registry_path: str, type_name: str, version: str) -> dict:
	"""Load an archive type schema YAML file.

	Args:
		registry_path: Root path to the schema registry directory.
		type_name: Archive type name (e.g., "practice_log").
		version: Schema version (e.g., "v1").

	Returns:
		Parsed dict from the YAML file.

	Raises:
		FileNotFoundError: If the schema file does not exist.
	"""
	file_path = os.path.join(registry_path, "archive_types", f"{type_name}.{version}.yaml")
	if not os.path.isfile(file_path):
		raise FileNotFoundError(f"Archive type schema not found: {file_path}")
	with open(file_path, "r") as f:
		return yaml.safe_load(f)


def load_sync_type(registry_path: str, type_name: str, version: str) -> dict:
	"""Load a sync type schema YAML file from the sync_types/ subdirectory.

	Args:
		registry_path: Root path to the schema registry directory.
		type_name: Sync type name (e.g., "practice_log_live").
		version: Schema version (e.g., "v1").

	Returns:
		Parsed dict from the YAML file.

	Raises:
		FileNotFoundError: If the schema file does not exist.
	"""
	file_path = os.path.join(registry_path, "sync_types", f"{type_name}.{version}.yaml")
	if not os.path.isfile(file_path):
		raise FileNotFoundError(f"Sync type schema not found: {file_path}")
	with open(file_path, "r") as f:
		return yaml.safe_load(f)


def list_sync_types(registry_path: str) -> list[dict]:
	"""Discover all sync type YAML files in the registry.

	Returns:
		List of parsed sync type dicts (skips empty/invalid files).
	"""
	types_dir = os.path.join(registry_path, "sync_types")
	if not os.path.isdir(types_dir):
		return []
	results = []
	for filename in sorted(os.listdir(types_dir)):
		if filename.endswith(".yaml") or filename.endswith(".yml"):
			file_path = os.path.join(types_dir, filename)
			with open(file_path, "r") as f:
				parsed = yaml.safe_load(f)
			if parsed and isinstance(parsed, dict):
				results.append(parsed)
	return results


def list_archive_types(registry_path: str) -> list[dict]:
	"""Discover all archive type YAML files in the registry.

	Returns:
		List of parsed archive type dicts (skips empty/invalid files).
	"""
	types_dir = os.path.join(registry_path, "archive_types")
	if not os.path.isdir(types_dir):
		return []
	results = []
	for filename in sorted(os.listdir(types_dir)):
		if filename.endswith(".yaml") or filename.endswith(".yml"):
			file_path = os.path.join(types_dir, filename)
			with open(file_path, "r") as f:
				parsed = yaml.safe_load(f)
			if parsed and isinstance(parsed, dict):
				results.append(parsed)
	return results


def validate_archive_type_dimensions(registry_path: str, archive_type: dict) -> list[str]:
	"""Validate that all dimension versions referenced in an archive type exist.

	Args:
		registry_path: Root path to the schema registry directory.
		archive_type: Parsed archive type dict with a 'dimensions' key.

	Returns:
		List of error messages. Empty list means all references are valid.
	"""
	errors = []
	for dim in archive_type.get("dimensions", []):
		entity = dim.get("entity")
		version = dim.get("schema_version")
		if not entity or not version:
			errors.append(f"Dimension entry missing entity or schema_version: {dim}")
			continue
		file_path = os.path.join(registry_path, "dimensions", f"{entity}.{version}.yaml")
		if not os.path.isfile(file_path):
			errors.append(
				f"Dimension schema not found: {entity}.{version} "
				f"(expected at {file_path})"
			)
	return errors


def validate_registry(registry_path: str) -> list[str]:
	"""Validate all archive types in the registry have valid dimension references.

	Returns:
		List of error messages. Empty list means the registry is valid.
	"""
	errors = []
	for archive_type in list_archive_types(registry_path):
		type_name = archive_type.get("archive_type", "unknown")
		version = archive_type.get("version", "unknown")
		dim_errors = validate_archive_type_dimensions(registry_path, archive_type)
		for err in dim_errors:
			errors.append(f"[{type_name}.{version}] {err}")
	return errors
