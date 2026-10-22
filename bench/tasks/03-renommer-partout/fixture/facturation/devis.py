from . import calculs


def resume_devis(lignes):
    ht = sum(lignes)
    return {"ht": ht, "tva": calculs.calc_tva(ht)}
