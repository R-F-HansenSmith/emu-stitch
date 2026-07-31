"""
Backups module for emu-stitch: prunes old `<path>.bak-YYYYMMDD-HHMMSS`
backups created during real-directory-to-symlink migrations, keeping only
the N most recent per path.
"""

from __future__ import annotations

import os
import shutil
import logging


def prune_old_backups(link_path: str, retention: int) -> None:
    """
    Keep only the `retention` most recent `<link_path>.bak-*` backups next
    to `link_path`, deleting older ones. `retention <= 0` disables pruning
    (keep every backup forever).
    """
    if retention <= 0:
        return

    parent = os.path.dirname(link_path) or "."
    prefix = f"{os.path.basename(link_path)}.bak-"

    try:
        siblings = sorted(e for e in os.listdir(parent) if e.startswith(prefix))
    except OSError:
        return

    excess = siblings[:-retention] if len(siblings) > retention else []
    for name in excess:
        full_path = os.path.join(parent, name)
        try:
            if os.path.islink(full_path) or os.path.isfile(full_path):
                os.remove(full_path)
            elif os.path.isdir(full_path):
                shutil.rmtree(full_path)
        except OSError as e:
            logging.error(f"Error pruning old backup {full_path}: {e}")
