"""Lecture des métadonnées d'un fichier GGUF (en-tête uniquement, sans charger les poids)."""

import struct

GGUF_MAGIC = b"GGUF"

# Types de valeurs définis par la spec GGUF
_SCALARS = {
    0: "<B", 1: "<b", 2: "<H", 3: "<h", 4: "<I", 5: "<i",
    6: "<f", 7: "<?", 10: "<Q", 11: "<q", 12: "<d",
}
_STRING = 8
_ARRAY = 9

# Garde-fou contre un fichier corrompu qui annoncerait une chaîne gigantesque
_MAX_STRING = 64 * 1024 * 1024


class GGUFError(Exception):
    pass


def read_metadata(path):
    """Renvoie {"version", "tensor_count", "metadata"}.

    Le contenu des tableaux (vocabulaire du tokenizer...) est sauté : seule
    leur longueur est conservée.
    """
    with open(path, "rb", buffering=1024 * 1024) as f:
        if f.read(4) != GGUF_MAGIC:
            raise GGUFError("pas un fichier GGUF")
        (version,) = struct.unpack("<I", _read_exact(f, 4))
        if version < 2:
            raise GGUFError(f"version GGUF {version} non supportée")
        tensor_count, kv_count = struct.unpack("<QQ", _read_exact(f, 16))
        metadata = {}
        for _ in range(kv_count):
            key = _read_str(f)
            (vtype,) = struct.unpack("<I", _read_exact(f, 4))
            metadata[key] = _read_value(f, vtype)
    return {"version": version, "tensor_count": tensor_count, "metadata": metadata}


def _read_exact(f, n):
    data = f.read(n)
    if len(data) != n:
        raise GGUFError("fichier tronqué")
    return data


def _read_str(f):
    (n,) = struct.unpack("<Q", _read_exact(f, 8))
    if n > _MAX_STRING:
        raise GGUFError("chaîne invalide (fichier corrompu ?)")
    return _read_exact(f, n).decode("utf-8", errors="replace")


def _read_value(f, vtype):
    if vtype in _SCALARS:
        fmt = _SCALARS[vtype]
        return struct.unpack(fmt, _read_exact(f, struct.calcsize(fmt)))[0]
    if vtype == _STRING:
        return _read_str(f)
    if vtype == _ARRAY:
        itype, count = struct.unpack("<IQ", _read_exact(f, 12))
        if itype in _SCALARS:
            f.seek(count * struct.calcsize(_SCALARS[itype]), 1)
        elif itype == _STRING:
            for _ in range(count):
                (n,) = struct.unpack("<Q", _read_exact(f, 8))
                f.seek(n, 1)
        else:
            for _ in range(count):
                _read_value(f, itype)
        return {"array_length": count}
    raise GGUFError(f"type de valeur inconnu : {vtype}")
