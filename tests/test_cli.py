"""Tests for emu_stitch.cli: argument dispatch and top-level error handling."""

import os
import sys

import pytest

import emu_stitch.cli as cli_mod


def test_cli_version_flag_prints_version_and_exits_cleanly(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["emu-stitch", "--version"])

    with pytest.raises(SystemExit) as exc_info:
        cli_mod.main()

    assert exc_info.value.code == 0
    out = capsys.readouterr().out
    assert "emu-stitch" in out


def test_main_reports_unexpected_errors_cleanly_instead_of_crashing(monkeypatch, capsys):
    """An unexpected exception from a command handler must produce a clean
    error message and exit(1), not a raw traceback to the end user."""
    monkeypatch.setattr(sys, "argv", ["emu-stitch", "switch"])
    monkeypatch.setattr(cli_mod, "detect_emulation_dir", lambda: "/tmp/does-not-matter")

    def boom(*a, **k):
        raise RuntimeError("boom")

    monkeypatch.setattr(cli_mod, "run_switch", boom)

    with pytest.raises(SystemExit) as exc_info:
        cli_mod.main()

    assert exc_info.value.code == 1
    captured = capsys.readouterr()
    assert "boom" in captured.err


def test_main_reports_keyboard_interrupt_cleanly(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["emu-stitch", "switch"])
    monkeypatch.setattr(cli_mod, "detect_emulation_dir", lambda: "/tmp/does-not-matter")

    def interrupt(*a, **k):
        raise KeyboardInterrupt()

    monkeypatch.setattr(cli_mod, "run_switch", interrupt)

    with pytest.raises(SystemExit) as exc_info:
        cli_mod.main()

    assert exc_info.value.code == 130


def test_cli_switch_passes_dir_flag_through_to_run_switch(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["emu-stitch", "--dir", "/custom/Emulation", "switch"])
    calls = []
    monkeypatch.setattr(cli_mod, "run_switch", lambda emu_dir: calls.append(emu_dir) or ("Alice", "/custom/Emulation/saves_by_user/Alice"))

    cli_mod.main()

    assert calls == ["/custom/Emulation"]


def test_cli_no_command_defaults_to_switch(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["emu-stitch"])
    monkeypatch.setattr(cli_mod, "detect_emulation_dir", lambda: "/detected/Emulation")
    calls = []
    monkeypatch.setattr(cli_mod, "run_switch", lambda emu_dir: calls.append(emu_dir) or ("Alice", "/detected/Emulation/saves_by_user/Alice"))

    cli_mod.main()

    assert calls == ["/detected/Emulation"]


VALID_DEVICE_ID = "MFZWI3D-BONSGYC-YLTMK4F-WMZ7IPP-NEEQAIZ-G7JXVJZ-IWNJI4D-YAHOMQ2"


def test_cli_pair_dispatches_device_id_to_auto_pair_device(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["emu-stitch", "pair", VALID_DEVICE_ID])
    calls = []
    monkeypatch.setattr(
        cli_mod, "auto_pair_device",
        lambda device_id: calls.append(device_id) or (True, "paired!"),
    )

    cli_mod.main()

    assert calls == [VALID_DEVICE_ID]
    assert "paired!" in capsys.readouterr().out


def test_cli_pair_reports_failure_without_raising(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["emu-stitch", "pair", VALID_DEVICE_ID])
    monkeypatch.setattr(cli_mod, "auto_pair_device", lambda device_id: (False, "not found"))

    cli_mod.main()

    assert "not found" in capsys.readouterr().out


def test_cli_pair_rejects_malformed_device_id_without_calling_api(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["emu-stitch", "pair", "not-a-real-device-id"])

    def fail_pair(*a, **k):
        raise AssertionError("auto_pair_device should not be called for a malformed device ID")

    monkeypatch.setattr(cli_mod, "auto_pair_device", fail_pair)

    cli_mod.main()

    assert "Invalid Device ID" in capsys.readouterr().out


def test_cli_unpair_dispatches_device_id_to_remove_paired_device(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["emu-stitch", "unpair", VALID_DEVICE_ID])
    calls = []
    monkeypatch.setattr(
        cli_mod, "remove_paired_device",
        lambda device_id: calls.append(device_id) or (True, "unpaired!"),
    )

    cli_mod.main()

    assert calls == [VALID_DEVICE_ID]
    assert "unpaired!" in capsys.readouterr().out


