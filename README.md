# 🧵 emu-stitch

> **Open-source multi-user emulator save synchronizer & profile switcher for Linux gaming PCs and handhelds**

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python 3.9+](https://img.shields.io/badge/python-3.9+-blue.svg)](https://www.python.org/)
[![Platform: Linux](https://img.shields.io/badge/platform-Linux-lightgrey.svg)](https://www.linux.org/)

Works on any Linux machine with Steam: Steam Deck · ROG Ally · Legion Go · desktops · CachyOS · Bazzite · ChimeraOS

---

## What is emu-stitch?

If several people share a Linux gaming machine (a handheld, a living-room PC, a family desktop), their emulator saves overwrite each other. emu-stitch fixes this by detecting which Steam account is active and routing every emulator's save folders to that person's own profile, switching atomically whenever the Steam account changes. After the first run, no configuration is needed.

It also keeps saves synced across machines via [Syncthing](https://syncthing.net/), so your progress follows you from your handheld to your desktop and back. That's useful even if you're the only player.

emu-stitch works alongside [EmuDeck](https://www.emudeck.com/) and uses its `~/Emulation` folder layout, but EmuDeck isn't required: without it, emu-stitch simply creates `~/Emulation/saves_by_user/` (or uses any folder you point it at with `--dir`).

**Key guarantees:**
- Active save data is never deleted. Real directories are merged into the profile and then renamed to a timestamped backup before being replaced with symlinks. Old backups are pruned on a rolling basis (configurable, default: keep the last 3; set to 0 to keep every backup forever), but a backup is only ever pruned if every file in it is also in the profile, byte for byte, both when it's made and again right before it's pruned.
- Switching profiles is atomic: `~/Emulation/saves` is repointed with a single `rename()`, so it never goes missing. (The one-time migration of an emulator's own save folder into a profile is not atomic, but it never deletes anything; see [Data safety](#data-safety).)
- Installed and run via [uv](https://docs.astral.sh/uv/) — dependencies are isolated in their own environment, no manual `pip install` or virtualenv management needed.

---

## Supported Emulators

| Emulator | Profile Switching | Save Detection | Notes |
|---|---|---|---|
| Ryujinx (Nintendo Switch) | Automatic | Yes | Routes `bis/user/save`, `saveMeta` and the save index. Native and Flatpak. See [Ryujinx save index](#ryujinx-save-index) |
| Cemu (Wii U) | Automatic | Yes | Routes `mlc01/usr/save`. Native and Flatpak. Not switched while Cemu is running |
| RetroArch / Standalone | Manual setup | Yes | See [RetroArch setup](#retroarch--standalone-emulators) |

**Running emulators are left alone.** If Ryujinx or Cemu is running when you switch, `switch` doesn't touch that emulator's saves (an open save could be mid-write) and tells you to close it and run `emu-stitch switch` again. The watcher and autostart entry run `switch` for you the next time the account changes or you log in.

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
- Ryujinx and/or Cemu, installed any way you like (native, AppImage, Flatpak or via EmuDeck)
- EmuDeck is optional
- [Syncthing](https://syncthing.net/) 1.12 or newer (optional — only needed for cross-machine sync)

---

## Installation

> [!IMPORTANT]
> **Back up your saves before installing or running `setup`.** emu-stitch never deletes save data on its own, but it does move save folders around. A copy you made yourself is the only thing that protects you from a bug, a mistake, or a bad sync. This saves every location emu-stitch touches (native and Flatpak) into one archive:
>
> ```bash
> tar --ignore-failed-read -czf ~/emu-saves-backup-$(date +%Y%m%d).tar.gz -C ~ \
>   .config/Ryujinx/bis .var/app/org.ryujinx.Ryujinx/config/Ryujinx/bis \
>   .local/share/Cemu/mlc01/usr/save .var/app/info.cemu.Cemu/data/Cemu/mlc01/usr/save \
>   Emulation/saves Emulation/saves_by_user
> ```
>
> "Cannot stat" warnings just mean you don't have that emulator or install type. If your Emulation folder isn't `~/Emulation`, adjust the last line. Keep the archive somewhere Syncthing doesn't sync.

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
3. Optionally install a watcher that switches profiles as soon as the Steam account changes, and picks up Ryujinx saves synced in from other machines
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
| Ryujinx's save index replaced by a symlink into the profile (original kept as `*.bak-*`); its save counter set to this machine's range | `~/.config/Ryujinx/bis/system/save/8000000000000000` (and Flatpak equivalent) | `switch` / `setup` |
| Profile `.stignore`: skips lock files, Ryujinx's save counter and temporary commit folders | `saves_by_user/<name>/.stignore` | `switch` / `setup` |
| Merged-away Ryujinx index conflict copies | `~/.local/state/emu-stitch/ryujinx-index-conflicts/` | `switch` (only after a sync conflict) |
| Autostart entry | `~/.config/autostart/emu_stitch.desktop` | `setup` (if accepted) |
| Watcher: runs `switch` when the Steam account changes, or when Ryujinx saves arrive from another machine | `~/.config/systemd/user/emu-stitch-watcher.{path,service}` | `setup` (if accepted) |
| Syncthing user service enabled | `systemctl --user enable syncthing` | `setup` (if accepted) |
| Syncthing folders (`emustitch-<name>`) and paired devices | Syncthing's config, via its local REST API | `setup` / `pair`, and `switch` for new profiles if sync is enabled |
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
emu-stitch ryujinx-reindex    # Repair: rebuild the active profile's Ryujinx save index (see below)

emu-stitch --dir /path/to/Emulation setup   # Use a custom Emulation directory
emu-stitch --debug audit      # Verbose logging and full tracebacks
```

### `emu-stitch audit` output

```
  _____ emu-stitch ______

1. Emulation Directory ───────────────────────────
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
       ├── ~/.config/Ryujinx/bis/system/save/8000000000000000  ──▶  .../alice/ryujinx/saveIndex/
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
    │   │   ├── saveMeta/
    │   │   └── saveIndex/                  ← Ryujinx's save index for this profile
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
3. If every file in it was already in the profile, byte for byte, marks the backup as safe to prune and records which profile folder it was merged into. If anything differed, the backup holds the only copy of that version, so it's left unmarked, a warning is printed, and it's **never pruned automatically**.

Backups are kept on a rolling basis: only the N most recent `.bak-*` folders per path are retained, and older ones that are safe to prune are removed automatically. Right before pruning, emu-stitch compares the backup with its profile folder again, byte for byte. If any file in the backup has since changed or gone missing in the profile (corruption, a bad sync, or simply newer progress; emu-stitch can't tell these apart), the backup is kept. Backups marked by versions before this check don't record their profile, so they're kept too. You're asked to set N (default 3) the first time you run `emu-stitch setup`; enter `0` to keep every backup forever. Change it later by editing `backup_retention` in `~/.config/emu-stitch/config.json`.

Profile names come from your Steam account name. If two Steam accounts would end up with the same folder name (including names that differ only by case), the second one gets its Steam ID appended, so two people never share a profile by accident.

### Ryujinx save index

Ryujinx doesn't find saves by scanning folders. It looks each game up in a *save index* (`bis/system/save/8000000000000000`) to get the numbered folder (`0000000000000001`, …) to open, and it numbers new saves from a counter stored next to that index. emu-stitch handles this automatically:

- **The index belongs to the profile.** It's kept in `saves_by_user/<name>/ryujinx/saveIndex` and swapped in on every switch, just like the saves. From Ryujinx's point of view there is only ever one user, and their saves and index always match. The first time a profile becomes active, it starts from a copy of the index that was in place, so existing saves stay findable.
- **Each machine numbers new saves from its own range.** The counter is excluded from Syncthing, and each machine (and each Ryujinx install on it) starts counting from its own range, derived from `/etc/machine-id`. Two machines can never give the same folder number to two different games, even if both start a new game while they aren't syncing. Saves numbered before emu-stitch managed Ryujinx keep their numbers.
- **Conflicts are merged, newest save wins.** If both machines add saves while apart, Syncthing keeps both copies of the index. On the next `switch` (or as soon as the copies arrive, if the watcher is enabled) emu-stitch merges them. If the same game was started on both machines, it uses the most recently played save; the other copy stays on disk, untouched, and `audit` lists it.
- **Nothing happens while Ryujinx is running.** Ryujinx keeps the index in memory, so `switch` leaves Ryujinx alone until it's closed, and says so.

**Setting up a new machine:** if Ryujinx has never been launched there, launch it once *without starting a game*, close it, then run `emu-stitch switch`. That gives the machine its own save numbers before any saves exist. `setup` reminds you if this is needed.

**Checking and repairing.** `emu-stitch audit` checks the active profile against its index and warns about folders Ryujinx would load for the wrong game, never load, or could reuse. That shouldn't happen with the steps above. It can show up once for saves from before this version, or if a second machine brought its own pre-existing Ryujinx saves. To fix it, close Ryujinx and run:

```bash
emu-stitch ryujinx-reindex
```

This rebuilds the active profile's index from its own save folders (each folder's `ExtraData` records exactly which game and save it belongs to). It shows every change and asks first, and backs up the index outside the synced folder. Where a profile has several folders for the same save, it uses the most recently played one and leaves the others untouched. Because the index belongs to the profile, a repair is permanent: switching profiles doesn't undo it.

**Limitations:**

- If a second machine already has its own Ryujinx saves when you first set it up, those saves are numbered from `0001` like the first machine's. Where the numbers overlap, the first machine's saves win and the second machine's are kept in a backup (`bis/user/save.bak-*`, never pruned automatically) with a warning. Nothing is lost, but those saves have to be moved in by hand.
- Playing the *same* save on two machines while they aren't syncing produces Syncthing conflicts, as it does for every emulator.

> **New in 1.1.0.** Tested across two machines with real Ryujinx saves, and covered by tests including a byte-for-byte round trip of a real Ryujinx index. As always, [back up your saves](#installation) first.

Cemu saves are stored by title ID and don't need any of this.

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

Each user's profile is registered as a separate Syncthing folder (`emustitch-<username>`), so alice and bob's saves sync independently. If you enabled sync in `setup`, profiles created later (a new Steam account on the handheld) are registered on their first `switch` and shared with the same devices as your other profiles.

**Syncing is not a backup.** Syncthing copies deletions and mistakes to every device just as faithfully as it copies progress, so keep separate backups (see [Installation](#installation)).

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

## AI Disclosure

emu-stitch was built with the help of AI (Anthropic's Claude), which wrote a substantial part of the code, tests and documentation. The project is designed, directed and tested on real hardware by R. F. Hansen-Smith, a software engineer with 10 years of experience.

---

## License

MIT License — see [LICENSE](LICENSE) for details.

*Created by R. F. Hansen-Smith*
