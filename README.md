# 🧵 emu-stitch

> **Open-source multi-user emulator save synchronizer & profile switcher for Linux handheld consoles**

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python 3.8+](https://img.shields.io/badge/python-3.9+-blue.svg)](https://www.python.org/)
[![Platform: Linux](https://img.shields.io/badge/platform-Linux-lightgrey.svg)](https://www.linux.org/)

Built for Steam Deck · ROG Ally · Legion Go · CachyOS · Bazzite · ChimeraOS

---

## What is emu-stitch?

If multiple people share a gaming handheld, their emulator save files will overwrite each other. emu-stitch fixes this by detecting which Steam user is active and atomically routing all emulator save directories to that user's isolated profile — no configuration needed after the first run.

It also keeps saves synced across multiple machines via [Syncthing](https://syncthing.net/), so your progress follows you from your Steam Deck to your desktop and back.

**Key guarantees:**
- Active save data is never deleted. Real directories are merged into the profile and then renamed to a timestamped backup before being replaced with symlinks. Old backups are pruned on a rolling basis (configurable, default: keep the last 3; set to 0 to keep every backup forever), but a backup is only ever pruned if every file in it is also in the profile, byte for byte.
- Switching profiles is atomic: `~/Emulation/saves` is repointed with a single `rename()`, so it never goes missing. (The one-time migration of an emulator's own save folder into a profile is not atomic, but it never deletes anything; see [Data safety](#data-safety).)
- Installed and run via [uv](https://docs.astral.sh/uv/) — dependencies are isolated in their own environment, no manual `pip install` or virtualenv management needed.

---

## Supported Emulators

| Emulator | Profile Switching | Save Detection | Notes |
|---|---|---|---|
| Ryujinx (Nintendo Switch) | Automatic | Yes | Routes `bis/user/save` and `saveMeta`. Native and Flatpak. See [Ryujinx save index](#ryujinx-save-index) |
| Cemu (Wii U) | Automatic | Yes | Routes `mlc01/usr/save`. Native and Flatpak |
| RetroArch / Standalone | Manual setup | Yes | See [RetroArch setup](#retroarch--standalone-emulators) |

**Flatpak emulators** are routed inside their sandbox (`~/.var/app/<app-id>/…`). The sandbox must be able to read your Emulation folder; if it lives outside your home directory (e.g. on an SD card), grant access with Flatseal or `flatpak override --user --filesystem=/run/media <app-id>`.

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
- [uv](https://docs.astral.sh/uv/getting-started/installation/) (manages the Python environment and dependencies)
- Steam installed (for user detection; native, `~/.steam`, and Flatpak installs are all detected)
- [Syncthing](https://syncthing.net/) 1.12 or newer (optional — only needed for cross-machine sync)

---

## Installation

```bash
git clone https://github.com/R-F-HansenSmith/emu-stitch.git
cd emu-stitch
./install.sh
```

This installs `emu-stitch` as an isolated [uv tool](https://docs.astral.sh/uv/guides/tools/), with an `emu-stitch` command placed on your `PATH` (typically `~/.local/bin/`). If that directory isn't on your `PATH` yet, the installer runs `uv tool update-shell`, which adds it to your shell's startup file.

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
3. Optionally install a Steam account watcher (switches profiles as soon as the Steam account changes)
4. Optionally enable the Syncthing background service
5. Optionally register your save folder with Syncthing for sync

Nothing is changed until you've answered every question and confirmed the summary.

### What emu-stitch changes on your system

Everything is per-user; nothing needs root.

| What | Where | Created by |
|---|---|---|
| Save profiles and the active `saves` symlink | `~/Emulation/saves_by_user/`, `~/Emulation/saves` | `switch` / `setup` |
| Emulator save folders replaced by symlinks (originals kept as `*.bak-*`) | `~/.config/Ryujinx/bis/user/save{,Meta}`, `~/.local/share/Cemu/mlc01/usr/save` (and Flatpak equivalents) | `switch` / `setup` |
| Settings (backup retention) | `~/.config/emu-stitch/config.json` | `setup` |
| Autostart entry | `~/.config/autostart/emu_stitch.desktop` | `setup` (if accepted) |
| Steam account watcher | `~/.config/systemd/user/emu-stitch-watcher.{path,service}` | `setup` (if accepted) |
| Syncthing user service enabled | `systemctl --user enable syncthing` | `setup` (if accepted) |
| Syncthing folders (`emustitch-<name>`) and paired devices | Syncthing's config, via its local REST API | `setup` / `pair` |
| `PATH` entry in your shell startup file | e.g. `~/.bashrc` | `install.sh` (only if needed) |

`./uninstall.sh` removes the command, autostart entry, watcher units and settings. It leaves your saves, backups and Syncthing config alone.

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
emu-stitch pair --auto-accept <DEVICE-ID>   # ...and trust it to add new synced folders here (see below)
emu-stitch unpair <DEVICE-ID> # Remove a previously paired remote machine

emu-stitch --dir /path/to/Emulation setup   # Use a custom Emulation directory
emu-stitch --debug audit      # Verbose logging and full tracebacks
```

### `emu-stitch audit` output

```
  _____ emu-stitch ______

1. EmuDeck Directory ───────────────────────────
   /home/user/Emulation
   ✔ Internal Storage (/home) - exec permissions active

2. Save Profiles (2 known) ─────────────────────
   ╭───────┬───────────┬────────┬─────────┬──────╮
   │ NAME  │ STEAM ID3 │ ACTIVE │ RYUJINX │ CEMU │
   ├───────┼───────────┼────────┼─────────┼──────┤
   │ alice │ 123456789 │   ●    │       3 │    1 │
   │ bob   │ 987654321 │        │       1 │    0 │
   ╰───────┴───────────┴────────┴─────────┴──────╯

3. Detected System Emulators ───────────────────
   • Ryujinx (Switch) INSTALLED -> Symlink routing active
   • Cemu (Wii U) NOT INSTALLED -> Symlink routing skipped

4. Save Games & Profile Sync Status ────────────
   100% IN SYNC — All files synced (142.3 MB across 87 files)
   • 3 games tracked Ryujinx (Switch)
     3 unique title(s) across 3 save record(s)

5. Syncthing & Paired Devices ──────────────────
   This Machine's Device ID: XXXXXXX-XXXXXXX-XXXXXXX-XXXXXXX-XXXXXXX-XXXXXXX-XXXXXXX-XXXXXXX

   Paired Remote Devices (1 paired):
   ╭──────────────────┬────────┬─────────────────────╮
   │ NAME             │ STATUS │ ADDRESS             │
   ├──────────────────┼────────┼─────────────────────┤
   │ deck-living-room │ ONLINE │ 192.168.1.42:22000  │
   ╰──────────────────┴────────┴─────────────────────╯

   Device IDs:
     deck-living-room  YYYYYYY-YYYYYYY-YYYYYYY-YYYYYYY-YYYYYYY-YYYYYYY-YYYYYYY-YYYYYYY
```

If Steam is logged in as a different account than the one currently active (e.g. you switched users but `emu-stitch switch` hasn't run yet), section 2 prints a warning instead of silently showing stale data.

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
2. Renames the original to `<path>.bak-YYYYMMDD-HHMMSS`
3. If every file in it was already in the profile, byte for byte, marks the backup as safe to prune. If anything differed, the backup holds the only copy of that version, so it's left unmarked, a warning is printed, and it's **never pruned automatically**.

Backups are kept on a rolling basis: only the N most recent `.bak-*` folders per path are retained, and older ones that are safe to prune are removed automatically. You're asked to set N (default 3) the first time you run `emu-stitch setup`; enter `0` to keep every backup forever. Change it later by editing `backup_retention` in `~/.config/emu-stitch/config.json`.

Profile names come from your Steam account name. If two Steam accounts would end up with the same folder name (including names that differ only by case), the second one gets its Steam ID appended, so two people never share a profile by accident.

### Ryujinx save index

Ryujinx doesn't find saves by scanning folders. It looks each game up in a machine-wide *save index* (`bis/system/save/8000000000000000`) to get the numbered folder (`0000000000000001`, …) to open. That index belongs to the Ryujinx install, not to a profile, so it is **not** switched or synced by emu-stitch.

On a single machine this works: every profile shares the same numbering. Across machines, each Ryujinx install numbers saves independently, so a synced folder can end up mapped to a different game, or not be in the index at all. `emu-stitch audit` checks every profile against the local index and warns about:

- **mismatched** folders (Ryujinx would hand that data to a different game),
- **orphaned** folders (Ryujinx will never load them), and
- folder numbers **above the last ID Ryujinx has issued** (a new save may be given the same number).

If `audit` flags anything, back up the profile before playing the affected games. Cross-machine Ryujinx sync should be treated as **experimental** for now; Cemu saves are stored by title ID and aren't affected.

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
5. Accept the device and share requests in Syncthing's web UI on machine B

`pair` shares every `emustitch-*` folder with the other device. It does **not** let that device push new folders to you unless you pass `--auto-accept`. That option makes Syncthing create any folder the device offers (by default under your home directory) without asking, so only use it between machines you fully control.

If a Syncthing folder for a profile already exists but points at a different path (for example because your Emulation folder moved to an SD card), emu-stitch won't change it. Update the path in Syncthing's web UI yourself, after checking both locations.

Each user's profile is registered as a separate Syncthing folder (`emustitch-<username>`), so alice and bob's saves sync independently.

**A note on availability:** Syncthing only transfers files while both devices are online and connected to each other — there's no store-and-forward relay of your actual data. If your handheld syncs a save while your desktop is asleep, that save just waits until both are online at the same time again.

If you want saves to always be up to date regardless of whether your other devices are awake, add a third, always-on Syncthing instance (e.g. a small home server or VM) as a hub: pair it with each of your machines and share the same `emustitch-<username>` folders with it. Each machine then only needs to be online at the same time as the always-on hub, not at the same time as each other.

---

## Environment Variables

| Variable | Default | Description |
|---|---|---|
| `EMU_DIR` | `~/Emulation` | Override the Emulation directory path |
| `EMUDECK_DIR` | — | Alternative env var for the Emulation directory |
| `SYNCTHING_URL` | Read from Syncthing's `config.xml` (usually `http://127.0.0.1:8384`) | Override the Syncthing REST API base URL. Your Syncthing API key is sent to this URL, so only point it at a Syncthing instance you trust, and use `https://` for anything that isn't on this machine |

---

## Security

Please report vulnerabilities privately; see [SECURITY.md](SECURITY.md).

---

## License

MIT License — see [LICENSE](LICENSE) for details.

*Created by R. F. Hansen-Smith*
