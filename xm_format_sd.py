#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
================================================================================
XM-DualStorage SD Card Formatter (Xiongmai XM530 / XM510 / XM550 / Sofia)
================================================================================
Herramienta universal multiplataforma (Windows y Linux) para particionar y
preparar físicamente tarjetas MicroSD con arquitectura de Doble Almacenamiento:

- Partición 1 (FAT32 - 'XM_BOOT'):
    Espacio de usuario para scripts, modificaciones y acceso Telnet Root.
    (512MB en tarjetas de 2GB; 1GB en 4GB; 2GB en tarjetas de 8GB+)
- Partición 2 (Resto del disco - WFS Video):
    Reservada para la grabación continua de video del sistema de vigilancia Sofia.

Soluciona el Error 1002 y previene formateos destructivos automáticos.
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

VERSION = "1.1.0"

# ==============================================================================
# HOOK UNIVERSAL LIMPIO (sd_run.sh)
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
# - Partition 1 (FAT32, mounted at /home): Custom scripts, binaries & Telnet root.
# - Partition 2 (WFS Raw Block Device): Dedicated solely to continuous surveillance video.
#
# Key Features:
# 1. Kernel Block Shield: Redirects /dev/mmcblk0 to Partition 2 (Major 179, Minor 2)
#    preventing Sofia from wiping or formatting the FAT32 boot partition (Fixes Error 1002).
# 2. Root Access: Spawns background telnetd service on port 23.
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
# UTILIDADES Y PRIVILEGIOS
# ==============================================================================

def is_admin():
    """Verifica si el script cuenta con privilegios de Administrador / Root."""
    if platform.system() == "Windows":
        try:
            import ctypes
            return ctypes.windll.shell32.IsUserAnAdmin() != 0
        except Exception:
            return False
    else:
        return os.geteuid() == 0

def elevate_windows():
    """Solicita elevacion de privilegios UAC en Windows."""
    import ctypes
    params = " ".join([f'"{arg}"' for arg in sys.argv])
    ctypes.windll.shell32.ShellExecuteW(None, "runas", sys.executable, params, None, 1)
    sys.exit(0)

def print_banner():
    print("""
================================================================================
          XM-DUALSTORAGE: FORMATEADOR FISICO DE MICROSD (XM530 / SOFIA)
                                Version """ + VERSION + """
================================================================================
""")

# ==============================================================================
# DETECCION Y FORMATEO EN WINDOWS
# ==============================================================================

def get_disks_windows():
    """Lista los discos físicos en Windows utilizando PowerShell, incluyendo letras de unidad."""
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

        # Mapear letras de unidad por número de disco
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
                "name": d.get("FriendlyName", "Disco Desconocido"),
                "size_bytes": size_bytes,
                "size_gb": size_gb,
                "bus": d.get("BusType", "Unknown"),
                "is_boot": d.get("IsBoot", False),
                "is_system": d.get("IsSystem", False),
                "letters": letters
            })
        return disks
    except Exception as e:
        print(f"[!] Error consultando discos: {e}")
        return []

def get_volume_letter_by_label_windows(label="XM_BOOT"):
    """Localiza la letra de unidad asignada a la partición recién formateada."""
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
    """Calcula dinámicamente el tamaño de la Partición 1 (FAT32) según la capacidad total."""
    size_mb = size_bytes / (1024 * 1024)
    if size_mb <= 2200:
        return 512   # Tarjetas de 2GB: 512 MB boot, ~1.4 GB para video Sofia
    elif size_mb <= 4500:
        return 1024  # Tarjetas de 4GB: 1024 MB boot, ~3 GB para video Sofia
    else:
        return 2048  # Tarjetas de 8GB o más: 2048 MB boot, resto para video Sofia

def format_disk_windows(disk_number, size_bytes):
    """Ejecuta DiskPart para crear el esquema MBR Dual: FAT32 (Boot) + RAW (Video Sofia)."""
    fat32_mb = calculate_fat32_size_mb(size_bytes)
    print(f"[*] Calculando particiones: Particion 1 (FAT32) = {fat32_mb} MB | Particion 2 (Video Sofia) = Resto")

    # Script DiskPart limpio y compatible:
    # 1. Limpia la tabla MBR
    # 2. Crea partición 1 primaria en FAT32 etiquetada 'XM_BOOT'
    # 3. Asigna letra de unidad automáticamente
    # 4. Crea partición 2 primaria sin formatear para Sofia (WFS)
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
        print("[*] Ejecutando DiskPart para particionar en MBR...")
        proc = subprocess.run(
            ["diskpart", "/s", script_path],
            capture_output=True,
            text=True
        )
        if proc.returncode != 0:
            print("[!] Error durante DiskPart:")
            print(proc.stdout)
            return False

        print(f"[+] Particion 1 (FAT32, {fat32_mb}MB) y Particion 2 (Sofia WFS) creadas con exito.")
        return True
    finally:
        if os.path.exists(script_path):
            os.remove(script_path)

