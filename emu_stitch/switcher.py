"""
Switcher module for emu-stitch: Generic profile switcher,
save symlink repointing, and profile folder isolation.
"""

from __future__ import annotations

import os
import re
import json
import fcntl
import shutil
import logging
import subprocess
from typing import Dict, List, Optional, Tuple

from .backups import migrate_to_backup
from .detector import detect_emulation_dir, detect_active_steam_user, sanitize_name, steam_root
from .config import get_ryujinx_auto_reindex
from .emulators import configure_all_emulators, ryujinx_config_dirs
from .ryujinx import auto_reindex
from .syncthing import generate_stignore


def _atomic_write_json(path: str, data: Dict[str, str]) -> None:
    """Write JSON to `path` via temp-file + os.replace, so a failed or
    interrupted write can never leave `path` truncated or corrupted."""
    tmp_path = f"{path}.tmp-{os.getpid()}"
    with open(tmp_path, "w") as f:
        json.dump(data, f, indent=2)
    os.replace(tmp_path, path)


def load_user_map(map_file: str) -> Dict[str, str]:
    """Load or initialize generic user profile map JSON.

    Every profile name is re-sanitized on load: the file may have been
    edited by hand or synced in from another machine, and its values are
    used as directory names, so they must never contain path separators."""
    if not os.path.exists(map_file):
        initial_map: Dict[str, str] = {}
        _atomic_write_json(map_file, initial_map)
        return initial_map
    try:
        with open(map_file, "r") as f:
            raw = json.load(f)
    except Exception as e:
        logging.error(f"Error loading map file: {e}")
        return {}
    if not isinstance(raw, dict):
        logging.error(f"Ignoring malformed map file (expected a JSON object): {map_file}")
        return {}
    user_map: Dict[str, str] = {}
    for steamid3, name in raw.items():
        clean = sanitize_name(str(name), fallback=f"User_{steamid3}")
        if clean != name:
            logging.warning(f"Unsafe profile name {name!r} in {map_file}; using {clean!r}")
        user_map[str(steamid3)] = clean
    return user_map


def _unique_profile_name(user_map: Dict[str, str], name: str, steamid3: str) -> str:
    """Disambiguate `name` if another Steam account already owns it. The
    comparison ignores case because Syncthing folder IDs are lowercased."""
    taken = {v.lower() for k, v in user_map.items() if k != steamid3}
    if name.lower() in taken:
        return f"{name}_{steamid3}"
    return name


