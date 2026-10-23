"""Démarrage de l'appli avec la sortie redirigée (run.ps1 > journal.txt) : plus de plantage sur le « → »."""

import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

import httpx
from base import APP_PORT, BASE_URL, HOME, ORIGIN, SRC


class RedirectedStartup(unittest.TestCase):
    def test_demarre_et_repond_sortie_redirigee(self):
        log = Path(tempfile.mkdtemp(dir=HOME)) / "journal.txt"
        # Python sans mode UTF-8 forcé, comme un lancement ordinaire ; l'appli tourne dans le dossier de test
        env = {k: v for k, v in os.environ.items() if k not in ("PYTHONUTF8", "PYTHONIOENCODING")}
        with open(log, "w") as out:
            proc = subprocess.Popen([sys.executable, "-m", "aria_pilote"], cwd=SRC, stdout=out, stderr=subprocess.STDOUT,
                                    env=env, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            up = False
            try:
                for _ in range(60):
                    try:
                        up = httpx.get(f"{BASE_URL}/api/status", timeout=2).status_code == 200
                        break
                    except httpx.HTTPError:
                        if proc.poll() is not None:
                            break
                        time.sleep(0.5)
                if up:
                    httpx.post(f"{BASE_URL}/api/shutdown", headers=ORIGIN, timeout=10)
                proc.wait(timeout=30)
            finally:
                if proc.poll() is None:
                    proc.kill()
        text = log.read_text(encoding="utf-8", errors="replace")
        self.assertTrue(up, text[-400:])
        self.assertIn(f"Aria Pilote → http://127.0.0.1:{APP_PORT}", text)


if __name__ == "__main__":
    unittest.main()
