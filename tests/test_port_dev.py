"""Ports de la version de développement : à côté de la version installée, jamais à sa place."""

import contextlib
import io
import json
import sys
import unittest

from base import HOME, SRC

from aria_pilote import config

sys.path.insert(0, str(SRC / "tools"))
import build_portable  # noqa: E402

SETTINGS = HOME / "services" / "searxng" / "settings.yml"


class DevPorts(unittest.TestCase):
    def setUp(self):
        saved = {k: config.get()[k] for k in ("app_port", "llama_port", "searxng_url", "model_dirs")}
        self.addCleanup(config.update, saved)  # par config, pas par le fichier : elle garde une copie en mémoire
        SETTINGS.parent.mkdir(parents=True, exist_ok=True)
        SETTINGS.write_text("server:\n  bind_address: \"127.0.0.1\"\n  port: 8888\n", encoding="utf-8")
        self.addCleanup(SETTINGS.unlink)
        self.release = HOME.parent / "Aria-Pilote-9.9.9"

    def point(self):
        with contextlib.redirect_stdout(io.StringIO()):
            build_portable.point_dev_config(self.release)
        return json.loads((HOME / "config.json").read_text(encoding="utf-8"))

    def test_ports_par_defaut_deplaces(self):
        config.update({"app_port": 8700, "llama_port": 8701, "searxng_url": "http://127.0.0.1:8888"})
        cfg = self.point()
        self.assertEqual((cfg["app_port"], cfg["llama_port"], cfg["searxng_url"]),
                         (8710, 8711, "http://127.0.0.1:8898"))
        self.assertIn("port: 8898", SETTINGS.read_text(encoding="utf-8"))
        self.assertEqual(cfg["model_dirs"], ["../Aria-Pilote-9.9.9/models"])
        self.assertEqual(self.point()["app_port"], 8710)  # une seconde construction ne change rien

    def test_port_choisi_a_la_main_garde(self):
        config.update({"app_port": 9000, "llama_port": 8701, "searxng_url": "http://127.0.0.1:7777"})
        cfg = self.point()
        self.assertEqual((cfg["app_port"], cfg["llama_port"], cfg["searxng_url"]),
                         (9000, 8711, "http://127.0.0.1:7777"))
        self.assertIn("port: 8888", SETTINGS.read_text(encoding="utf-8"))  # SearXNG non plus


if __name__ == "__main__":
    unittest.main()
