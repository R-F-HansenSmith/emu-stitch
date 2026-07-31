"""
CLI entry point for emu-stitch: Command-line interface, terminal visualization,
state-aware setup wizard, smart emulator auditing, device pairing, and system health checks.
"""

import os
import re
import sys
import logging
import argparse
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

GREEN = "\033[92m"
YELLOW = "\033[93m"
CYAN = "\033[96m"
BOLD = "\033[1m"
RESET = "\033[0m"

# A Syncthing Device ID is 8 groups of 7 base32 characters (A-Z, 2-7), joined by hyphens.
_DEVICE_ID_RE = re.compile(r"^[A-Z2-7]{7}(-[A-Z2-7]{7}){7}$")


def _is_valid_syncthing_device_id(device_id: str) -> bool:
    return bool(_DEVICE_ID_RE.match(device_id.upper()))

def print_banner():
    art = r"""
  _____                        ____   _   _  _   _       _     
 | ____|_ __ ___  _   _       / ___| | |_(_)| |_ ___| |__  
 |  _| | '_ ` _ \| | | |  ___  \___ \ | __| | __/ __| '_ \ 
 | |___| | | | | | |_| | |___|  ___) || |_| | || (__| | | |
 |_____|_| |_| |_|\__,_|       |____/  \__|_|\__\___|_| |_|
"""
    print(f"{CYAN}{BOLD}{art}{RESET}")
    print(f"{YELLOW} Open-Source Multi-User Emulator Save Synchronizer & Profile Switcher{RESET}\n")

def prompt_yes_no(question, default=True, auto_yes=False):
    """Interactive helper to ask user confirmation."""
    if auto_yes:
        return True
    suffix = " [Y/n]: " if default else " [y/N]: "
    try:
        choice = input(f"{BOLD}{question}{RESET}{suffix}").strip().lower()
        if not choice:
            return default
        return choice in ("y", "yes")
    except (EOFError, KeyboardInterrupt):
        print()
        return False

def prompt_int(question, default, auto_yes=False):
    """Interactive helper to ask for a non-negative integer, falling back to
    `default` on empty/invalid input or non-interactive mode."""
    if auto_yes:
        return default
    try:
        raw = input(f"{BOLD}{question}{RESET} [{default}]: ").strip()
        if not raw:
            return default
        value = int(raw)
        if value < 0:
            raise ValueError("negative")
        return value
    except (EOFError, KeyboardInterrupt):
        print()
        return default
    except ValueError:
        print(f"{YELLOW}Invalid number entered, using default ({default}).{RESET}")
        return default

