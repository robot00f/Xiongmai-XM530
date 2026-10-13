# 🔬 Reverse Engineering the Xiongmai (XM530) Storage Architecture & The WFS0.4 Collision

---

## 1. Abstract

Xiongmai (XM) IP cameras powered by the XM530 SoC series (ARMv7 Cortex-A7) rely on a proprietary surveillance daemon (`/usr/bin/Sofia`) linked against `/usr/lib/libdvr.so`. Storage operations are managed by **WFS (Xiongmai File System)**, a non-standard raw disk storage architecture.

This document details the reverse engineering analysis conducted using **Ghidra MCP**, uncovering:
1. The exact assembly-level root cause of **Error 1002 (`0x3EA`: `"wfs_read_partitions ver no match"`)**.
2. The boot-time destructive auto-format routine (`FUN_001fe3ac`) that wipes custom partition tables.
3. The process handover mechanism between `/usr/bin/netinit`, `/usr/bin/dvrHelper`, and `/home/sd_run.sh`.
4. The architectural kernel block layer redirection method that guarantees seamless coexistence between a Linux FAT32 partition and a native WFS0.4 video recording partition on the same physical MicroSD card.

---

## 2. Hardware & Firmware Profile

| Subsystem | Specification |
| :--- | :--- |
| **SoC** | Xiongmai XM530 Dual-Core Cortex-A7 (`ARMv7l`, VFPv4, NEON) |
| **Memory** | 64 MB Total DDR RAM (~7.3 MB free under normal operation) |
| **Flash** | 8 MB SPI NOR Flash (`/dev/mtd0` through `/dev/mtd5`) |
| **Storage Card** | 64 GB MicroSDXC (`/dev/mmcblk0`, `Major 179, Minor 0`) |
| **OS / Toolchain** | Linux 3.10.103+ (Buildroot 2014.08, uClibc 0.9.33.3) |
| **Rootfs** | `/dev/root` on CRAMFS (read-only) |
| **Read-Only Partitions** | `/usr` on SquashFS (`/dev/mtdblock3`), `/mnt/custom` on CRAMFS |
| **Writable Partitions** | `/mnt/mtd` on JFFS2 (`/dev/mtdblock5`), `/dev` on tmpfs |

---

## 3. Storage Subsystem Architecture (libdvr.so & Sofia)

### 3.1. Disk Discovery & Hardcoded Device Enumeration (`libdvr.so`)
Storage discovery originates inside `InitStorageDev()` (`0x00027754` in `libdvr.so`). The function calls `scan_storage_dev()` -> `scan_mmc_info()`:

```c
// Decompiled from libdvr.so: scan_mmc_info() @ 0x0002710c
undefined4 scan_mmc_info(char *param_1, undefined4 *param_2, int param_3) {
    // param_1 is "/proc/partitions"
    __stream = fopen64(param_1, "r");
    ...
    do {
        pcVar1 = fgets(acStack_58, 0x40, __stream);
        if (pcVar1 == 0) goto LAB_00027214;
        pcVar1 = strstr(acStack_58, "mmc");
    } while (pcVar1 == 0);
    sscanf(acStack_58, "%d %d %d", param_2 + 2, param_2 + 3, param_2 + 4);
    ...
    // Hardcoded device string generation:
    sprintf((char *)(param_2 + 5), "mmcblk%d", 0);
    ...
    return 1;
}
```

In `InitStorageDev()`, the device path is formed as `/dev/mmcblk0` and opened with read/write access:
```c
iVar3 = open64("/dev/mmcblk0", O_RDWR);
*(int *)(g_dev_info + iVar4 + 0x14) = iVar3; // Saves descriptor in g_dev_info
ioctl(iVar3, BLKGETSIZE, &local_b50);         // Queries device sector count
```
The sector capacity and descriptor are then exported to `Sofia` via the global structure `g_ide_info` (`0x00040888`).

---

### 3.2. WFS Storage Initialization & Verification (`Sofia`)
When `Sofia` boots, it invokes `wfs_file_sys_init()` (`FUN_002000f4` in `Sofia`). It attempts to read and validate the filesystem structure of disk index `0`:

```c
// Decompiled from Sofia: FUN_002000f4 (wfs_file_sys_init)
for (; uVar2 != uVar1; uVar2 = uVar2 + 1) {
    func_0x001f3a50("disk %d:\n", uVar2);
    
    // Reads Sector 0 (LBA 0):
    iVar6 = FUN_00200eb0(uVar2, extraout_r1, 0, 0);
    if (iVar6 != 0) {
        puVar7 = *(undefined4 **)(iVar6 + 0x24);
        // Checks for WFS0.3 legacy header:
        iVar4 = func_0x0000fd58(puVar7, "WFS0.3", 7);
        if (iVar4 == 0) {
            // Converts WFS0.3 to WFS0.4 and writes 'X', 'M' to offsets 510-511
            *puVar7 = 0x30534657; // "WFS0"
            *(undefined2 *)(puVar7 + 1) = 0x342e; // ".4"
            *(undefined1 *)(*(int *)(iVar6 + 0x24) + 0x1fe) = 0x58; // 'X'
            *(undefined1 *)(*(int *)(iVar6 + 0x24) + 0x1ff) = 0x4d; // 'M'
            func_0x00200bbc(iVar6); // Writes updated sector 0
        }
    }

    // Reads partition table from sector 20 (0x14):
    iVar6 = func_0x001fd160(uVar2, auStack_68, 0x20);
    if (iVar6 == 0) {
        // Valid WFS partition table found -> verify each partition
        ...
    } else {
        // ERROR: Partition read failed -> TRIGGER DESTRUCTIVE AUTO-FORMAT!
        func_0x001f3a50("read fs partitions failed.\n");
        FUN_001fe3ac(uVar2); // <=== WIPES THE ENTIRE DISK!
    }
}
```