def test_cli_unpair_reports_failure_without_raising(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["emu-stitch", "unpair", VALID_DEVICE_ID])
    monkeypatch.setattr(cli_mod, "remove_paired_device", lambda device_id: (False, "not paired"))

    cli_mod.main()

    assert "not paired" in capsys.readouterr().out


def test_cli_unpair_rejects_malformed_device_id_without_calling_api(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["emu-stitch", "unpair", "not-a-real-device-id"])

    def fail_unpair(*a, **k):
        raise AssertionError("remove_paired_device should not be called for a malformed device ID")

    monkeypatch.setattr(cli_mod, "remove_paired_device", fail_unpair)

    cli_mod.main()

    assert "Invalid Device ID" in capsys.readouterr().out


def test_cli_audit_prints_full_paired_device_id(tmp_path, monkeypatch, capsys):
    """The paired-device ID must be shown in full: it's needed to verify or
    re-pair a device, and a truncated ID is useless for that."""
    full_device_id = "GRCJWQE-FENHH3D-YLTMK4F-WMZ7IPP-NEEQAIZ-G7JXVJZ-IWNJI4D-YAHOMQ2"
    emu_dir = tmp_path / "Emulation"
    emu_dir.mkdir()

    monkeypatch.setattr(sys, "argv", ["emu-stitch", "audit"])
    monkeypatch.setattr(cli_mod, "detect_emulation_dir", lambda: str(emu_dir))
    monkeypatch.setattr(cli_mod, "audit_mount_permissions", lambda d: (False, "/home", "ok"))
    monkeypatch.setattr(cli_mod, "detect_active_steam_user", lambda: ("123", "Alice"))
    monkeypatch.setattr(cli_mod, "detect_installed_emulators", lambda: {"ryujinx": False, "cemu": False})
    monkeypatch.setattr(cli_mod, "get_profile_sync_status", lambda p: ("UNKNOWN", "n/a"))
    monkeypatch.setattr(cli_mod, "audit_emulator_saves", lambda p: [])
    monkeypatch.setattr(cli_mod, "ensure_syncthing_service", lambda enable=False: (True, "running"))
    monkeypatch.setattr(cli_mod, "get_syncthing_credentials", lambda: ("apikey", "SELF-ID"))
    monkeypatch.setattr(
        cli_mod, "get_paired_devices_status",
        lambda: [{"id": full_device_id, "name": "Device-GRCJWQE", "connected": False, "address": "offline"}],
    )

    cli_mod.main()

    assert full_device_id in capsys.readouterr().out


def test_cli_audit_device_ids_are_explicitly_labeled_by_name(tmp_path, monkeypatch, capsys):
    """Each device's full ID must be paired with its name explicitly (not
    left to positional/row-order inference), since IDs are too long to fit
    as a table column."""
    emu_dir = tmp_path / "Emulation"
    emu_dir.mkdir()

    monkeypatch.setattr(sys, "argv", ["emu-stitch", "audit"])
    monkeypatch.setattr(cli_mod, "detect_emulation_dir", lambda: str(emu_dir))
    monkeypatch.setattr(cli_mod, "audit_mount_permissions", lambda d: (False, "/home", "ok"))
    monkeypatch.setattr(cli_mod, "detect_active_steam_user", lambda: (None, None))
    monkeypatch.setattr(cli_mod, "detect_installed_emulators", lambda: {"ryujinx": False, "cemu": False})
    monkeypatch.setattr(cli_mod, "get_profile_sync_status", lambda p: ("UNKNOWN", "n/a"))
    monkeypatch.setattr(cli_mod, "audit_emulator_saves", lambda p: [])
    monkeypatch.setattr(cli_mod, "ensure_syncthing_service", lambda enable=False: (True, "running"))
    monkeypatch.setattr(cli_mod, "get_syncthing_credentials", lambda: ("apikey", "SELF-ID"))
    monkeypatch.setattr(
        cli_mod, "get_paired_devices_status",
        lambda: [
            {"id": "AAAAAAA-AAAAAAA-AAAAAAA-AAAAAAA-AAAAAAA-AAAAAAA-AAAAAAA-AAAAAAA", "name": "alpha", "connected": True, "address": "10.0.0.1"},
            {"id": "BBBBBBB-BBBBBBB-BBBBBBB-BBBBBBB-BBBBBBB-BBBBBBB-BBBBBBB-BBBBBBB", "name": "beta", "connected": False, "address": "offline"},
        ],
    )

    cli_mod.main()

    out = capsys.readouterr().out
    assert "Device IDs:" in out

    legend = out.split("Device IDs:")[1]
    alpha_line = next(l for l in legend.splitlines() if "alpha" in l)
    beta_line = next(l for l in legend.splitlines() if "beta" in l)
    assert "AAAAAAA-AAAAAAA-AAAAAAA-AAAAAAA-AAAAAAA-AAAAAAA-AAAAAAA-AAAAAAA" in alpha_line
    assert "BBBBBBB-BBBBBBB-BBBBBBB-BBBBBBB-BBBBBBB-BBBBBBB-BBBBBBB-BBBBBBB" in beta_line
    assert "BBBBBBB" not in alpha_line
    assert "AAAAAAA" not in beta_line


