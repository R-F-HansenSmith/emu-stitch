"""
Ryujinx save-index module for emu-stitch.

Ryujinx (like real Switch firmware) never finds a save by scanning
bis/user/save. It looks the game up in a save-data index, a key/value
archive (imkvdb.arc) inside the 8000000000000000 system save, to get the
numbered folder to open. That index belongs to the Ryujinx install, not to a
profile, so folders switched or synced in by emu-stitch can disagree with it.

Each save folder's ExtraData file starts with the exact 0x40-byte key the
index uses for it (the SaveDataAttribute: title ID, user ID, save type, ...),
so the index can be rebuilt from the active profile's folders alone.

Archive format:  "IMKV" | u32 reserved | u32 entry count, then per entry
                 "IMEN" | u32 key size | u32 value size | key | value
Value (0x40):    u64 save ID | u64 size | u64 reserved | u8 space ID | u8 state | zero padding
"""

from __future__ import annotations

import os
import shutil
import struct
import datetime
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

INDEX_SAVE = os.path.join("bis", "system", "save", "8000000000000000")
KEY_SIZE = 0x40
VALUE_SIZE = 0x40
SPACE_SYSTEM = 0
SPACE_USER = 1
# Save IDs at or above this are system saves, stored outside bis/user/save.
SYSTEM_SAVE_ID_MIN = 0x8000000000000000
# Index backups are small, pure copies that can be rebuilt at any time.
INDEX_BACKUPS_KEPT = 5
PROC_DIR = "/proc"

Entry = Tuple[bytes, bytes]


# ---------------------------------------------------------------------------
# Archive encoding
# ---------------------------------------------------------------------------

def parse_index(data: bytes) -> List[Entry]:
    """Parse an imkvdb.arc archive into (key, value) pairs. Raises ValueError
    if it isn't a well-formed archive."""
    try:
        magic, _, count = struct.unpack_from("<4sII", data, 0)
        if magic != b"IMKV":
            raise ValueError("missing IMKV header")
        entries: List[Entry] = []
        offset = 12
        for _ in range(count):
            entry_magic, key_size, value_size = struct.unpack_from("<4sII", data, offset)
            offset += 12
            if entry_magic != b"IMEN" or key_size < 8 or value_size < 8:
                raise ValueError(f"malformed entry at offset {offset - 12}")
            key = data[offset:offset + key_size]
            value = data[offset + key_size:offset + key_size + value_size]
            if len(key) != key_size or len(value) != value_size:
                raise ValueError("truncated entry")
            entries.append((key, value))
            offset += key_size + value_size
        if offset != len(data):
            raise ValueError("trailing data after last entry")
        return entries
    except struct.error as e:
        raise ValueError(f"truncated archive: {e}") from None


def serialize_index(entries: List[Entry]) -> bytes:
    """Encode entries as an imkvdb.arc archive, sorted by key bytes like
    Ryujinx's own writer."""
    out = [b"IMKV", struct.pack("<II", 0, len(entries))]
    for key, value in sorted(entries, key=lambda e: e[0]):
        out += [b"IMEN", struct.pack("<II", len(key), len(value)), key, value]
    return b"".join(out)


def value_save_id(value: bytes) -> int:
    return struct.unpack_from("<Q", value, 0)[0]


def value_space_id(value: bytes) -> int:
    return value[0x18] if len(value) > 0x18 else SPACE_USER


def with_save_id(value: bytes, save_id: int) -> bytes:
    return struct.pack("<Q", save_id) + value[8:]


def new_user_value(save_id: int) -> bytes:
    return (struct.pack("<QQQ", save_id, 0, 0) + bytes([SPACE_USER])).ljust(VALUE_SIZE, b"\x00")


def describe_key(key: bytes) -> str:
    title = f"{struct.unpack_from('<Q', key, 0)[0]:016x}"
    save_type = key[0x20] if len(key) > 0x20 else 1
    return title if save_type == 1 else f"{title} (save type {save_type})"


# ---------------------------------------------------------------------------
# Reading Ryujinx's index and a profile's save folders
# ---------------------------------------------------------------------------

