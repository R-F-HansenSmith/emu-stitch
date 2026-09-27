"""
Syncthing module for emu-stitch: Manages systemd user service status,
reads Syncthing configuration/device IDs, generates .stignore files,
registers profile folders, handles automated pairing, and queries live device status.
"""

from __future__ import annotations

import os
import ssl
import json
import shutil
import socket
import urllib.parse
import urllib.request
import urllib.error
import subprocess
import logging
import xml.etree.ElementTree as ET
from typing import Dict, List, Optional, Tuple

Result = Tuple[bool, str]

TIMEOUT_SECONDS = 5
DEFAULT_BASE_URL = "http://127.0.0.1:8384"
FOLDER_ID_PREFIX = "emustitch-"

CONFIG_XML_CANDIDATES = [
    "~/.local/state/syncthing/config.xml",
    "~/.config/syncthing/config.xml",
]


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


def _read_gui_config() -> Tuple[Optional[str], Optional[str], bool]:
    """Read (api_key, gui_address, tls) from the <gui> element of
    Syncthing's config.xml, checking both the XDG State and Config dirs."""
    for candidate in CONFIG_XML_CANDIDATES:
        config_xml = os.path.expanduser(candidate)
        if not os.path.exists(config_xml):
            continue
        try:
            gui = ET.parse(config_xml).getroot().find("gui")
        except (ET.ParseError, OSError) as e:
            logging.error(f"Error reading syncthing config.xml ({config_xml}): {e}")
            continue
        if gui is None:
            continue
        api_key = (gui.findtext("apikey") or "").strip()
        if api_key:
            address = (gui.findtext("address") or "").strip() or None
            tls = gui.get("tls", "false").lower() == "true"
            return api_key, address, tls
    return None, None, False


def _base_url_from_gui(address: Optional[str], tls: bool) -> str:
    """Turn the GUI listen address into a URL we can connect to. SYNCTHING_URL
    always wins. Unix-socket GUIs aren't supported and fall back to the
    default."""
    override = os.environ.get("SYNCTHING_URL")
    if override:
        return override.rstrip("/")
    if not address or address.startswith(("unix:", "/")) or ":" not in address:
        return DEFAULT_BASE_URL
    host, _, port = address.rpartition(":")
    if host in ("", "0.0.0.0"):
        host = "127.0.0.1"
    elif host == "[::]":
        host = "[::1]"
    return f"{'https' if tls else 'http'}://{host}:{port}"


class SyncthingAPI:
    """Minimal client for Syncthing's REST API."""

    def __init__(self, api_key: str, base_url: str = DEFAULT_BASE_URL):
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        parsed = urllib.parse.urlsplit(self.base_url)
        self.context: Optional[ssl.SSLContext] = None
        if parsed.scheme == "https" and parsed.hostname in ("127.0.0.1", "::1", "localhost"):
            # Syncthing's GUI uses a self-signed certificate. Skipping
            # verification is only acceptable for a loopback connection.
            self.context = ssl.create_default_context()
            self.context.check_hostname = False
            self.context.verify_mode = ssl.CERT_NONE

    def request(self, method: str, path: str, body: Optional[object] = None) -> object:
        data = json.dumps(body).encode("utf-8") if body is not None else None
        headers = {"X-API-Key": self.api_key}
        if data is not None:
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(self.base_url + path, data=data, headers=headers, method=method)
        kwargs: Dict[str, object] = {"timeout": TIMEOUT_SECONDS}
        if self.context is not None:
            kwargs["context"] = self.context
        with urllib.request.urlopen(req, **kwargs) as resp:
            raw = resp.read()
        return json.loads(raw.decode("utf-8")) if raw else None


def _connect() -> Optional[SyncthingAPI]:
    api_key, address, tls = _read_gui_config()
    if not api_key:
        return None
    return SyncthingAPI(api_key, _base_url_from_gui(address, tls))


def _q(value: str) -> str:
    return urllib.parse.quote(value, safe="")


def _api_error(e: Exception, action: str) -> str:
    if isinstance(e, urllib.error.HTTPError):
        return f"Syncthing API returned HTTP {e.code} while {action}"
    if isinstance(e, (urllib.error.URLError, socket.timeout)):
        return f"Could not reach Syncthing REST API (timeout or connection error): {e}"
    return f"Syncthing API error while {action}: {e}"


