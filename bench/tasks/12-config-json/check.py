import json
from pathlib import Path


def _values(data):
    """Toutes les valeurs du JSON, à plat (le modèle choisit librement les noms et l'imbrication)."""
    if isinstance(data, dict):
        for value in data.values():
            yield from _values(value)
    elif isinstance(data, list):
        for value in data:
            yield from _values(value)
    else:
        yield data


def check(ws, run):
    path = Path(ws) / "config.json"
    if not path.is_file():
        return False, "config.json absent"
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError as exc:
        return False, f"config.json invalide (ligne {exc.lineno}) : {exc.msg}"
    values = [str(v).lower() for v in _values(data)]
    missing = [label for label, ok in [("port 8443", "8443" in values), ("rétention 30", "30" in values),
                                       ("SMS", any("sms" in v for v in values))] if not ok]
    if missing:
        return False, f"réglages absents : {', '.join(missing)}"
    warned = [c for c in run["calls"] if c["name"] in ("write_file", "edit_file", "multi_edit") and c.get("syntax") is False]
    return True, "ok (corrigé après l'avertissement)" if warned else "ok"