def _index_slot(ryujinx_dir: str) -> Optional[str]:
    """The index save has a committed copy (0/) and a working copy (1/).
    Ryujinx rebuilds the working copy from the committed one on start, so
    0/ is authoritative."""
    for slot in ("0", "1"):
        path = os.path.join(ryujinx_dir, INDEX_SAVE, slot)
        if os.path.isfile(os.path.join(path, "imkvdb.arc")):
            return path
    return None


def load_index(ryujinx_dir: str) -> Optional[Tuple[List[Entry], int]]:
    """(entries, last published save ID) from Ryujinx's index, or None if
    there is no index or it can't be parsed."""
    slot = _index_slot(ryujinx_dir)
    if slot is None:
        return None
    try:
        with open(os.path.join(slot, "imkvdb.arc"), "rb") as f:
            entries = parse_index(f.read())
        last_published = 0
        last_path = os.path.join(slot, "lastPublishedId")
        if os.path.isfile(last_path):
            with open(last_path, "rb") as f:
                raw = f.read(8)
            if len(raw) == 8:
                last_published = struct.unpack("<Q", raw)[0]
        return entries, last_published
    except (OSError, ValueError):
        return None


@dataclass
class SaveFolder:
    name: str
    save_id: int
    key: Optional[bytes]   # None if ExtraData is missing or unreadable
    mtime: float           # newest modification time of anything inside


def _read_folder_key(folder: str) -> Optional[bytes]:
    for fname in ("ExtraData0", "ExtraData1"):
        try:
            with open(os.path.join(folder, fname), "rb") as f:
                key = f.read(KEY_SIZE)
        except OSError:
            continue
        if len(key) == KEY_SIZE:
            return key
    return None


def _newest_mtime(folder: str) -> float:
    newest = os.path.getmtime(folder)
    for root, _, files in os.walk(folder):
        for fname in files:
            try:
                newest = max(newest, os.path.getmtime(os.path.join(root, fname)))
            except OSError:
                pass
    return newest


def scan_profile_saves(profile_dir: str) -> List[SaveFolder]:
    """All user save folders (16 hex digits) in a profile's Ryujinx saves."""
    saves_dir = os.path.join(profile_dir, "ryujinx", "saves")
    if not os.path.isdir(saves_dir):
        return []
    folders = []
    for name in sorted(os.listdir(saves_dir)):
        path = os.path.join(saves_dir, name)
        if len(name) != 16 or not os.path.isdir(path):
            continue
        try:
            save_id = int(name, 16)
        except ValueError:
            continue
        if save_id == 0 or save_id >= SYSTEM_SAVE_ID_MIN:
            continue
        folders.append(SaveFolder(name, save_id, _read_folder_key(path), _newest_mtime(path)))
    return folders


# ---------------------------------------------------------------------------
# Read-only consistency check (used by `audit`)
# ---------------------------------------------------------------------------

def check_save_index(profile_dir: str, ryujinx_dir: str) -> Dict[str, List[str]]:
    """
    Compare a profile's Ryujinx save folders with the machine's save index:

    - "mismatched": the index maps that folder number to a different save,
      so Ryujinx would hand this folder's data to the wrong game.
    - "orphaned": neither the folder nor its save is in the index, so
      Ryujinx will never load it.
    - "reusable": the folder's number is above the index's last issued ID,
      so the next new save Ryujinx creates may be given the same number.
    - "duplicates" (informational): the save is indexed, but to a
      different folder, so this copy is unused.

    Returns {"mismatched": [...], "orphaned": [...], "reusable": [...],
    "duplicates": [...]} of human-readable descriptions (all empty if no
    index is available).
    """
    problems: Dict[str, List[str]] = {"mismatched": [], "orphaned": [], "reusable": [], "duplicates": []}
    loaded = load_index(ryujinx_dir)
    if loaded is None:
        return problems
    entries, last_published = loaded
    key_by_id = {value_save_id(v): k for k, v in entries if value_space_id(v) == SPACE_USER}
    id_by_key = {k: i for i, k in key_by_id.items()}
    folders = scan_profile_saves(profile_dir)
    folder_key_by_id = {f.save_id: f.key for f in folders}

    for folder in folders:
        label = describe_key(folder.key) if folder.key else "unknown title"
        indexed_key = key_by_id.get(folder.save_id)
        if indexed_key is None:
            loaded_id = id_by_key.get(folder.key)
            if loaded_id is not None and folder_key_by_id.get(loaded_id) == folder.key:
                problems["duplicates"].append(
                    f"{folder.name} ({label}) is unused; Ryujinx loads {loaded_id:016x}"
                )
            else:
                problems["orphaned"].append(f"{folder.name} ({label})")
        elif folder.key is not None and indexed_key != folder.key:
            problems["mismatched"].append(
                f"{folder.name} holds {label}, but Ryujinx maps it to {describe_key(indexed_key)}"
            )
        if folder.save_id > last_published:
            problems["reusable"].append(folder.name)
    return problems


