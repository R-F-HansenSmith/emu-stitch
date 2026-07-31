"""Tests for emu_stitch.cli: argument dispatch and top-level error handling."""

import sys

import pytest

import emu_stitch.cli as cli_mod


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
            {"name": "alice", "path": "/profiles/alice", "active": True},
            {"name": "bob", "path": "/profiles/bob", "active": False},
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
    assert "alice" in out and "(ACTIVE)" in out
    assert "5 Ryujinx, 1 Cemu" in out
    assert "bob" in out
    assert "2 Ryujinx, 0 Cemu" in out


class TestPromptYesNo:
    def test_auto_yes_skips_prompt_entirely(self, monkeypatch):
        def fail_input(*a, **k):
            raise AssertionError("input() should not be called when auto_yes=True")

        monkeypatch.setattr("builtins.input", fail_input)

        assert cli_mod.prompt_yes_no("Proceed?", auto_yes=True) is True

    def test_empty_input_returns_default(self, monkeypatch):
        monkeypatch.setattr("builtins.input", lambda prompt: "")

        assert cli_mod.prompt_yes_no("Proceed?", default=True) is True
        assert cli_mod.prompt_yes_no("Proceed?", default=False) is False

    def test_parses_yes_and_no_answers(self, monkeypatch):
        monkeypatch.setattr("builtins.input", lambda prompt: "y")
        assert cli_mod.prompt_yes_no("Proceed?", default=False) is True

        monkeypatch.setattr("builtins.input", lambda prompt: "n")
        assert cli_mod.prompt_yes_no("Proceed?", default=True) is False

    def test_keyboard_interrupt_returns_false(self, monkeypatch):
        def raise_interrupt(prompt):
            raise KeyboardInterrupt()

        monkeypatch.setattr("builtins.input", raise_interrupt)

        assert cli_mod.prompt_yes_no("Proceed?", default=True) is False
