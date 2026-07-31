"""
CLI entry point for emu-stitch: Command-line interface, terminal visualization,
state-aware setup wizard, smart emulator auditing, device pairing, and system health checks.
"""

import os
import re
import sys
import logging
import argparse

from rich import box
from rich.console import Console
from rich.markup import escape as esc
from rich.table import Table

from .config import DEFAULT_BACKUP_RETENTION, is_configured, set_backup_retention
from . import __version__
from .detector import detect_emulation_dir, detect_active_steam_user
from .switcher import run_switch, setup_systemd_watcher, list_profiles
from .fstab import audit_mount_permissions
from .emulators import audit_emulator_saves, detect_installed_emulators
from .syncthing import (
    check_syncthing_installed,
    ensure_syncthing_service,
    get_syncthing_credentials,
    auto_add_syncthing_folder,
    auto_pair_device,
    remove_paired_device,
    get_paired_devices_status,
    get_profile_sync_status
)

console = Console()
error_console = Console(stderr=True)

BANNER_ART = r"""
  _____                        ____   _   _  _   _       _
 | ____|_ __ ___  _   _       / ___| | |_(_)| |_ ___| |__
 |  _| | '_ ` _ \| | | |  ___  \___ \ | __| | __/ __| '_ \
 | |___| | | | | | |_| | |___|  ___) || |_| | || (__| | | |
 |_____|_| |_| |_|\__,_|       |____/  \__|_|\__\___|_| |_|
"""

# A Syncthing Device ID is 8 groups of 7 base32 characters (A-Z, 2-7), joined by hyphens.
_DEVICE_ID_RE = re.compile(r"^[A-Z2-7]{7}(-[A-Z2-7]{7}){7}$")


def _is_valid_syncthing_device_id(device_id: str) -> bool:
    return bool(_DEVICE_ID_RE.match(device_id.upper()))


def cprint(*args, **kwargs):
    """console.print with soft-wrap on by default, so long values (device
    IDs, paths) are never hard-wrapped mid-string on narrow terminals."""
    kwargs.setdefault("soft_wrap", True)
    console.print(*args, **kwargs)


def print_banner():
    cprint(f"[bold cyan]{BANNER_ART}[/]")
    cprint("[yellow]Open-Source Multi-User Emulator Save Synchronizer & Profile Switcher[/]\n")


def print_section_header(number, title):
    """A left-aligned horizontal rule with a numbered title, used to divide
    audit output into scannable sections."""
    console.print()
    console.rule(f"[bold cyan]{number}. {esc(str(title))}[/]", style="cyan", align="left")


def print_success(msg: str) -> None:
    cprint(f"[green]✔[/] {esc(msg)}")


def print_warning(msg: str) -> None:
    cprint(f"[yellow]⚠[/] {esc(msg)}")


def print_error(msg: str) -> None:
    error_console.print(f"[bold red]Error:[/] {esc(msg)}", soft_wrap=True)


def prompt_yes_no(question, default=True, auto_yes=False):
    """Interactive helper to ask user confirmation."""
    if auto_yes:
        return True
    suffix = "[Y/n]" if default else "[y/N]"
    try:
        choice = console.input(f"[bold]{esc(question)}[/] {esc(suffix)}: ").strip().lower()
        if not choice:
            return default
        return choice in ("y", "yes")
    except (EOFError, KeyboardInterrupt):
        cprint("")
        return False


def prompt_int(question, default, auto_yes=False):
    """Interactive helper to ask for a non-negative integer, falling back to
    `default` on empty/invalid input or non-interactive mode."""
    if auto_yes:
        return default
    try:
        raw = console.input(f"[bold]{esc(question)}[/] {esc(f'[{default}]')}: ").strip()
        if not raw:
            return default
        value = int(raw)
        if value < 0:
            raise ValueError("negative")
        return value
    except (EOFError, KeyboardInterrupt):
        cprint("")
        return default
    except ValueError:
        print_warning(f"Invalid number entered, using default ({default}).")
        return default

