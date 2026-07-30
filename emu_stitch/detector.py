"""
Detector module for emu-stitch: Dynamically scans common handheld locations
for the Emulation directory and detects active Steam AccountName/PersonaName.
"""

from __future__ import annotations

import os
import glob
import re
import logging
from typing import Optional, Tuple


def _has_emulation_signatures(path: str) -> bool:
    """Check if directory contains active emulation subfolders."""
    if not os.path.isdir(path):
        return False
    signatures = {"saves", "roms", "saves_by_user", "hd_packs", "tools", "bios"}
    try:
        subdirs = {d for d in os.listdir(path) if os.path.isdir(os.path.join(path, d))}
        return bool(subdirs.intersection(signatures))
    except Exception:
        return False


def detect_emulation_dir() -> str:
    """
    Scans common internal and external handheld paths for the Emulation directory.
    Prioritizes active Emulation directories (containing saves/roms/etc.).
    """
    env_dir = os.environ.get("EMU_DIR") or os.environ.get("EMUDECK_DIR")
    if env_dir and os.path.exists(env_dir):
        return os.path.abspath(env_dir)

    home_emu = os.path.expanduser("~/Emulation")

    sd_patterns = [
        "/run/media/*/*/Emulation",
        "/run/media/*/Emulation",
        "/run/media/Emulation",
        "/mnt/*/*/Emulation",
        "/mnt/*/Emulation",
        "/mnt/Emulation",
        "/media/*/*/Emulation",
        "/media/*/Emulation",
        "/media/Emulation"
    ]
    external_matches = []
    for pattern in sd_patterns:
        for m in glob.glob(pattern):
            if os.path.isdir(m) and m not in external_matches:
                external_matches.append(m)

    active_external = [m for m in external_matches if _has_emulation_signatures(m)]
    home_has_signatures = _has_emulation_signatures(home_emu)

    if active_external and not home_has_signatures:
        return max(active_external, key=os.path.getmtime)

    if os.path.exists(home_emu):
        return home_emu

    if external_matches:
        return max(external_matches, key=os.path.getmtime)

    return home_emu


def extract_account_name(block: str) -> Optional[str]:
    """Extract AccountName (or fallback to PersonaName) from Steam VDF block."""
    match = re.search(r'"AccountName"\s*"([^"]+)"', block)
    if match:
        return match.group(1)
    match_p = re.search(r'"PersonaName"\s*"([^"]+)"', block)
    if match_p:
        return match_p.group(1)
    return None


def sanitize_name(name: str) -> str:
    """Sanitize account/persona name into a valid, clean folder name."""
    sanitized = re.sub(r'[^a-zA-Z0-9_-]', '_', name)
    return sanitized.strip('_') or "Default_User"


def detect_active_steam_user() -> Tuple[Optional[str], Optional[str]]:
    """
    Generic Steam active user detection.
    Reads Steam's loginusers.vdf (sorted by MostRecent and highest Timestamp)
    and extracts AccountName (or PersonaName), falling back to userdata/ mtime.
    Returns: (steamid3, sanitized_account_name)
    """
    loginusers_vdf = os.path.expanduser("~/.local/share/Steam/config/loginusers.vdf")
    userdata_dir = os.path.expanduser("~/.local/share/Steam/userdata")

    # 1. Inspect loginusers.vdf
    if os.path.exists(loginusers_vdf):
        try:
            with open(loginusers_vdf, "r", encoding="utf-8", errors="ignore") as f:
                content = f.read()
            blocks = re.findall(r'"(\d{17})"\s*\{([^}]+)\}', content, re.DOTALL)
            candidates = []
            for steamid64, block in blocks:
                steamid3 = str(int(steamid64) - 76561197960265728)
                account_name = extract_account_name(block)
                is_recent = 1 if re.search(r'"MostRecent"\s+"1"', block) else 0
                ts_match = re.search(r'"Timestamp"\s*"(\d+)"', block)
                timestamp = int(ts_match.group(1)) if ts_match else 0
                candidates.append((is_recent, timestamp, steamid3, account_name))
            if candidates:
                candidates.sort(key=lambda x: (x[0], x[1]), reverse=True)
                top = candidates[0]
                s_id3, raw_name = top[2], top[3]
                clean_name = sanitize_name(raw_name) if raw_name else f"User_{s_id3}"
                return s_id3, clean_name
        except Exception as e:
            logging.error(f"Error parsing loginusers.vdf: {e}")

    # 2. Fallback to userdata directory modification time
    if os.path.exists(userdata_dir):
        dirs = [d for d in glob.glob(os.path.join(userdata_dir, "[0-9]*")) if os.path.isdir(d)]
        if dirs:
            latest_dir = max(dirs, key=os.path.getmtime)
            steamid3 = os.path.basename(latest_dir)
            account_name = None
            if os.path.exists(loginusers_vdf):
                try:
                    with open(loginusers_vdf, "r", encoding="utf-8", errors="ignore") as f:
                        content = f.read()
                    steamid64 = str(int(steamid3) + 76561197960265728)
                    match = re.search(r'"' + steamid64 + r'"\s*\{([^}]+)\}', content, re.DOTALL)
                    if match:
                        raw_name = extract_account_name(match.group(1))
                        account_name = sanitize_name(raw_name) if raw_name else f"User_{steamid3}"
                except Exception:
                    pass
            if not account_name:
                account_name = f"User_{steamid3}"
            return steamid3, account_name

    return None, None
