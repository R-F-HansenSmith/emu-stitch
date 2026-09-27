"""
Emulators module for emu-stitch: Manages smart emulator detection,
conditional save symlinking, anti-loop safeguards, save payload mirroring, and save auditing.
"""

from __future__ import annotations

import os
import shutil
import struct
import subprocess
from typing import Dict, List, Optional

from .backups import merge_tree, migrate_to_backup
from .ryujinx import (
    INDEX_SAVE,
    PROFILE_INDEX,
    index_present,
    maintain_profile_index,
    ryujinx_running,
    scan_profile_saves,
)

# Wii U title-ID high half for the "Game" category (retail/eShop base games).
# Other categories under 0005xxxx exist (0005000c DLC, 0005000e Update,
# 00050010 System Applications like Mii Maker / Health & Safety Info) but
# only 00050000 represents an actual installed game with its own save data.
CEMU_GAME_CATEGORY = "00050000"

RYUJINX_FLATPAK = "org.ryujinx.Ryujinx"
CEMU_FLATPAK = "info.cemu.Cemu"


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
    """Ensure Ryujinx's bis/user/save and saveMeta, and its save index, are
    routed into the active profile."""
    ryujinx_dir = ryujinx_dir or os.path.expanduser("~/.config/Ryujinx")
    ryujinx_user = os.path.join(ryujinx_dir, "bis", "user")
    os.makedirs(ryujinx_user, exist_ok=True)

    for name, subtarget in [("save", "saves"), ("saveMeta", "saveMeta")]:
        link_path = os.path.join(ryujinx_user, name)
        expected_target = os.path.join(active_link, "ryujinx", subtarget)
        _safe_replace_with_symlink(link_path, expected_target)

    route_ryujinx_index(ryujinx_dir, active_link)


def route_ryujinx_index(ryujinx_dir: str, active_link: str) -> None:
    """
    Swap Ryujinx's save index (the 8000000000000000 system save) in with the
    profile, so a profile's saves and the index describing them always
    travel together.

    A profile without an index yet is seeded with a copy of the one
    currently in place (Ryujinx's own, or the previous profile's): its saves
    were numbered by that shared index, so the copy already describes them.
    Nothing happens until Ryujinx has created its index on first launch.
    """
    link_path = os.path.join(ryujinx_dir, INDEX_SAVE)
    target = os.path.join(active_link, PROFILE_INDEX)
    if not os.path.lexists(link_path):
        return
    if not index_present(target):
        if not index_present(link_path):
            return
        merge_tree(os.path.realpath(link_path), target)
    _safe_replace_with_symlink(link_path, target)


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
    # Save folder numbers can be in any machine's range (see ryujinx.py), so
    # use the same scan as the index code rather than matching a prefix.
    ryu_folders = scan_profile_saves(active_link)
    title_ids = {struct.unpack_from("<Q", f.key, 0)[0] for f in ryu_folders if f.key is not None}
    if title_ids:
        count = len(title_ids)
        detected_saves.append({
            "emulator": "Ryujinx (Switch)",
            "name": f"{count} game{'s' if count != 1 else ''} tracked",
            "details": f"{count} unique title(s) across {len(ryu_folders)} save record(s)",
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


def configure_all_emulators(emu_dir: str, active_link: str, profile_name: str) -> List[str]:
    """Run emulator configuration routines ONLY for detected/installed
    emulators. Returns messages worth showing the user."""
    messages: List[str] = []

    ryujinx_dirs = ryujinx_config_dirs()
    if ryujinx_dirs and ryujinx_running():
        messages.append(
            "Ryujinx is running, so its saves were not switched. Close Ryujinx, then run 'emu-stitch switch'."
        )
    else:
        backup_root = os.path.expanduser("~/.local/state/emu-stitch/ryujinx-index-conflicts")
        for ryujinx_dir in ryujinx_dirs:
            configure_ryujinx_symlinks(active_link, ryujinx_dir)
            messages += maintain_profile_index(ryujinx_dir, os.path.realpath(active_link), backup_root)

    configure_cemu_symlinks(emu_dir, active_link)
    return messages
