"""Tests for emu_stitch.syncthing: credentials, stignore, folder registration, pairing, status."""

import json
import os
import subprocess
from io import BytesIO
from unittest.mock import MagicMock, patch

import pytest

import emu_stitch.syncthing as st_mod
from emu_stitch.syncthing import (
    auto_add_syncthing_folder,
    auto_pair_device,
    ensure_syncthing_service,
    generate_stignore,
    get_paired_devices_status,
    get_profile_sync_status,
    get_syncthing_credentials,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

CONFIG_XML_TEMPLATE = """\
<configuration>
  <folder id="emustitch-alice" path="/home/user/Emulation/saves_by_user/alice" />
  <device id="REMOTE1-AAAAAAA" name="Remote1">
    <address>dynamic</address>
  </device>
  <gui>
    <apikey>test-api-key-123</apikey>
  </gui>
  <myID>LOCAL-DEVICE-XXXXXXXXXX</myID>
</configuration>
"""


def _write_config_xml(tmp_path: "pytest.TempPathFactory", content: str = CONFIG_XML_TEMPLATE) -> str:
    config_dir = tmp_path / ".local" / "state" / "syncthing"
    config_dir.mkdir(parents=True)
    config_file = config_dir / "config.xml"
    config_file.write_text(content)
    return str(config_file)


def _fake_expanduser(tmp_path):
    def _inner(path):
        return path.replace("~", str(tmp_path))
    return _inner


def _mock_urlopen(response_body: bytes, status: int = 200):
    """Return a context-manager mock that yields a fake HTTP response."""
    mock_resp = MagicMock()
    mock_resp.read.return_value = response_body
    mock_resp.status = status
    mock_resp.__enter__ = lambda s: s
    mock_resp.__exit__ = MagicMock(return_value=False)
    return mock_resp


# ---------------------------------------------------------------------------
# get_syncthing_credentials
# ---------------------------------------------------------------------------

class TestGetSyncthinqCredentials:
    def test_extracts_api_key_and_my_id(self, tmp_path, monkeypatch):
        _write_config_xml(tmp_path)
        monkeypatch.setattr(os.path, "expanduser", _fake_expanduser(tmp_path))

        api_key, device_id = get_syncthing_credentials()

        assert api_key == "test-api-key-123"
        assert device_id == "LOCAL-DEVICE-XXXXXXXXXX"

    def test_returns_none_when_no_config_file(self, tmp_path, monkeypatch):
        monkeypatch.setattr(os.path, "expanduser", _fake_expanduser(tmp_path))

        api_key, device_id = get_syncthing_credentials()

        assert api_key is None
        assert device_id is None

    def test_does_not_confuse_remote_device_id_with_myid(self, tmp_path, monkeypatch):
        # Config has a remote <device id=...> appearing BEFORE <myID>
        content = """\
<configuration>
  <device id="REMOTE-WRONG-ID" name="other" />
  <gui><apikey>key123</apikey></gui>
  <myID>CORRECT-LOCAL-ID</myID>
</configuration>
"""
        _write_config_xml(tmp_path, content)
        monkeypatch.setattr(os.path, "expanduser", _fake_expanduser(tmp_path))

        _, device_id = get_syncthing_credentials()

        assert device_id == "CORRECT-LOCAL-ID"


# ---------------------------------------------------------------------------
# generate_stignore
# ---------------------------------------------------------------------------

class TestGenerateStignore:
    def test_creates_stignore_with_lock_pattern(self, tmp_path):
        profile_dir = str(tmp_path / "alice")
        os.makedirs(profile_dir)

        ok, msg = generate_stignore(profile_dir)

        assert ok is True
        stignore = os.path.join(profile_dir, ".stignore")
        assert os.path.exists(stignore)
        assert "*.lock" in open(stignore).read()

    def test_noop_when_stignore_already_exists(self, tmp_path):
        profile_dir = str(tmp_path / "alice")
        os.makedirs(profile_dir)
        stignore_path = os.path.join(profile_dir, ".stignore")
        with open(stignore_path, "w") as f:
            f.write("custom content\n")

        ok, _ = generate_stignore(profile_dir)

        assert ok is True
        assert open(stignore_path).read() == "custom content\n"


# ---------------------------------------------------------------------------
# ensure_syncthing_service
# ---------------------------------------------------------------------------

class TestEnsureSyncthing:
    def test_returns_false_when_syncthing_not_installed(self, monkeypatch):
        monkeypatch.setattr(st_mod, "check_syncthing_installed", lambda: False)

        ok, msg = ensure_syncthing_service()

        assert ok is False
        assert "not found" in msg

    def test_returns_true_when_service_active(self, monkeypatch):
        monkeypatch.setattr(st_mod, "check_syncthing_installed", lambda: True)
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "active\n"
        monkeypatch.setattr(subprocess, "run", lambda *a, **k: mock_result)

        ok, msg = ensure_syncthing_service(enable=False)

        assert ok is True

    def test_returns_false_when_service_inactive(self, monkeypatch):
        monkeypatch.setattr(st_mod, "check_syncthing_installed", lambda: True)
        mock_result = MagicMock()
        mock_result.returncode = 3
        mock_result.stdout = "inactive\n"
        monkeypatch.setattr(subprocess, "run", lambda *a, **k: mock_result)

        ok, msg = ensure_syncthing_service(enable=False)

        assert ok is False

    def test_enable_returns_false_on_systemctl_failure(self, monkeypatch):
        monkeypatch.setattr(st_mod, "check_syncthing_installed", lambda: True)
        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stderr = "Failed to enable unit"
        monkeypatch.setattr(subprocess, "run", lambda *a, **k: mock_result)

        ok, msg = ensure_syncthing_service(enable=True)

        assert ok is False
        assert "systemctl failed" in msg
        assert "1" in msg

    def test_enable_returns_true_on_systemctl_success(self, monkeypatch):
        monkeypatch.setattr(st_mod, "check_syncthing_installed", lambda: True)
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stderr = ""
        monkeypatch.setattr(subprocess, "run", lambda *a, **k: mock_result)

        ok, msg = ensure_syncthing_service(enable=True)

        assert ok is True


# ---------------------------------------------------------------------------
# auto_add_syncthing_folder
# ---------------------------------------------------------------------------

class TestAutoAddSyncthinqFolder:
    def _base_config(self, profile_path="/existing/path"):
        return json.dumps({"folders": [{"id": "other", "path": profile_path}], "devices": []}).encode()

    def test_returns_false_when_no_api_key(self, monkeypatch):
        monkeypatch.setattr(st_mod, "get_syncthing_credentials", lambda: (None, None))

        ok, msg = auto_add_syncthing_folder("alice", "/some/path")

        assert ok is False
        assert "API Key" in msg

    def test_already_registered_same_path(self, tmp_path, monkeypatch):
        profile_path = str(tmp_path / "saves_by_user" / "alice")
        os.makedirs(profile_path)
        config_body = json.dumps({"folders": [{"id": "emustitch-alice", "path": profile_path}], "devices": []}).encode()
        monkeypatch.setattr(st_mod, "get_syncthing_credentials", lambda: ("key", "myid"))

        mock_resp = _mock_urlopen(config_body)
        with patch("urllib.request.urlopen", return_value=mock_resp):
            ok, msg = auto_add_syncthing_folder("alice", profile_path)

        assert ok is True
        assert "already registered" in msg

    def test_updates_existing_folder_path_when_migrated(self, tmp_path, monkeypatch):
        old_path = str(tmp_path / "home" / "Emulation" / "saves_by_user" / "alice")
        new_path = str(tmp_path / "mnt" / "Storage" / "Emulation" / "saves_by_user" / "alice")
        os.makedirs(new_path, exist_ok=True)

        config_body = json.dumps({"folders": [{"id": "emustitch-alice", "path": old_path}], "devices": []}).encode()
        monkeypatch.setattr(st_mod, "get_syncthing_credentials", lambda: ("key", "myid"))

        captured_requests = []

        def fake_urlopen(req, timeout=None):
            captured_requests.append(req)
            if len(captured_requests) == 1:
                return _mock_urlopen(config_body)
            return _mock_urlopen(b"{}", status=200)

        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            ok, msg = auto_add_syncthing_folder("alice", new_path)

        assert ok is True
        assert "updated" in msg.lower()
        # Verify the PUT request payload contained the updated path
        put_req = captured_requests[1]
        sent_config = json.loads(put_req.data.decode("utf-8"))
        assert sent_config["folders"][0]["path"] == os.path.abspath(new_path)

    def test_registers_new_folder_on_success(self, tmp_path, monkeypatch):
        profile_path = str(tmp_path / "saves_by_user" / "bob")
        config_body = json.dumps({"folders": [], "devices": []}).encode()
        monkeypatch.setattr(st_mod, "get_syncthing_credentials", lambda: ("key", "myid"))

        call_count = {"n": 0}

        def fake_urlopen(req, timeout=None):
            call_count["n"] += 1
            if call_count["n"] == 1:
                return _mock_urlopen(config_body)
            return _mock_urlopen(b"{}", status=200)

        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            ok, msg = auto_add_syncthing_folder("bob", profile_path)

        assert ok is True
        assert "Successfully" in msg

    def test_returns_false_when_api_unreachable(self, monkeypatch):
        import urllib.error
        monkeypatch.setattr(st_mod, "get_syncthing_credentials", lambda: ("key", "myid"))

        with patch("urllib.request.urlopen", side_effect=urllib.error.URLError("connection refused")):
            ok, msg = auto_add_syncthing_folder("alice", "/some/path")

        assert ok is False
        assert "timeout or connection error" in msg


# ---------------------------------------------------------------------------
# auto_pair_device
# ---------------------------------------------------------------------------

class TestAutoPairDevice:
    def test_returns_false_when_no_api_key(self, monkeypatch):
        monkeypatch.setattr(st_mod, "get_syncthing_credentials", lambda: (None, None))

        ok, msg = auto_pair_device("ABCDEFG-1234567-XXXXXXX")

        assert ok is False

    def test_pairs_new_device_successfully(self, monkeypatch):
        config_body = json.dumps({"folders": [], "devices": []}).encode()
        monkeypatch.setattr(st_mod, "get_syncthing_credentials", lambda: ("key", "myid"))

        captured_requests = []

        def fake_urlopen(req, timeout=None):
            captured_requests.append(req)
            if len(captured_requests) == 1:
                return _mock_urlopen(config_body)
            return _mock_urlopen(b"{}", status=200)

        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            ok, msg = auto_pair_device("NEWDEV1-AAAAAA-BBBBBBB")

        assert ok is True
        assert "paired" in msg.lower()
        put_req = captured_requests[1]
        sent_config = json.loads(put_req.data.decode("utf-8"))
        dev = sent_config["devices"][0]
        assert dev["deviceID"] == "NEWDEV1-AAAAAA-BBBBBBB"
        assert dev["autoAcceptFolders"] is True

    def test_returns_false_on_api_error(self, monkeypatch):
        import urllib.error
        monkeypatch.setattr(st_mod, "get_syncthing_credentials", lambda: ("key", "myid"))

        with patch("urllib.request.urlopen", side_effect=urllib.error.URLError("refused")):
            ok, msg = auto_pair_device("SOMEID")

        assert ok is False


# ---------------------------------------------------------------------------
# get_profile_sync_status
# ---------------------------------------------------------------------------

class TestGetProfileSyncStatus:
    def test_returns_unknown_when_no_api_key(self, monkeypatch):
        monkeypatch.setattr(st_mod, "get_syncthing_credentials", lambda: (None, None))

        status, msg = get_profile_sync_status("/some/path")

        assert status == "UNKNOWN"

    def test_returns_not_registered_when_folder_absent(self, tmp_path, monkeypatch):
        profile_path = str(tmp_path / "alice")
        config_body = json.dumps({"folders": [{"id": "other", "path": "/different/path"}]}).encode()
        monkeypatch.setattr(st_mod, "get_syncthing_credentials", lambda: ("key", "myid"))

        with patch("urllib.request.urlopen", return_value=_mock_urlopen(config_body)):
            status, msg = get_profile_sync_status(profile_path)

        assert status == "NOT REGISTERED"

    def test_returns_in_sync_when_synced(self, tmp_path, monkeypatch):
        profile_path = str(tmp_path / "alice")
        config_body = json.dumps({"folders": [{"id": "emustitch-alice", "path": profile_path}]}).encode()
        db_status_body = json.dumps({
            "state": "idle", "needBytes": 0, "globalBytes": 1048576, "inSyncFiles": 42
        }).encode()
        monkeypatch.setattr(st_mod, "get_syncthing_credentials", lambda: ("key", "myid"))

        call_count = {"n": 0}
        def fake_urlopen(req, timeout=None):
            call_count["n"] += 1
            if call_count["n"] == 1:
                return _mock_urlopen(config_body)
            return _mock_urlopen(db_status_body)

        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            status, msg = get_profile_sync_status(profile_path)

        assert status == "100% IN SYNC"
        assert "42 files" in msg

    def test_returns_syncing_when_bytes_needed(self, tmp_path, monkeypatch):
        profile_path = str(tmp_path / "alice")
        config_body = json.dumps({"folders": [{"id": "emustitch-alice", "path": profile_path}]}).encode()
        db_status_body = json.dumps({
            "state": "syncing", "needBytes": 512000, "globalBytes": 1024000, "inSyncFiles": 10
        }).encode()
        monkeypatch.setattr(st_mod, "get_syncthing_credentials", lambda: ("key", "myid"))

        call_count = {"n": 0}
        def fake_urlopen(req, timeout=None):
            call_count["n"] += 1
            if call_count["n"] == 1:
                return _mock_urlopen(config_body)
            return _mock_urlopen(db_status_body)

        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            status, msg = get_profile_sync_status(profile_path)

        assert status == "SYNCING"
        assert "50%" in msg


# ---------------------------------------------------------------------------
# get_paired_devices_status
# ---------------------------------------------------------------------------

class TestGetPairedDevicesStatus:
    def test_returns_empty_list_when_no_api_key(self, monkeypatch):
        monkeypatch.setattr(st_mod, "get_syncthing_credentials", lambda: (None, None))

        result = get_paired_devices_status()

        assert result == []

    def test_excludes_self_device(self, monkeypatch):
        self_id = "LOCAL-DEVICE-XXXXXXXXXX"
        config_body = json.dumps({
            "devices": [
                {"deviceID": self_id, "name": "This machine"},
                {"deviceID": "REMOTE1", "name": "Remote1"},
            ]
        }).encode()
        conns_body = json.dumps({"connections": {"REMOTE1": {"connected": True, "address": "10.0.0.1:22000"}}}).encode()
        monkeypatch.setattr(st_mod, "get_syncthing_credentials", lambda: ("key", self_id))

        call_count = {"n": 0}
        def fake_urlopen(req, timeout=None):
            call_count["n"] += 1
            if call_count["n"] == 1:
                return _mock_urlopen(config_body)
            return _mock_urlopen(conns_body)

        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            devices = get_paired_devices_status()

        assert len(devices) == 1
        assert devices[0]["id"] == "REMOTE1"
        assert devices[0]["connected"] is True

    def test_returns_offline_address_when_device_not_connected(self, monkeypatch):
        config_body = json.dumps({
            "devices": [{"deviceID": "REMOTE1", "name": "Remote1"}]
        }).encode()
        conns_body = json.dumps({"connections": {}}).encode()
        monkeypatch.setattr(st_mod, "get_syncthing_credentials", lambda: ("key", None))

        call_count = {"n": 0}
        def fake_urlopen(req, timeout=None):
            call_count["n"] += 1
            if call_count["n"] == 1:
                return _mock_urlopen(config_body)
            return _mock_urlopen(conns_body)

        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            devices = get_paired_devices_status()

        assert devices[0]["connected"] is False
        assert devices[0]["address"] == "offline"
