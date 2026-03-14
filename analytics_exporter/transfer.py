"""Transfer analytics exports to the remote analytics server via rsync.

Rsyncs all Parquet and manifest files from the local output directory to
the analytics server at ``{remote_analytics_path}/datasets/``.
"""

import logging
import shlex
import subprocess

from .config import Config

log = logging.getLogger("analytics_exporter")


def transfer_exports(config: Config) -> bool:
    """Rsync the analytics output directory to the remote server.

    Returns True on success, False on failure.
    Skips (returns True) if SSH is not configured.
    """
    if not config.has_ssh_config():
        log.info("transfer: skipped (SSH not configured)")
        return True

    remote_dest = f"{config.remote_analytics_path.rstrip('/')}/datasets/"
    local_src = f"{config.analytics_output_path.rstrip('/')}/"

    ssh_cmd = f"ssh -p {config.ssh_port} -o StrictHostKeyChecking=accept-new -o BatchMode=yes"
    if config.ssh_key_path:
        ssh_cmd = f"ssh -i {shlex.quote(config.ssh_key_path)} -p {config.ssh_port} -o StrictHostKeyChecking=accept-new -o BatchMode=yes"

    cmd = [
        "rsync",
        "-avz",
        "--checksum",
        "--partial",
        "-e", ssh_cmd,
        local_src,
        f"{config.ssh_user}@{config.ssh_host}:{remote_dest}",
    ]

    log.info("transfer: rsync %s -> %s@%s:%s", local_src, config.ssh_user, config.ssh_host, remote_dest)

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=config.ssh_timeout,
        )
        if result.returncode != 0:
            log.error("transfer: rsync failed (rc=%d): %s", result.returncode, result.stderr.strip())
            return False

        log.info("transfer: rsync complete")
        return True

    except subprocess.TimeoutExpired:
        log.error("transfer: rsync timed out after %ds", config.ssh_timeout)
        return False
    except Exception as exc:
        log.error("transfer: rsync error: %s", exc)
        return False
