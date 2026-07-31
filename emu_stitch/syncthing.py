"""
Syncthing module for emu-stitch: Manages systemd user service status,
reads Syncthing configuration/device IDs, generates .stignore files,
registers profile folders, handles automated pairing, and queries live device status.
"""

from __future__ import annotations

import os
import re
import json
import shutil
import socket
import urllib.request
import urllib.error
import subprocess
import logging
from typing import Dict, List, Optional, Tuple

Result = Tuple[bool, str]

TIMEOUT_SECONDS = 5


def _syncthing_base_url() -> str:
    """Base URL for the local Syncthing REST API, overridable via SYNCTHING_URL."""
    return os.environ.get("SYNCTHING_URL", "http://127.0.0.1:8384").rstrip("/")


def check_syncthing_installed() -> bool:
    """Check if syncthing is installed in PATH."""
    return shutil.which("syncthing") is not None


def ensure_syncthing_service(enable: bool = False) -> Result:
    """
    Check systemd syncthing.service status.
    If enable=True, explicitly enables and starts the service.
    """
    if not check_syncthing_installed():
        return False, "Syncthing executable not found in PATH."

    if enable:
        try:
            res = subprocess.run(
                ["systemctl", "--user", "enable", "--now", "syncthing.service"],
                capture_output=True, text=True, timeout=10,
            )
            if res.returncode != 0:
                return False, f"systemctl failed (exit {res.returncode}): {res.stderr.strip()}"
            return True, "Syncthing systemd user service enabled and running."
        except Exception as e:
            return False, f"Error enabling syncthing service: {e}"

    # Check active status
    try:
        res = subprocess.run(
            ["systemctl", "--user", "is-active", "syncthing.service"],
            capture_output=True, text=True, timeout=10,
        )
    except Exception as e:
        return False, f"Error checking syncthing service status: {e}"
    if res.returncode == 0 and "active" in res.stdout:
        return True, "Syncthing service is running."
    return False, "Syncthing service is currently inactive."


def get_syncthing_credentials() -> Tuple[Optional[str], Optional[str]]:
    """Extract Syncthing API Key and Device ID from config.xml (checking both XDG State and Config dirs)."""
    candidate_paths = [
        os.path.expanduser("~/.local/state/syncthing/config.xml"),
        os.path.expanduser("~/.config/syncthing/config.xml")
    ]
    api_key: Optional[str] = None
    device_id: Optional[str] = None

    for config_xml in candidate_paths:
        if os.path.exists(config_xml):
            try:
                with open(config_xml, "r", encoding="utf-8", errors="ignore") as f:
                    content = f.read()
                api_match = re.search(r'<apikey>([^<]+)</apikey>', content)
                if api_match:
                    api_key = api_match.group(1).strip()
                dev_match = re.search(r'<myID>([^<]+)</myID>', content)
                if dev_match:
                    device_id = dev_match.group(1).strip()
                if api_key:
                    break
            except Exception as e:
                logging.error(f"Error reading syncthing config.xml ({config_xml}): {e}")

    return api_key, device_id