# ---------------------------------------------------------------------------
# Rebuilding the index from a profile
# ---------------------------------------------------------------------------

@dataclass
class ReindexPlan:
    entries: List[Entry]
    last_published: int
    changes: List[str] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)

    @property
    def has_changes(self) -> bool:
        return bool(self.changes)


def plan_reindex(profile_dir: str, ryujinx_dir: str, all_profile_dirs: Optional[List[str]] = None) -> Optional[ReindexPlan]:
    """
    Work out the index that exactly matches `profile_dir`'s save folders:

    - System and non-user entries (e.g. SD-card cache saves) are kept as-is.
    - Every user save folder with a readable ExtraData is indexed under the
      key recorded in its own ExtraData.
    - If several folders hold the same save (same key), the most recently
      modified one wins; the others are left on disk, untouched.
    - User entries for saves this profile doesn't have are kept, because
      other profiles on this machine rely on them, unless their number is
      taken by a different save in this profile.
    - lastPublishedId never goes down, and is raised past every folder
      number in this profile and in `all_profile_dirs`, so new saves never
      reuse an existing number.

    Returns None if Ryujinx has no index yet (it creates one on first run).
    """
    loaded = load_index(ryujinx_dir)
    if loaded is None:
        return None
    entries, last_published = loaded

    kept = [(k, v) for k, v in entries if value_space_id(v) != SPACE_USER]
    kept_ids = {value_save_id(v) for _, v in kept}
    current: Dict[bytes, bytes] = {k: v for k, v in entries if value_space_id(v) == SPACE_USER}

    folders = scan_profile_saves(profile_dir)
    plan = ReindexPlan(entries=list(kept), last_published=last_published)

    by_key: Dict[bytes, List[SaveFolder]] = {}
    for folder in folders:
        if folder.key is None:
            plan.notes.append(f"{folder.name}: no readable ExtraData, left unindexed")
        elif folder.save_id in kept_ids:
            plan.notes.append(f"{folder.name}: number already used by a system/SD save, left unindexed")
        else:
            by_key.setdefault(folder.key, []).append(folder)

    for key, group in by_key.items():
        group.sort(key=lambda f: (f.mtime, f.save_id), reverse=True)
        chosen = group[0]
        label = describe_key(key)
        if len(group) > 1:
            others = ", ".join(f.name for f in group[1:])
            plan.notes.append(
                f"{label}: {len(group)} save folders; using {chosen.name} (most recently modified). "
                f"Not used, left untouched: {others}"
            )
        old_value = current.get(key)
        if old_value is None:
            plan.entries.append((key, new_user_value(chosen.save_id)))
            plan.changes.append(f"{label}: add -> {chosen.name}")
        else:
            old_id = value_save_id(old_value)
            plan.entries.append((key, with_save_id(old_value, chosen.save_id)))
            if old_id != chosen.save_id:
                plan.changes.append(f"{label}: {old_id:016x} -> {chosen.name}")

    folder_ids = {f.save_id for f in folders}
    for key, value in current.items():
        if key in by_key:
            continue
        if value_save_id(value) in folder_ids:
            plan.changes.append(
                f"{describe_key(key)}: remove (its number {value_save_id(value):016x} holds a different save in this profile)"
            )
        else:
            plan.entries.append((key, value))

    other_folders = [
        f for d in (all_profile_dirs or []) if os.path.realpath(d) != os.path.realpath(profile_dir)
        for f in scan_profile_saves(d)
    ]
    highest = max((f.save_id for f in folders + other_folders), default=0)
    if highest > last_published:
        plan.last_published = highest
        plan.changes.append(f"last issued save ID: {last_published:016x} -> {highest:016x}")

    return plan


