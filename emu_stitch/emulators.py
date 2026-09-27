"""
Emulators module for emu-stitch: Manages smart emulator detection,
conditional save symlinking, anti-loop safeguards, save payload mirroring, and save auditing.
"""

from __future__ import annotations

import os
import shutil
import struct
import subprocess
from typing import Dict, List, Optional, Tuple

from .backups import migrate_to_backup

# Wii U title-ID high half for the "Game" category (retail/eShop base games).
# Other categories under 0005xxxx exist (0005000c DLC, 0005000e Update,
# 00050010 System Applications like Mii Maker / Health & Safety Info) but
# only 00050000 represents an actual installed game with its own save data.
CEMU_GAME_CATEGORY = "00050000"

RYUJINX_FLATPAK = "org.ryujinx.Ryujinx"
CEMU_FLATPAK = "info.cemu.Cemu"

# Ryujinx (like real Switch firmware) never finds a save by scanning
# bis/user/save; it looks the game up in a machine-wide save-data index — a
# key/value store in the 8000000000000000 system save — to get the numbered
# save folder to open. That index is not part of any emu-stitch profile.
RYUJINX_INDEX_SAVE = os.path.join("bis", "system", "save", "8000000000000000")
# Save IDs at or above this are system saves, stored outside bis/user/save.
_SYSTEM_SAVE_ID_MIN = 0x8000000000000000


def is_flatpak_installed(app_id: str) -> bool:
    """Check if a Flatpak application is installed."""
    try:
        res = subprocess.run(["flatpak", "info", app_id], capture_output=True, text=True, timeout=5)
        return res.returncode == 0
    except Exception:
        return False


def ryujinx_config_dirs() -> List[str]:
    """Every Ryujinx config root in use on this system: the native one
    (~/.config/Ryujinx) and/or the Flatpak sandbox's own copy."""
    dirs = []
    native = os.path.expanduser("~/.config/Ryujinx")
    if os.path.exists(native) or shutil.which("ryujinx") or shutil.which("Ryujinx"):
        dirs.append(native)
    if is_flatpak_installed(RYUJINX_FLATPAK):
        dirs.append(os.path.expanduser(f"~/.var/app/{RYUJINX_FLATPAK}/config/Ryujinx"))
    return dirs


def cemu_save_paths(emu_dir: Optional[str] = None) -> List[str]:
    """Every Cemu `mlc01/usr/save` path in use on this system: native,
    Flatpak, and (if `emu_dir` is given and has a roms/wiiu folder) EmuDeck's
    in-tree mlc01."""
    paths = []
    native_data = os.path.expanduser("~/.local/share/Cemu")
    if (os.path.exists(native_data) or os.path.exists(os.path.expanduser("~/.config/Cemu"))
            or shutil.which("cemu") or shutil.which("Cemu")):
        paths.append(os.path.join(native_data, "mlc01/usr/save"))
    if is_flatpak_installed(CEMU_FLATPAK):
        paths.append(os.path.expanduser(f"~/.var/app/{CEMU_FLATPAK}/data/Cemu/mlc01/usr/save"))
    if paths and emu_dir and os.path.isdir(os.path.join(emu_dir, "roms/wiiu")):
        paths.append(os.path.join(emu_dir, "roms/wiiu/mlc01/usr/save"))
    return paths


def detect_installed_emulators() -> Dict[str, bool]:
    """
    Detect which emulators are installed on the system via config paths, binaries, or Flatpaks.
    Returns: dict { 'ryujinx': True/False, 'cemu': True/False }
    """
    return {
        "ryujinx": bool(ryujinx_config_dirs()),
        "cemu": bool(cemu_save_paths()),
    }


def _safe_replace_with_symlink(link_path: str, target_dir: str) -> None:
    """
    Ensure `link_path` is a symlink pointing at `target_dir`, without ever
    deleting real data.

    - If `link_path` is already a symlink pointing at `target_dir`: no-op.
    - If `link_path` is a symlink pointing elsewhere (stale): replace it
      atomically, creating `target_dir` if needed.
    - If `link_path` is a real file/directory: migrate it into `target_dir`
      and keep the original as a timestamped backup (see
      backups.migrate_to_backup), then create the symlink.
    """
    if os.path.islink(target_dir):
        os.unlink(target_dir)

    os.makedirs(target_dir, exist_ok=True)
    real_target = os.path.realpath(os.path.abspath(target_dir))

    if os.path.islink(link_path):
        if os.path.realpath(link_path) == real_target:
            return
        tmp_link = link_path + f".tmp-{os.getpid()}"
        if os.path.lexists(tmp_link):
            os.unlink(tmp_link)
        os.symlink(real_target, tmp_link)
        os.replace(tmp_link, link_path)
        return

    if os.path.lexists(link_path):
        migrate_to_backup(link_path, target_dir)

    os.symlink(real_target, link_path)


