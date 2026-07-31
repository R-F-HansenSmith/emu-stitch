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
    remove_paired_device,
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
    def test_extracts_api_key_from_config_and_device_id_from_rest_api(self, tmp_path, monkeypatch):
        # Syncthing derives its own Device ID from its TLS certificate at
        # runtime rather than storing it in config.xml, so it must come
        # from the REST API's /rest/system/status endpoint instead.
        _write_config_xml(tmp_path)
        monkeypatch.setattr(os.path, "expanduser", _fake_expanduser(tmp_path))
        status_body = json.dumps({"myID": "LOCAL-DEVICE-XXXXXXXXXX"}).encode()

        with patch("urllib.request.urlopen", return_value=_mock_urlopen(status_body)):
            api_key, device_id = get_syncthing_credentials()

        assert api_key == "test-api-key-123"
        assert device_id == "LOCAL-DEVICE-XXXXXXXXXX"

    def test_returns_none_when_no_config_file(self, tmp_path, monkeypatch):
        monkeypatch.setattr(os.path, "expanduser", _fake_expanduser(tmp_path))

        def fail_urlopen(*a, **k):
            raise AssertionError("should not attempt a REST call without an API key")

        with patch("urllib.request.urlopen", side_effect=fail_urlopen):
            api_key, device_id = get_syncthing_credentials()

        assert api_key is None
        assert device_id is None

    def test_device_id_is_none_when_rest_api_unreachable(self, tmp_path, monkeypatch):
        """A found API key but an unreachable Syncthing REST API (e.g. the
        service isn't actually running yet) must not raise — just leave the
        device ID unresolved."""
        import urllib.error
        _write_config_xml(tmp_path)
        monkeypatch.setattr(os.path, "expanduser", _fake_expanduser(tmp_path))

        with patch("urllib.request.urlopen", side_effect=urllib.error.URLError("refused")):
            api_key, device_id = get_syncthing_credentials()

        assert api_key == "test-api-key-123"
        assert device_id is None


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
        assert "(?d)*.lock" in open(stignore).read()

    def test_upgrades_existing_stignore_without_d_flag(self, tmp_path):
        profile_dir = str(tmp_path / "alice")
        os.makedirs(profile_dir)
        stignore_path = os.path.join(profile_dir, ".stignore")
        with open(stignore_path, "w") as f:
            f.write("*.lock\n")

        ok, _ = generate_stignore(profile_dir)

        assert ok is True
        assert "(?d)*.lock" in open(stignore_path).read()


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

    def test_status_check_survives_a_hung_systemctl(self, monkeypatch):
        """A hung `systemctl is-active` call must not hang the CLI forever."""
        monkeypatch.setattr(st_mod, "check_syncthing_installed", lambda: True)
        captured = {}

        def fake_run(cmd, capture_output=False, text=False, timeout=None):
            captured["timeout"] = timeout
            raise subprocess.TimeoutExpired(cmd=cmd, timeout=timeout)

        monkeypatch.setattr(subprocess, "run", fake_run)

        ok, msg = ensure_syncthing_service(enable=False)

        assert ok is False
        assert captured["timeout"] is not None


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
# remove_paired_device
# ---------------------------------------------------------------------------

class TestRemovePairedDevice:
    def test_returns_false_when_no_api_key(self, monkeypatch):
        monkeypatch.setattr(st_mod, "get_syncthing_credentials", lambda: (None, None))

        ok, msg = remove_paired_device("EXISTING-DEV1234")

        assert ok is False

    def test_returns_false_when_device_not_paired(self, monkeypatch):
        config_body = json.dumps({"folders": [], "devices": []}).encode()
        monkeypatch.setattr(st_mod, "get_syncthing_credentials", lambda: ("key", "myid"))

        with patch("urllib.request.urlopen", side_effect=lambda req, timeout=None: _mock_urlopen(config_body)):
            ok, msg = remove_paired_device("NEVER-PAIRED-DEVICE")

        assert ok is False
        assert "not paired" in msg.lower()

    def test_unpairs_existing_device_and_removes_from_all_folders(self, monkeypatch):
        config_body = json.dumps({
            "devices": [
                {"deviceID": "REMOTE1-AAAAAAA", "name": "Remote1", "autoAcceptFolders": True},
                {"deviceID": "REMOTE2-BBBBBBB", "name": "Remote2"},
            ],
            "folders": [
                {
                    "id": "emustitch-alice",
                    "devices": [{"deviceID": "REMOTE1-AAAAAAA"}, {"deviceID": "REMOTE2-BBBBBBB"}],
                },
                {
                    "id": "emustitch-bob",
                    "devices": [{"deviceID": "REMOTE1-AAAAAAA"}],
                },
            ],
        }).encode()
        monkeypatch.setattr(st_mod, "get_syncthing_credentials", lambda: ("key", "myid"))

        captured_requests = []

        def fake_urlopen(req, timeout=None):
            captured_requests.append(req)
            if len(captured_requests) == 1:
                return _mock_urlopen(config_body)
            return _mock_urlopen(b"{}", status=200)

        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            ok, msg = remove_paired_device("REMOTE1-AAAAAAA")

        assert ok is True
        put_req = captured_requests[1]
        sent_config = json.loads(put_req.data.decode("utf-8"))

        device_ids = [d["deviceID"] for d in sent_config["devices"]]
        assert "REMOTE1-AAAAAAA" not in device_ids
        assert "REMOTE2-BBBBBBB" in device_ids

        for folder in sent_config["folders"]:
            folder_device_ids = [d["deviceID"] for d in folder["devices"]]
            assert "REMOTE1-AAAAAAA" not in folder_device_ids

    def test_returns_false_on_api_error(self, monkeypatch):
        import urllib.error
        monkeypatch.setattr(st_mod, "get_syncthing_credentials", lambda: ("key", "myid"))

        with patch("urllib.request.urlopen", side_effect=urllib.error.URLError("refused")):
            ok, msg = remove_paired_device("SOMEID")

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

    def test_returns_offline_address_when_connection_entry_has_empty_address(self, monkeypatch):
        """Syncthing includes a connection entry for every known device, even
        ones that have never connected, with an empty-string address rather
        than omitting the key entirely."""
        config_body = json.dumps({
            "devices": [{"deviceID": "REMOTE1", "name": "Remote1"}]
        }).encode()
        conns_body = json.dumps({
            "connections": {"REMOTE1": {"connected": False, "address": ""}}
        }).encode()
        monkeypatch.setattr(st_mod, "get_syncthing_credentials", lambda: ("key", None))

        call_count = {"n": 0}
        def fake_urlopen(req, timeout=None):
            call_count["n"] += 1
            if call_count["n"] == 1:
                return _mock_urlopen(config_body)
            return _mock_urlopen(conns_body)

        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            devices = get_paired_devices_status()

        assert devices[0]["address"] == "offline"