def _atomic_write(path: str, data: bytes) -> None:
    tmp = f"{path}.tmp-{os.getpid()}"
    with open(tmp, "wb") as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def apply_reindex(ryujinx_dir: str, plan: ReindexPlan) -> str:
    """Back up the whole index save, then write the planned index to both
    of its slots. Returns the backup path."""
    base = os.path.join(ryujinx_dir, INDEX_SAVE)
    data = serialize_index(plan.entries)
    if sorted(parse_index(data)) != sorted(plan.entries):
        raise RuntimeError("internal error: rebuilt index does not round-trip")

    stamp = datetime.datetime.now().strftime('%Y%m%d-%H%M%S')
    backup = f"{base}.bak-{stamp}"
    n = 1
    while os.path.lexists(backup):
        backup = f"{base}.bak-{stamp}-{n}"
        n += 1
    shutil.copytree(base, backup, symlinks=True)

    for slot in ("0", "1"):
        slot_dir = os.path.join(base, slot)
        if not os.path.isdir(slot_dir):
            continue
        _atomic_write(os.path.join(slot_dir, "imkvdb.arc"), data)
        _atomic_write(os.path.join(slot_dir, "lastPublishedId"), struct.pack("<Q", plan.last_published))

    _prune_index_backups(base)
    return backup


def _prune_index_backups(base: str) -> None:
    parent, prefix = os.path.dirname(base), os.path.basename(base) + ".bak-"
    backups = sorted(
        (e for e in os.listdir(parent) if e.startswith(prefix)),
        key=lambda e: os.path.getmtime(os.path.join(parent, e)),
    )
    for name in backups[:-INDEX_BACKUPS_KEPT]:
        shutil.rmtree(os.path.join(parent, name), ignore_errors=True)


def auto_reindex(profile_dir: str, ryujinx_dirs: List[str], all_profile_dirs: List[str]) -> List[str]:
    """Non-interactive reindex used by `switch`. Returns messages to show."""
    if not ryujinx_dirs:
        return []
    if ryujinx_running():
        return ["Ryujinx is running, so its save index was not updated for this profile. "
                "Close it and run 'emu-stitch ryujinx-reindex'."]
    messages = []
    for ryujinx_dir in ryujinx_dirs:
        plan = plan_reindex(profile_dir, ryujinx_dir, all_profile_dirs)
        if plan is None or not plan.has_changes:
            continue
        apply_reindex(ryujinx_dir, plan)
        messages.append(f"Ryujinx save index updated for this profile ({len(plan.changes)} change(s)) in {ryujinx_dir}")
        messages += plan.notes
    return messages


# ---------------------------------------------------------------------------
# Process detection
# ---------------------------------------------------------------------------

def ryujinx_running() -> bool:
    """Whether a Ryujinx process is running. Ryujinx keeps the index in
    memory and writes it back on exit, so it must not be running while the
    index is rebuilt."""
    own_pid = str(os.getpid())
    try:
        pids = [p for p in os.listdir(PROC_DIR) if p.isdigit() and p != own_pid]
    except OSError:
        return False
    for pid in pids:
        try:
            with open(os.path.join(PROC_DIR, pid, "comm")) as f:
                comm = f.read().strip().lower()
            with open(os.path.join(PROC_DIR, pid, "cmdline"), "rb") as f:
                argv0 = f.read().split(b"\x00", 1)[0].decode("utf-8", "replace")
        except OSError:
            continue
        if comm.startswith("ryujinx") or os.path.basename(argv0).lower().startswith("ryujinx"):
            return True
    return False
