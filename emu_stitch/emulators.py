"""
Emulators module for emu-stitch: Manages smart emulator detection,
conditional save symlinking, anti-loop safeguards, save payload mirroring, and save auditing.
"""

from __future__ import annotations

import os
import shutil
import struct
import subprocess
import datetime
from typing import Dict, List, Optional

from .backups import prune_old_backups
from .config import get_backup_retention

# Wii U title-ID high half for the "Game" category (retail/eShop base games).
# Other categories under 0005xxxx exist (0005000c DLC, 0005000e Update,
# 00050010 System Applications like Mii Maker / Health & Safety Info) but
# only 00050000 represents an actual installed game with its own save data.
CEMU_GAME_CATEGORY = "00050000"


def is_flatpak_installed(app_id: str) -> bool:
    """Check if a Flatpak application is installed."""
    try:
        res = subprocess.run(["flatpak", "info", app_id], capture_output=True, text=True, timeout=5)
        return res.returncode == 0
    except Exception:
        return False


def detect_installed_emulators() -> Dict[str, bool]:
    """
    Detect which emulators are installed on the system via config paths, binaries, or Flatpaks.
    Returns: dict { 'ryujinx': True/False, 'cemu': True/False }
    """
    results: Dict[str, bool] = {}

    # 1. Ryujinx Check
    ryu_config = os.path.expanduser("~/.config/Ryujinx")
    ryu_bin = shutil.which("ryujinx") or shutil.which("Ryujinx")
    ryu_flatpak = is_flatpak_installed("org.ryujinx.Ryujinx")
    results["ryujinx"] = os.path.exists(ryu_config) or bool(ryu_bin) or ryu_flatpak

    # 2. Cemu Check
    cemu_data = os.path.expanduser("~/.local/share/Cemu")
    cemu_config = os.path.expanduser("~/.config/Cemu")
    cemu_bin = shutil.which("cemu") or shutil.which("Cemu")
    cemu_flatpak = is_flatpak_installed("info.cemu.Cemu")
    results["cemu"] = os.path.exists(cemu_data) or os.path.exists(cemu_config) or bool(cemu_bin) or cemu_flatpak

    return results


def _safe_replace_with_symlink(link_path: str, target_dir: str) -> None:
    """
    Ensure `link_path` is a symlink pointing at `target_dir`, without ever
    deleting real data.

    - If `link_path` is already a symlink pointing at `target_dir`: no-op.
    - If `link_path` is a symlink pointing elsewhere (stale): replace it
      atomically, creating `target_dir` if needed.
    - If `link_path` is a real file/directory: merge-copy its contents into
      `target_dir` (existing files at the destination win, source is never
      overwritten), then rename the original to
      `<link_path>.bak-<YYYYMMDD-HHMMSS>` (never delete), then create the
      symlink.
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

    if os.path.isdir(link_path):
        for root, dirs, files in os.walk(link_path):
            rel = os.path.relpath(root, link_path)
            dest_root = target_dir if rel == "." else os.path.join(target_dir, rel)
            os.makedirs(dest_root, exist_ok=True)
            for fname in files:
                src_f = os.path.join(root, fname)
                dst_f = os.path.join(dest_root, fname)
                if not os.path.exists(dst_f):
                    shutil.copy2(src_f, dst_f)
        backup_path = f"{link_path}.bak-{datetime.datetime.now().strftime('%Y%m%d-%H%M%S')}"
        os.rename(link_path, backup_path)
        prune_old_backups(link_path, get_backup_retention())
    elif os.path.isfile(link_path):
        backup_path = f"{link_path}.bak-{datetime.datetime.now().strftime('%Y%m%d-%H%M%S')}"
        os.rename(link_path, backup_path)
        prune_old_backups(link_path, get_backup_retention())

    os.symlink(real_target, link_path)


def configure_ryujinx_symlinks(active_link: str) -> None:
    """Ensure Ryujinx bis/user/save and saveMeta symlinks are correctly routed."""
    ryujinx_user = os.path.expanduser("~/.config/Ryujinx/bis/user")
    os.makedirs(ryujinx_user, exist_ok=True)

    for name, subtarget in [("save", "saves"), ("saveMeta", "saveMeta")]:
        link_path = os.path.join(ryujinx_user, name)
        expected_target = os.path.join(active_link, "ryujinx", subtarget)
        _safe_replace_with_symlink(link_path, expected_target)


def configure_cemu_symlinks(emu_dir: str, active_link: str) -> None:
    """Ensure Cemu mlc01/usr/save symlinks are correctly routed."""
    cemu_targets = [
        os.path.expanduser("~/.local/share/Cemu/mlc01/usr/save"),
        os.path.join(emu_dir, "roms/wiiu/mlc01/usr/save")
    ]
    expected_cemu_target = os.path.join(active_link, "Cemu/saves")
    for link_path in cemu_targets:
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
    installed = detect_installed_emulators()

    if installed.get("ryujinx"):
        configure_ryujinx_symlinks(active_link)

    if installed.get("cemu"):
        configure_cemu_symlinks(emu_dir, active_link)