---

## 4. Root Cause: Error 1002 & The Signature Collision

Inside `FUN_001fd160` (`wfs_read_partitions`), the verification helper `FUN_001fc880` (`check_disk_ver`) is called:

```c
// Decompiled from Sofia: FUN_001fc880 (check_disk_ver)
undefined4 FUN_001fc880(undefined4 param_1, undefined4 *param_2) {
    ...
    iVar2 = FUN_00200eb0(param_1, ..., 0, 0); // Reads Sector 0
    if (iVar2 != 0) {
        iVar4 = *(int *)(iVar2 + 0x24);
        iVar3 = func_0x0000fd58(iVar4, "WFS0.4", 7); // Validates string "WFS0.4"
        
        // CRITICAL CHECK: Offsets 510 and 511
        if ((iVar3 == 0) && 
            (*(char *)(iVar4 + 0x1fe) == 'X') && 
            (*(char *)(iVar4 + 0x1ff) == 'M')) {
            uVar1 = 1; // MATCH!
        } else {
            // MISMATCH!
            func_0x001f3a50("check_disk_ver no match: ver %s, flg %c %c\n", ...);
            uVar1 = 3; // MISMATCH CODE
        }
        *param_2 = uVar1;
    }
    return 0;
}
```

### The Inherent Incompatibility
- **Linux MBR Requirement**: The Linux kernel partition parser (`block/partitions/msdos.c`) requires bytes `510-511` to be `0x55 0xAA`. Without this signature, the kernel refuses to register partition device nodes (`p1`, `p2`).
- **Sofia WFS Requirement**: `Sofia` demands bytes `510-511` to be `0x58 0x4D` (`'X' 'M'`) and bytes `0-6` to be `"WFS0.4\0"`.
- If an MBR table is present, `check_disk_ver` fails with code `3`.
- `FUN_001fd160` returns `0x3EA` (**1002 decimal**: `"wfs_read_partitions ver no match"`).
- `Sofia` aborts video recording with:
  ```text
  [debugtest]: check abality: VIDEO, error num: 1002, ret: 1
  ```
- On the next boot sequence, `FUN_002000f4` treats the disk as unformatted and executes `FUN_001fe3ac(0)`:
  1. Overwrites Sector 0 with `"WFS0.4"` and `'X' 'M'`, destroying the MBR.
  2. Writes a single 100% monolithic partition entry at Sector 20 (`0x14`).
  3. Formats the full 64 GB in raw WFS clusters (`FUN_001fd348`).
  4. **Permanently deletes `/home`, scripts, and all user tools.**

---

## 5. Process Lifecycle: netinit vs. dvrHelper

Reverse engineering of `/usr/bin/netinit` and `/usr/bin/dvrHelper` revealed that **both files are identical** (same inode/size: 19,268 bytes, entry point `0x934c`):

```c
// Decompiled from netinit / dvrHelper: FUN_0000aafc
iVar3 = access("/dev/mmcblk0p1", 0);
if (iVar3 == 0) {
    mount("/dev/mmcblk0p1", "/home", "vfat", 4, 0);
    
    // Check for user boot hook:
    iVar2 = access("/home/sd_run.sh", 0);
    if (iVar2 == 0) {
        system("rmmod xm_wdt"); // Disable hardware watchdog
        sleep(1);
        printf("run sd_run.sh!");
        execl("/bin/sh", "sh", "/home/sd_run.sh", 0); // REPLACES PROCESS!
    }
}
```

### Architectural Discovery
In `/etc/init.d/rcS`, the startup flow executes:
1. `netinit` (synchronously).
2. `dvrHelper /lib/modules /usr/bin/app.sh 127.0.0.1 9578 1 &` (asynchronously).

Because both binaries check for `/home/sd_run.sh`:
- When `/home/sd_run.sh` exists, `execl` replaces the calling process.
- `dvrHelper` does **not** proceed to execute `/usr/bin/app.sh` (Sofia supervisor).
- **The manufacturer explicitly designed `sd_run.sh` to take full custody of device initialization.**
- Therefore, `/home/sd_run.sh` must explicitly launch `/usr/bin/Sofia &`.

---

## 6. The Solution: Kernel Block Device Virtual Redirection

Since `/dev` is mounted on `tmpfs` (writable RAM) and `sd_run.sh` executes with root privileges prior to `Sofia` startup, we implement a **block layer virtualization trick**:

```sh
# 1. Back up full raw physical disk node:
mv -f /dev/mmcblk0 /dev/mmcblk0_raw 2>/dev/null || true

# 2. Re-create /dev/mmcblk0 pointing to Partition 2 (Major 179, Minor 2):
rm -f /dev/mmcblk0
mknod /dev/mmcblk0 b 179 2
```

### Technical Impact
1. When `libdvr.so` opens `/dev/mmcblk0`, the Linux kernel grants a file descriptor to block device `(179, 2)` (Partition 2).
2. `ioctl(BLKGETSIZE)` returns the sector count of Partition 2 (~57.5 GB).
3. Sector 0 from `Sofia`'s perspective is physical LBA 4,000,000 (start of Partition 2).
4. `Sofia` writes its `"WFS0.4"` header and `'X' 'M'` signature at LBA 4,000,000 without touching physical LBA 0.
5. The Linux kernel strictly enforces block device boundaries: **writes through `(179, 2)` cannot cross into Partition 1 (`179, 1`) or physical LBA 0**.
6. `Sofia` formats Partition 2 as native WFS0.4, starts `CRecordManager`, and records 24/7.
7. Partition 1 (`/home`, FAT32) and the real MBR (`0x55 0xAA`) remain 100% immune, permanent, and untouched across all reboots.
