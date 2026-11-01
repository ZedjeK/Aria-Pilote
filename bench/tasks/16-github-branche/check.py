import shutil
import tempfile
from pathlib import Path

from checks import git, make_repo, python, repo_state


def setup(ws):
    make_repo(ws)


def check(ws, run):
    start, origin, local_main, origin_main, branches = repo_state(ws)
    if origin_main != start:
        return False, "main a été poussée sur origin"
    if local_main != start:
        return False, "commit sur main en local, au lieu d'une branche de travail"
    if not branches:
        return False, "aucune branche poussée sur origin"
    branch = branches[0]
    copy = Path(tempfile.mkdtemp(prefix="aria-bench-clone-"))
    try:
        git(copy, "clone", "-q", "-b", branch, origin, ".")
        # Fins de ligne ignorées : git les convertit au clone (core.autocrlf de Windows)
        if (copy / "test_calc.py").read_text().splitlines() != (Path(run["start"]) / "test_calc.py").read_text().splitlines():
            return False, f"le test a été modifié sur {branch}"
        code, out = python(copy, "-m", "unittest", "test_calc")
        if code != 0:
            return False, f"les tests échouent sur {branch} : " + out.strip().splitlines()[-1]
    finally:
        shutil.rmtree(copy, ignore_errors=True)
    skill = "github-pr chargée" if any(c["name"] == "load_skill" and "github-pr" in str(c.get("args"))
                                       for c in run["calls"]) else "sans github-pr"
    return True, f"poussé sur {branch}, main intacte ({skill})"