# ==============================================================================
# DETECCION Y FORMATEO EN LINUX
# ==============================================================================

def get_disks_linux():
    """Lista los dispositivos de almacenamiento en Linux mediante lsblk."""
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
            model = " ".join(parts[4:]) if len(parts) > 4 else "Generico"
            disks.append({
                "dev": f"/dev/{name}",
                "name": f"{name} ({model})",
                "size_bytes": size_bytes,
                "size_gb": size_gb,
                "removable": rm == "1"
            })
        return disks
    except Exception as e:
        print(f"[!] Error listando discos: {e}")
        return []

def format_disk_linux(dev_path, size_bytes):
    """Particiona en MBR y formatea la partición 1 en FAT32 en Linux."""
    try:
        fat32_mb = calculate_fat32_size_mb(size_bytes)
        print(f"[*] Creando tabla de particiones MBR en {dev_path}...")
        subprocess.run(["parted", "-s", dev_path, "mklabel", "msdos"], check=True)
        print(f"[*] Creando Particion 1 (FAT32, {fat32_mb} MB)...")
        subprocess.run(["parted", "-s", dev_path, "mkpart", "primary", "fat32", "1MiB", f"{fat32_mb + 1}MiB"], check=True)
        print("[*] Creando Particion 2 (Video Sofia WFS / Linux)...")
        subprocess.run(["parted", "-s", dev_path, "mkpart", "primary", f"{fat32_mb + 1}MiB", "100%"], check=True)

        p1 = f"{dev_path}1" if not dev_path[-1].isdigit() else f"{dev_path}p1"
        print(f"[*] Formateando {p1} en FAT32...")
        subprocess.run(["mkfs.vfat", "-F", "32", "-n", "XM_BOOT", p1], check=True)
        return p1
    except Exception as e:
        print(f"[!] Error durante el formateo en Linux: {e}")
        return None

# ==============================================================================
# DESPLIEGUE DEL HOOK UNIVERSAL
# ==============================================================================

def write_starter_kit(dest_dir):
    """Instala el hook sd_run.sh y las carpetas base en la particion FAT32."""
    print(f"\n[*] Instalando hook universal en: {dest_dir}")

    # Carpetas requeridas
    for folder in ["cam_00", "log", "conf", "backups"]:
        os.makedirs(os.path.join(dest_dir, folder), exist_ok=True)

    # sd_run.sh con saltos de línea LF (Linux)
    sd_run_path = os.path.join(dest_dir, "sd_run.sh")
    with open(sd_run_path, "wb") as f:
        f.write(UNIVERSAL_SD_RUN_SH.replace("\r\n", "\n").encode("utf-8"))
    print("  [+] Creado: sd_run.sh (Hook de blindaje y arranque automatico)")

    # Testigo FormatOk
    format_ok_path = os.path.join(dest_dir, "FormatOk")
    with open(format_ok_path, "wb") as f:
        pass
    print("  [+] Creado: FormatOk (Testigo de proteccion contra sobreescrituras)")

    # Opcional: Si el usuario tiene un binario busybox en la misma carpeta del script, copiarlo
    script_dir = os.path.dirname(os.path.abspath(__file__))
    for cand in ["busybox-armv7l", "busybox"]:
        candidate_path = os.path.join(script_dir, cand)
        if os.path.isfile(candidate_path) and os.path.getsize(candidate_path) > 100000:
            shutil.copy2(candidate_path, os.path.join(dest_dir, "busybox"))
            print(f"  [+] Binario incluido: busybox (desde {cand})")
            break

# ==============================================================================
# MENU PRINCIPAL
# ==============================================================================