def test_cli_audit_lists_all_profiles_with_per_profile_game_counts(tmp_path, monkeypatch, capsys):
    """The Save Profiles section must list every known profile (not just the
    active one), marking which is active, with its own game counts —
    counts must come from that profile's own save data, not the active
    profile's."""
    emu_dir = tmp_path / "Emulation"
    emu_dir.mkdir()

    monkeypatch.setattr(sys, "argv", ["emu-stitch", "audit"])
    monkeypatch.setattr(cli_mod, "detect_emulation_dir", lambda: str(emu_dir))
    monkeypatch.setattr(cli_mod, "audit_mount_permissions", lambda d: (False, "/home", "ok"))
    monkeypatch.setattr(cli_mod, "detect_active_steam_user", lambda: ("123", "alice"))
    monkeypatch.setattr(cli_mod, "detect_installed_emulators", lambda: {"ryujinx": False, "cemu": False})
    monkeypatch.setattr(cli_mod, "get_profile_sync_status", lambda p: ("UNKNOWN", "n/a"))
    monkeypatch.setattr(cli_mod, "ensure_syncthing_service", lambda enable=False: (False, "not running"))
    monkeypatch.setattr(
        cli_mod, "list_profiles",
        lambda emu_dir: [
            {"name": "alice", "path": "/profiles/alice", "active": True, "steamid3": "123"},
            {"name": "bob", "path": "/profiles/bob", "active": False, "steamid3": None},
        ],
    )

    def fake_audit_emulator_saves(path):
        if path == "/profiles/alice":
            return [
                {"emulator": "Ryujinx (Switch)", "name": "5 games tracked", "details": "", "count": 5},
                {"emulator": "Cemu (Wii U)", "name": "1 game tracked", "details": "", "count": 1},
            ]
        return [{"emulator": "Ryujinx (Switch)", "name": "2 games tracked", "details": "", "count": 2}]

    monkeypatch.setattr(cli_mod, "audit_emulator_saves", fake_audit_emulator_saves)

    cli_mod.main()

    out = capsys.readouterr().out
    assert "Save Profiles (2 known)" in out

    profiles_section = out.split("Save Profiles")[1].split("Detected System Emulators")[0]
    lines = profiles_section.splitlines()

    alice_row = next(l for l in lines if "alice" in l)
    assert "123" in alice_row  # steamid3
    assert "●" in alice_row  # active marker
    assert "5" in alice_row  # ryujinx count
    assert "1" in alice_row  # cemu count

    bob_row = next(l for l in lines if "bob" in l)
    assert "●" not in bob_row  # not active
    assert "-" in bob_row  # no known steamid3
    assert "2" in bob_row  # ryujinx count
    assert "0" in bob_row  # cemu count


def _mock_audit_deps(monkeypatch, cli_mod, emu_dir, active_steamid, profiles):
    monkeypatch.setattr(cli_mod, "detect_emulation_dir", lambda: str(emu_dir))
    monkeypatch.setattr(cli_mod, "audit_mount_permissions", lambda d: (False, "/home", "ok"))
    monkeypatch.setattr(cli_mod, "detect_active_steam_user", lambda: (active_steamid, "alice"))
    monkeypatch.setattr(cli_mod, "detect_installed_emulators", lambda: {"ryujinx": False, "cemu": False})
    monkeypatch.setattr(cli_mod, "get_profile_sync_status", lambda p: ("UNKNOWN", "n/a"))
    monkeypatch.setattr(cli_mod, "ensure_syncthing_service", lambda enable=False: (False, "not running"))
    monkeypatch.setattr(cli_mod, "list_profiles", lambda emu_dir: profiles)
    monkeypatch.setattr(cli_mod, "audit_emulator_saves", lambda path: [])


