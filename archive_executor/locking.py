"""Shared file-based locking for archive and live sync executors."""

import fcntl
import os


def acquire_lock(lock_file: str):
	"""Acquire an exclusive file lock. Returns the fd or None if already held."""
	os.makedirs(os.path.dirname(lock_file) or ".", exist_ok=True)
	fd = open(lock_file, "w")
	try:
		fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
		fd.write(str(os.getpid()))
		fd.flush()
		return fd
	except (BlockingIOError, OSError):
		fd.close()
		return None


def release_lock(fd):
	"""Release the file lock."""
	if fd:
		try:
			fcntl.flock(fd, fcntl.LOCK_UN)
			fd.close()
		except OSError:
			pass
