import time


def _appel_api(ville):
    time.sleep(2)  # simulation d'un service lent
    return {"ville": ville, "temperature": 14}


def prevision(ville):
    return _appel_api(ville)
