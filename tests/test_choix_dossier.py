"""Fenêtre « Choisir un dossier » : processus à part simulé (jamais de vraie fenêtre pendant les tests)."""

import asyncio
import json
import subprocess
import sys
import unittest
from unittest import mock

from base import HOME, ORIGIN, client

from aria_pilote import pick_folder


class FakeProc:
    def __init__(self, out=b"", code=0, hang=False):
        self.out, self.returncode, self.hang = out, code, hang
        self.killed = False

    def communicate(self, timeout=None):
        if self.hang and not self.killed:
            raise subprocess.TimeoutExpired("python", timeout)
        return self.out, b""

    def kill(self):
        self.killed = True


def fake_popen(proc, seen=None):
    def popen(args, **kwargs):
        if seen is not None:
            seen["args"] = args
        return proc
    return popen


@unittest.skipUnless(sys.platform == "win32", "la fenêtre de choix est propre à Windows")
class Picker(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch.object(pick_folder.jobs, "attach", lambda proc: None)
        patcher.start()
        self.addCleanup(patcher.stop)

    def pick(self, proc, initial=""):
        seen = {}
        with mock.patch.object(pick_folder.subprocess, "Popen", fake_popen(proc, seen)):
            return asyncio.run(pick_folder.pick(initial, "Titre")), seen

    def test_dossier_choisi_en_chemin_windows(self):
        path, seen = self.pick(FakeProc(json.dumps({"path": "C:/Users/zeman/Desktop/projet"}).encode()), str(HOME))
        self.assertEqual(path, r"C:\Users\zeman\Desktop\projet")
        self.assertEqual(seen["args"][3:], [str(HOME), "Titre"])  # arguments séparés : jamais d'interprétation

    def test_annulation(self):
        self.assertIsNone(self.pick(FakeProc(json.dumps({"path": None}).encode()))[0])

    def test_fenetre_oubliee_fermee(self):
        proc = FakeProc(hang=True)
        self.assertIsNone(self.pick(proc)[0])
        self.assertTrue(proc.killed)

    def test_tkinter_absent(self):
        with self.assertRaises(pick_folder.PickerUnavailable):
            self.pick(FakeProc(code=pick_folder.UNAVAILABLE))

    def test_depart_sur_le_parent_existant(self):
        self.assertEqual(pick_folder._initial(str(HOME / "efface" / "sous-dossier")), str(HOME))
        self.assertEqual(pick_folder._initial(""), "")


class PickerApi(unittest.TestCase):
    def setUp(self):
        self.api = client()

    def post(self):
        return self.api.post("/api/pick-folder", json={"initial": "", "title": "x"}, headers=ORIGIN)

    def test_reponses_de_l_api(self):
        with mock.patch.object(pick_folder, "pick", mock.AsyncMock(return_value=r"C:\projet")):
            self.assertEqual(self.post().json(), {"path": r"C:\projet"})
        with mock.patch.object(pick_folder, "pick", mock.AsyncMock(side_effect=pick_folder.PickerBusy("ouverte"))):
            self.assertEqual(self.post().status_code, 409)  # une seule fenêtre à la fois
        with mock.patch.object(pick_folder, "pick", mock.AsyncMock(side_effect=pick_folder.PickerUnavailable("non"))):
            self.assertEqual(self.post().status_code, 501)  # la page passe alors à la saisie du chemin

    def test_une_seule_fenetre_a_la_fois(self):
        async def scenario():
            async with pick_folder._lock:
                with self.assertRaises(pick_folder.PickerBusy):
                    await pick_folder.pick()
        asyncio.run(scenario())


if __name__ == "__main__":
    unittest.main()
