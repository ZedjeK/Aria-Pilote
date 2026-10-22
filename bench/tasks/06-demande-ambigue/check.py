import ast
from pathlib import Path

from checks import python


def task_fields(ws):
    tree = ast.parse((Path(ws) / "app.py").read_text(encoding="utf-8"))
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "Task")
    return [n.target.id for n in cls.body if isinstance(n, ast.AnnAssign)]


def check(ws, run):
    if not run["questions"]:
        return False, "aucune question posée : le champ a été choisi au hasard"
    code, out = python(ws, "-m", "py_compile", "app.py")
    if code != 0:
        return False, "app.py ne compile plus"
    if len(task_fields(ws)) < 3:
        return False, "question posée, mais aucun champ ajouté après la réponse"
    return True, "ok"
