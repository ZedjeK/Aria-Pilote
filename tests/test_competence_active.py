"""Compétence active : gardée entière dans le prompt système pendant toute la tâche, même quand l'historique est
rogné ou résumé ; nom normalisé ; registre d'une compétence du projet."""

import shutil
import unittest

from base import workspace

from aria_pilote import agent, chat, persona, skills, tools


def skill_output(name):
    """Résultat réel de load_skill, tel que l'historique l'enregistre."""
    return tools.prepare_load_skill(None, {"name": name})["output"]


def long_task(skill="github-pr", steps=6):
    """Une demande, la compétence chargée par le modèle, puis `steps` appels d'outils aux longs résultats."""
    messages = [{"role": "user", "content": "Ouvre une pull request pour ma correction."},
                {"role": "assistant", "content": "", "tool_calls": [{"id": "s", "function": {"name": "load_skill"}}]},
                {"role": "tool", "tool_call_id": "s", "name": "load_skill", "status": "ok",
                 "args": {"name": skill}, "content": skill_output(skill)}]
    for n in range(steps):
        messages += [{"role": "assistant", "content": "", "tool_calls": [{"id": f"c{n}", "function": {"name": "read_file"}}]},
                     {"role": "tool", "tool_call_id": f"c{n}", "name": "read_file", "status": "ok",
                      "args": {"path": f"f{n}.py"}, "content": f"fichier {n} " + "x" * 3000}]
    return {"persona_id": "none", "messages": messages}


def middle(text):
    """Un passage du milieu : c'est lui que le rognage (début et fin gardés) faisait disparaître."""
    return text[len(text) // 2:len(text) // 2 + 80]


class ActiveSkillKept(unittest.TestCase):
    def test_entiere_apres_six_appels(self):
        conv = long_task()
        content = conv["messages"][2]["content"]
        prompt = agent._system_prompt(conv, None, None)
        self.assertIn(agent.ACTIVE_SKILL_PROMPT + content, prompt)
        built = chat.build_messages(conv, prompt)
        sent = [m for m in built if m["role"] == "tool"]
        self.assertEqual(sent[0]["content"], chat.SKILL_IN_PROMPT.format(name="github-pr"))
        self.assertLess(len(sent[1]["content"]), 3000)  # les vieux résultats sont toujours rognés
        # Le milieu de la compétence part une seule fois, dans le prompt système
        everything = "\n".join(m["content"] for m in built if isinstance(m["content"], str))
        self.assertEqual(everything.count(middle(content)), 1)

    def test_entiere_apres_un_resume(self):
        conv = long_task()
        content = conv["messages"][2]["content"]
        conv["summary"] = {"upto": 10, "keep": 0, "text": "- l'agent a lu f0 à f3"}
        prompt = agent._system_prompt(conv, None, None)
        built = chat.build_messages(conv, prompt)
        self.assertIn(middle(content), built[0]["content"])
        self.assertNotIn("Compétence « github-pr » chargée", "\n".join(m["content"] for m in built[1:]
                                                                       if isinstance(m["content"], str)))

    def test_sans_la_copie_dans_le_prompt_rien_ne_change(self):
        # Sous-agent : son prompt ne recopie pas la compétence, le résultat reste dans l'historique
        conv = long_task(steps=1)
        built = chat.build_messages(conv, "Tu es un sous-agent.")
        self.assertEqual(built[3]["content"], conv["messages"][2]["content"])

    def test_nouvelle_demande_compétence_retirée(self):
        conv = long_task()
        conv["messages"].append({"role": "user", "content": "Merci, et sinon ?"})
        prompt = agent._system_prompt(conv, None, None)
        self.assertNotIn(agent.ACTIVE_SKILL_PROMPT, prompt)
        built = chat.build_messages(conv, prompt)
        self.assertIn("caractères retirés", built[3]["content"])  # ancienne tâche : rognée comme les autres

    def test_slash_nom_reste_dans_le_message(self):
        conv = {"persona_id": "none", "messages": [{"role": "user", "content": "relis ça", "skill": "revue-code"}]}
        prompt = agent._system_prompt(conv, None, None)
        self.assertNotIn(agent.ACTIVE_SKILL_PROMPT, prompt)
        self.assertIn("revue-code", chat.build_messages(conv, prompt)[-1]["content"])

    def test_fichier_annexe_pas_compétence_active(self):
        conv = long_task(steps=0)
        conv["messages"][2]["args"] = {"name": "github-pr", "file": "modele.md"}
        self.assertNotIn(agent.ACTIVE_SKILL_PROMPT, agent._system_prompt(conv, None, None))


class SkillName(unittest.TestCase):
    def test_nom_normalisé(self):
        conv = {"messages": [{"role": "user", "content": "une idée de nom"},
                             {"role": "tool", "name": "load_skill", "status": "ok", "args": {"name": "Créatif"},
                              "content": "…"}]}
        self.assertEqual(agent.active_skill(conv), "creatif")

    def test_registre_d_une_compétence_du_projet(self):
        root = workspace("projet-registre")
        folder = root / skills.PROJECT_SUBDIR / "maison"
        folder.mkdir(parents=True)
        self.addCleanup(shutil.rmtree, root / ".aria", True)
        (folder / "SKILL.md").write_text("---\nname: maison\ndescription: Les conventions de la maison.\n"
                                         "registre: professionnel\n---\n# Maison\n", encoding="utf-8")
        self.assertEqual(persona.register_for({}, "maison", str(root)), "professionnel")
        self.assertEqual(persona.register_for({}, "maison"), persona.DEFAULT_REGISTER)  # sans dossier : inconnue


if __name__ == "__main__":
    unittest.main()
