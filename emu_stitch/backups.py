"""
Backups module for emu-stitch: migrates real directories into a profile
(merge, then rename the original to `<path>.bak-YYYYMMDD-HHMMSS`) and prunes
old backups, keeping only the N most recent per path.

A backup is only ever pruned if it carries MERGED_MARKER, which is written
when every file in it was already present, byte-identical, in the profile it
was merged into, and records where that profile is. Before pruning, the
backup is compared with that profile again, byte for byte: if any file has
since changed or gone missing there (corruption, a bad sync, or simply newer
progress), the backup is kept. Backups holding anything that exists nowhere
else (e.g. a save that conflicted with a different version at the
destination) are kept forever, regardless of the retention setting.
"""

from __future__ import annotations

import os
import shutil
import filecmp
import logging
import datetime

from .config import get_backup_retention

MERGED_MARKER = ".emu-stitch-merged"


def merge_tree(src: str, dst: str) -> int:
    """
    Copy everything in `src` into `dst` that isn't already there. Existing
    destination entries always win and are never overwritten. Symlinks are
    copied as symlinks, never followed.

    Returns the number of conflicts: source entries whose destination
    counterpart exists but differs, i.e. data that now lives only in `src`.
    """
    os.makedirs(dst, exist_ok=True)
    conflicts = 0
    for name in os.listdir(src):
        s = os.path.join(src, name)
        d = os.path.join(dst, name)
        if os.path.islink(s):
            if not os.path.lexists(d):
                os.symlink(os.readlink(s), d)
            elif not (os.path.islink(d) and os.readlink(d) == os.readlink(s)):
                conflicts += 1
        elif os.path.isdir(s):
            if os.path.lexists(d) and not os.path.isdir(d):
                conflicts += 1
            else:
                conflicts += merge_tree(s, d)
        elif not os.path.lexists(d):
            shutil.copy2(s, d)
        elif not (os.path.isfile(d) and filecmp.cmp(s, d, shallow=False)):
            conflicts += 1
    return conflicts


def count_missing(src: str, dst: str) -> int:
    """
    Count entries in `src` that aren't also in `dst`, byte for byte, using
    the same rules as merge_tree (symlinks compared by target). Entries only
    in `dst` don't count. The merged marker itself is ignored.
    """
    missing = 0
    for name in os.listdir(src):
        if name == MERGED_MARKER:
            continue
        s = os.path.join(src, name)
        d = os.path.join(dst, name)
        if os.path.islink(s):
            if not (os.path.islink(d) and os.readlink(d) == os.readlink(s)):
                missing += 1
        elif os.path.isdir(s):
            if os.path.isdir(d):
                missing += count_missing(s, d)
            else:
                missing += 1
        elif not (os.path.isfile(d) and filecmp.cmp(s, d, shallow=False)):
            missing += 1
    return missing


def _still_fully_merged(backup_path: str) -> bool:
    """Whether a marked backup is still fully contained in the profile its
    marker points at. Older markers are empty (no recorded profile), so
    those backups can't be checked and are kept."""
    try:
        with open(os.path.join(backup_path, MERGED_MARKER), encoding="utf-8") as f:
            target = f.read().strip()
        if not target or not os.path.isdir(target):
            return False
        return count_missing(backup_path, target) == 0
    except (OSError, UnicodeDecodeError):
        return False


def migrate_to_backup(path: str, target_dir: str) -> str:
    """
    Replace-in-waiting for a real file/directory at `path`: merge its
    contents into `target_dir` (see merge_tree), rename it to a timestamped
    backup, mark the backup as safe to prune if nothing was lost in the
    merge, then apply the retention policy. Never deletes `path` itself.
    Returns the backup path.
    """
    if os.path.isdir(path):
        conflicts = merge_tree(path, target_dir)
    else:
        # A plain file where a directory belongs is never merged, so its
        # backup is the only copy.
        conflicts = 1

    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    backup_path = f"{path}.bak-{stamp}"
    n = 1
    while os.path.lexists(backup_path):
        backup_path = f"{path}.bak-{stamp}-{n}"
        n += 1
    os.rename(path, backup_path)

    if conflicts == 0:
        # Record the real profile directory (not the ~/Emulation/saves
        # symlink, which moves on every switch) so pruning can re-check it.
        with open(os.path.join(backup_path, MERGED_MARKER), "w", encoding="utf-8") as f:
            f.write(os.path.realpath(target_dir) + "\n")
    else:
        logging.warning(
            f"{conflicts} item(s) in {path} differed from the copy already in "
            f"{target_dir}. The originals are kept in {backup_path}, which "
            "will never be pruned automatically."
        )

    prune_old_backups(path, get_backup_retention())
    return backup_path


def prune_old_backups(link_path: str, retention: int) -> None:
    """
    Keep only the `retention` most recent `<link_path>.bak-*` backups next
    to `link_path`, deleting older ones that are marked as fully merged and
    are still, byte for byte, contained in the profile they were merged
    into. Anything else is always kept. `retention <= 0` disables pruning
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
        if os.path.islink(full_path) or not os.path.isfile(os.path.join(full_path, MERGED_MARKER)):
            logging.debug(f"Keeping unmerged backup {full_path}")
            continue
        if not _still_fully_merged(full_path):
            logging.debug(f"Keeping backup {full_path}: its profile copy has changed or can't be checked")
            continue
        try:
            shutil.rmtree(full_path)
        except OSError as e:
            logging.error(f"Error pruning old backup {full_path}: {e}")
