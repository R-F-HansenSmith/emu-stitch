"""
CLI entry point for emu-stitch: Command-line interface, terminal visualization,
state-aware setup wizard (emu-stitch setup), device pairing, and system auditing.
"""

import os
import sys
import argparse
from .detector import detect_emulation_dir, detect_active_steam_user
from .switcher import run_switch
from .fstab import audit_mount_permissions
from .emulators import audit_emulator_saves
from .syncthing import (
    ensure_syncthing_service,
    get_syncthing_credentials,
    auto_add_syncthing_folder,
    auto_pair_device,
    get_paired_devices_status,
    get_profile_sync_status
)

GREEN = "\033[92m"
YELLOW = "\033[93m"
CYAN = "\033[96m"
BOLD = "\033[1m"
RESET = "\033[0m"

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

def cmd_setup(args):
    print_banner()
    auto_yes = getattr(args, "yes", False)
    
    print(f"{BOLD}=== Interactive Configuration Wizard ==={RESET}\n")
    print(f"{CYAN}This wizard configures emu-stitch on your machine:{RESET}")
    print(" 1. Active Save Profile Symlinking & Emulator Payload Mirroring")
    print(" 2. System Boot Autostart (~/.config/autostart/emu_stitch.desktop)")
    print(" 3. Syncthing Background Service (syncthing.service)")
    print(" 4. Syncthing Save Folder REST API Registration\n")

    emu_dir = args.dir or detect_emulation_dir()
    profile, path = run_switch(emu_dir)
    print(f"{GREEN}✔ Active save profile set to:{RESET} {BOLD}{profile}{RESET} ({path})\n")

    # 1. Desktop Autostart State Check & Prompt
    autostart_dir = os.path.expanduser("~/.config/autostart")
    desktop_file = os.path.join(autostart_dir, "emu_stitch.desktop")
    wrapper_bin = os.path.expanduser("~/.local/bin/emu-stitch")

    if os.path.exists(desktop_file):
        print(f"{GREEN}✔ Desktop autostart is already configured.{RESET} ({desktop_file})\n")
    else:
        if prompt_yes_no("Do you want to enable desktop autostart on system boot?", default=True, auto_yes=auto_yes):
            os.makedirs(autostart_dir, exist_ok=True)
            with open(desktop_file, "w") as f:
                f.write(f"[Desktop Entry]\nType=Application\nName=emu-stitch Save Switcher\nExec={wrapper_bin} switch\nTerminal=false\nX-GNOME-Autostart-enabled=true\n")
            print(f"  {GREEN}✔ Desktop autostart shortcut created:{RESET} {desktop_file}\n")
        else:
            print(f"  {YELLOW}• Desktop autostart skipped.{RESET}\n")

    # 2. Syncthing Background Service State Check & Prompt
    st_active, st_msg = ensure_syncthing_service(enable=False)
    if st_active:
        print(f"{GREEN}✔ Syncthing background service is already active and running.{RESET}\n")
    else:
        if prompt_yes_no("Do you want to enable and start the Syncthing user background service?", default=True, auto_yes=auto_yes):
            ok, msg = ensure_syncthing_service(enable=True)
            if ok:
                print(f"  {GREEN}✔ Syncthing Service:{RESET} {msg}\n")
            else:
                print(f"  {YELLOW}⚠ Syncthing Service Note:{RESET} {msg}\n")
        else:
            print(f"  {YELLOW}• Syncthing background service activation skipped.{RESET}\n")

    # 3. Syncthing Folder Registration State Check & Prompt
    sync_status, _ = get_profile_sync_status(path)
    if sync_status not in ("NOT REGISTERED", "UNKNOWN"):
        print(f"{GREEN}✔ Save folder for '{profile}' is already registered in Syncthing.{RESET}\n")
    else:
        if prompt_yes_no(f"Do you want to auto-register save folder for '{profile}' in Syncthing?", default=True, auto_yes=auto_yes):
            st_ok, st_msg = auto_add_syncthing_folder(profile, path)
            if st_ok:
                print(f"  {GREEN}✔ Syncthing Folder Registration:{RESET} {st_msg}\n")
            else:
                print(f"  {YELLOW}⚠ Syncthing Folder Registration Note:{RESET} {st_msg}\n")
        else:
            print(f"  {YELLOW}• Syncthing folder registration skipped.{RESET}\n")

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

    ok, msg = auto_pair_device(device_id)
    if ok:
        print(f"{GREEN}✔ Device Pairing Complete!{RESET}")
        print(f"  {msg}")
    else:
        print(f"{YELLOW}⚠ Device Pairing Error:{RESET} {msg}")

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

    # Audit Game Saves & Sync Status
    real_profile_path = os.readlink(active_link) if os.path.islink(active_link) else active_link
    sync_status, sync_msg = get_profile_sync_status(real_profile_path)
    status_badge = f"{GREEN}{sync_status}{RESET}" if "100%" in sync_status else f"{YELLOW}{sync_status}{RESET}"
    
    print(f"\n{CYAN}3. Save Games & Profile Sync Status [{status_badge}]:{RESET}")
    print(f"   {sync_msg}")

    detected_games = audit_emulator_saves(active_link)
    if not detected_games:
        print(f"  {YELLOW}No active emulator save files detected in profile.{RESET}")
    else:
        for g in detected_games:
            print(f"  • {BOLD}{g['name']}{RESET} [{CYAN}{g['emulator']}{RESET}]")
            print(f"    {g['details']}")

    # Syncthing & Device Audit
    st_ok, st_msg = ensure_syncthing_service(enable=False)
    if st_ok:
        print(f"\n{CYAN}4. Syncthing & Paired Devices:{RESET}")
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
                print(f"     ID: {d['id'][:14]}... | Address: {d['address']}")
    else:
        print(f"\n{CYAN}4. Syncthing Status:{RESET} {st_msg}")

def main():
    parser = argparse.ArgumentParser(
        description="emu-stitch: Open-Source Multi-User Emulator Save Synchronizer"
    )
    parser.add_argument("--dir", help="Custom EmuDeck directory path")

    subparsers = parser.add_subparsers(dest="command")
    
    parser_setup = subparsers.add_parser("setup", help="Interactive configuration wizard for autostart & Syncthing services")
    parser_setup.add_argument("-y", "--yes", action="store_true", help="Non-interactive setup with default 'Yes' confirmations")
    parser_setup.set_defaults(func=cmd_setup)

    parser_switch = subparsers.add_parser("switch", help="Run active profile switch and emulator link check")
    parser_switch.set_defaults(func=cmd_switch)

    parser_audit = subparsers.add_parser("audit", help="Audit system paths, mount flags, and services")
    parser_audit.set_defaults(func=cmd_audit)

    parser_pair = subparsers.add_parser("pair", help="Pair with a remote machine using its Device ID")
    parser_pair.add_argument("device_id", help="The 52-character Syncthing Device ID of the remote machine")
    parser_pair.set_defaults(func=cmd_pair)

    args = parser.parse_args()

    if not args.command:
        print_banner()
        cmd_switch(args)
    else:
        args.func(args)

if __name__ == "__main__":
    main()
