"""
Ryujinx save-index module for emu-stitch.

Ryujinx (like real Switch firmware) never finds a save by scanning
bis/user/save. It looks the game up in a save-data index, a key/value
archive (imkvdb.arc) inside the 8000000000000000 system save, to get the
numbered folder to open. That index belongs to the Ryujinx install, not to a
profile, so folders switched or synced in by emu-stitch can disagree with it.

emu-stitch keeps that index consistent by treating it like the saves:

- Each profile has its own copy (ryujinx/saveIndex), symlinked into place
  on switch, so a profile's saves and index always travel together.
- Each machine numbers new saves from its own range. The counter file
  (lastPublishedId) is excluded from Syncthing, so two machines can never
  give the same folder number to different games.
- If both machines add saves while apart, Syncthing keeps both copies of
  the index. Because numbers never collide, the copies can simply be
  merged; a save started on both machines resolves to the most recently
  played folder.

Each save folder's ExtraData file starts with the exact 0x40-byte key the
index uses for it (the SaveDataAttribute: title ID, user ID, save type, ...),
so the index can also be rebuilt from the folders alone (`ryujinx-reindex`,
a manual repair tool).

Archive format:  "IMKV" | u32 reserved | u32 entry count, then per entry
                 "IMEN" | u32 key size | u32 value size | key | value
Value (0x40):    u64 save ID | u64 size | u64 reserved | u8 space ID | u8 state | zero padding
"""

from __future__ import annotations

import os
import re
import socket
import shutil
import struct
import hashlib
import datetime
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

INDEX_SAVE = os.path.join("bis", "system", "save", "8000000000000000")
# Where each profile keeps its own copy of the index save.
PROFILE_INDEX = os.path.join("ryujinx", "saveIndex")
COUNTER_FILE = "lastPublishedId"
INDEX_SLOTS = ("0", "1")
# Each machine allocates new save IDs from its own 2^32-wide range.
RANGE_BITS = 32
MACHINE_ID_PATHS = ("/etc/machine-id", "/var/lib/dbus/machine-id")
CONFLICT_RE = re.compile(r"^imkvdb\.sync-conflict-.*\.arc$")
KEY_SIZE = 0x40
VALUE_SIZE = 0x40
SPACE_SYSTEM = 0
SPACE_USER = 1
# Save IDs at or above this are system saves, stored outside bis/user/save.
SYSTEM_SAVE_ID_MIN = 0x8000000000000000
# Index backups are small, pure copies that can be rebuilt at any time.
INDEX_BACKUPS_KEPT = 5
REINDEX_BACKUP_SUFFIX = ".reindex-bak-"
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


def index_present(index_dir: str) -> bool:
    """Whether `index_dir` (an 8000000000000000 save) holds an index."""
    return any(os.path.isfile(os.path.join(index_dir, slot, "imkvdb.arc")) for slot in INDEX_SLOTS)


# ---------------------------------------------------------------------------
# Per-machine save numbering
# ---------------------------------------------------------------------------

def machine_range_base(ryujinx_dir: str) -> int:
    """
    First save ID of this machine's own numbering range for `ryujinx_dir`.

    The range number comes from /etc/machine-id plus the Ryujinx install
    path (so native and Flatpak Ryujinx on one machine differ too), and is
    in 1..2^31-1, keeping every ID below the system-save range. Range 0
    (IDs below 2^32) is where Ryujinx numbered saves before emu-stitch
    managed it: those stay valid, but are never handed out again.
    """
    machine_id = ""
    for path in MACHINE_ID_PATHS:
        try:
            with open(path) as f:
                machine_id = f.read().strip()
        except OSError:
            continue
        if machine_id:
            break
    seed = f"{machine_id or socket.gethostname()}\0{os.path.realpath(ryujinx_dir)}"
    n = int(hashlib.sha256(seed.encode()).hexdigest(), 16) % ((1 << 31) - 1) + 1
    return n << RANGE_BITS


def same_range(a: int, b: int) -> bool:
    return a >> RANGE_BITS == b >> RANGE_BITS


def read_counter(slot_dir: str) -> Optional[int]:
    try:
        with open(os.path.join(slot_dir, COUNTER_FILE), "rb") as f:
            raw = f.read(8)
    except OSError:
        return None
    return struct.unpack("<Q", raw)[0] if len(raw) == 8 else None


