from inventaire.stock import a_commander

ARTICLES = [{"nom": "vis", "quantite": 120}, {"nom": "écrous", "quantite": 8}]

if __name__ == "__main__":
    print(a_commander(ARTICLES))
