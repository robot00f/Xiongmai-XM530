#!/bin/sh
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