def ensure_counter(index_dir: str, profile_dir: str, base: int) -> Optional[str]:
    """
    Make sure Ryujinx hands out new save IDs from this machine's range:
    set lastPublishedId to at least `base`, and past every folder in the
    profile that is already in this range. Counters already in range are
    only ever raised. Returns a message the first time a profile is moved
    onto this machine's range, else None.
    """
    own = [f.save_id for f in scan_profile_saves(profile_dir) if same_range(f.save_id, base)]
    floor = max([base] + own)
    moved = False
    for slot in INDEX_SLOTS:
        slot_dir = os.path.join(index_dir, slot)
        if not os.path.isdir(slot_dir):
            continue
        current = read_counter(slot_dir)
        in_range = current is not None and same_range(current, base)
        if in_range and current >= floor:
            continue
        _atomic_write(os.path.join(slot_dir, COUNTER_FILE), struct.pack("<Q", max(floor, current) if in_range else floor))
        moved = moved or not in_range
    if moved:
        return f"Ryujinx: new saves in this profile on this machine will be numbered from {floor + 1:016x}"
    return None


# ---------------------------------------------------------------------------
# Merging Syncthing conflict copies of the index
# ---------------------------------------------------------------------------

def merge_index_versions(versions: List[List[Entry]], folders: List["SaveFolder"]) -> Tuple[List[Entry], List[str]]:
    """
    Union of several versions of an index. Keys present in several versions
    with different values are resolved deterministically, so every machine
    merging the same versions gets the same result: for user saves, prefer
    a folder that exists, then the most recently modified one, then the
    higher ID.
    """
    mtime_by_id = {f.save_id: f.mtime for f in folders}
    values_by_key: Dict[bytes, List[bytes]] = {}
    for entries in versions:
        for key, value in entries:
            bucket = values_by_key.setdefault(key, [])
            if value not in bucket:
                bucket.append(value)

    merged: List[Entry] = []
    notes: List[str] = []
    for key, values in values_by_key.items():
        if len(values) == 1:
            merged.append((key, values[0]))
            continue
        if all(value_space_id(v) == SPACE_USER for v in values):
            def rank(v: bytes) -> Tuple[bool, float, int]:
                sid = value_save_id(v)
                return sid in mtime_by_id, mtime_by_id.get(sid, 0.0), sid
            chosen = max(values, key=rank)
            others = ", ".join(f"{value_save_id(v):016x}" for v in values if v != chosen)
            notes.append(
                f"{describe_key(key)} was saved on two machines while they weren't syncing; using "
                f"{value_save_id(chosen):016x} (most recently played). Kept on disk, unused: {others}"
            )
        else:
            chosen = max(values)
        merged.append((key, chosen))
    return merged, notes


def merge_index_conflicts(index_dir: str, profile_dir: str, backup_root: str) -> List[str]:
    """
    If Syncthing left conflict copies of imkvdb.arc in `index_dir`, merge
    them into the index (see merge_index_versions), write it to both slots,
    and move the conflict copies to `backup_root`. Returns messages.
    """
    slots = [os.path.join(index_dir, s) for s in INDEX_SLOTS if os.path.isdir(os.path.join(index_dir, s))]
    conflicts = [os.path.join(d, f) for d in slots for f in sorted(os.listdir(d)) if CONFLICT_RE.match(f)]
    if not conflicts:
        return []

    versions: List[List[Entry]] = []
    for path in [os.path.join(d, "imkvdb.arc") for d in slots] + conflicts:
        if not os.path.isfile(path):
            continue
        try:
            with open(path, "rb") as f:
                versions.append(parse_index(f.read()))
        except (OSError, ValueError) as e:
            return [f"Ryujinx: couldn't read {path} ({e}); save index conflicts left for manual review"]

    merged, notes = merge_index_versions(versions, scan_profile_saves(profile_dir))
    data = serialize_index(merged)
    for d in slots:
        _atomic_write(os.path.join(d, "imkvdb.arc"), data)

    dest = os.path.join(backup_root, datetime.datetime.now().strftime("%Y%m%d-%H%M%S-%f"))
    os.makedirs(dest, exist_ok=True)
    for path in conflicts:
        slot = os.path.basename(os.path.dirname(path))
        shutil.move(path, os.path.join(dest, f"{slot}-{os.path.basename(path)}"))

    return [f"Ryujinx: merged {len(conflicts)} conflicting copy(ies) of this profile's save index "
            f"(originals kept in {dest})"] + notes


