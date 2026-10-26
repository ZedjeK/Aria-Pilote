"""Chantier 3 : les anciens modes deviennent des compétences au format du modèle (formulaire <-> fichier)."""
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import base  # noqa: F401  (en premier : Aria tourne dans un dossier temporaire)
from aria_pilote import persona, skills

BUILTINS = ("analyste", "createur-competence", "creatif", "dev", "message-commit", "prof", "projet", "redaction",
            "resume", "revue-code", "script-powershell", "securite")


class FormatTest(unittest.TestCase):
    def test_toutes_les_integrees_suivent_le_modele(self):
        found = skills._scan(skills.BUILTIN_DIR, "intégrée")
        self.assertEqual(tuple(sorted(found)), BUILTINS)
        for name, skill in found.items():
            text = (skill.folder / "SKILL.md").read_text(encoding="utf-8")
            fields = skills.parse_markdown(text)
            self.assertIsNotNone(fields, name)
            self.assertTrue(fields["objectif"], name)
            self.assertTrue(fields["eviter"], name)
            self.assertTrue(fields["verifier"], name)
            # L'aller-retour ne perd rien : le fichier regénéré est identique
            self.assertEqual(skills.render_markdown(fields), text, name)

    def test_registres_suggeres(self):
        found = skills._scan(skills.BUILTIN_DIR, "intégrée")
        self.assertEqual(found["securite"].registre, "professionnel")
        self.assertEqual(found["prof"].registre, "naturel")
        self.assertEqual(found["dev"].registre, "sobre")

    def test_texte_libre_reste_en_mode_texte(self):
        text = "---\nname: libre\ndescription: Un essai.\n---\nFais comme tu le sens, en prose.\n"
        self.assertIsNone(skills.parse_markdown(text))
        self.assertIsNone(skills.parse_markdown("---\nname: x\ndescription: y\n---\n## Mes notes\n- a\n"))

    def test_sections_vides_omises(self):
        fields = {**skills.empty_fields(), "name": "mini", "description": "Essai.", "objectif": "Un but."}
        text = skills.render_markdown(fields)
        self.assertNotIn("## Méthode", text)
        self.assertNotIn("## Exemple", text)
        self.assertEqual(skills.parse_markdown(text)["objectif"], "Un but.")


class UserSkillsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        patcher = mock.patch.object(skills, "USER_DIR", Path(self.tmp.name))
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(self.tmp.cleanup)

    def test_formulaire_enregistre(self):
        fields = {**skills.empty_fields(), "name": "revue-ansible", "description": "Relire un playbook Ansible.",
                  "registre": "professionnel", "objectif": "Un playbook sûr.",
                  "methode": [{"cas": "", "etapes": ["Lis le playbook.", "Lance ansible-lint."]}]}
        skill = skills.save_user_fields(fields)
        self.assertEqual((skill.source, skill.registre), ("perso", "professionnel"))
        back = skills.to_dict(skill, with_body=True)["fields"]
        self.assertEqual(back["methode"][0]["etapes"], ["Lis le playbook.", "Lance ansible-lint."])

    def test_description_obligatoire(self):
        with self.assertRaises(skills.SkillError):
            skills.save_user_fields({**skills.empty_fields(), "name": "vide"})

    def test_mode_texte_garde_le_registre(self):
        skill = skills.save_user_skill("notes", "", "---\ndescription: Prendre des notes.\nregistre: naturel\n---\nCorps.")
        self.assertEqual(skill.registre, "naturel")

    def test_personnaliser_une_integree(self):
        skill = skills.customize("dev")
        self.assertEqual(skill.source, "perso")
        self.assertTrue(skill.editable)
        self.assertEqual(skills.discover()["dev"].source, "perso")
        with self.assertRaises(skills.SkillError):
            skills.customize("dev")  # déjà personnalisée
        with self.assertRaises(skills.SkillError):
            skills.customize("inconnue")


class SelectionTest(unittest.TestCase):
    def test_prompt_section_guide_le_choix(self):
        section = skills.prompt_section()
        self.assertIn("charge-la d'abord avec load_skill", section)
        self.assertIn("n'en charge aucune", section)
        self.assertIn("- dev : ", section)
        self.assertIn("Objectif : Du code qui fonctionne", section)

    def test_nom_avec_accent_ou_majuscule(self):
        self.assertEqual(skills.get("créatif").name, "creatif")
        self.assertEqual(skills.get("Revue code").name, "revue-code")

    def test_registre_suggere_si_le_persona_ne_dit_rien(self):
        p = {"registers": {"discussion": "naturel", "dev": "naturel"}}
        self.assertEqual(persona.register_for(p, "dev"), "naturel")  # réglé dans le persona
        self.assertEqual(persona.register_for(p, "securite"), "professionnel")  # suggéré par la compétence
        self.assertEqual(persona.register_for(p, "inconnue"), persona.DEFAULT_REGISTER)
        self.assertEqual(persona.register_for(p), "naturel")


if __name__ == "__main__":
    unittest.main()
