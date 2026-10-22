from checks import missing, unchanged


def check(ws, run):
    if not unchanged(ws, run):
        return False, "fichiers modifiés alors que la question ne demandait que de lire"
    absent = missing(run, "reglages.py", "37")
    if absent:
        return False, f"réponse incomplète : {', '.join(absent)} absent"
    return True, "ok"
