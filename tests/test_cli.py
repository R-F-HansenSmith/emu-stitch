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


def test_cli_pair_dispatches_device_id_to_auto_pair_device(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["emu-stitch", "pair", "ABCDEFG-HIJKLMN"])
    calls = []
    monkeypatch.setattr(
        cli_mod, "auto_pair_device",
        lambda device_id: calls.append(device_id) or (True, "paired!"),
    )

    cli_mod.main()

    assert calls == ["ABCDEFG-HIJKLMN"]
    assert "paired!" in capsys.readouterr().out


def test_cli_pair_reports_failure_without_raising(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["emu-stitch", "pair", "bad-id"])
    monkeypatch.setattr(cli_mod, "auto_pair_device", lambda device_id: (False, "not found"))

    cli_mod.main()

    assert "not found" in capsys.readouterr().out


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