def auto_add_syncthing_folder(profile_name: str, profile_dir: str) -> Result:
    """
    Automatically add profile_dir as a shared folder in Syncthing via REST API
    if it is not already configured.
    """
    api_key, _ = get_syncthing_credentials()
    if not api_key:
        return False, "Syncthing API Key not found in config.xml"

    url = f"{_syncthing_base_url()}/rest/config"
    req = urllib.request.Request(url, headers={"X-API-Key": api_key})

    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_SECONDS) as resp:
            config = json.loads(resp.read().decode("utf-8"))

        folder_label = f"Emulator Saves - {profile_name}"
        folder_id = f"emustitch-{profile_name.lower()}"
        abs_profile_dir = os.path.abspath(profile_dir)

        existing_folder = None
        for f in config.get("folders", []):
            if f.get("id") == folder_id:
                existing_folder = f
                break

        if existing_folder:
            current_path = os.path.abspath(existing_folder.get("path", ""))
            if current_path == abs_profile_dir:
                return True, f"Syncthing folder already registered for '{profile_name}'"
            # Update path if it points elsewhere (e.g. cross-machine path migration)
            existing_folder["path"] = abs_profile_dir
        else:
            new_folder = {
                "id": folder_id,
                "label": folder_label,
                "filesystemType": "basic",
                "path": abs_profile_dir,
                "type": "sendreceive",
                "rescanIntervalS": 3600,
                "fsWatcherEnabled": True,
                "fsWatcherDelayS": 10
            }
            config.setdefault("folders", []).append(new_folder)

        post_data = json.dumps(config).encode("utf-8")
        post_req = urllib.request.Request(
            url,
            data=post_data,
            headers={"X-API-Key": api_key, "Content-Type": "application/json"},
            method="PUT"
        )
        with urllib.request.urlopen(post_req, timeout=TIMEOUT_SECONDS) as post_resp:
            if post_resp.status // 100 == 2:
                msg = (
                    f"Successfully updated Syncthing folder path for '{profile_name}' to {abs_profile_dir}"
                    if existing_folder else
                    f"Successfully auto-registered Syncthing folder for '{profile_name}'"
                )
                return True, msg
            return False, f"Syncthing API returned unexpected status {post_resp.status}"

    except (urllib.error.URLError, socket.timeout) as e:
        return False, f"Could not reach Syncthing REST API (timeout or connection error): {e}"
    except Exception as e:
        return False, f"Syncthing REST API config update note: {e}"

    return True, "Syncthing folder configuration verified."


def get_profile_sync_status(profile_dir: str) -> Tuple[str, str]:
    """
    Query Syncthing REST API for live folder sync status matching profile_dir.
    Returns: (status_str, detailed_msg)
    """
    api_key, _ = get_syncthing_credentials()
    if not api_key:
        return "UNKNOWN", "Syncthing API Key not found"

    url_cfg = f"{_syncthing_base_url()}/rest/config"
    headers = {"X-API-Key": api_key}
    abs_profile_dir = os.path.abspath(profile_dir)

    try:
        req_cfg = urllib.request.Request(url_cfg, headers=headers)
        with urllib.request.urlopen(req_cfg, timeout=TIMEOUT_SECONDS) as resp:
            cfg = json.loads(resp.read().decode("utf-8"))

        matching_folder_id = None
        for f in cfg.get("folders", []):
            if os.path.abspath(f.get("path", "")) == abs_profile_dir:
                matching_folder_id = f.get("id")
                break

        if not matching_folder_id:
            return "NOT REGISTERED", "Folder not registered in Syncthing yet."

        url_st = f"{_syncthing_base_url()}/rest/db/status?folder={matching_folder_id}"
        req_st = urllib.request.Request(url_st, headers=headers)
        with urllib.request.urlopen(req_st, timeout=TIMEOUT_SECONDS) as resp_st:
            st = json.loads(resp_st.read().decode("utf-8"))

        state = st.get("state", "unknown")
        need_bytes = st.get("needBytes", 0)
        global_bytes = st.get("globalBytes", 0)
        in_sync_files = st.get("inSyncFiles", 0)

        mb_str = f"{global_bytes / (1024*1024):.1f} MB"

        if need_bytes == 0 and state in ("idle", "sync-waiting"):
            return "100% IN SYNC", f"All files synced ({mb_str} across {in_sync_files} files)"
        elif need_bytes > 0:
            pct = 100 - int((need_bytes / global_bytes) * 100) if global_bytes > 0 else 0
            return "SYNCING", f"Syncing in progress ({pct}% synced, {need_bytes / 1024:.1f} KB remaining)"
        else:
            return state.upper(), f"State: {state} ({mb_str})"

    except (urllib.error.URLError, socket.timeout) as e:
        return "UNKNOWN", f"Could not reach Syncthing REST API (timeout or connection error): {e}"
    except Exception as e:
        return "UNKNOWN", f"Error querying folder status: {e}"


