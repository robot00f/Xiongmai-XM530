#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
================================================================================
XM-DualStorage SD Card Formatter (Xiongmai XM530 / XM510 / XM550 / Sofia)
================================================================================
Cross-platform tool (Windows & Linux) to physically partition and prepare
MicroSD cards for the Dual-Storage architecture:

- Partition 1 (FAT32 - 'XM_BOOT'):
    User space for custom automation scripts, binaries, Telnet (:23) & FTP (:21).
    (512 MB for 2GB cards; 1GB for 4GB cards; 2GB for 8GB+ cards)
- Partition 2 (Remaining disk space - Sofia WFS Video):
    Isolated partition reserved for 24/7 continuous native surveillance recording.

Fixes Error 1002 and prevents destructive vendor auto-formatting.
================================================================================
"""

import sys
import os
import platform
import subprocess
import time
import shutil
import tempfile
import json

VERSION = "1.2.0"

# ==============================================================================
# UNIVERSAL HOOK TEMPLATE (sd_run.sh)
# ==============================================================================

UNIVERSAL_SD_RUN_SH = """#!/bin/sh
# ==============================================================================
# sd_run.sh - Universal Dual Storage & Root Coexistence Hook (Xiongmai / Sofia)
# ==============================================================================
# Description:
# Automatically executed at boot by Sofia (/home/sd_run.sh) when a MicroSD
# card is detected.
#
# Architecture:
# - Partition 1 (FAT32, mounted at /home): Custom scripts, binaries & Telnet/FTP access.
# - Partition 2 (WFS Raw Block Device): Dedicated solely to continuous surveillance video.
#
# Key Features:
# 1. Kernel Block Shield: Redirects /dev/mmcblk0 to Partition 2 (Major 179, Minor 2)
#    preventing Sofia from wiping or formatting the FAT32 boot partition (Fixes Error 1002).
# 2. Remote Access: Spawns Telnet (port 23) and optional FTP server (port 21).
# 3. Clean Coexistence: Automatically launches Sofia in the background to record video.
# ==============================================================================

# 1. KERNEL BLOCK SHIELD: Protect FAT32 & Bind Sofia directly to Partition 2
# Save original hardware block device handle
mv -f /dev/mmcblk0 /dev/mmcblk0_raw 2>/dev/null || true

# Recreate /dev/mmcblk0 pointing strictly to Partition 2 (b 179 2)
rm -f /dev/mmcblk0
mknod /dev/mmcblk0 b 179 2

# 2. BASE ENVIRONMENT & REQUIRED DIRECTORIES IN PARTITION 1 (/home)
mkdir -p /home/cam_00 /home/log /home/conf /home/backups
touch /home/FormatOk

# 3. OPTIONAL BUSYBOX EXPANSION (If busybox binary is present)
if [ -x "/home/busybox" ]; then
    mkdir -p /tmp/bin
    /home/busybox --install -s /tmp/bin 2>/dev/null || true
    export PATH="/tmp/bin:/home:/bin:/sbin:/usr/bin:/usr/sbin:$PATH"
fi

# 4. REMOTE ACCESS SERVICES: Telnet (Port 23) & FTP Server (Port 21)
if [ -x "/home/busybox" ]; then
    /home/busybox telnetd -l /bin/sh -p 23 &
    /home/busybox tcpsvd -vE 0.0.0.0 21 /home/busybox ftpd -w /home >/dev/null 2>&1 &
else
    telnetd -p 23 &
fi

# 5. LAUNCH SOFIA: Native surveillance daemon over isolated Partition 2
if [ -x "/usr/bin/Sofia" ] && ! ps | grep -v grep | grep -q 'Sofia'; then
    /usr/bin/Sofia > /dev/null 2>&1 &
fi

exit 0
"""

# ==============================================================================
# UTILITIES AND PRIVILEGES
# ==============================================================================

def is_admin():
    """Checks whether the script is running with Administrator / root privileges."""
    if platform.system() == "Windows":
        try:
            import ctypes
            return ctypes.windll.shell32.IsUserAnAdmin() != 0
        except Exception:
            return False
    else:
        return os.geteuid() == 0

def elevate_windows():
    """Requests Windows UAC elevation prompt."""
    import ctypes
    params = " ".join([f'"{arg}"' for arg in sys.argv])
    ctypes.windll.shell32.ShellExecuteW(None, "runas", sys.executable, params, None, 1)
    sys.exit(0)

def print_banner():
    print("""
================================================================================
          XM-DUALSTORAGE: MICROSD CARD FORMATTER (XM530 / SOFIA)
                                Version """ + VERSION + """