def configure_ryujinx_symlinks(active_link: str, ryujinx_dir: Optional[str] = None) -> None:
    """Ensure Ryujinx bis/user/save and saveMeta symlinks are correctly routed."""
    ryujinx_dir = ryujinx_dir or os.path.expanduser("~/.config/Ryujinx")
    ryujinx_user = os.path.join(ryujinx_dir, "bis", "user")
    os.makedirs(ryujinx_user, exist_ok=True)

    for name, subtarget in [("save", "saves"), ("saveMeta", "saveMeta")]:
        link_path = os.path.join(ryujinx_user, name)
        expected_target = os.path.join(active_link, "ryujinx", subtarget)
        _safe_replace_with_symlink(link_path, expected_target)


def configure_cemu_symlinks(emu_dir: str, active_link: str) -> None:
    """Ensure Cemu mlc01/usr/save symlinks are correctly routed."""
    expected_cemu_target = os.path.join(active_link, "Cemu/saves")
    for link_path in cemu_save_paths(emu_dir):
        os.makedirs(os.path.dirname(link_path), exist_ok=True)
        _safe_replace_with_symlink(link_path, expected_cemu_target)


def _read_ryujinx_save_title_id(save_dir: str) -> Optional[int]:
    """Read the 8-byte little-endian Title ID from a Ryujinx save's
    ExtraData file (the Nintendo Switch save-data extra-info struct)."""
    for fname in ("ExtraData0", "ExtraData1"):
        path = os.path.join(save_dir, fname)
        if not os.path.isfile(path):
            continue
        try:
            with open(path, "rb") as f:
                header = f.read(8)
        except OSError:
            continue
        if len(header) == 8:
            return struct.unpack("<Q", header)[0]
    return None


def _newest_ryujinx_index_slot(ryujinx_dir: str) -> Optional[str]:
    """The index is committed to two alternating slots (0/ and 1/); the one
    written most recently is current."""
    slots = [
        os.path.join(ryujinx_dir, RYUJINX_INDEX_SAVE, slot)
        for slot in ("0", "1")
        if os.path.isfile(os.path.join(ryujinx_dir, RYUJINX_INDEX_SAVE, slot, "imkvdb.arc"))
    ]
    if not slots:
        return None
    return max(slots, key=lambda d: os.path.getmtime(os.path.join(d, "imkvdb.arc")))


def read_ryujinx_save_index(ryujinx_dir: str) -> Optional[Tuple[Dict[int, int], int]]:
    """
    Parse Ryujinx's save-data index (imkvdb.arc): an "IMKV" header with an
    entry count, then "IMEN" entries each holding a 0x40-byte key (a
    SaveDataAttribute, whose first u64 is the title ID) and a 0x40-byte
    value (a SaveDataIndexerValue, whose first u64 is the save ID).

    Returns ({user save ID: title ID}, last published save ID), or None if
    there's no index or it can't be parsed.
    """
    slot = _newest_ryujinx_index_slot(ryujinx_dir)
    if slot is None:
        return None
    try:
        with open(os.path.join(slot, "imkvdb.arc"), "rb") as f:
            data = f.read()
        magic, _, count = struct.unpack_from("<4sII", data, 0)
        if magic != b"IMKV":
            return None
        index: Dict[int, int] = {}
        offset = 12
        for _ in range(count):
            entry_magic, key_size, value_size = struct.unpack_from("<4sII", data, offset)
            if entry_magic != b"IMEN" or key_size < 8 or value_size < 8:
                return None
            offset += 12
            title_id = struct.unpack_from("<Q", data, offset)[0]
            save_id = struct.unpack_from("<Q", data, offset + key_size)[0]
            offset += key_size + value_size
            if save_id < _SYSTEM_SAVE_ID_MIN:
                index[save_id] = title_id
        last_published = 0
        last_path = os.path.join(slot, "lastPublishedId")
        if os.path.isfile(last_path):
            with open(last_path, "rb") as f:
                raw = f.read(8)
            if len(raw) == 8:
                last_published = struct.unpack("<Q", raw)[0]
        return index, last_published
    except (OSError, struct.error):
        return None