def get_syncthing_credentials() -> Tuple[Optional[str], Optional[str]]:
    """Return (api_key, this machine's Device ID).

    Syncthing derives its Device ID from its TLS certificate at runtime —
    it is never written to config.xml — so it can only be read back via
    /rest/system/status ("myID" field)."""
    api = _connect()
    if api is None:
        return None, None
    try:
        status = api.request("GET", "/rest/system/status")
        return api.api_key, status.get("myID")
    except Exception as e:
        logging.error(f"Error fetching Syncthing device ID from REST API: {e}")
        return api.api_key, None


def auto_add_syncthing_folder(profile_name: str, profile_dir: str) -> Result:
    """
    Add profile_dir as a shared folder in Syncthing via REST API if it is
    not already configured. An existing emu-stitch folder with the same ID
    but a different path is never repointed automatically: that could
    merge two profiles' saves, or make Syncthing see every file as deleted.
    """
    api = _connect()
    if api is None:
        return False, "Syncthing API Key not found in config.xml"

    folder_id = f"{FOLDER_ID_PREFIX}{profile_name.lower()}"
    abs_profile_dir = os.path.abspath(profile_dir)

    try:
        folders = api.request("GET", "/rest/config/folders") or []
        existing = next((f for f in folders if f.get("id") == folder_id), None)
        if existing:
            current_path = os.path.abspath(existing.get("path", ""))
            if current_path == abs_profile_dir:
                return True, f"Syncthing folder already registered for '{profile_name}'"
            return False, (
                f"Syncthing folder '{folder_id}' already exists but points at {current_path}, "
                f"not {abs_profile_dir}. Not changing it automatically: it may belong to another "
                "profile, or your Emulation folder may have moved. If the new location is "
                "correct, update the folder path in the Syncthing web UI."
            )

        api.request("PUT", f"/rest/config/folders/{_q(folder_id)}", {
            "id": folder_id,
            "label": f"Emulator Saves - {profile_name}",
            "filesystemType": "basic",
            "path": abs_profile_dir,
            "type": "sendreceive",
            "rescanIntervalS": 3600,
            "fsWatcherEnabled": True,
            "fsWatcherDelayS": 10,
        })
        return True, f"Successfully auto-registered Syncthing folder for '{profile_name}'"
    except Exception as e:
        return False, _api_error(e, "registering the folder")


def get_profile_sync_status(profile_dir: str) -> Tuple[str, str]:
    """
    Query Syncthing REST API for live folder sync status matching profile_dir.
    Returns: (status_str, detailed_msg)
    """
    api = _connect()
    if api is None:
        return "UNKNOWN", "Syncthing API Key not found"

    abs_profile_dir = os.path.abspath(profile_dir)
    try:
        folders = api.request("GET", "/rest/config/folders") or []
        matching_folder_id = next(
            (f.get("id") for f in folders if os.path.abspath(f.get("path", "")) == abs_profile_dir),
            None,
        )
        if not matching_folder_id:
            return "NOT REGISTERED", "Folder not registered in Syncthing yet."

        st = api.request("GET", f"/rest/db/status?folder={_q(matching_folder_id)}")

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

    except Exception as e:
        return "UNKNOWN", _api_error(e, "querying folder status")


def auto_pair_device(remote_device_id: str, auto_accept: bool = False) -> Result:
    """
    Add remote_device_id to Syncthing and share every emu-stitch folder
    with it. `auto_accept` additionally lets that device create new shared
    folders on this machine without confirmation — off by default, since it
    extends far more trust than sharing save folders needs.
    """
    api = _connect()
    if api is None:
        return False, "Syncthing API Key not found in config.xml"

    dev_path = f"/rest/config/devices/{_q(remote_device_id)}"
    try:
        devices = api.request("GET", "/rest/config/devices") or []
        if not any(d.get("deviceID") == remote_device_id for d in devices):
            api.request("PUT", dev_path, {
                "deviceID": remote_device_id,
                "name": f"Device-{remote_device_id[:7]}",
                "addresses": ["dynamic"],
                "autoAcceptFolders": auto_accept,
            })
        elif auto_accept:
            api.request("PATCH", dev_path, {"autoAcceptFolders": True})

        shared = 0
        for folder in api.request("GET", "/rest/config/folders") or []:
            folder_id = folder.get("id", "")
            if not folder_id.startswith(FOLDER_ID_PREFIX):
                continue
            f_devices = folder.get("devices", [])
            if not any(d.get("deviceID") == remote_device_id for d in f_devices):
                api.request("PATCH", f"/rest/config/folders/{_q(folder_id)}",
                            {"devices": f_devices + [{"deviceID": remote_device_id}]})
            shared += 1

        return True, f"Successfully paired remote device '{remote_device_id[:7]}...' and shared {shared} save folder(s)."
    except Exception as e:
        return False, _api_error(e, "pairing the device")