def cmd_setup(args):
    print_banner()
    auto_yes = getattr(args, "yes", False)

    console.rule("[bold]Interactive Configuration Wizard[/]")

    emu_dir = args.dir or detect_emulation_dir()
    cprint(f"[cyan]Emulation directory:[/] {esc(emu_dir)}\n")

    # --- Emulator detection (read-only, shown before any prompts) ---
    installed_emu = detect_installed_emulators()
    any_found = any(installed_emu.values())
    cprint("[cyan]Detected Emulators:[/]")
    for emu_key, emu_name in [("ryujinx", "Ryujinx (Switch)"), ("cemu", "Cemu (Wii U)")]:
        if installed_emu.get(emu_key):
            cprint(f"  [green]✔ {esc(emu_name)}[/] — save routing will be active")
        else:
            cprint(f"  [yellow]• {esc(emu_name)}[/] — not installed, routing will be skipped")
    if not any_found:
        cprint("\n  [yellow]⚠ No supported emulators detected. Install Ryujinx or Cemu and re-run setup.[/]")

    # --- Syncthing availability (read-only) ---
    syncthing_available = check_syncthing_installed()
    if not syncthing_available:
        cprint("\n[yellow]⚠ Syncthing not found in PATH — cross-machine save sync unavailable.[/]")
        cprint("  Install Syncthing and re-run [cyan]emu-stitch setup[/] to configure sync.")

    # --- Determine what's already done so we only prompt for what's needed ---
    autostart_dir = os.path.expanduser("~/.config/autostart")
    desktop_file = os.path.join(autostart_dir, "emu_stitch.desktop")
    autostart_done = os.path.exists(desktop_file)

    st_active = False
    if syncthing_available:
        st_active, _ = ensure_syncthing_service(enable=False)

    # --- Collect all answers up front, make no changes yet ---
    console.rule("[bold]Confirm changes[/]")

    do_autostart = False
    if autostart_done:
        print_success("Autostart on login: already configured")
    else:
        do_autostart = prompt_yes_no(
            "Run emu-stitch automatically on login to switch save profiles?",
            default=True, auto_yes=auto_yes,
        )

    do_syncthing_enable = False
    do_syncthing_register = False
    if syncthing_available:
        if st_active:
            print_success("Syncthing service: already running")
        else:
            do_syncthing_enable = prompt_yes_no(
                "Enable and start the Syncthing background service?",
                default=True, auto_yes=auto_yes,
            )

        do_syncthing_register = prompt_yes_no(
            "Register your save folder with Syncthing for cross-machine sync?",
            default=True, auto_yes=auto_yes,
        )

    backup_config_done = is_configured()
    backup_retention = None
    if backup_config_done:
        print_success("Backup retention: already configured")
    else:
        backup_retention = prompt_int(
            "How many old backup copies to keep per profile? (0 = keep forever)",
            default=DEFAULT_BACKUP_RETENTION, auto_yes=auto_yes,
        )

    # --- Summary of planned changes ---
    changes = []
    if not autostart_done and do_autostart:
        changes.append(f"Create autostart entry: {desktop_file}")
    if do_syncthing_enable:
        changes.append("Enable and start syncthing.service")
    if do_syncthing_register:
        changes.append("Register save folder in Syncthing")
    if not backup_config_done:
        changes.append(f"Set backup retention to {backup_retention} (0 = keep forever)")
    changes.append("Switch active save profile symlink")

    cprint("\n[bold]The following changes will be made:[/]")
    for c in changes:
        cprint(f"  • {esc(c)}")

    cprint("")
    if not prompt_yes_no("Proceed?", default=True, auto_yes=auto_yes):
        print_warning("Setup cancelled. No changes were made.")
        return

    # --- Apply changes ---
    cprint("")

    if not backup_config_done:
        set_backup_retention(backup_retention)
        print_success(f"Backup retention set to: {backup_retention} (0 = keep forever)")

    profile, path = run_switch(emu_dir)
    print_success(f"Active save profile: {profile} ({path})")

    if not autostart_done and do_autostart:
        wrapper_bin = os.path.expanduser("~/.local/bin/emu-stitch")
        os.makedirs(autostart_dir, exist_ok=True)
        with open(desktop_file, "w") as f:
            f.write(f"[Desktop Entry]\nType=Application\nName=emu-stitch Save Switcher\nExec={wrapper_bin} switch\nTerminal=false\nX-GNOME-Autostart-enabled=true\n")
        print_success(f"Autostart entry created: {desktop_file}")

    w_ok, w_msg = setup_systemd_watcher()
    if w_ok:
        print_success(f"Steam User Watcher: {w_msg}")

    if do_syncthing_enable:
        ok, msg = ensure_syncthing_service(enable=True)
        if ok:
            print_success(f"Syncthing service: {msg}")
        else:
            print_warning(f"Syncthing service: {msg}")

    if do_syncthing_register:
        st_ok, st_msg = auto_add_syncthing_folder(profile, path)
        if st_ok:
            print_success(f"Syncthing folder: {st_msg}")
        else:
            print_warning(f"Syncthing folder: {st_msg}")

    console.rule("[bold green]Setup Complete![/]", style="green")
    cprint("Run [cyan]emu-stitch audit[/] to view your system health and device status.")

