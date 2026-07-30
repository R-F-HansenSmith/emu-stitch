# emu-stitch

**Open-Source Multi-User Emulator Save Synchronizer & Profile Switcher for Handheld Consoles**

*Created by **R. F. Hansen-Smith***

---

## Overview

`emu-stitch` is a lightweight, open-source utility designed for Linux handheld gaming consoles (Steam Deck, ROG Ally, Legion Go, CachyOS, Bazzite, ChimeraOS) and PCs.

It automatically detects the active Steam user, isolates save profiles per user, routes emulator save directories via symlinks, and synchronizes save data between machines via Syncthing.

---

## Supported Emulators

| Emulator | Profile Switching | Save Detection |
|---|---|---|
| Ryujinx (Switch) | Full automatic | Yes |
| Cemu (Wii U) | Full automatic | Yes |
| RetroArch / Standalone | Manual setup required | Yes (detection only) |

### RetroArch / Standalone Emulators

RetroArch save files (`.srm`, `.sav`, `.state`) are detected and shown in `emu-stitch audit`, but are **not** automatically symlink-routed on profile switch.

To use RetroArch with per-user profiles, configure RetroArch's **Save Files** and **Save States** directories (Settings → Directory) to point inside the emu-stitch profile directory:

```
~/Emulation/saves_by_user/<your-username>/retroarch/saves
~/Emulation/saves_by_user/<your-username>/retroarch/states
```

Once configured, emu-stitch's profile switching will cover RetroArch saves automatically because the RetroArch directories already live inside the managed profile tree.

---

## Installation & Setup

### 1. Install CLI Tool

```bash
./install.sh
```

To remove:

```bash
./uninstall.sh
```

### 2. Run Interactive Configuration Wizard

```bash
emu-stitch setup
```

*(Optionally run `emu-stitch setup -y` for automated non-interactive setup).*

---

## CLI Usage

```bash
# Interactive setup wizard (autostart, Syncthing services, folder registration)
emu-stitch setup

# Audit system environment, game saves, and live sync status
emu-stitch audit

# Perform an active profile switch and update emulator symlinks
emu-stitch switch

# Pair with a remote machine using its Syncthing Device ID
emu-stitch pair <REMOTE-DEVICE-ID>

# Specify a custom EmuDeck directory path
emu-stitch --dir /path/to/Custom/Emulation setup
```

---

## How It Works

1. **Profile detection** — reads Steam's `loginusers.vdf` to determine the active user.
2. **Atomic switch** — repoints `~/Emulation/saves` (a symlink) to the active user's isolated directory under `~/Emulation/saves_by_user/<username>/`.
3. **Emulator routing** — rewires Ryujinx and Cemu save paths to point into the active profile. Only runs for installed emulators.
4. **Syncthing sync** — manages a Syncthing background service and registers each profile directory as a shared folder for cross-machine sync.

No save data is ever deleted. When emu-stitch replaces a real directory with a symlink, the original is renamed to a timestamped backup (`*.bak-YYYYMMDD-HHMMSS`).

---

## License

Distributed under the **MIT License**. Created by **R. F. Hansen-Smith**.