================================================================================
""")

# ==============================================================================
# WINDOWS DISK DETECTION & FORMATTING
# ==============================================================================

def get_disks_windows():
    """Enumerates physical disks in Windows via PowerShell, mapping drive letters."""
    ps_disks = (
        "Get-Disk | Select-Object Number, FriendlyName, Size, BusType, IsBoot, IsSystem "
        "| ConvertTo-Json"
    )
    ps_parts = (
        "Get-Partition -ErrorAction SilentlyContinue | Where-Object DriveLetter "
        "| Select-Object DiskNumber, DriveLetter | ConvertTo-Json"
    )
    try:
        p_d = subprocess.run(
            ["powershell", "-NoProfile", "-Command", ps_disks],
            capture_output=True,
            text=True,
            check=True
        )
        data = json.loads(p_d.stdout)
        if isinstance(data, dict):
            data = [data]

        # Map drive letters by disk number
        letters_map = {}
        try:
            p_p = subprocess.run(
                ["powershell", "-NoProfile", "-Command", ps_parts],
                capture_output=True,
                text=True
            )
            parts_data = json.loads(p_p.stdout) if p_p.stdout.strip() else []
            if isinstance(parts_data, dict):
                parts_data = [parts_data]
            for pt in parts_data:
                dn = pt.get("DiskNumber")
                dl = pt.get("DriveLetter")
                if dn is not None and dl:
                    letters_map.setdefault(dn, []).append(f"{dl}:")
        except Exception:
            pass

        disks = []
        for d in data:
            size_bytes = d.get("Size") or 0
            size_gb = round(size_bytes / (1024**3), 2)
            dn = d["Number"]
            letters = letters_map.get(dn, [])
            disks.append({
                "number": dn,
                "name": d.get("FriendlyName", "Unknown Disk"),
                "size_bytes": size_bytes,
                "size_gb": size_gb,
                "bus": d.get("BusType", "Unknown"),
                "is_boot": d.get("IsBoot", False),
                "is_system": d.get("IsSystem", False),
                "letters": letters
            })
        return disks
    except Exception as e:
        print(f"[!] Error querying disks: {e}")
        return []

def get_volume_letter_by_label_windows(label="XM_BOOT"):
    """Locates the drive letter assigned to the freshly formatted partition."""
    ps_cmd = f"Get-Volume -FileSystemLabel '{label}' -ErrorAction SilentlyContinue | Select-Object -ExpandProperty DriveLetter"
    for _ in range(10):
        time.sleep(1)
        try:
            p = subprocess.run(
                ["powershell", "-NoProfile", "-Command", ps_cmd],
                capture_output=True,
                text=True
            )
            letter = p.stdout.strip()
            if letter and len(letter) == 1 and letter.isalpha():
                return f"{letter}:"
        except Exception:
            pass
    return None

def calculate_fat32_size_mb(size_bytes):
    """Calculates Partition 1 (FAT32) size dynamically according to total card capacity."""
    size_mb = size_bytes / (1024 * 1024)
    if size_mb <= 2200:
        return 512   # 2GB cards: 512 MB boot/scripts, ~1.4 GB for Sofia video
    elif size_mb <= 4500:
        return 1024  # 4GB cards: 1024 MB boot/scripts, ~3 GB for Sofia video
    else:
        return 2048  # 8GB+ cards: 2048 MB boot/scripts, remainder for Sofia video

def format_disk_windows(disk_number, size_bytes):
    """Executes DiskPart to create Dual MBR layout: FAT32 (Boot) + RAW (Sofia Video)."""
    fat32_mb = calculate_fat32_size_mb(size_bytes)
    print(f"[*] Calculating partitions: Partition 1 (FAT32) = {fat32_mb} MB | Partition 2 (Sofia WFS) = Remainder")

    # Clean & compatible DiskPart sequence:
    # 1. Wipe partition table (MBR)
    # 2. Create primary partition 1 in FAT32 labeled 'XM_BOOT'
    # 3. Automatically assign mount point / drive letter
    # 4. Create primary partition 2 unformatted for Sofia (WFS)
    script_content = f"""select disk {disk_number}