def cmd_switch(args):
    emu_dir = args.dir or detect_emulation_dir()
    profile, path = run_switch(emu_dir)
    print_success(f"Active save profile set to: {profile}")
    cprint(f"  Target Path: {esc(path)}")

def cmd_pair(args):
    print_banner()
    device_id = args.device_id
    console.rule("[bold]Pairing Remote Device[/]")
    cprint(f"Target Device ID: [cyan]{esc(device_id)}[/]\n")

    if not _is_valid_syncthing_device_id(device_id):
        print_warning(
            "Invalid Device ID: expected 8 groups of 7 characters separated by hyphens "
            "(e.g. XXXXXXX-XXXXXXX-XXXXXXX-XXXXXXX-XXXXXXX-XXXXXXX-XXXXXXX-XXXXXXX)."
        )
        return

    ok, msg = auto_pair_device(device_id)
    if ok:
        print_success("Device Pairing Complete!")
        cprint(f"  {esc(msg)}")
    else:
        print_warning(f"Device Pairing Error: {msg}")

def cmd_unpair(args):
    print_banner()
    device_id = args.device_id
    console.rule("[bold]Unpairing Remote Device[/]")
    cprint(f"Target Device ID: [cyan]{esc(device_id)}[/]\n")

    if not _is_valid_syncthing_device_id(device_id):
        print_warning(
            "Invalid Device ID: expected 8 groups of 7 characters separated by hyphens "
            "(e.g. XXXXXXX-XXXXXXX-XXXXXXX-XXXXXXX-XXXXXXX-XXXXXXX-XXXXXXX-XXXXXXX)."
        )
        return

    ok, msg = remove_paired_device(device_id)
    if ok:
        print_success("Device Unpaired!")
        cprint(f"  {esc(msg)}")
    else:
        print_warning(f"Device Unpairing Error: {msg}")

