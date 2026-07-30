# 🧵 emu-stitch

**Open-Source Multi-User Emulator Save Synchronizer & Profile Switcher for Handheld Consoles**

*Created by **R. F. Hansen-Smith***

---

## 🌟 Overview

`emu-stitch` is a lightweight, open-source, atomic, and idempotent CLI utility designed for Linux handheld gaming consoles (Steam Deck, ROG Ally, Legion Go, CachyOS, Bazzite, ChimeraOS) and desktop PCs.

It turns multi-user, multi-machine emulator save management into a plug-and-play experience by automatically discovering active Steam user accounts, isolating save profiles dynamically, and routing emulator save directories seamlessly to synchronization backends (Syncthing, with a modular roadmap for Cloud Storage like Google Drive via `rclone`).

---

## ✨ Key Features

- **🔒 Generic Multi-User Profile Isolation:** Dynamically reads Steam's `loginusers.vdf` (using `MostRecent` flags & epoch `Timestamp` sorting) and `userdata/` `mtime` fallbacks to discover active users on the fly (e.g. `Player_1`, `Player_2`, `Gamer_Tag`). No hardcoded usernames.
- **📂 Smart Storage Path Discovery:** Prioritizes internal handheld storage (`~/Emulation`) first, followed by SD cards and external mounts (`/run/media/*`, `/mnt/*`, `/media/*`). Accepts custom path overrides via `--dir`.
- **🎮 Emulator Save Routing & Anti-Loop Safeguards:**
  - **Ryujinx:** Dynamically routes `bis/user/save` and `bis/user/saveMeta`, preserves `bis/system/save` as a native directory (preventing circular symlink loops), and auto-mirrors save payloads across all numerical save folders (`00000000...`) & profile slots (`0` and `1`).
  - **Cemu:** Dynamically routes `mlc01/usr/save`.
- **🛡️ Non-Intrusive Mount Auditor:** Detects if an external drive mount contains `noexec` flags (which can block AppImages like Steam ROM Manager) and provides optional user guidance.
- **🔄 Syncthing Integration:** Enables `syncthing.service`, creates per-profile shared folders, and auto-generates `.stignore` files (`*.lock`).
- **⚡ Atomic & Idempotent:** Safe to run repeatedly on any machine without breaking existing paths, symlinks, or save files.

---

## 🚀 Quickstart & Installation

### 1-Line Installer
```bash
bash ~/emu-stitch/install.sh
```

### Manual Installation
```bash
cd ~/emu-stitch
python3 setup.py install --user
```

---

## 💻 CLI Usage

```bash
# Run system audit (inspects paths, active Steam user, and Syncthing status)
python3 -m emu_stitch.cli audit

# Run active profile switch and update emulator symlinks
python3 -m emu_stitch.cli switch

# Pair with a remote handheld machine using its Device ID
python3 -m emu_stitch.cli pair <REMOTE-DEVICE-ID>

# Run with custom EmuDeck path
python3 -m emu_stitch.cli --dir /path/to/Custom/Emulation switch
```

---

## 📐 Project Architecture

```text
emu-stitch/
├── emu_stitch/
│   ├── __init__.py      # Package metadata & version (Author: R. F. Hansen-Smith)
│   ├── cli.py           # Command-line interface & ANSI visual status banners
│   ├── detector.py      # Dynamic internal/external path & generic Steam user discovery
│   ├── emulators.py     # Ryujinx & Cemu routing rules & payload mirroring
│   ├── fstab.py         # Non-intrusive mount auditor & noexec checker
│   ├── switcher.py      # Generic profile switcher & symlink manager
│   └── syncthing.py     # Syncthing service, .stignore manager, & REST API pairing
├── install.sh           # Automated 1-line installer
├── LICENSE              # MIT License
├── README.md            # Documentation
└── setup.py             # Python package configuration
```

---

## 📄 License

Distributed under the **MIT License**. Created by **R. F. Hansen-Smith**.
