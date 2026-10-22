def celsius_vers_fahrenheit(c):
    return c * 9 / 5 + 32


def fahrenheit_vers_celsius(f):
    return (f - 32) * 5 / 9


def arrondi(valeur):
    return int(valeur)  # tronque au lieu d'arrondir


def kelvin(c):
    if c < 0:
        return None  # oubli : les températures négatives sont valides
    return c + 273.15
