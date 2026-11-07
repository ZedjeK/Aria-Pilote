"""SearXNG embarqué (AGPL-3.0) : l'offre de source cite le commit exact du code livré, et sa licence l'accompagne."""

import json
import sys
import tempfile
import unittest
from pathlib import Path

from base import HOME, SRC

sys.path.insert(0, str(SRC / "tools"))
import build_portable  # noqa: E402
import install_searxng  # noqa: E402

COMMIT = "0123456789abcdef0123456789abcdef01234567"


class OffreDeSource(unittest.TestCase):
    def setUp(self):
        self.service = Path(tempfile.mkdtemp(dir=HOME)) / "searxng"
        self.src = self.service / "src"
        self.src.mkdir(parents=True)

    def test_notice_avec_commit_et_lien(self):
        (self.src / "LICENSE").write_text("GNU AFFERO GENERAL PUBLIC LICENSE", encoding="utf-8")
        install_searxng.write_source_notice(self.service, self.src, COMMIT)
        text = (self.service / install_searxng.SOURCE_NOTICE).read_text(encoding="utf-8")
        self.assertIn(f"commit {COMMIT} de searxng/searxng", text)
        self.assertIn(f"https://github.com/searxng/searxng/tree/{COMMIT}", text)
        self.assertIn("AGPL-3.0", text)
        self.assertIn("src\\LICENSE", text)
        self.assertIn("searx/valkeydb.py", text)  # la retouche Windows est signalée

    def test_licence_absente_refusee(self):
        with self.assertRaises(SystemExit):
            install_searxng.write_source_notice(self.service, self.src, COMMIT)
        self.assertFalse((self.service / install_searxng.SOURCE_NOTICE).exists())

    def test_commit_du_code_recopie(self):
        # Code de dev recopié : son commit (installé avec --commit), pas celui épinglé par défaut
        (self.service / "service.json").write_text(json.dumps({"commit": COMMIT}), encoding="utf-8")
        self.assertEqual(build_portable.searxng_commit(self.src), COMMIT)
        # Pas de code de dev : téléchargé au commit épinglé
        self.assertEqual(build_portable.searxng_commit(self.service / "absent"), install_searxng.COMMIT)


if __name__ == "__main__":
    unittest.main()