clean
convert mbr
create partition primary size={fat32_mb}
format fs=fat32 quick label="XM_BOOT"
assign
create partition primary
"""
    with tempfile.NamedTemporaryFile("w", delete=False, suffix=".txt") as tf:
        tf.write(script_content)
        script_path = tf.name

    try:
        print("[*] Running DiskPart to configure MBR Dual-Storage...")
        proc = subprocess.run(
            ["diskpart", "/s", script_path],
            capture_output=True,
            text=True
        )
        if proc.returncode != 0:
            print("[!] Error during DiskPart execution:")
            print(proc.stdout)
            return False

        print(f"[+] Partition 1 (FAT32, {fat32_mb}MB) and Partition 2 (Sofia WFS) created successfully.")
        return True
    finally:
        if os.path.exists(script_path):
            os.remove(script_path)

# ==============================================================================
# LINUX DISK DETECTION & FORMATTING
# ==============================================================================

def get_disks_linux():
    """Enumerates storage block devices in Linux via lsblk."""
    try:
        p = subprocess.run(
            ["lsblk", "-d", "-b", "-n", "-o", "NAME,SIZE,ROTA,RM,MODEL"],
            capture_output=True,
            text=True,
            check=True
        )
        disks = []
        for line in p.stdout.strip().splitlines():
            parts = line.split()
            if not parts:
                continue
            name = parts[0]
            if name.startswith("loop") or name.startswith("zram"):
                continue
            size_bytes = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 0
            size_gb = round(size_bytes / (1024**3), 2)
            rm = parts[3] if len(parts) > 3 else "0"
            model = " ".join(parts[4:]) if len(parts) > 4 else "Generic"
            disks.append({
                "dev": f"/dev/{name}",
                "name": f"{name} ({model})",
                "size_bytes": size_bytes,
                "size_gb": size_gb,
                "removable": rm == "1"
            })
        return disks
    except Exception as e:
        print(f"[!] Error listing disks: {e}")
        return []

def format_disk_linux(dev_path, size_bytes):
    """Partitions MBR layout and formats Partition 1 as FAT32 in Linux."""
    try:
        fat32_mb = calculate_fat32_size_mb(size_bytes)
        print(f"[*] Creating MBR partition table on {dev_path}...")
        subprocess.run(["parted", "-s", dev_path, "mklabel", "msdos"], check=True)
        print(f"[*] Creating Partition 1 (FAT32, {fat32_mb} MB)...")
        subprocess.run(["parted", "-s", dev_path, "mkpart", "primary", "fat32", "1MiB", f"{fat32_mb + 1}MiB"], check=True)
        print("[*] Creating Partition 2 (Sofia WFS Video / Linux)...")
        subprocess.run(["parted", "-s", dev_path, "mkpart", "primary", f"{fat32_mb + 1}MiB", "100%"], check=True)

        p1 = f"{dev_path}1" if not dev_path[-1].isdigit() else f"{dev_path}p1"
        print(f"[*] Formatting {p1} as FAT32...")
        subprocess.run(["mkfs.vfat", "-F", "32", "-n", "XM_BOOT", p1], check=True)
        return p1
    except Exception as e:
        print(f"[!] Error during Linux partitioning: {e}")
        return None

# ==============================================================================
# STARTER KIT DEPLOYMENT
# ==============================================================================

def write_starter_kit(dest_dir):
    """Installs sd_run.sh hook, protection flag, and directories onto the FAT32 partition."""
    print(f"\n[*] Installing universal starter kit to: {dest_dir}")

    # Required base folders
    for folder in ["cam_00", "log", "conf", "backups"]:
        os.makedirs(os.path.join(dest_dir, folder), exist_ok=True)

    # sd_run.sh with native Linux LF line endings
    sd_run_path = os.path.join(dest_dir, "sd_run.sh")
    with open(sd_run_path, "wb") as f:
        f.write(UNIVERSAL_SD_RUN_SH.replace("\r\n", "\n").encode("utf-8"))
    print("  [+] Created: sd_run.sh (Kernel Block Shield & Auto-start hook)")

    # FormatOk protection witness flag
    format_ok_path = os.path.join(dest_dir, "FormatOk")
    with open(format_ok_path, "wb") as f:
        pass
    print("  [+] Created: FormatOk (Vendor overwrite protection flag)")

    # Optional: Bundled static ARMv7l busybox if present in script folder
    script_dir = os.path.dirname(os.path.abspath(__file__))
    for cand in ["busybox-armv7l", "busybox"]:
        candidate_path = os.path.join(script_dir, cand)
        if os.path.isfile(candidate_path) and os.path.getsize(candidate_path) > 100000:
            shutil.copy2(candidate_path, os.path.join(dest_dir, "busybox"))
            print(f"  [+] Bundled binary: busybox (copied from {cand})")
            break

# ==============================================================================
# MAIN ENTRYPOINT
# ==============================================================================

def main():
    print_banner()

    # Privilege check
    if not is_admin():
        if platform.system() == "Windows":
            print("[*] Requesting Administrator privileges to manage storage disks...")
            elevate_windows()
        else:
            print("[!] Please run with root privileges: sudo python3 xm_format_sd.py")
            sys.exit(1)

    os_type = platform.system()

    if os_type == "Windows":
        disks = get_disks_windows()
        if not disks:
            print("[!] No storage disks detected on the system.")
            sys.exit(1)

        print("Storage disks detected on your machine:\n")
        selectable = []
        drive_to_disk = {}

        for d in disks:
            is_sys = d["is_boot"] or d["is_system"]
            tag = " [SYSTEM DISK - PROTECTED]" if is_sys else ""
            bus_tag = f"[{d['bus']}]"
            letters_str = f" [Drive: {', '.join(d['letters'])}]" if d["letters"] else ""
            print(f"  [{d['number']}] {d['name']} ({d['size_gb']} GB) {bus_tag}{letters_str}{tag}")

            if not is_sys:
                selectable.append(d["number"])
                for ltr in d["letters"]:
                    drive_to_disk[ltr.replace(":", "").upper()] = d["number"]

        if not selectable:
            print("\n[!] No removable storage drives available.")
            print("[i] Please plug in your MicroSD card via a USB reader and run the script again.")
            sys.exit(0)

        print("\n" + "-"*75)
        raw_choice = input("\nEnter disk NUMBER (e.g. 4) or drive LETTER (e.g. H) ('q' to quit): ").strip().upper()
        if raw_choice == 'Q' or not raw_choice:
            print("[*] Operation canceled.")
            sys.exit(0)

        target_disk = None
        clean_choice = raw_choice.replace(":", "")

        if clean_choice.isdigit():
            target_disk = int(clean_choice)
        elif clean_choice in drive_to_disk:
            target_disk = drive_to_disk[clean_choice]
            print(f"[*] Drive letter {clean_choice}: matched to Disk {target_disk}.")
        else:
            print(f"[!] Invalid selection: '{raw_choice}'.")
            sys.exit(1)

        if target_disk not in selectable:
            print("[!] Selected disk number is invalid or protected as a system drive.")
            sys.exit(1)

        chosen = next(d for d in disks if d["number"] == target_disk)
        print(f"\n[!] WARNING: ALL DATA ON DISK {target_disk} ({chosen['name']}, {chosen['size_gb']} GB) WILL BE PERMANENTLY ERASED.")
        confirm = input("Type 'YES' to confirm formatting: ").strip().upper()
        if confirm not in ["YES", "Y", "SI", "S"]:
            print("[*] Operation canceled by user.")
            sys.exit(0)

        success = format_disk_windows(target_disk, chosen["size_bytes"])
        if not success:
            sys.exit(1)

        drive_letter = get_volume_letter_by_label_windows("XM_BOOT")
        if not drive_letter:
            drive_letter = input("Enter drive letter assigned to XM_BOOT (e.g. H:): ").strip().upper()
            if not drive_letter.endswith(":"):
                drive_letter += ":"

        write_starter_kit(drive_letter + "\\")

    else:
        # Linux
        disks = get_disks_linux()
        if not disks:
            print("[!] No storage disks detected.")
            sys.exit(1)

        print("Storage devices detected:\n")
        for i, d in enumerate(disks):
            tag = "[REMOVABLE/SD]" if d["removable"] else "[FIXED DISK]"
            print(f"  [{i}] {d['dev']} - {d['name']} ({d['size_gb']} GB) {tag}")

        choice = input("\nEnter device index (e.g. 1) or 'q' to cancel: ").strip()
        if choice.lower() == 'q' or not choice.isdigit():
            sys.exit(0)

        idx = int(choice)
        if idx < 0 or idx >= len(disks):
            print("[!] Invalid index.")
            sys.exit(1)

        target = disks[idx]
        print(f"\n[!] WARNING: ALL DATA ON {target['dev']} WILL BE PERMANENTLY ERASED.")
        confirm = input("Type 'YES' to proceed: ").strip().upper()
        if confirm not in ["YES", "Y", "SI", "S"]:
            sys.exit(0)

        p1 = format_disk_linux(target["dev"], target["size_bytes"])
        if not p1:
            sys.exit(1)

        with tempfile.TemporaryDirectory() as mount_point:
            subprocess.run(["mount", p1, mount_point], check=True)
            try:
                write_starter_kit(mount_point)
            finally:
                subprocess.run(["umount", mount_point], check=True)

    print("""
================================================================================
                    MICROSD CARD PREPARED SUCCESSFULLY!
================================================================================
Ready Dual-Storage layout:
  - Partition 1 (FAT32 - 'XM_BOOT'):
      Contains 'sd_run.sh' for root access, Telnet (:23), and FTP (:21).
  - Partition 2 (Sofia WFS Video, remaining space):
      Isolated for continuous native surveillance video recording.

Instructions for user:
  1. Safely eject the MicroSD card from your computer.
  2. Insert it into the slot of your powered-off Xiongmai camera.
  3. Power on the camera. It will boot in ~16 seconds with 24/7 recording
     active and Telnet / FTP root access available!
================================================================================
""")

if __name__ == "__main__":
    main()