def cmd_audit(args):
    emu_dir = args.dir or detect_emulation_dir()
    active_link = os.path.join(emu_dir, "saves")
    print_banner()
    cprint("[bold]Auditing EmuDeck & System Environment...[/]")

    # 1. EmuDeck Directory
    print_section_header(1, "EmuDeck Directory")
    cprint(f"   {esc(emu_dir)}")
    is_noexec, mount_pt, msg = audit_mount_permissions(emu_dir)
    if is_noexec:
        cprint(f"   [yellow]⚠ {esc(msg)}[/]")
    else:
        cprint(f"   [green]✔ {esc(msg)}[/]")

    # 2. Active Steam User Profile
    s_id, account_name = detect_active_steam_user()
    print_section_header(2, "Active Steam User Profile")
    cprint(f"   [bold]{esc(str(account_name))}[/]  (ID3: {esc(str(s_id))})")

    # 3. Save Profiles
    profiles = list_profiles(emu_dir)
    print_section_header(3, f"Save Profiles ({len(profiles)} known)")
    if not profiles:
        print_warning("No save profiles found yet. Run 'emu-stitch switch' to create one.")
    else:
        table = Table(box=box.ROUNDED, header_style="bold")
        table.add_column("NAME")
        table.add_column("STEAM ID3")
        table.add_column("ACTIVE", justify="center")
        table.add_column("RYUJINX", justify="right")
        table.add_column("CEMU", justify="right")
        for p in profiles:
            profile_games = audit_emulator_saves(p["path"])
            ryu_count = next((g["count"] for g in profile_games if g["emulator"].startswith("Ryujinx")), 0)
            cemu_count = next((g["count"] for g in profile_games if g["emulator"].startswith("Cemu")), 0)
            table.add_row(
                p["name"],
                str(p["steamid3"]) if p.get("steamid3") else "-",
                "●" if p["active"] else "",
                str(ryu_count),
                str(cemu_count),
            )
        console.print(table)

    # 4. Detected System Emulators
    installed_emu = detect_installed_emulators()
    print_section_header(4, "Detected System Emulators")
    for emu_key, emu_name in [("ryujinx", "Ryujinx (Switch)"), ("cemu", "Cemu (Wii U)")]:
        if installed_emu.get(emu_key):
            cprint(f"   • [bold]{esc(emu_name)}[/] [green]INSTALLED[/] -> Symlink routing active")
        else:
            cprint(f"   • [bold]{esc(emu_name)}[/] [yellow]NOT INSTALLED[/] -> Symlink routing skipped")

    # 5. Save Games & Profile Sync Status
    real_profile_path = os.readlink(active_link) if os.path.islink(active_link) else active_link
    sync_status, sync_msg = get_profile_sync_status(real_profile_path)
    status_style = "green" if "100%" in sync_status else "yellow"

    print_section_header(5, "Save Games & Profile Sync Status")
    cprint(f"   [{status_style}]{esc(sync_status)}[/] — {esc(sync_msg)}")

    detected_games = audit_emulator_saves(active_link)
    if not detected_games:
        print_warning("No active emulator save files detected in profile.")
    else:
        for g in detected_games:
            cprint(f"   • [bold]{esc(g['name'])}[/] [cyan]{esc(g['emulator'])}[/]")
            cprint(f"     {esc(g['details'])}")
            if "RetroArch" in g["emulator"]:
                cprint("     [yellow]Note: RetroArch saves are detected but not auto-routed. See README for setup.[/]")

    # 6. Syncthing & Device Audit
    st_ok, st_msg = ensure_syncthing_service(enable=False)
    if st_ok:
        print_section_header(6, "Syncthing & Paired Devices")
        api_key, dev_id = get_syncthing_credentials()
        if dev_id:
            cprint(f"   Device ID: [bold]{esc(dev_id)}[/]")

        devices = get_paired_devices_status()
        cprint(f"\n   Paired Remote Devices ({len(devices)} paired):")
        if not devices:
            print_warning("No remote devices paired yet. (Run 'emu-stitch pair <DEVICE-ID>' to pair).")
        else:
            table = Table(box=box.ROUNDED, header_style="bold")
            table.add_column("NAME")
            table.add_column("STATUS")
            table.add_column("ADDRESS")
            for d in devices:
                table.add_row(d["name"], "ONLINE" if d["connected"] else "OFFLINE", d["address"])
            console.print(table)
            for d in devices:
                cprint(f"     ID: {esc(d['id'])}")
    else:
        print_section_header(6, "Syncthing Status")
        cprint(f"   {esc(st_msg)}")

def main():
    parser = argparse.ArgumentParser(
        description="emu-stitch: Open-Source Multi-User Emulator Save Synchronizer"
    )
    parser.add_argument("--dir", help="Custom EmuDeck directory path")
    parser.add_argument("--version", action="version", version=f"emu-stitch {__version__}")

    subparsers = parser.add_subparsers(dest="command")

    parser_setup = subparsers.add_parser("setup", help="Interactive configuration wizard for autostart & Syncthing services")
    parser_setup.add_argument("-y", "--yes", action="store_true", help="Non-interactive setup with default 'Yes' confirmations")
    parser_setup.set_defaults(func=cmd_setup)

    parser_switch = subparsers.add_parser("switch", help="Run active profile switch and emulator link check")
    parser_switch.set_defaults(func=cmd_switch)

    parser_audit = subparsers.add_parser("audit", help="Audit system paths, mount flags, and services")
    parser_audit.set_defaults(func=cmd_audit)

    parser_pair = subparsers.add_parser("pair", help="Pair with a remote machine using its Device ID")
    parser_pair.add_argument("device_id", help="The Syncthing Device ID of the remote machine (8 groups of 7 characters, e.g. XXXXXXX-XXXXXXX-...)")
    parser_pair.set_defaults(func=cmd_pair)

    parser_unpair = subparsers.add_parser("unpair", help="Remove a previously paired remote machine using its Device ID")
    parser_unpair.add_argument("device_id", help="The Syncthing Device ID of the remote machine (8 groups of 7 characters, e.g. XXXXXXX-XXXXXXX-...)")
    parser_unpair.set_defaults(func=cmd_unpair)

    args = parser.parse_args()

    logging.basicConfig(level=logging.WARNING, format="%(levelname)s: %(message)s")

    if not args.command:
        print_banner()
        func = cmd_switch
    else:
        func = args.func

    try:
        func(args)
    except KeyboardInterrupt:
        print_warning("Interrupted.")
        sys.exit(130)
    except Exception as e:
        print_error(str(e))
        sys.exit(1)

if __name__ == "__main__":
    main()
