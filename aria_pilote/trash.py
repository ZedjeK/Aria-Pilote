"""Envoi de fichiers à la corbeille Windows (récupérables), au lieu d'une suppression définitive."""

import ctypes
import sys
from ctypes import wintypes

FO_DELETE = 0x3
FOF_SILENT = 0x4
FOF_NOCONFIRMATION = 0x10
FOF_ALLOWUNDO = 0x40
FOF_NOERRORUI = 0x400


class _FileOp(ctypes.Structure):
    _fields_ = [
        ("hwnd", wintypes.HWND),
        ("wFunc", wintypes.UINT),
        ("pFrom", ctypes.c_wchar_p),
        ("pTo", ctypes.c_wchar_p),
        ("fFlags", ctypes.c_ushort),
        ("fAnyOperationsAborted", wintypes.BOOL),
        ("hNameMappings", ctypes.c_void_p),
        ("lpszProgressTitle", ctypes.c_wchar_p),
    ]


def to_recycle_bin(paths):
    if sys.platform != "win32":
        raise OSError("Corbeille disponible uniquement sous Windows.")
    # Liste de chemins séparés par \0 et terminée par un double \0
    op = _FileOp(
        wFunc=FO_DELETE,
        pFrom="\0".join(str(p) for p in paths) + "\0\0",
        fFlags=FOF_ALLOWUNDO | FOF_NOCONFIRMATION | FOF_SILENT | FOF_NOERRORUI,
    )
    code = ctypes.windll.shell32.SHFileOperationW(ctypes.byref(op))
    if code != 0 or op.fAnyOperationsAborted:
        raise OSError(f"Envoi à la corbeille impossible (code {code}).")