def cmd_setup(args):
    print_banner()
    auto_yes = getattr(args, "yes", False)

    print(f"{BOLD}=== Interactive Configuration Wizard ==={RESET}\n")

    emu_dir = args.dir or detect_emulation_dir()
    print(f"{CYAN}Emulation directory:{RESET} {emu_dir}\n")

    # --- Emulator detection (read-only, shown before any prompts) ---
    installed_emu = detect_installed_emulators()
    any_found = any(installed_emu.values())
    print(f"{CYAN}Detected Emulators:{RESET}")
    for emu_key, emu_name in [("ryujinx", "Ryujinx (Switch)"), ("cemu", "Cemu (Wii U)")]:
        if installed_emu.get(emu_key):
            print(f"  {GREEN}✔ {emu_name}{RESET} — save routing will be active")
        else:
            print(f"  {YELLOW}• {emu_name}{RESET} — not installed, routing will be skipped")
    if not any_found:
        print(f"\n  {YELLOW}⚠ No supported emulators detected. Install Ryujinx or Cemu and re-run setup.{RESET}")

    # --- Syncthing availability (read-only) ---
    syncthing_available = check_syncthing_installed()
    if not syncthing_available:
        print(f"\n{YELLOW}⚠ Syncthing not found in PATH — cross-machine save sync unavailable.{RESET}")
        print(f"  Install Syncthing and re-run {CYAN}emu-stitch setup{RESET} to configure sync.")

    # --- Determine what's already done so we only prompt for what's needed ---
    autostart_dir = os.path.expanduser("~/.config/autostart")
    desktop_file = os.path.join(autostart_dir, "emu_stitch.desktop")
    autostart_done = os.path.exists(desktop_file)

    st_active = False
    if syncthing_available:
        st_active, _ = ensure_syncthing_service(enable=False)

    # --- Collect all answers up front, make no changes yet ---
    print(f"\n{BOLD}--- Confirm changes ---{RESET}\n")

    do_autostart = False
    if autostart_done:
        print(f"{GREEN}✔ Autostart on login:{RESET} already configured\n")
    else:
        do_autostart = prompt_yes_no(
            "Run emu-stitch automatically on login to switch save profiles?",
            default=True, auto_yes=auto_yes,
        )

    do_syncthing_enable = False
    do_syncthing_register = False
    if syncthing_available:
        if st_active:
            print(f"{GREEN}✔ Syncthing service:{RESET} already running\n")
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
        print(f"{GREEN}✔ Backup retention:{RESET} already configured\n")
    else:
        backup_retention = prompt_int(
            "How many old backup copies to keep per profile? (0 = keep forever)",
            default=DEFAULT_BACKUP_RETENTION, auto_yes=auto_yes,
        )

    # --- Summary of planned changes ---
    changes = []
    if not autostart_done and do_autostart:
        changes.append(f"  • Create autostart entry: {desktop_file}")
    if do_syncthing_enable:
        changes.append("  • Enable and start syncthing.service")
    if do_syncthing_register:
        changes.append("  • Register save folder in Syncthing")
    if not backup_config_done:
        changes.append(f"  • Set backup retention to {backup_retention} (0 = keep forever)")
    changes.append("  • Switch active save profile symlink")

    print(f"\n{BOLD}The following changes will be made:{RESET}")
    for c in changes:
        print(c)

    print()
    if not prompt_yes_no("Proceed?", default=True, auto_yes=auto_yes):
        print(f"\n{YELLOW}Setup cancelled. No changes were made.{RESET}")
        return

    # --- Apply changes ---
    print()

    if not backup_config_done:
        set_backup_retention(backup_retention)
        print(f"{GREEN}✔ Backup retention set to:{RESET} {backup_retention} (0 = keep forever)\n")

    profile, path = run_switch(emu_dir)
    print(f"{GREEN}✔ Active save profile:{RESET} {BOLD}{profile}{RESET} ({path})\n")

    if not autostart_done and do_autostart:
        wrapper_bin = os.path.expanduser("~/.local/bin/emu-stitch")
        os.makedirs(autostart_dir, exist_ok=True)
        with open(desktop_file, "w") as f:
            f.write(f"[Desktop Entry]\nType=Application\nName=emu-stitch Save Switcher\nExec={wrapper_bin} switch\nTerminal=false\nX-GNOME-Autostart-enabled=true\n")
        print(f"{GREEN}✔ Autostart entry created:{RESET} {desktop_file}\n")

    w_ok, w_msg = setup_systemd_watcher()
    if w_ok:
        print(f"{GREEN}✔ Steam User Watcher:{RESET} {w_msg}\n")

    if do_syncthing_enable:
        ok, msg = ensure_syncthing_service(enable=True)
        if ok:
            print(f"{GREEN}✔ Syncthing service:{RESET} {msg}\n")
        else:
            print(f"{YELLOW}⚠ Syncthing service:{RESET} {msg}\n")

    if do_syncthing_register:
        st_ok, st_msg = auto_add_syncthing_folder(profile, path)
        if st_ok:
            print(f"{GREEN}✔ Syncthing folder:{RESET} {st_msg}\n")
        else:
            print(f"{YELLOW}⚠ Syncthing folder:{RESET} {st_msg}\n")

    print(f"{GREEN}{BOLD}=== Setup Complete! ==={RESET}")
    print(f"Run {CYAN}emu-stitch audit{RESET} to view your system health and device status.")

def cmd_switch(args):
    emu_dir = args.dir or detect_emulation_dir()
    profile, path = run_switch(emu_dir)
    print(f"\n{GREEN}✔ Active save profile set to:{RESET} {BOLD}{profile}{RESET}")
    print(f"  Target Path: {path}")

def cmd_pair(args):
    print_banner()
    device_id = args.device_id
    print(f"{BOLD}Pairing Remote Device...{RESET}\n")
    print(f"Target Device ID: {CYAN}{device_id}{RESET}\n")

    if not _is_valid_syncthing_device_id(device_id):
        print(f"{YELLOW}⚠ Invalid Device ID:{RESET} expected 8 groups of 7 characters separated by hyphens (e.g. XXXXXXX-XXXXXXX-XXXXXXX-XXXXXXX-XXXXXXX-XXXXXXX-XXXXXXX-XXXXXXX).")
        return

    ok, msg = auto_pair_device(device_id)
    if ok:
        print(f"{GREEN}✔ Device Pairing Complete!{RESET}")
        print(f"  {msg}")
    else:
        print(f"{YELLOW}⚠ Device Pairing Error:{RESET} {msg}")

def cmd_unpair(args):
    print_banner()
    device_id = args.device_id
    print(f"{BOLD}Unpairing Remote Device...{RESET}\n")
    print(f"Target Device ID: {CYAN}{device_id}{RESET}\n")

    if not _is_valid_syncthing_device_id(device_id):
        print(f"{YELLOW}⚠ Invalid Device ID:{RESET} expected 8 groups of 7 characters separated by hyphens (e.g. XXXXXXX-XXXXXXX-XXXXXXX-XXXXXXX-XXXXXXX-XXXXXXX-XXXXXXX-XXXXXXX).")
        return

    ok, msg = remove_paired_device(device_id)
    if ok:
        print(f"{GREEN}✔ Device Unpaired!{RESET}")
        print(f"  {msg}")
    else:
        print(f"{YELLOW}⚠ Device Unpairing Error:{RESET} {msg}")