def maintain_profile_index(ryujinx_dir: str, profile_dir: str, backup_root: str) -> List[str]:
    """Merge any index conflicts, then pin the counter to this machine's
    range. Ryujinx must not be running. Returns messages."""
    index_dir = os.path.join(profile_dir, PROFILE_INDEX)
    if not index_present(index_dir):
        return []
    messages = merge_index_conflicts(index_dir, profile_dir, backup_root)
    msg = ensure_counter(index_dir, profile_dir, machine_range_base(ryujinx_dir))
    if msg:
        messages.append(msg)
    return messages


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
    - "reusable": the folder's number is in the same range as, and above,
      the index's last issued ID, so a new save may be given that number.
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
        if same_range(folder.save_id, last_published) and folder.save_id > last_published:
            problems["reusable"].append(folder.name)
    return problems


# ---------------------------------------------------------------------------
# Rebuilding the index from a profile
# ---------------------------------------------------------------------------

@dataclass
class ReindexPlan:
    entries: List[Entry]
    changes: List[str] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)

    @property
    def has_changes(self) -> bool:
        return bool(self.changes)


def plan_reindex(profile_dir: str, ryujinx_dir: str) -> Optional[ReindexPlan]:
    """
    Work out the index that exactly matches `profile_dir`'s save folders:

    - System and non-user entries (e.g. SD-card cache saves) are kept as-is.
    - Every user save folder with a readable ExtraData is indexed under the
      key recorded in its own ExtraData.
    - If several folders hold the same save (same key), the most recently
      modified one wins; the others are left on disk, untouched.
    - User entries for saves this profile doesn't have are kept, unless
      their number is taken by a different save in this profile.

    The save-ID counter is left alone; ensure_counter manages it.

    Returns None if Ryujinx has no index yet (it creates one on first run).
    """
    loaded = load_index(ryujinx_dir)
    if loaded is None:
        return None
    entries, _ = loaded

    kept = [(k, v) for k, v in entries if value_space_id(v) != SPACE_USER]
    kept_ids = {value_save_id(v) for _, v in kept}
    current: Dict[bytes, bytes] = {k: v for k, v in entries if value_space_id(v) == SPACE_USER}

    folders = scan_profile_saves(profile_dir)
    plan = ReindexPlan(entries=list(kept))

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

    return plan


def _atomic_write(path: str, data: bytes) -> None:
    tmp = f"{path}.tmp-{os.getpid()}"
    with open(tmp, "wb") as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def apply_reindex(ryujinx_dir: str, plan: ReindexPlan) -> str:
    """Back up the whole index save (machine-locally, next to Ryujinx's own
    copy), then write the planned index to both of its slots. Returns the
    backup path."""
    base = os.path.join(ryujinx_dir, INDEX_SAVE)
    data = serialize_index(plan.entries)
    if sorted(parse_index(data)) != sorted(plan.entries):
        raise RuntimeError("internal error: rebuilt index does not round-trip")

    stamp = datetime.datetime.now().strftime('%Y%m%d-%H%M%S')
    backup = f"{base}{REINDEX_BACKUP_SUFFIX}{stamp}"
    n = 1
    while os.path.lexists(backup):
        backup = f"{base}{REINDEX_BACKUP_SUFFIX}{stamp}-{n}"
        n += 1
    shutil.copytree(base, backup, symlinks=True)

    for slot in ("0", "1"):
        slot_dir = os.path.join(base, slot)
        if not os.path.isdir(slot_dir):
            continue
        _atomic_write(os.path.join(slot_dir, "imkvdb.arc"), data)

    _prune_index_backups(base)
    return backup


def _prune_index_backups(base: str) -> None:
    parent, prefix = os.path.dirname(base), os.path.basename(base) + REINDEX_BACKUP_SUFFIX
    backups = sorted(
        (e for e in os.listdir(parent) if e.startswith(prefix)),
        key=lambda e: os.path.getmtime(os.path.join(parent, e)),
    )
    for name in backups[:-INDEX_BACKUPS_KEPT]:
        shutil.rmtree(os.path.join(parent, name), ignore_errors=True)


# ---------------------------------------------------------------------------
# Process detection
# ---------------------------------------------------------------------------

def process_running(name: str) -> bool:
    """Whether a process whose name (or argv[0], for AppImages) starts with
    `name` is running, case-insensitively."""
    name = name.lower()
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
        if comm.startswith(name) or os.path.basename(argv0).lower().startswith(name):
            return True
    return False


def ryujinx_running() -> bool:
    """Whether a Ryujinx process is running. Ryujinx keeps its save index in
    memory and writes it back on exit, so emu-stitch must not relink its
    saves or touch the index while it runs."""
    return process_running("ryujinx")
