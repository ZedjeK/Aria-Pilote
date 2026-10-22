import re
from pathlib import Path

from checks import powershell


def setup(ws):
    # 5 fichiers .log répartis dans des sous-dossiers, plus des leurres
    for rel in ["web/acces.log", "web/erreurs.log", "web/2026/janvier.log", "dns/requetes.log", "systeme.log",
                "web/notes.txt", "dns/requetes.log.bak", "lisezmoi.md"]:
        path = Path(ws) / "journaux" / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("ligne de journal\n", encoding="utf-8")


def check(ws, run):
    script = Path(ws) / "compter.ps1"
    if not script.is_file():
        return False, "compter.ps1 absent"
    code, out = powershell(ws, str(script))
    if code != 0:
        return False, f"le script échoue (code {code}) : {out.strip()[-120:]}"
    if not re.search(r"Total\s*:\s*5\b", out):
        return False, f"sortie inattendue : {out.strip()[-80:]!r} (attendu : Total : 5)"
    return True, "ok"
