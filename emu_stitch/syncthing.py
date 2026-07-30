"""
Syncthing module for emu-stitch: Manages systemd user service status,
reads Syncthing configuration/device IDs, generates .stignore files,
registers profile folders, handles automated pairing, and queries live folder sync status.
"""

import os
import re
import json
import urllib.request
import subprocess
import logging

def ensure_syncthing_service():
    """Ensure syncthing.service user systemd unit is enabled and running."""
    try:
        res = subprocess.run(["command -v syncthing"], capture_output=True, text=True, shell=True)
        if res.returncode != 0:
            return False, "Syncthing executable not found in PATH. Install syncthing via package manager."

        subprocess.run(["systemctl", "--user", "enable", "--now", "syncthing.service"], capture_output=True, text=True)
        return True, "Syncthing systemd user service enabled and running."
    except Exception as e:
        return False, f"Error managing syncthing service: {e}"

def get_syncthing_credentials():
    """Extract Syncthing API Key and Device ID from config.xml (checking both XDG State and Config dirs)."""
    candidate_paths = [
        os.path.expanduser("~/.local/state/syncthing/config.xml"),
        os.path.expanduser("~/.config/syncthing/config.xml")
    ]
    api_key = None
    device_id = None

    for config_xml in candidate_paths:
        if os.path.exists(config_xml):
            try:
                with open(config_xml, "r", encoding="utf-8", errors="ignore") as f:
                    content = f.read()
                api_match = re.search(r'<apikey>([^<]+)</apikey>', content)
                if api_match:
                    api_key = api_match.group(1).strip()
                dev_match = re.search(r'<device id="([^"]+)"', content)
                if dev_match:
                    device_id = dev_match.group(1).strip()
                if api_key:
                    break
            except Exception as e:
                logging.error(f"Error reading syncthing config.xml ({config_xml}): {e}")

    return api_key, device_id

def auto_add_syncthing_folder(profile_name, profile_dir):
    """
    Automatically add profile_dir as a shared folder in Syncthing via REST API
    if it is not already configured.
    """
    api_key, _ = get_syncthing_credentials()
    if not api_key:
        return False, "Syncthing API Key not found in config.xml"

    url = "http://127.0.0.1:8384/rest/config"
    req = urllib.request.Request(url, headers={"X-API-Key": api_key})

    try:
        with urllib.request.urlopen(req) as resp:
            config = json.loads(resp.read().decode("utf-8"))

        folder_label = f"Emulator Saves - {profile_name}"
        folder_id = f"emustitch-{profile_name.lower()}"

        existing_ids = [f.get("id") for f in config.get("folders", [])]
        existing_paths = [os.path.abspath(f.get("path", "")) for f in config.get("folders", [])]
        abs_profile_dir = os.path.abspath(profile_dir)

        if folder_id in existing_ids or abs_profile_dir in existing_paths:
            return True, f"Syncthing folder already registered for '{profile_name}'"

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
        config["folders"].append(new_folder)

        post_data = json.dumps(config).encode("utf-8")
        post_req = urllib.request.Request(
            url,
            data=post_data,
            headers={"X-API-Key": api_key, "Content-Type": "application/json"},
            method="PUT"
        )
        with urllib.request.urlopen(post_req) as post_resp:
            if post_resp.status in (200, 204):
                return True, f"Successfully auto-registered Syncthing folder for '{profile_name}'"

    except Exception as e:
        return False, f"Syncthing REST API config update note: {e}"

    return True, "Syncthing folder configuration verified."

def get_profile_sync_status(profile_dir):
    """
    Query Syncthing REST API for live folder sync status matching profile_dir.
    Returns: (status_str, detailed_msg)
    """
    api_key, _ = get_syncthing_credentials()
    if not api_key:
        return "UNKNOWN", "Syncthing API Key not found"

    url_cfg = "http://127.0.0.1:8384/rest/config"
    headers = {"X-API-Key": api_key}
    abs_profile_dir = os.path.abspath(profile_dir)

    try:
        req_cfg = urllib.request.Request(url_cfg, headers=headers)
        with urllib.request.urlopen(req_cfg) as resp:
            cfg = json.loads(resp.read().decode("utf-8"))

        matching_folder_id = None
        for f in cfg.get("folders", []):
            if os.path.abspath(f.get("path", "")) == abs_profile_dir:
                matching_folder_id = f.get("id")
                break

        if not matching_folder_id:
            return "NOT REGISTERED", "Folder not registered in Syncthing yet."

        url_st = f"http://127.0.0.1:8384/rest/db/status?folder={matching_folder_id}"
        req_st = urllib.request.Request(url_st, headers=headers)
        with urllib.request.urlopen(req_st) as resp_st:
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

    except Exception as e:
        return "UNKNOWN", f"Error querying folder status: {e}"

def auto_pair_device(remote_device_id):
    """
    Automatically add remote_device_id to Syncthing config and share all
    emu-stitch folders with it via REST API.
    """
    api_key, _ = get_syncthing_credentials()
    if not api_key:
        return False, "Syncthing API Key not found in config.xml"

    url = "http://127.0.0.1:8384/rest/config"
    req = urllib.request.Request(url, headers={"X-API-Key": api_key})

    try:
        with urllib.request.urlopen(req) as resp:
            config = json.loads(resp.read().decode("utf-8"))

        devices = config.get("devices", [])
        existing_dev_ids = [d.get("deviceID") for d in devices]
        if remote_device_id not in existing_dev_ids:
            devices.append({
                "deviceID": remote_device_id,
                "name": f"Device-{remote_device_id[:7]}",
                "addresses": ["dynamic"]
            })
            config["devices"] = devices

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
        with urllib.request.urlopen(post_req) as post_resp:
            if post_resp.status in (200, 204):
                return True, f"Successfully paired remote device '{remote_device_id[:7]}...' and shared all save folders!"

    except Exception as e:
        return False, f"Device pairing error: {e}"

    return True, "Device paired successfully."

def get_paired_devices_status():
    """
    Returns list of paired devices with their live connection status.
    Returns: list of dicts [{ 'id': ..., 'name': ..., 'connected': True/False, 'address': ... }]
    """
    api_key, self_dev_id = get_syncthing_credentials()
    if not api_key:
        return []

    url_cfg = "http://127.0.0.1:8384/rest/config"
    url_conns = "http://127.0.0.1:8384/rest/system/connections"
    headers = {"X-API-Key": api_key}

    try:
        req_cfg = urllib.request.Request(url_cfg, headers=headers)
        with urllib.request.urlopen(req_cfg) as resp:
            cfg = json.loads(resp.read().decode("utf-8"))

        conns = {}
        try:
            req_conns = urllib.request.Request(url_conns, headers=headers)
            with urllib.request.urlopen(req_conns) as resp_c:
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

def generate_stignore(profile_dir):
    """Generate .stignore file to ignore lock files during active emulation."""
    stignore_path = os.path.join(profile_dir, ".stignore")
    if not os.path.exists(stignore_path):
        try:
            with open(stignore_path, "w") as f:
                f.write("*.lock\n")
            return True, f"Generated .stignore in {profile_dir}"
        except Exception as e:
            return False, f"Failed to write .stignore: {e}"
    return True, f".stignore already present in {profile_dir}"
