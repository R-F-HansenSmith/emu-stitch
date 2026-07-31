# 🧵 emu-stitch

> **Open-source multi-user emulator save synchronizer & profile switcher for Linux handheld consoles**

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python 3.8+](https://img.shields.io/badge/python-3.8+-blue.svg)](https://www.python.org/)
[![Platform: Linux](https://img.shields.io/badge/platform-Linux-lightgrey.svg)](https://www.linux.org/)

Built for Steam Deck · ROG Ally · Legion Go · CachyOS · Bazzite · ChimeraOS

---

## What is emu-stitch?

If multiple people share a gaming handheld, their emulator save files will overwrite each other. emu-stitch fixes this by detecting which Steam user is active and atomically routing all emulator save directories to that user's isolated profile — no configuration needed after the first run.

It also keeps saves synced across multiple machines via [Syncthing](https://syncthing.net/), so your progress follows you from your Steam Deck to your desktop and back.

**Key guarantees:**
- No save data is ever deleted. Real directories are renamed to a timestamped backup before being replaced with symlinks.
- Profile switches are atomic. There is no window where a save directory doesn't exist.
- Zero runtime dependencies. Pure Python stdlib — works on any Linux image without `pip install`.

---

## Supported Emulators

| Emulator | Profile Switching | Save Detection | Notes |
|---|---|---|---|
| Ryujinx (Nintendo Switch) | Automatic | Yes | Routes `bis/user/save` and `saveMeta` |
| Cemu (Wii U) | Automatic | Yes | Routes `mlc01/usr/save` |
| RetroArch / Standalone | Manual setup | Yes | See [RetroArch setup](#retroarch--standalone-emulators) |

### RetroArch / Standalone Emulators

RetroArch save files (`.srm`, `.sav`, `.state`) are detected and shown in `emu-stitch audit`, but save directories are not automatically relinked on switch.

To get per-user RetroArch profiles, point RetroArch's save directories at your emu-stitch profile tree (**Settings → Directory**):

```
Save Files:   ~/Emulation/saves_by_user/<username>/retroarch/saves
Save States:  ~/Emulation/saves_by_user/<username>/retroarch/states
```

Once configured, emu-stitch's profile switch covers RetroArch automatically because those directories already live inside the managed profile tree.

---

## Requirements

- Linux (Steam Deck / SteamOS, Arch, Bazzite, CachyOS, ChimeraOS, or any distro)
- Python 3.8+
- Steam installed (for user detection)
- [Syncthing](https://syncthing.net/) installed (optional — only needed for cross-machine sync)

---

## Installation

```bash
git clone https://github.com/R-F-HansenSmith/emu-stitch.git
cd emu-stitch
./install.sh
```

This places the `emu-stitch` binary in `~/.local/bin/` and the package in `~/.local/share/emu-stitch/`.

To uninstall:

```bash
./uninstall.sh
```

### First-time setup

Run the interactive wizard once after installing:

```bash
emu-stitch setup
```

This will:
1. Detect your active Steam profile and create your first save profile
2. Optionally enable desktop autostart (runs `emu-stitch switch` on login)
3. Optionally enable the Syncthing background service
4. Optionally register your save folder with Syncthing for sync

For fully automated setup (e.g. in a script):

```bash
emu-stitch setup -y
```

---

## CLI Reference

```bash
emu-stitch setup              # Interactive first-time setup wizard
emu-stitch setup -y           # Non-interactive setup (auto-yes to all prompts)

emu-stitch switch             # Switch to the active Steam user's save profile
emu-stitch audit              # Show full system health: saves, sync status, devices
emu-stitch pair <DEVICE-ID>   # Pair with a remote machine for save sync

emu-stitch --dir /path/to/Emulation setup   # Use a custom Emulation directory
```

### `emu-stitch audit` output

```
  _____ emu-stitch ______

1. EmuDeck Directory:        /home/user/Emulation
   Mount Audit:              Internal Storage (/home) — exec permissions active

2. Active Steam User:        alice (ID3: 123456789)

3. Detected System Emulators:
   • Ryujinx (Switch)        [INSTALLED] → Symlink routing active
   • Cemu (Wii U)            [NOT INSTALLED] → Symlink routing skipped

4. Save Games & Profile Sync Status [100% IN SYNC]:
   All files synced (142.3 MB across 87 files)
   • Switch Game Save Data   [Ryujinx (Switch)]
     3 save ID index(es) active

5. Syncthing & Paired Devices:
   This Machine's Device ID: XXXXXXX-XXXXXXX-XXXXXXX-XXXXXXX-XXXXXXX-XXXXXXX-XXXXXXX-XXXXXXX
   Paired Remote Devices (1 paired):
   • deck-living-room        [ONLINE]
     ID: YYYYYYY-YYYYYYY-YYYYYYY-YYYYYYY-YYYYYYY-YYYYYYY-YYYYYYY-YYYYYYY  |  Address: 192.168.1.42:22000
```

---

## How It Works

```
Steam login detected
       │
       ▼
loginusers.vdf parsed → active user: alice
       │
       ▼
~/Emulation/saves  ──symlink──▶  ~/Emulation/saves_by_user/alice/
       │
       ├── ~/.config/Ryujinx/bis/user/save  ──▶  .../alice/ryujinx/saves/
       ├── ~/.config/Ryujinx/bis/user/saveMeta  ──▶  .../alice/ryujinx/saveMeta/
       └── ~/.local/share/Cemu/mlc01/usr/save  ──▶  .../alice/Cemu/saves/
```

1. **Detect** — reads `~/.local/share/Steam/config/loginusers.vdf` to find the most-recently-active Steam account, falling back to `userdata/` modification time.
2. **Switch** — atomically repoints `~/Emulation/saves` to `saves_by_user/<username>/` using `os.replace()` (POSIX-atomic, no window where the link doesn't exist).
3. **Route** — rewires emulator-specific save paths (Ryujinx, Cemu) into the active profile directory. Only touches emulators that are actually installed.
4. **Sync** — manages a Syncthing user service via systemd, registers the profile directory via the Syncthing REST API, and can pair remote devices.

### Profile directory layout

```
~/Emulation/
├── saves  → saves_by_user/alice/          ← active symlink
└── saves_by_user/
    ├── alice/
    │   ├── .stignore                       ← ignores *.lock during sync
    │   ├── ryujinx/
    │   │   ├── saves/
    │   │   └── saveMeta/
    │   ├── Cemu/
    │   │   └── saves/
    │   └── retroarch/                      ← manual setup only
    └── bob/
        └── ...
```

### Data safety

When emu-stitch needs to replace a real directory with a symlink (first run or emulator config migration), it:
1. Merges the contents into the new profile directory (destination files win — existing saves are never overwritten)
2. Renames the original to `<path>.bak-YYYYMMDD-HHMMSS` — never deletes it

---

## Cross-machine Sync with Syncthing

emu-stitch uses [Syncthing](https://syncthing.net/) to sync saves between machines (e.g. Steam Deck ↔ desktop).

**Setup:**

1. Install Syncthing on both machines
2. Run `emu-stitch setup` on each machine to register save folders
3. Find the Device ID on machine B: `emu-stitch audit` (shown in section 5)
4. On machine A, pair with machine B:
   ```bash
   emu-stitch pair <MACHINE-B-DEVICE-ID>
   ```
5. Accept the share request in Syncthing's UI on machine B

Each user's profile is registered as a separate Syncthing folder (`emustitch-<username>`), so alice and bob's saves sync independently.

---

## Environment Variables

| Variable | Default | Description |
|---|---|---|
| `EMU_DIR` | `~/Emulation` | Override the Emulation directory path |
| `EMUDECK_DIR` | — | Alternative env var for the Emulation directory |
| `SYNCTHING_URL` | `http://127.0.0.1:8384` | Override the Syncthing REST API base URL |

---

## License

MIT License — see [LICENSE](LICENSE) for details.

*Created by R. F. Hansen-Smith*
