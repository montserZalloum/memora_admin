"""Remote transfer module — rsync + SSH for archive/live batch delivery."""

import json
import os
import shlex
import subprocess

from .config import Config
from .logger import StructuredLogger


class TransferError(Exception):
	"""Raised when a transfer operation fails."""


def _validate_ssh_config(config: Config) -> bool:
	"""Check SSH fields are populated. Returns False if not configured."""
	return config.has_ssh_config()


def _run_ssh_command(
	config: Config,
	command: str,
	timeout: int | None = None,
) -> tuple[int, str, str]:
	"""Execute command on analytics server via subprocess ssh.

	Returns:
		Tuple of (returncode, stdout, stderr).
	"""
	if not _validate_ssh_config(config):
		raise TransferError("SSH not configured (missing host/user/key)")

	ssh_args = [
		"ssh",
		"-i", config.ssh_key_path,
		"-p", str(config.ssh_port),
		"-o", "StrictHostKeyChecking=accept-new",
		"-o", "ConnectTimeout=30",
		"-o", "BatchMode=yes",
		f"{config.ssh_user}@{config.ssh_host}",
		command,
	]

	effective_timeout = timeout or config.ssh_timeout

	result = subprocess.run(
		ssh_args,
		capture_output=True,
		text=True,
		timeout=effective_timeout,
	)
	return result.returncode, result.stdout, result.stderr


def transfer_batch(
	config: Config,
	local_dir: str,
	remote_base_path: str,
	job_name: str,
	log: StructuredLogger,
) -> str:
	"""Rsync local batch dir to analytics server. Returns remote_path.

	Args:
		config: Executor configuration with SSH credentials.
		local_dir: Local batch directory to transfer.
		remote_base_path: Remote base directory (e.g., config.remote_archive_path).
		job_name: Job name for logging and remote path construction.
		log: Structured logger.

	Returns:
		Full remote path where files were transferred.

	Raises:
		TransferError: If SSH is not configured or rsync fails.
	"""
	if not _validate_ssh_config(config):
		raise TransferError("SSH not configured — cannot transfer")

	if not os.path.isdir(local_dir):
		raise TransferError(f"Local directory not found: {local_dir}")

	remote_path = f"{remote_base_path.rstrip('/')}/{job_name}/"

	# Ensure trailing slash on local_dir so rsync copies contents
	local_src = local_dir.rstrip("/") + "/"

	rsync_args = [
		"rsync",
		"-avz",
		"--checksum",
		"--partial",
		"--compress",
		"-e", f"ssh -i {shlex.quote(config.ssh_key_path)} -p {config.ssh_port} -o StrictHostKeyChecking=accept-new -o BatchMode=yes",
		local_src,
		f"{config.ssh_user}@{config.ssh_host}:{remote_path}",
	]

	log.info("transfer_started", job=job_name, remote_path=remote_path)

	result = subprocess.run(
		rsync_args,
		capture_output=True,
		text=True,
		timeout=config.ssh_timeout,
	)

	if result.returncode != 0:
		raise TransferError(
			f"rsync failed (exit {result.returncode}): {result.stderr[:2000]}"
		)

	log.info("transfer_completed", job=job_name, remote_path=remote_path)
	return remote_path


def verify_remote_checksums(
	config: Config,
	remote_path: str,
	manifest: dict,
	log: StructuredLogger,
) -> dict:
	"""SSH into remote, sha256sum each file, compare with manifest.

	Args:
		config: Executor configuration with SSH credentials.
		remote_path: Remote directory containing the transferred files.
		manifest: Parsed manifest dict with 'files' list.
		log: Structured logger.

	Returns:
		Dict with {valid: bool, errors: list, files_checked: int}.
	"""
	errors = []
	files = manifest.get("files", [])
	if not files:
		return {"valid": True, "errors": [], "files_checked": 0}

	# Build sha256sum command for all files
	file_list = " ".join(
		shlex.quote(f"{remote_path.rstrip('/')}/{f['filename']}") for f in files
	)
	command = f"sha256sum {file_list}"

	returncode, stdout, stderr = _run_ssh_command(config, command)
	if returncode != 0:
		errors.append(f"Remote sha256sum failed: {stderr[:1000]}")
		return {"valid": False, "errors": errors, "files_checked": 0}

	# Parse sha256sum output: "hash  filename\n"
	remote_checksums = {}
	for line in stdout.strip().splitlines():
		parts = line.split(None, 1)
		if len(parts) == 2:
			checksum_hex, filepath = parts
			filename = os.path.basename(filepath)
			remote_checksums[filename] = f"sha256:{checksum_hex}"

	files_checked = 0
	for file_entry in files:
		filename = file_entry["filename"]
		expected = file_entry["checksum"]
		actual = remote_checksums.get(filename)

		if actual is None:
			errors.append(f"File missing on remote: {filename}")
		elif actual != expected:
			errors.append(
				f"Checksum mismatch for {filename}: expected {expected}, got {actual}"
			)

		files_checked += 1

	valid = len(errors) == 0
	if valid:
		log.info("remote_checksum_verified", files_checked=files_checked)
	else:
		log.warning("remote_checksum_failed", errors=errors)

	return {"valid": valid, "errors": errors, "files_checked": files_checked}
