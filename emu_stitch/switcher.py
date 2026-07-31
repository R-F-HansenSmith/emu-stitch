"""
Switcher module for emu-stitch: Generic profile switcher,
save symlink repointing, and profile folder isolation.
"""

from __future__ import annotations

import os
import json
import shutil
import logging
import datetime
from typing import Dict, Optional, Tuple

from .detector import detect_emulation_dir, detect_active_steam_user, sanitize_name
from .emulators import configure_all_emulators
from .syncthing import generate_stignore


def _atomic_write_json(path: str, data: Dict[str, str]) -> None:
    """Write JSON to `path` via temp-file + os.replace, so a failed or
    interrupted write can never leave `path` truncated or corrupted."""
    tmp_path = f"{path}.tmp-{os.getpid()}"
    with open(tmp_path, "w") as f:
        json.dump(data, f, indent=2)
    os.replace(tmp_path, path)


def load_user_map(map_file: str) -> Dict[str, str]:
    """Load or initialize generic user profile map JSON."""
    if not os.path.exists(map_file):
        initial_map: Dict[str, str] = {}
        _atomic_write_json(map_file, initial_map)
        return initial_map
    try:
        with open(map_file, "r") as f:
            return json.load(f)
    except Exception as e:
        logging.error(f"Error loading map file: {e}")
        return {}


def run_switch(emu_dir: Optional[str] = None) -> Tuple[str, str]:
    """Perform atomic save profile switch and emulator symlink update generically."""
    if not emu_dir:
        emu_dir = detect_emulation_dir()

    saves_base = os.path.join(emu_dir, "saves_by_user")
    active_link = os.path.join(emu_dir, "saves")
    map_file = os.path.join(saves_base, "user_map.json")

    os.makedirs(saves_base, exist_ok=True)
    user_map = load_user_map(map_file)

    steamid3, persona = detect_active_steam_user()
    if not steamid3:
        profile_name = "Default_User"
        print("Warning: Could not determine active Steam ID. Using 'Default_User'")
    else:
        if steamid3 in user_map:
            profile_name = user_map[steamid3]
        elif persona:
            profile_name = sanitize_name(persona)
            user_map[steamid3] = profile_name
            _atomic_write_json(map_file, user_map)
        else:
            profile_name = f"User_{steamid3}"
            user_map[steamid3] = profile_name
            _atomic_write_json(map_file, user_map)

    target_profile_dir = os.path.join(saves_base, profile_name)
    os.makedirs(target_profile_dir, exist_ok=True)
    generate_stignore(target_profile_dir)

    # Initial setup migration if saves is still a real directory.
    # Merge contents into the new profile dir (destination wins), then rename
    # the original to a timestamped backup — never delete it.
    if os.path.exists(active_link) and not os.path.islink(active_link):
        for item in os.listdir(active_link):
            src = os.path.join(active_link, item)
            dst = os.path.join(target_profile_dir, item)
            if not os.path.exists(dst):
                if os.path.isdir(src):
                    shutil.copytree(src, dst, symlinks=True)
                else:
                    shutil.copy2(src, dst)
        backup_path = active_link + ".bak-" + datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
        os.rename(active_link, backup_path)

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
    return profile_name, real_target


def setup_systemd_watcher() -> Tuple[bool, str]:
    """Create and enable a systemd user path unit to watch loginusers.vdf for instant profile switching."""
    user_systemd_dir = os.path.expanduser("~/.config/systemd/user")
    os.makedirs(user_systemd_dir, exist_ok=True)

    path_unit = os.path.join(user_systemd_dir, "emu-stitch-watcher.path")
    service_unit = os.path.join(user_systemd_dir, "emu-stitch-watcher.service")
    wrapper_bin = os.path.expanduser("~/.local/bin/emu-stitch")

    path_content = (
        "[Unit]\n"
        "Description=Watch Steam loginusers.vdf for active user changes\n\n"
        "[Path]\n"
        "PathModified=%h/.local/share/Steam/config/loginusers.vdf\n"
        "Unit=emu-stitch-watcher.service\n\n"
        "[Install]\n"
        "WantedBy=default.target\n"
    )

    service_content = (
        "[Unit]\n"
        "Description=emu-stitch automatic save profile switcher\n\n"
        "[Service]\n"
        "Type=oneshot\n"
        f"ExecStart={wrapper_bin} switch\n"
    )

    try:
        import subprocess
        with open(path_unit, "w") as f:
            f.write(path_content)
        with open(service_unit, "w") as f:
            f.write(service_content)

        subprocess.run(["systemctl", "--user", "daemon-reload"], capture_output=True)
        res = subprocess.run(["systemctl", "--user", "enable", "--now", "emu-stitch-watcher.path"], capture_output=True, text=True)
        if res.returncode == 0:
            return True, "systemd loginusers.vdf watcher enabled for automatic profile switching on Steam user change."
        return False, f"systemctl failed: {res.stderr.strip()}"
    except Exception as e:
        return False, f"Error configuring systemd watcher: {e}"
