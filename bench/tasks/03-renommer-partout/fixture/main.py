from facturation.devis import resume_devis
from facturation.factures import ligne_facture

print(ligne_facture("Câble RJ45", 12.5))
print(resume_devis([100, 50]))