def remove_paired_device(remote_device_id: str) -> Result:
    """
    Unshare remote_device_id from every emu-stitch folder, then remove it
    from Syncthing via REST API.
    """
    api = _connect()
    if api is None:
        return False, "Syncthing API Key not found in config.xml"

    try:
        devices = api.request("GET", "/rest/config/devices") or []
        if not any(d.get("deviceID") == remote_device_id for d in devices):
            return False, f"Device '{remote_device_id[:7]}...' is not paired."

        for folder in api.request("GET", "/rest/config/folders") or []:
            folder_id = folder.get("id", "")
            f_devices = folder.get("devices", [])
            if folder_id.startswith(FOLDER_ID_PREFIX) and any(d.get("deviceID") == remote_device_id for d in f_devices):
                api.request("PATCH", f"/rest/config/folders/{_q(folder_id)}",
                            {"devices": [d for d in f_devices if d.get("deviceID") != remote_device_id]})

        api.request("DELETE", f"/rest/config/devices/{_q(remote_device_id)}")
        return True, f"Successfully unpaired device '{remote_device_id[:7]}...' and removed it from all save folders."
    except Exception as e:
        return False, _api_error(e, "unpairing the device")


def get_paired_devices_status() -> List[Dict[str, object]]:
    """
    Returns list of paired devices with their live connection status.
    Returns: list of dicts [{ 'id': ..., 'name': ..., 'connected': True/False, 'address': ... }]
    """
    api = _connect()
    if api is None:
        return []

    try:
        devices = api.request("GET", "/rest/config/devices") or []

        self_dev_id = None
        try:
            self_dev_id = (api.request("GET", "/rest/system/status") or {}).get("myID")
        except Exception:
            pass

        conns: Dict[str, object] = {}
        try:
            conns = (api.request("GET", "/rest/system/connections") or {}).get("connections", {})
        except Exception:
            pass

        device_list = []
        for dev in devices:
            dev_id = dev.get("deviceID")
            if dev_id == self_dev_id:
                continue
            name = dev.get("name") or f"Device-{dev_id[:7]}"
            conn_info = conns.get(dev_id, {})
            is_connected = conn_info.get("connected", False)
            addr = conn_info.get("address") or "offline"
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


# Lines every profile's .stignore must contain.
STIGNORE_LINES = [
    "(?d)*.lock",
    # Each machine numbers new Ryujinx saves from its own range, so the
    # counter must never sync. No (?d): Ryujinx replaces this folder on every
    # save, and deleting the counter would make it count from 0 again.
    "/ryujinx/saveIndex/*/lastPublishedId",
    # Ryujinx's transient commit folders (renamed to 0/ once written).
    "(?d)/ryujinx/saveIndex/_",
    "(?d)/ryujinx/saves/*/_",
]


def generate_stignore(profile_dir: str) -> Result:
    """Generate or update the profile's .stignore so it contains every line
    in STIGNORE_LINES, keeping anything the user added."""
    stignore_path = os.path.join(profile_dir, ".stignore")
    try:
        content = ""
        if os.path.exists(stignore_path):
            with open(stignore_path, "r", encoding="utf-8", errors="ignore") as f:
                content = f.read()
        lines = content.splitlines()
        # Older versions wrote a bare *.lock; upgrade it in place.
        lines = ["(?d)*.lock" if line.strip() == "*.lock" else line for line in lines]
        missing = [line for line in STIGNORE_LINES if line not in lines]
        if not missing and lines == content.splitlines():
            return True, f".stignore already up to date in {profile_dir}"
        with open(stignore_path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines + missing) + "\n")
        return True, f"Updated .stignore in {profile_dir}"
    except Exception as e:
        return False, f"Failed to write .stignore: {e}"
