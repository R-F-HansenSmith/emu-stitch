"""
fstab module for emu-stitch: Audits mount flags (such as noexec) for external drives
and secondary mount points (e.g. /mnt, /run/media), skipping standard /home paths.
"""

from __future__ import annotations

import os
import subprocess
from typing import Tuple


def audit_mount_permissions(emulation_dir: str) -> Tuple[bool, str, str]:
    """
    Audit mount flags for external or secondary drive mounts (e.g. /mnt, /run/media).
    Skips auditing standard /home internal storage paths.
    Returns: (is_noexec, mount_point, advice_msg)
    """
    real_path = os.path.realpath(emulation_dir)
    home_path = os.path.realpath(os.path.expanduser("~"))

    # Skip auditing internal /home paths
    if real_path.startswith(home_path) or real_path.startswith("/home"):
        return False, "/home", "Internal Storage (/home) - standard execution permissions active."

    try:
        res = subprocess.run(["mount"], capture_output=True, text=True, check=True)
        mounts = res.stdout.splitlines()

        matching_mount = None
        for line in mounts:
            parts = line.split()
            if len(parts) >= 3:
                m_point = parts[2]
                if real_path == m_point or real_path.startswith(m_point.rstrip("/") + "/"):
                    if matching_mount is None or len(m_point) > len(matching_mount[0]):
                        matching_mount = (m_point, line)

        if matching_mount:
            m_point, m_line = matching_mount
            if "noexec" in m_line:
                advice = (
                    f"Notice: External mount point '{m_point}' is mounted with 'noexec'.\n"
                    "If you encounter AppImage launch issues (e.g. Steam ROM Manager), "
                    "consider adding 'exec' to the mount options in /etc/fstab."
                )
                return True, m_point, advice
            return False, m_point, f"External mount point '{m_point}' has valid 'exec' permissions."
    except Exception as e:
        return False, emulation_dir, f"External mount check skipped: {e}"

    return False, emulation_dir, "Mount permissions verified."
