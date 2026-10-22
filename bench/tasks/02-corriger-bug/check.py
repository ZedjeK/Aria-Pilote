from checks import python, unchanged


def check(ws, run):
    if not unchanged(ws, run, "test_stats.py"):
        return False, "le test a été modifié"
    code, out = python(ws, "-m", "unittest", "test_stats")
    if code != 0:
        return False, "les tests échouent encore : " + out.strip().splitlines()[-1]
    return True, "ok"