def main():
    print_banner()

    # Comprobación de permisos
    if not is_admin():
        if platform.system() == "Windows":
            print("[*] Solicitando permisos de Administrador para gestionar discos...")
            elevate_windows()
        else:
            print("[!] Ejecuta el script con privilegios root: sudo python3 xm_format_sd.py")
            sys.exit(1)

    os_type = platform.system()

    if os_type == "Windows":
        disks = get_disks_windows()
        if not disks:
            print("[!] No se detectaron discos en el sistema.")
            sys.exit(1)

        print("Discos detectados en tu equipo:\n")
        selectable = []
        drive_to_disk = {}

        for d in disks:
            is_sys = d["is_boot"] or d["is_system"]
            tag = " [DISCO DEL SISTEMA - BLOQUEADO]" if is_sys else ""
            bus_tag = f"[{d['bus']}]"
            letters_str = f" [Unidad: {', '.join(d['letters'])}]" if d["letters"] else ""
            print(f"  [{d['number']}] {d['name']} ({d['size_gb']} GB) {bus_tag}{letters_str}{tag}")
            
            if not is_sys:
                selectable.append(d["number"])
                for ltr in d["letters"]:
                    drive_to_disk[ltr.replace(":", "").upper()] = d["number"]

        if not selectable:
            print("\n[!] No se encontraron unidades extraibles disponibles.")
            print("[i] Conecta tu tarjeta MicroSD mediante un lector USB y vuelve a ejecutar el script.")
            sys.exit(0)

        print("\n" + "-"*75)
        raw_choice = input("\nIntroduce el NUMERO de disco (ej. 4) o la LETRA de unidad (ej. H) (o 'q' para salir): ").strip().upper()
        if raw_choice == 'Q' or not raw_choice:
            print("[*] Operacion cancelada.")
            sys.exit(0)

        target_disk = None
        clean_choice = raw_choice.replace(":", "")

        if clean_choice.isdigit():
            target_disk = int(clean_choice)
        elif clean_choice in drive_to_disk:
            target_disk = drive_to_disk[clean_choice]
            print(f"[*] Letra {clean_choice}: identificada correctamente como Disco {target_disk}.")
        else:
            print(f"[!] Opcion '{raw_choice}' no valida.")
            sys.exit(1)

        if target_disk not in selectable:
            print("[!] Numero de disco no valido o protegido por ser disco del sistema.")
            sys.exit(1)

        chosen = next(d for d in disks if d["number"] == target_disk)
        print(f"\n[!] ATENCION: Se BORRARAN todos los datos en el Disco {target_disk} ({chosen['name']}, {chosen['size_gb']} GB).")
        confirm = input("Escribe 'SI' para confirmar el formateo: ").strip().upper()
        if confirm not in ["SI", "YES"]:
            print("[*] Cancelado por el usuario.")
            sys.exit(0)

        success = format_disk_windows(target_disk, chosen["size_bytes"])
        if not success:
            sys.exit(1)

        drive_letter = get_volume_letter_by_label_windows("XM_BOOT")
        if not drive_letter:
            drive_letter = input("Introduce la letra de unidad asignada a XM_BOOT (ej. H:): ").strip().upper()
            if not drive_letter.endswith(":"):
                drive_letter += ":"

        write_starter_kit(drive_letter + "\\")

    else:
        # Linux
        disks = get_disks_linux()
        if not disks:
            print("[!] No se detectaron discos.")
            sys.exit(1)

        print("Dispositivos detectados:\n")
        for i, d in enumerate(disks):
            tag = "[EXTRAIBLE/SD]" if d["removable"] else "[DISCO FIJO]"
            print(f"  [{i}] {d['dev']} - {d['name']} ({d['size_gb']} GB) {tag}")

        choice = input("\nIntroduce el indice del dispositivo (ej. 1) o 'q' para cancelar: ").strip()
        if choice.lower() == 'q' or not choice.isdigit():
            sys.exit(0)

        idx = int(choice)
        if idx < 0 or idx >= len(disks):
            print("[!] Indice no valido.")
            sys.exit(1)

        target = disks[idx]
        print(f"\n[!] ATENCION: Se borraran todos los datos en {target['dev']}.")
        confirm = input("Escribe 'SI' para proceder: ").strip().upper()
        if confirm != "SI":
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
                    ¡TARJETA MICROSD PREPARADA CON EXITO!
================================================================================
Estructura lista para cualquier usuario:
  - Particion 1 (FAT32 - 'XM_BOOT'):
      Contiene 'sd_run.sh' para iniciar el sistema y activar Telnet Root.
  - Particion 2 (Video Sofia WFS, Resto del espacio):
      Aislada para la grabacion de video nativa continua de Sofia.

Instrucciones para el usuario:
  1. Expulsa la tarjeta de forma segura de tu computadora.
  2. Introducela en la ranura de la camara Xiongmai apagada.
  3. Enciende la camara. Arrancara en ~16 segundos con grabacion activa
     y acceso Telnet en el puerto 23.
================================================================================
""")

if __name__ == "__main__":
    main()