def check_ryujinx_save_index(profile_dir: str, ryujinx_dir: str) -> Dict[str, List[str]]:
    """
    Read-only consistency check between a profile's Ryujinx save folders and
    the machine's save index. Save folders created on another machine, or
    under a different index, can end up:

    - "mismatched": the index maps that folder number to a *different* game,
      so Ryujinx will hand this folder's data to the wrong game.
    - "orphaned": the index doesn't reference the folder at all, so Ryujinx
      will never load it.
    - "reusable": the folder's number is above the index's last issued ID,
      so the next new save Ryujinx creates may be given the same number.

    Returns {"mismatched": [...], "orphaned": [...], "reusable": [...]}
    of human-readable descriptions (all empty if no index is available).
    """
    problems: Dict[str, List[str]] = {"mismatched": [], "orphaned": [], "reusable": []}
    parsed = read_ryujinx_save_index(ryujinx_dir)
    saves_dir = os.path.join(profile_dir, "ryujinx", "saves")
    if parsed is None or not os.path.isdir(saves_dir):
        return problems
    index, last_published = parsed

    for name in sorted(os.listdir(saves_dir)):
        try:
            save_id = int(name, 16)
        except ValueError:
            continue
        if len(name) != 16 or not os.path.isdir(os.path.join(saves_dir, name)):
            continue
        title_id = _read_ryujinx_save_title_id(os.path.join(saves_dir, name))
        title = f"{title_id:016x}" if title_id is not None else "unknown title"
        if save_id in index:
            if title_id is not None and index[save_id] != title_id:
                problems["mismatched"].append(
                    f"{name} holds {title}, but Ryujinx maps it to {index[save_id]:016x}"
                )
        else:
            problems["orphaned"].append(f"{name} ({title})")
        if save_id > last_published:
            problems["reusable"].append(name)
    return problems


def audit_emulator_saves(active_link: str) -> List[Dict[str, str]]:
    """
    Scans active save profile directory for detected game save data.
    Returns: list of dicts [{ 'emulator': ..., 'name': ..., 'details': ... }]
    """
    detected_saves: List[Dict[str, str]] = []
    if not os.path.exists(active_link):
        return detected_saves

    # 1. Ryujinx (Nintendo Switch)
    # A single game can have multiple save-ID folders (e.g. one per
    # in-emulator user profile), so distinct games are counted by their real
    # Title ID, not by save folder. There's no local source for game names
    # (Ryujinx keeps no local title/name cache), so only a count is shown.
    ryu_saves = os.path.join(active_link, "ryujinx", "saves")
    if os.path.exists(ryu_saves):
        save_ids = sorted(
            d for d in os.listdir(ryu_saves)
            if os.path.isdir(os.path.join(ryu_saves, d)) and d.startswith("00000000")
        )
        title_ids = {
            title_id
            for sid in save_ids
            for title_id in [_read_ryujinx_save_title_id(os.path.join(ryu_saves, sid))]
            if title_id is not None
        }
        if title_ids:
            count = len(title_ids)
            detected_saves.append({
                "emulator": "Ryujinx (Switch)",
                "name": f"{count} game{'s' if count != 1 else ''} tracked",
                "details": f"{count} unique title(s) across {len(save_ids)} save record(s)",
                "count": count,
            })

    # 2. Cemu (Wii U)
    # Only the 00050000 (Game) title-ID category is an actual installed
    # game; other 0005xxxx categories are DLC/updates/system applications
    # that share the base game's save or aren't games at all.
    cemu_saves = os.path.join(active_link, "Cemu", "saves", CEMU_GAME_CATEGORY)
    if os.path.isdir(cemu_saves):
        game_ids = [d for d in os.listdir(cemu_saves) if os.path.isdir(os.path.join(cemu_saves, d))]
        if game_ids:
            count = len(game_ids)
            detected_saves.append({
                "emulator": "Cemu (Wii U)",
                "name": f"{count} game{'s' if count != 1 else ''} tracked",
                "details": f"{count} title(s) under the Game category",
                "count": count,
            })

    # 3. RetroArch / General Save Files (.srm, .sav, .state)
    # NOTE: RetroArch saves are detected and reported here but are NOT automatically
    # symlink-routed on profile switch. RetroArch users should manually configure
    # their saves/states directories to point inside ~/Emulation/saves/<profile>/retroarch/.
    for root, _, files in os.walk(active_link):
        rel = os.path.relpath(root, active_link)
        if rel.startswith("ryujinx") or rel.startswith("Cemu"):
            continue
        save_files = [f for f in files if f.endswith((".srm", ".sav", ".state", ".mcd"))]
        if save_files:
            system_name = os.path.basename(root).upper()
            detected_saves.append({
                "emulator": f"RetroArch / Standalone ({system_name})",
                "name": f"{len(save_files)} save file(s)",
                "details": f"Folder: {rel}"
            })

    return detected_saves


def configure_all_emulators(emu_dir: str, active_link: str, profile_name: str) -> None:
    """Run emulator configuration routines ONLY for detected/installed emulators."""
    for ryujinx_dir in ryujinx_config_dirs():
        configure_ryujinx_symlinks(active_link, ryujinx_dir)

    configure_cemu_symlinks(emu_dir, active_link)