def test_cli_audit_warns_when_steam_user_diverges_from_active_profile(tmp_path, monkeypatch, capsys):
    """If Steam is logged in as a different user than the one emu-stitch last
    switched to (e.g. switch hasn't run yet since an account change), that
    must be surfaced as an actionable warning."""
    emu_dir = tmp_path / "Emulation"
    emu_dir.mkdir()
    monkeypatch.setattr(sys, "argv", ["emu-stitch", "audit"])
    _mock_audit_deps(
        monkeypatch, cli_mod, emu_dir, active_steamid="999",
        profiles=[{"name": "alice", "path": "/profiles/alice", "active": True, "steamid3": "123"}],
    )

    cli_mod.main()

    out = capsys.readouterr().out
    assert "Steam is currently logged in as 'alice'" in out
    assert "isn't the active save profile" in out


def test_cli_audit_does_not_warn_when_steam_user_matches_active_profile(tmp_path, monkeypatch, capsys):
    emu_dir = tmp_path / "Emulation"
    emu_dir.mkdir()
    monkeypatch.setattr(sys, "argv", ["emu-stitch", "audit"])
    _mock_audit_deps(
        monkeypatch, cli_mod, emu_dir, active_steamid="123",
        profiles=[{"name": "alice", "path": "/profiles/alice", "active": True, "steamid3": "123"}],
    )

    cli_mod.main()

    out = capsys.readouterr().out
    assert "isn't the active save profile" not in out


def test_cli_audit_does_not_warn_when_steam_detection_fails(tmp_path, monkeypatch, capsys):
    """A failed Steam-user detection (steamid3=None) must not be mistaken
    for a real divergence from the active profile."""
    emu_dir = tmp_path / "Emulation"
    emu_dir.mkdir()
    monkeypatch.setattr(sys, "argv", ["emu-stitch", "audit"])
    _mock_audit_deps(
        monkeypatch, cli_mod, emu_dir, active_steamid=None,
        profiles=[{"name": "Default_User", "path": "/profiles/default", "active": True, "steamid3": None}],
    )

    cli_mod.main()

    out = capsys.readouterr().out
    assert "isn't the active save profile" not in out


def test_cli_audit_omits_steamid_note_for_unmapped_profile(tmp_path, monkeypatch, capsys):
    """A profile with no user_map.json entry (steamid3=None) must not print
    a bogus 'ID3: None' note."""
    emu_dir = tmp_path / "Emulation"
    emu_dir.mkdir()

    monkeypatch.setattr(sys, "argv", ["emu-stitch", "audit"])
    monkeypatch.setattr(cli_mod, "detect_emulation_dir", lambda: str(emu_dir))
    monkeypatch.setattr(cli_mod, "audit_mount_permissions", lambda d: (False, "/home", "ok"))
    monkeypatch.setattr(cli_mod, "detect_active_steam_user", lambda: ("123", "alice"))
    monkeypatch.setattr(cli_mod, "detect_installed_emulators", lambda: {"ryujinx": False, "cemu": False})
    monkeypatch.setattr(cli_mod, "get_profile_sync_status", lambda p: ("UNKNOWN", "n/a"))
    monkeypatch.setattr(cli_mod, "ensure_syncthing_service", lambda enable=False: (False, "not running"))
    monkeypatch.setattr(cli_mod, "audit_emulator_saves", lambda path: [])
    monkeypatch.setattr(
        cli_mod, "list_profiles",
        lambda emu_dir: [{"name": "bob", "path": "/profiles/bob", "active": False, "steamid3": None}],
    )

    cli_mod.main()

    out = capsys.readouterr().out
    assert "ID3: None" not in out
    assert "bob" in out


