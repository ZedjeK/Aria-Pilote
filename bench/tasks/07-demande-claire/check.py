import ast
from pathlib import Path

from checks import python


def check(ws, run):
    if run["questions"]:
        return False, f"question inutile : {run['questions'][0].get('question', '')[:60]}"
    code, out = python(ws, "-m", "py_compile", "app.py")
    if code != 0:
        return False, "app.py ne compile plus"
    source = (Path(ws) / "app.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "Task")
    field = next((n for n in cls.body if isinstance(n, ast.AnnAssign) and n.target.id == "priority"), None)
    if field is None:
        return False, "champ priority absent de Task"
    if field.value is None or "medium" not in ast.unparse(field.value):
        return False, "priority n'a pas medium par défaut"
    if not all(v in source for v in ("low", "high")):
        return False, "valeurs low / high non prises en compte"
    return True, "ok"