def auto_pair_device(remote_device_id: str) -> Result:
    """
    Automatically add remote_device_id to Syncthing config and share all
    emu-stitch folders with it via REST API.
    """
    api_key, _ = get_syncthing_credentials()
    if not api_key:
        return False, "Syncthing API Key not found in config.xml"

    url = f"{_syncthing_base_url()}/rest/config"
    req = urllib.request.Request(url, headers={"X-API-Key": api_key})

    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_SECONDS) as resp:
            config = json.loads(resp.read().decode("utf-8"))

        devices = config.get("devices", [])
        existing_dev = next((d for d in devices if d.get("deviceID") == remote_device_id), None)
        if not existing_dev:
            devices.append({
                "deviceID": remote_device_id,
                "name": f"Device-{remote_device_id[:7]}",
                "addresses": ["dynamic"],
                "autoAcceptFolders": True
            })
            config["devices"] = devices
        else:
            existing_dev["autoAcceptFolders"] = True

        for folder in config.get("folders", []):
            if folder.get("id", "").startswith("emustitch-"):
                f_devices = folder.get("devices", [])
                if not any(d.get("deviceID") == remote_device_id for d in f_devices):
                    f_devices.append({"deviceID": remote_device_id})
                    folder["devices"] = f_devices

        post_data = json.dumps(config).encode("utf-8")
        post_req = urllib.request.Request(
            url,
            data=post_data,
            headers={"X-API-Key": api_key, "Content-Type": "application/json"},
            method="PUT"
        )
        with urllib.request.urlopen(post_req, timeout=TIMEOUT_SECONDS) as post_resp:
            if post_resp.status // 100 == 2:
                return True, f"Successfully paired remote device '{remote_device_id[:7]}...' and shared all save folders!"
            return False, f"Syncthing API returned unexpected status {post_resp.status}"

    except (urllib.error.URLError, socket.timeout) as e:
        return False, f"Could not reach Syncthing REST API (timeout or connection error): {e}"
    except Exception as e:
        return False, f"Device pairing error: {e}"

    return True, "Device paired successfully."


def get_paired_devices_status() -> List[Dict[str, object]]:
    """
    Returns list of paired devices with their live connection status.
    Returns: list of dicts [{ 'id': ..., 'name': ..., 'connected': True/False, 'address': ... }]
    """
    api_key, self_dev_id = get_syncthing_credentials()
    if not api_key:
        return []

    url_cfg = f"{_syncthing_base_url()}/rest/config"
    url_conns = f"{_syncthing_base_url()}/rest/system/connections"
    headers = {"X-API-Key": api_key}

    try:
        req_cfg = urllib.request.Request(url_cfg, headers=headers)
        with urllib.request.urlopen(req_cfg, timeout=TIMEOUT_SECONDS) as resp:
            cfg = json.loads(resp.read().decode("utf-8"))

        conns: Dict[str, object] = {}
        try:
            req_conns = urllib.request.Request(url_conns, headers=headers)
            with urllib.request.urlopen(req_conns, timeout=TIMEOUT_SECONDS) as resp_c:
                conns_data = json.loads(resp_c.read().decode("utf-8"))
                conns = conns_data.get("connections", {})
        except Exception:
            pass

        device_list = []
        for dev in cfg.get("devices", []):
            dev_id = dev.get("deviceID")
            if dev_id == self_dev_id:
                continue
            name = dev.get("name") or f"Device-{dev_id[:7]}"
            conn_info = conns.get(dev_id, {})
            is_connected = conn_info.get("connected", False)
            addr = conn_info.get("address", "offline")
            device_list.append({
                "id": dev_id,
                "name": name,
                "connected": is_connected,
                "address": addr
            })
        return device_list
    except Exception as e:
        logging.error(f"Error fetching device status: {e}")
        return []


def generate_stignore(profile_dir: str) -> Result:
    """Generate or update .stignore file to ignore lock files with (?d) delete-prefix."""
    stignore_path = os.path.join(profile_dir, ".stignore")
    try:
        if os.path.exists(stignore_path):
            with open(stignore_path, "r", encoding="utf-8", errors="ignore") as f:
                content = f.read()
            if "(?d)*.lock" not in content:
                content = content.replace("*.lock", "(?d)*.lock")
                if "(?d)*.lock" not in content:
                    content += "(?d)*.lock\n"
                with open(stignore_path, "w", encoding="utf-8") as f:
                    f.write(content)
                return True, f"Updated .stignore with (?d) prefix in {profile_dir}"
            return True, f".stignore already present in {profile_dir}"
        else:
            with open(stignore_path, "w", encoding="utf-8") as f:
                f.write("(?d)*.lock\n")
            return True, f"Generated .stignore in {profile_dir}"
    except Exception as e:
        return False, f"Failed to write .stignore: {e}"
