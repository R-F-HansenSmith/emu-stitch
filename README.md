# 🧵 emu-stitch

**Open-Source Multi-User Emulator Save Synchronizer & Profile Switcher for Handheld Consoles**

*Created by **R. F. Hansen-Smith***

---

## 🌟 Overview

`emu-stitch` is a lightweight, open-source utility designed for Linux handheld gaming consoles (Steam Deck, ROG Ally, Legion Go, CachyOS, Bazzite, ChimeraOS) and PCs.

It automatically detects active Steam user accounts, isolates save profiles dynamically, routes emulator save directories (`Ryujinx`, `Cemu`, `RetroArch`), and synchronizes save data between machines via Syncthing.

---

## 🚀 Installation & Setup

### 1. Install CLI Tool
```bash
./install.sh
```

### 2. Run Interactive Configuration Wizard
```bash
emu-stitch setup
```
*(Optionally run `emu-stitch setup -y` for automated non-interactive setup).*

---

## 💻 CLI Usage

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

## 📄 License

Distributed under the **MIT License**. Created by **R. F. Hansen-Smith**.
