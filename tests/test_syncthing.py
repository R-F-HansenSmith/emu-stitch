"""Tests for emu_stitch.syncthing: credentials, stignore, folder registration, pairing, status."""

import json
import os
import subprocess
from io import BytesIO
from unittest.mock import MagicMock, patch

import pytest

import emu_stitch.syncthing as st_mod
from emu_stitch.syncthing import (
    SyncthingAPI,
    _base_url_from_gui,
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


class FakeSyncthing:
    """In-memory stand-in for Syncthing's REST API, routing requests by
    method and path so tests assert on resulting state, not call order."""

    def __init__(self, folders=None, devices=None, my_id="SELF-ID", connections=None, db_status=None):
        self.folders = folders or []
        self.devices = devices or []
        self.my_id = my_id
        self.connections = connections or {}
        self.db_status = db_status or {}
        self.requests = []

    def urlopen(self, req, timeout=None, context=None):
        import urllib.parse
        method = req.get_method()
        parsed = urllib.parse.urlsplit(req.full_url)
        path = parsed.path
        body = json.loads(req.data.decode("utf-8")) if req.data else None
        self.requests.append((method, path, body))
        result = self._route(method, path, urllib.parse.parse_qs(parsed.query), body)
        return _mock_urlopen(json.dumps(result).encode() if result is not None else b"")

    def _route(self, method, path, query, body):
        import urllib.parse
        if path == "/rest/system/status":
            return {"myID": self.my_id}
        if path == "/rest/system/connections":
            return {"connections": self.connections}
        if path == "/rest/db/status":
            return self.db_status
        for collection, key in (("folders", "id"), ("devices", "deviceID")):
            items = getattr(self, collection)
            base = f"/rest/config/{collection}"
            if path == base and method == "GET":
                return items
            if path.startswith(base + "/"):
                item_id = urllib.parse.unquote(path[len(base) + 1:])
                existing = next((i for i in items if i.get(key) == item_id), None)
                if method == "PUT":
                    if existing:
                        items.remove(existing)
                    items.append(body)
                elif method == "PATCH":
                    existing.update(body)
                elif method == "DELETE":
                    items.remove(existing)
                return None
        raise AssertionError(f"unexpected request {method} {path}")


@pytest.fixture
def fake_st(monkeypatch):
    """Point the syncthing module at a FakeSyncthing with a valid API key."""
    fake = FakeSyncthing()
    monkeypatch.setattr(st_mod, "_read_gui_config", lambda: ("key", None, False))
    monkeypatch.delenv("SYNCTHING_URL", raising=False)
    with patch("urllib.request.urlopen", side_effect=fake.urlopen):
        yield fake


@pytest.fixture
def no_api_key(monkeypatch):
    monkeypatch.setattr(st_mod, "_read_gui_config", lambda: (None, None, False))


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


    def test_excludes_ryujinx_counter_without_delete_flag(self, tmp_path):
        """The counter must never sync, and must not be deleted when Ryujinx
        replaces its folder, so it gets no (?d) prefix."""
        profile_dir = str(tmp_path / "alice")
        os.makedirs(profile_dir)

        generate_stignore(profile_dir)

        lines = open(os.path.join(profile_dir, ".stignore")).read().splitlines()
        assert "/ryujinx/saveIndex/*/lastPublishedId" in lines
        assert not any("lastPublishedId" in l and l.startswith("(?d)") for l in lines)

    def test_keeps_user_lines_and_is_idempotent(self, tmp_path):
        profile_dir = str(tmp_path / "alice")
        os.makedirs(profile_dir)
        stignore_path = os.path.join(profile_dir, ".stignore")
        with open(stignore_path, "w") as f:
            f.write("// mine\n*.tmp\n")

        generate_stignore(profile_dir)
        first = open(stignore_path).read()
        ok, msg = generate_stignore(profile_dir)

        assert first.startswith("// mine\n*.tmp\n")
        assert open(stignore_path).read() == first
        assert "up to date" in msg


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


class TestGuiConfig:
    def test_reads_gui_address_and_tls_from_config_xml(self, tmp_path, monkeypatch):
        _write_config_xml(tmp_path, """<configuration>
  <gui enabled="true" tls="true"><address>127.0.0.1:9999</address><apikey>k</apikey></gui>
</configuration>""")
        monkeypatch.setattr(os.path, "expanduser", _fake_expanduser(tmp_path))

        assert st_mod._read_gui_config() == ("k", "127.0.0.1:9999", True)

    @pytest.mark.parametrize("address,tls,expected", [
        ("127.0.0.1:8384", False, "http://127.0.0.1:8384"),
        ("0.0.0.0:8385", False, "http://127.0.0.1:8385"),
        ("[::]:8384", True, "https://[::1]:8384"),
        ("unix:///run/syncthing.sock", False, "http://127.0.0.1:8384"),
        (None, False, "http://127.0.0.1:8384"),
    ])
    def test_base_url_from_gui_address(self, monkeypatch, address, tls, expected):
        monkeypatch.delenv("SYNCTHING_URL", raising=False)
        assert _base_url_from_gui(address, tls) == expected

    def test_syncthing_url_env_overrides_config(self, monkeypatch):
        monkeypatch.setenv("SYNCTHING_URL", "http://example.invalid:1/")
        assert _base_url_from_gui("127.0.0.1:8384", True) == "http://example.invalid:1"

    def test_only_loopback_https_skips_certificate_verification(self):
        assert SyncthingAPI("k", "https://127.0.0.1:8384").context is not None
        assert SyncthingAPI("k", "https://sync.example.com").context is None
        assert SyncthingAPI("k", "http://127.0.0.1:8384").context is None


# ---------------------------------------------------------------------------
# auto_add_syncthing_folder
# ---------------------------------------------------------------------------

class TestAutoAddSyncthingFolder:
    def test_returns_false_when_no_api_key(self, no_api_key):
        ok, msg = auto_add_syncthing_folder("alice", "/some/path")

        assert ok is False
        assert "API Key" in msg

    def test_already_registered_same_path(self, tmp_path, fake_st):
        profile_path = str(tmp_path / "saves_by_user" / "alice")
        fake_st.folders = [{"id": "emustitch-alice", "path": profile_path}]

        ok, msg = auto_add_syncthing_folder("alice", profile_path)

        assert ok is True
        assert "already registered" in msg
        assert all(method == "GET" for method, _, _ in fake_st.requests)

    def test_refuses_to_repoint_existing_folder_at_a_different_path(self, tmp_path, fake_st):
        """Silently repointing could merge two profiles (Alice vs alice share
        a lowercased folder ID) or make Syncthing treat every file as deleted."""
        old_path = str(tmp_path / "home" / "Emulation" / "saves_by_user" / "alice")
        new_path = str(tmp_path / "mnt" / "Emulation" / "saves_by_user" / "Alice")
        fake_st.folders = [{"id": "emustitch-alice", "path": old_path}]

        ok, msg = auto_add_syncthing_folder("Alice", new_path)

        assert ok is False
        assert old_path in msg
        assert fake_st.folders == [{"id": "emustitch-alice", "path": old_path}]

    def test_registers_new_folder_via_scoped_endpoint(self, tmp_path, fake_st):
        profile_path = str(tmp_path / "saves_by_user" / "bob")
        fake_st.folders = [{"id": "unrelated", "path": "/x"}]

        ok, msg = auto_add_syncthing_folder("bob", profile_path)

        assert ok is True
        assert "Successfully" in msg
        assert ("PUT", "/rest/config/folders/emustitch-bob") in [(m, p) for m, p, _ in fake_st.requests]
        assert not any(p == "/rest/config" for _, p, _ in fake_st.requests)
        added = next(f for f in fake_st.folders if f["id"] == "emustitch-bob")
        assert added["path"] == os.path.abspath(profile_path)
        assert {"id": "unrelated", "path": "/x"} in fake_st.folders

    def test_returns_false_when_api_unreachable(self, monkeypatch):
        import urllib.error
        monkeypatch.setattr(st_mod, "_read_gui_config", lambda: ("key", None, False))

        with patch("urllib.request.urlopen", side_effect=urllib.error.URLError("connection refused")):
            ok, msg = auto_add_syncthing_folder("alice", "/some/path")

        assert ok is False
        assert "timeout or connection error" in msg

    def test_reports_http_errors_distinctly(self, monkeypatch):
        import urllib.error
        monkeypatch.setattr(st_mod, "_read_gui_config", lambda: ("key", None, False))
        err = urllib.error.HTTPError("http://x", 403, "Forbidden", {}, None)

        with patch("urllib.request.urlopen", side_effect=err):
            ok, msg = auto_add_syncthing_folder("alice", "/some/path")

        assert ok is False
        assert "HTTP 403" in msg


# ---------------------------------------------------------------------------
# auto_pair_device
# ---------------------------------------------------------------------------

class TestAutoPairDevice:
    def test_returns_false_when_no_api_key(self, no_api_key):
        ok, msg = auto_pair_device("ABCDEFG-1234567-XXXXXXX")

        assert ok is False

    def test_pairs_new_device_without_auto_accept_by_default(self, fake_st):
        fake_st.folders = [
            {"id": "emustitch-alice", "devices": []},
            {"id": "personal-docs", "devices": []},
        ]

        ok, msg = auto_pair_device("NEWDEV1-AAAAAA-BBBBBBB")

        assert ok is True
        assert "paired" in msg.lower()
        dev = fake_st.devices[0]
        assert dev["deviceID"] == "NEWDEV1-AAAAAA-BBBBBBB"
        assert dev["autoAcceptFolders"] is False
        folders = {f["id"]: f for f in fake_st.folders}
        assert folders["emustitch-alice"]["devices"] == [{"deviceID": "NEWDEV1-AAAAAA-BBBBBBB"}]
        assert folders["personal-docs"]["devices"] == []

    def test_auto_accept_is_opt_in(self, fake_st):
        ok, _ = auto_pair_device("NEWDEV1-AAAAAA-BBBBBBB", auto_accept=True)

        assert ok is True
        assert fake_st.devices[0]["autoAcceptFolders"] is True

    def test_repairing_existing_device_does_not_grant_auto_accept(self, fake_st):
        fake_st.devices = [{"deviceID": "DEV", "name": "Mine", "autoAcceptFolders": False}]

        auto_pair_device("DEV")

        assert fake_st.devices == [{"deviceID": "DEV", "name": "Mine", "autoAcceptFolders": False}]

    def test_returns_false_on_api_error(self, monkeypatch):
        import urllib.error
        monkeypatch.setattr(st_mod, "_read_gui_config", lambda: ("key", None, False))

        with patch("urllib.request.urlopen", side_effect=urllib.error.URLError("refused")):
            ok, msg = auto_pair_device("SOMEID")

        assert ok is False


# ---------------------------------------------------------------------------
# remove_paired_device
# ---------------------------------------------------------------------------

class TestRemovePairedDevice:
    def test_returns_false_when_no_api_key(self, no_api_key):
        ok, msg = remove_paired_device("EXISTING-DEV1234")

        assert ok is False

    def test_returns_false_when_device_not_paired(self, fake_st):
        ok, msg = remove_paired_device("NEVER-PAIRED-DEVICE")

        assert ok is False
        assert "not paired" in msg.lower()

    def test_unpairs_existing_device_and_removes_from_all_folders(self, fake_st):
        fake_st.devices = [
            {"deviceID": "REMOTE1-AAAAAAA", "name": "Remote1", "autoAcceptFolders": True},
            {"deviceID": "REMOTE2-BBBBBBB", "name": "Remote2"},
        ]
        fake_st.folders = [
            {"id": "emustitch-alice", "devices": [{"deviceID": "REMOTE1-AAAAAAA"}, {"deviceID": "REMOTE2-BBBBBBB"}]},
            {"id": "emustitch-bob", "devices": [{"deviceID": "REMOTE1-AAAAAAA"}]},
        ]

        ok, msg = remove_paired_device("REMOTE1-AAAAAAA")

        assert ok is True
        assert [d["deviceID"] for d in fake_st.devices] == ["REMOTE2-BBBBBBB"]
        for folder in fake_st.folders:
            assert "REMOTE1-AAAAAAA" not in [d["deviceID"] for d in folder["devices"]]

    def test_returns_false_on_api_error(self, monkeypatch):
        import urllib.error
        monkeypatch.setattr(st_mod, "_read_gui_config", lambda: ("key", None, False))

        with patch("urllib.request.urlopen", side_effect=urllib.error.URLError("refused")):
            ok, msg = remove_paired_device("SOMEID")

        assert ok is False


# ---------------------------------------------------------------------------
# get_profile_sync_status
# ---------------------------------------------------------------------------

class TestGetProfileSyncStatus:
    def test_returns_unknown_when_no_api_key(self, no_api_key):
        status, msg = get_profile_sync_status("/some/path")

        assert status == "UNKNOWN"

    def test_returns_not_registered_when_folder_absent(self, tmp_path, fake_st):
        fake_st.folders = [{"id": "other", "path": "/different/path"}]

        status, msg = get_profile_sync_status(str(tmp_path / "alice"))

        assert status == "NOT REGISTERED"

    def test_returns_in_sync_when_synced(self, tmp_path, fake_st):
        profile_path = str(tmp_path / "alice")
        fake_st.folders = [{"id": "emustitch-alice", "path": profile_path}]
        fake_st.db_status = {"state": "idle", "needBytes": 0, "globalBytes": 1048576, "inSyncFiles": 42}

        status, msg = get_profile_sync_status(profile_path)

        assert status == "100% IN SYNC"
        assert "42 files" in msg

    def test_returns_syncing_when_bytes_needed(self, tmp_path, fake_st):
        profile_path = str(tmp_path / "alice")
        fake_st.folders = [{"id": "emustitch-alice", "path": profile_path}]
        fake_st.db_status = {"state": "syncing", "needBytes": 512000, "globalBytes": 1024000, "inSyncFiles": 10}

        status, msg = get_profile_sync_status(profile_path)

        assert status == "SYNCING"
        assert "50%" in msg

    def test_folder_id_is_url_encoded(self, tmp_path, fake_st):
        profile_path = str(tmp_path / "x")
        fake_st.folders = [{"id": "a&b c", "path": profile_path}]

        with patch("urllib.request.urlopen", side_effect=fake_st.urlopen) as m:
            get_profile_sync_status(profile_path)

        assert m.call_args_list[-1].args[0].full_url.endswith("?folder=a%26b%20c")


# ---------------------------------------------------------------------------
# get_paired_devices_status
# ---------------------------------------------------------------------------

class TestGetPairedDevicesStatus:
    def test_returns_empty_list_when_no_api_key(self, no_api_key):
        assert get_paired_devices_status() == []

    def test_excludes_self_device(self, fake_st):
        fake_st.my_id = "LOCAL-DEVICE-XXXXXXXXXX"
        fake_st.devices = [
            {"deviceID": "LOCAL-DEVICE-XXXXXXXXXX", "name": "This machine"},
            {"deviceID": "REMOTE1", "name": "Remote1"},
        ]
        fake_st.connections = {"REMOTE1": {"connected": True, "address": "10.0.0.1:22000"}}

        devices = get_paired_devices_status()

        assert len(devices) == 1
        assert devices[0]["id"] == "REMOTE1"
        assert devices[0]["connected"] is True

    def test_returns_offline_address_when_device_not_connected(self, fake_st):
        fake_st.devices = [{"deviceID": "REMOTE1", "name": "Remote1"}]

        devices = get_paired_devices_status()

        assert devices[0]["connected"] is False
        assert devices[0]["address"] == "offline"

    def test_returns_offline_address_when_connection_entry_has_empty_address(self, fake_st):
        """Syncthing includes a connection entry for every known device, even
        ones that have never connected, with an empty-string address rather
        than omitting the key entirely."""
        fake_st.devices = [{"deviceID": "REMOTE1", "name": "Remote1"}]
        fake_st.connections = {"REMOTE1": {"connected": False, "address": ""}}

        devices = get_paired_devices_status()

        assert devices[0]["address"] == "offline"


# ---------------------------------------------------------------------------
# share_profile_folder
# ---------------------------------------------------------------------------

class TestShareProfileFolder:
    def test_new_profile_is_shared_with_devices_of_existing_profiles(self, tmp_path, fake_st):
        from emu_stitch.syncthing import share_profile_folder
        fake_st.my_id = "SELF"
        fake_st.folders = [
            {"id": "emustitch-alice", "path": "/a", "devices": [{"deviceID": "SELF"}, {"deviceID": "HUB"}, {"deviceID": "DESK"}]},
            {"id": "personal", "path": "/p", "devices": [{"deviceID": "PHONE"}]},
        ]

        ok, msg = share_profile_folder("bob", str(tmp_path / "bob"))

        assert ok is True
        bob = next(f for f in fake_st.folders if f["id"] == "emustitch-bob")
        assert sorted(d["deviceID"] for d in bob["devices"]) == ["DESK", "HUB"]
        assert "shared with 2" in msg

    def test_is_idempotent(self, tmp_path, fake_st):
        from emu_stitch.syncthing import share_profile_folder
        fake_st.folders = [{"id": "emustitch-alice", "path": "/a", "devices": [{"deviceID": "HUB"}]}]
        share_profile_folder("bob", str(tmp_path / "bob"))
        before = [f.copy() for f in fake_st.folders]

        ok, msg = share_profile_folder("bob", str(tmp_path / "bob"))

        assert ok is True
        assert "shared with" not in msg
        assert fake_st.folders == before

    def test_no_api_key_reports_failure(self, no_api_key):
        from emu_stitch.syncthing import share_profile_folder
        ok, msg = share_profile_folder("bob", "/x")
        assert ok is False