def run_switch(emu_dir: Optional[str] = None) -> Tuple[str, str]:
    """Perform atomic save profile switch and emulator symlink update generically."""
    if not emu_dir:
        emu_dir = detect_emulation_dir()

    saves_base = os.path.join(emu_dir, "saves_by_user")
    os.makedirs(saves_base, exist_ok=True)

    # The autostart entry and the systemd watcher can both fire at login;
    # serialize runs so they never migrate or relink concurrently.
    with open(os.path.join(saves_base, ".emu-stitch.lock"), "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        return _run_switch_locked(emu_dir, saves_base)


def _run_switch_locked(emu_dir: str, saves_base: str) -> Tuple[str, str]:
    active_link = os.path.join(emu_dir, "saves")
    map_file = os.path.join(saves_base, "user_map.json")
    user_map = load_user_map(map_file)

    steamid3, persona = detect_active_steam_user()
    if not steamid3:
        profile_name = "Default_User"
        print("Warning: Could not determine active Steam ID. Using 'Default_User'")
    elif steamid3 in user_map:
        profile_name = user_map[steamid3]
    else:
        base_name = sanitize_name(persona or "", fallback=f"User_{steamid3}")
        profile_name = _unique_profile_name(user_map, base_name, steamid3)
        user_map[steamid3] = profile_name
        _atomic_write_json(map_file, user_map)

    target_profile_dir = os.path.join(saves_base, profile_name)
    os.makedirs(target_profile_dir, exist_ok=True)
    generate_stignore(target_profile_dir)

    # Initial setup migration if saves is still a real directory: merge it
    # into the profile and keep the original as a backup — never delete it.
    if os.path.exists(active_link) and not os.path.islink(active_link):
        migrate_to_backup(active_link, target_profile_dir)

    # Atomic symlink update: build the new symlink at a temp path, then
    # os.replace() it into place. This is atomic on POSIX and avoids any
    # window where `active_link` doesn't exist.
    real_target = os.path.abspath(target_profile_dir)
    real_current = os.path.realpath(active_link) if os.path.islink(active_link) else None

    if real_current != real_target:
        tmp_link = active_link + f".tmp-{os.getpid()}"
        if os.path.lexists(tmp_link):
            os.unlink(tmp_link)
        os.symlink(real_target, tmp_link)
        os.replace(tmp_link, active_link)
        print(f"Switched active save profile -> {profile_name} ({real_target})")
    else:
        print(f"Active save profile is already set to -> {profile_name}")

    # Route emulators & mirror save payloads
    configure_all_emulators(emu_dir, active_link, profile_name)

    if get_ryujinx_auto_reindex():
        all_profiles = [
            os.path.join(saves_base, d) for d in os.listdir(saves_base)
            if os.path.isdir(os.path.join(saves_base, d))
        ]
        for msg in auto_reindex(real_target, ryujinx_config_dirs(), all_profiles):
            print(msg)

    return profile_name, real_target


def list_profiles(emu_dir: Optional[str] = None) -> List[Dict[str, object]]:
    """List all known save profiles under saves_by_user, marking which one
    is currently active. Returns: [{ 'name', 'path', 'active', 'steamid3' }, ...]"""
    if not emu_dir:
        emu_dir = detect_emulation_dir()

    saves_base = os.path.join(emu_dir, "saves_by_user")
    active_link = os.path.join(emu_dir, "saves")
    active_target = os.path.realpath(active_link) if os.path.islink(active_link) else None

    if not os.path.isdir(saves_base):
        return []

    map_file = os.path.join(saves_base, "user_map.json")
    user_map = load_user_map(map_file) if os.path.exists(map_file) else {}
    steamid_by_name: Dict[str, str] = {}
    for steamid3, name in user_map.items():
        steamid_by_name.setdefault(name, steamid3)

    profiles = []
    for name in sorted(os.listdir(saves_base)):
        profile_dir = os.path.join(saves_base, name)
        if not os.path.isdir(profile_dir):
            continue
        profiles.append({
            "name": name,
            "path": profile_dir,
            "active": os.path.realpath(profile_dir) == active_target,
            "steamid3": steamid_by_name.get(name),
        })
    return profiles


def emu_stitch_executable() -> str:
    """Absolute path of the installed emu-stitch command. uv's tool bin
    directory is configurable (UV_TOOL_BIN_DIR, XDG_BIN_HOME), so look it up
    on PATH rather than assuming ~/.local/bin."""
    return shutil.which("emu-stitch") or os.path.expanduser("~/.local/bin/emu-stitch")


def quote_exec_arg(arg: str) -> str:
    """Quote a path for a systemd ExecStart= or .desktop Exec= line. Both
    accept double-quoted arguments with backslash escapes and treat `%` as
    a specifier, so the same escaping works for either."""
    arg = arg.replace("%", "%%")
    if re.fullmatch(r"[A-Za-z0-9_./+%-]+", arg):
        return arg
    return '"' + re.sub(r'(["`$\\])', r"\\\1", arg) + '"'


def watcher_installed() -> bool:
    return os.path.exists(os.path.expanduser("~/.config/systemd/user/emu-stitch-watcher.path"))


def setup_systemd_watcher() -> Tuple[bool, str]:
    """Create and enable a systemd user path unit to watch loginusers.vdf for instant profile switching."""
    user_systemd_dir = os.path.expanduser("~/.config/systemd/user")
    os.makedirs(user_systemd_dir, exist_ok=True)

    path_unit = os.path.join(user_systemd_dir, "emu-stitch-watcher.path")
    service_unit = os.path.join(user_systemd_dir, "emu-stitch-watcher.service")
    exec_bin = quote_exec_arg(emu_stitch_executable())
    loginusers_vdf = os.path.join(steam_root(), "config", "loginusers.vdf").replace("%", "%%")

    path_content = (
        "[Unit]\n"
        "Description=Watch Steam loginusers.vdf for active user changes\n\n"
        "[Path]\n"
        f"PathModified={loginusers_vdf}\n"
        "Unit=emu-stitch-watcher.service\n\n"
        "[Install]\n"
        "WantedBy=default.target\n"
    )

    service_content = (
        "[Unit]\n"
        "Description=emu-stitch automatic save profile switcher\n\n"
        "[Service]\n"
        "Type=oneshot\n"
        f"ExecStart={exec_bin} switch\n"
    )

    try:
        with open(path_unit, "w") as f:
            f.write(path_content)
        with open(service_unit, "w") as f:
            f.write(service_content)

        subprocess.run(["systemctl", "--user", "daemon-reload"], capture_output=True, timeout=10)
        res = subprocess.run(
            ["systemctl", "--user", "enable", "--now", "emu-stitch-watcher.path"],
            capture_output=True, text=True, timeout=10,
        )
        if res.returncode == 0:
            return True, "systemd loginusers.vdf watcher enabled for automatic profile switching on Steam user change."
        return False, f"systemctl failed: {res.stderr.strip()}"
    except Exception as e:
        return False, f"Error configuring systemd watcher: {e}"