class TestPromptYesNo:
    def test_auto_yes_skips_prompt_entirely(self, monkeypatch):
        def fail_input(*a, **k):
            raise AssertionError("input() should not be called when auto_yes=True")

        monkeypatch.setattr("builtins.input", fail_input)

        assert cli_mod.prompt_yes_no("Proceed?", auto_yes=True) is True

    def test_empty_input_returns_default(self, monkeypatch):
        monkeypatch.setattr("builtins.input", lambda: "")

        assert cli_mod.prompt_yes_no("Proceed?", default=True) is True
        assert cli_mod.prompt_yes_no("Proceed?", default=False) is False

    def test_parses_yes_and_no_answers(self, monkeypatch):
        monkeypatch.setattr("builtins.input", lambda: "y")
        assert cli_mod.prompt_yes_no("Proceed?", default=False) is True

        monkeypatch.setattr("builtins.input", lambda: "n")
        assert cli_mod.prompt_yes_no("Proceed?", default=True) is False

    def test_keyboard_interrupt_returns_false(self, monkeypatch):
        def raise_interrupt():
            raise KeyboardInterrupt()

        monkeypatch.setattr("builtins.input", raise_interrupt)

        assert cli_mod.prompt_yes_no("Proceed?", default=True) is False


class TestPromptInt:
    def test_auto_yes_skips_prompt_entirely(self, monkeypatch):
        def fail_input(*a, **k):
            raise AssertionError("input() should not be called when auto_yes=True")

        monkeypatch.setattr("builtins.input", fail_input)

        assert cli_mod.prompt_int("How many?", default=3, auto_yes=True) == 3

    def test_empty_input_returns_default(self, monkeypatch):
        monkeypatch.setattr("builtins.input", lambda: "")

        assert cli_mod.prompt_int("How many?", default=3) == 3

    def test_parses_a_valid_number(self, monkeypatch):
        monkeypatch.setattr("builtins.input", lambda: "5")

        assert cli_mod.prompt_int("How many?", default=3) == 5

    def test_zero_is_a_valid_answer(self, monkeypatch):
        monkeypatch.setattr("builtins.input", lambda: "0")

        assert cli_mod.prompt_int("How many?", default=3) == 0

    def test_non_numeric_input_falls_back_to_default(self, monkeypatch):
        monkeypatch.setattr("builtins.input", lambda: "banana")

        assert cli_mod.prompt_int("How many?", default=3) == 3

    def test_negative_input_falls_back_to_default(self, monkeypatch):
        monkeypatch.setattr("builtins.input", lambda: "-1")

        assert cli_mod.prompt_int("How many?", default=3) == 3

    def test_keyboard_interrupt_returns_default(self, monkeypatch):
        def raise_interrupt():
            raise KeyboardInterrupt()

        monkeypatch.setattr("builtins.input", raise_interrupt)

        assert cli_mod.prompt_int("How many?", default=3) == 3


class TestSetupBackupRetention:
    def _mock_common_setup_deps(self, monkeypatch, tmp_path):
        monkeypatch.setattr(sys, "argv", ["emu-stitch", "setup", "-y"])
        monkeypatch.setattr(cli_mod, "detect_emulation_dir", lambda: str(tmp_path / "Emulation"))
        monkeypatch.setattr(cli_mod, "detect_installed_emulators", lambda: {"ryujinx": False, "cemu": False})
        monkeypatch.setattr(cli_mod, "check_syncthing_installed", lambda: False)
        monkeypatch.setattr(cli_mod, "run_switch", lambda emu_dir: ("alice", "/path"))
        monkeypatch.setattr(cli_mod, "setup_systemd_watcher", lambda: (True, "watching"))

    def test_prompts_and_persists_default_on_first_run(self, tmp_path, monkeypatch, capsys):
        fake_home = tmp_path / "home"
        fake_home.mkdir()
        monkeypatch.setattr(os.path, "expanduser", lambda p: p.replace("~", str(fake_home)))
        self._mock_common_setup_deps(monkeypatch, tmp_path)

        cli_mod.main()

        from emu_stitch.config import get_backup_retention, is_configured
        assert is_configured() is True
        assert get_backup_retention() == 3
        out = capsys.readouterr().out
        assert "Backup retention set to:" in out
        assert "3 (0 = keep forever)" in out

    def test_skips_prompt_and_keeps_existing_value_when_already_configured(self, tmp_path, monkeypatch, capsys):
        fake_home = tmp_path / "home"
        fake_home.mkdir()
        monkeypatch.setattr(os.path, "expanduser", lambda p: p.replace("~", str(fake_home)))
        from emu_stitch.config import set_backup_retention
        set_backup_retention(7)

        self._mock_common_setup_deps(monkeypatch, tmp_path)

        cli_mod.main()

        from emu_stitch.config import get_backup_retention
        assert get_backup_retention() == 7
        out = capsys.readouterr().out
        assert "Backup retention:" in out
        assert "already configured" in out
