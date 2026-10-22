"""Scan du matériel : cartes graphiques NVIDIA (VRAM), mémoire vive et processeur.

Sert à calculer les meilleurs réglages de chargement pour chaque modèle (voir optimizer.py).
"""

import ctypes
import json
import os
import shutil
import subprocess
import sys

MIB = 1024 * 1024
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def _gpus():
    exe = shutil.which("nvidia-smi")
    if exe is None:
        return []
    try:
        out = subprocess.run([exe, "--query-gpu=index,name,memory.total,memory.used,memory.free,driver_version",
                              "--format=csv,noheader,nounits"], capture_output=True, text=True, timeout=10,
                             creationflags=_NO_WINDOW).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    gpus = []
    for line in out.strip().splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) < 6:
            continue
        try:
            index, name, total, used, free = int(parts[0]), parts[1], int(parts[2]), int(parts[3]), int(parts[4])
        except ValueError:
            continue
        gpus.append({"index": index, "name": name, "driver": parts[5],
                     "vram_total": total * MIB, "vram_used": used * MIB, "vram_free": free * MIB})
    return gpus


class _MemoryStatus(ctypes.Structure):
    _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]


def _ram():
    if sys.platform != "win32":
        return {"total": None, "free": None}
    status = _MemoryStatus()
    status.dwLength = ctypes.sizeof(status)
    if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
        return {"total": None, "free": None}
    return {"total": status.ullTotalPhys, "free": status.ullAvailPhys}


def _cpu():
    info = {"name": None, "cores": None, "threads": os.cpu_count()}
    exe = shutil.which("pwsh") or shutil.which("powershell")
    if exe is None:
        return info
    try:
        out = subprocess.run([exe, "-NoProfile", "-NonInteractive", "-Command",
                              "Get-CimInstance Win32_Processor | Select-Object Name, NumberOfCores, "
                              "NumberOfLogicalProcessors | ConvertTo-Json -Compress"],
                             capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=15,
                             creationflags=_NO_WINDOW).stdout
        data = json.loads(out)
    except (OSError, subprocess.SubprocessError, ValueError):
        return info
    items = data if isinstance(data, list) else [data]
    info["name"] = (items[0].get("Name") or "").strip() or None
    info["cores"] = sum(int(i.get("NumberOfCores") or 0) for i in items) or None
    info["threads"] = sum(int(i.get("NumberOfLogicalProcessors") or 0) for i in items) or info["threads"]
    return info


def scan():
    return {"gpus": _gpus(), "ram": _ram(), "cpu": _cpu()}