def cmd_audit(args):
    emu_dir = args.dir or detect_emulation_dir()
    active_link = os.path.join(emu_dir, "saves")
    print_banner()
    print(f"{BOLD}Auditing EmuDeck & System Environment...{RESET}\n")

    print(f"{CYAN}1. EmuDeck Directory:{RESET} {emu_dir}")
    
    is_noexec, mount_pt, msg = audit_mount_permissions(emu_dir)
    if is_noexec:
        print(f"{YELLOW}⚠ Mount Audit:{RESET} {msg}")
    else:
        print(f"{GREEN}✔ Mount Audit:{RESET} {msg}")

    s_id, account_name = detect_active_steam_user()
    print(f"{CYAN}2. Active Steam User Profile:{RESET} {BOLD}{account_name}{RESET} (ID3: {s_id})")

    # Save Profiles Audit
    profiles = list_profiles(emu_dir)
    print(f"\n{CYAN}3. Save Profiles ({len(profiles)} known):{RESET}")
    if not profiles:
        print(f"  {YELLOW}No save profiles found yet.{RESET} Run 'emu-stitch switch' to create one.")
    else:
        for p in profiles:
            marker = f" {GREEN}(ACTIVE){RESET}" if p["active"] else ""
            steamid_note = f" [ID3: {p['steamid3']}]" if p.get("steamid3") else ""
            profile_games = audit_emulator_saves(p["path"])
            ryu_count = next((g["count"] for g in profile_games if g["emulator"].startswith("Ryujinx")), 0)
            cemu_count = next((g["count"] for g in profile_games if g["emulator"].startswith("Cemu")), 0)
            print(f"  • {BOLD}{p['name']}{RESET}{marker}{steamid_note} — {ryu_count} Ryujinx, {cemu_count} Cemu")

    # Installed Emulators Audit
    installed_emu = detect_installed_emulators()
    print(f"\n{CYAN}4. Detected System Emulators:{RESET}")
    for emu_key, emu_name in [("ryujinx", "Ryujinx (Switch)"), ("cemu", "Cemu (Wii U)")]:
        if installed_emu.get(emu_key):
            print(f"  • {BOLD}{emu_name}{RESET} [{GREEN}INSTALLED{RESET}] -> Symlink routing active")
        else:
            print(f"  • {BOLD}{emu_name}{RESET} [{YELLOW}NOT INSTALLED{RESET}] -> Symlink routing skipped")

    # Audit Game Saves & Sync Status
    real_profile_path = os.readlink(active_link) if os.path.islink(active_link) else active_link
    sync_status, sync_msg = get_profile_sync_status(real_profile_path)
    status_badge = f"{GREEN}{sync_status}{RESET}" if "100%" in sync_status else f"{YELLOW}{sync_status}{RESET}"
    
    print(f"\n{CYAN}5. Save Games & Profile Sync Status [{status_badge}]:{RESET}")
    print(f"   {sync_msg}")

    detected_games = audit_emulator_saves(active_link)
    if not detected_games:
        print(f"  {YELLOW}No active emulator save files detected in profile.{RESET}")
    else:
        for g in detected_games:
            print(f"  • {BOLD}{g['name']}{RESET} [{CYAN}{g['emulator']}{RESET}]")
            print(f"    {g['details']}")
            if "RetroArch" in g["emulator"]:
                print(f"    {YELLOW}Note: RetroArch saves are detected but not auto-routed. See README for setup.{RESET}")

    # Syncthing & Device Audit
    st_ok, st_msg = ensure_syncthing_service(enable=False)
    if st_ok:
        print(f"\n{CYAN}6. Syncthing & Paired Devices:{RESET}")
        api_key, dev_id = get_syncthing_credentials()
        if dev_id:
            print(f"   This Machine's Device ID:\n   {BOLD}{dev_id}{RESET}")
            
        devices = get_paired_devices_status()
        print(f"\n   Paired Remote Devices ({len(devices)} paired):")
        if not devices:
            print(f"   {YELLOW}No remote devices paired yet.{RESET} (Run 'emu-stitch pair <DEVICE-ID>' to pair).")
        else:
            for d in devices:
                status_icon = f"{GREEN}ONLINE{RESET}" if d["connected"] else f"{YELLOW}OFFLINE{RESET}"
                print(f"   • {BOLD}{d['name']}{RESET} [{status_icon}]")
                print(f"     ID: {d['id']} | Address: {d['address']}")
    else:
        print(f"\n{CYAN}6. Syncthing Status:{RESET} {st_msg}")

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
        print(f"\n{YELLOW}Interrupted.{RESET}")
        sys.exit(130)
    except Exception as e:
        print(f"\n{YELLOW}Error:{RESET} {e}", file=sys.stderr)
        sys.exit(1)

if __name__ == "__main__":
    main()
