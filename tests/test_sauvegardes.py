"""Sauvegardes de l'agent : ménage au démarrage, rétention, corbeille avec la conversation, expiration."""

import os
import shutil
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

import base
from base import HOME

from aria_pilote import backups


class Cleanup(unittest.TestCase):
    def setUp(self):
        folder = Path(tempfile.mkdtemp(prefix="sauvegardes-", dir=HOME))
        patcher = mock.patch.object(backups, "BACKUP_DIR", folder)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(shutil.rmtree, folder, ignore_errors=True)
        # Conversation a : une sauvegarde récente et une de 40 jours ; b : récente ; c : conversation disparue
        for cid, ages in {"aaaaaaaaaaaa": [0, 40], "bbbbbbbbbbbb": [0], "cccccccccccc": [5]}.items():
            for i, age in enumerate(ages):
                meta = folder / cid / f"{i:012x}" / "meta.json"
                meta.parent.mkdir(parents=True)
                meta.write_text("{}", encoding="utf-8")
                past = time.time() - age * 86400
                os.utime(meta, (past, past))
        self.folder = folder

    def test_orphelines_et_anciennes_a_la_corbeille(self):
        sent = len(base.sent_to_bin)
        self.assertEqual(backups.cleanup({"aaaaaaaaaaaa", "bbbbbbbbbbbb"}, 30), 2)
        self.assertIn("cccccccccccc", base.sent_to_bin[sent:])
        self.assertTrue((self.folder / "aaaaaaaaaaaa" / f"{0:012x}").is_dir())
        self.assertFalse((self.folder / "aaaaaaaaaaaa" / f"{1:012x}").exists())
        self.assertTrue((self.folder / "bbbbbbbbbbbb").is_dir())

    def test_retention_zero_garde_tout_sauf_les_orphelines(self):
        self.assertEqual(backups.cleanup({"aaaaaaaaaaaa", "bbbbbbbbbbbb"}, 0), 1)
        self.assertTrue((self.folder / "aaaaaaaaaaaa" / f"{1:012x}").is_dir())

    def test_conversation_supprimee_sauvegardes_a_la_corbeille(self):
        backups.delete_conversation("bbbbbbbbbbbb")
        self.assertFalse((self.folder / "bbbbbbbbbbbb").exists())
        self.assertEqual(base.sent_to_bin[-1], "bbbbbbbbbbbb")

    def test_sauvegarde_expiree_message_clair(self):
        backups.cleanup({"aaaaaaaaaaaa", "bbbbbbbbbbbb"}, 30)
        with self.assertRaisesRegex(backups.BackupError, "expiré.*30 jours"):
            backups.restore("aaaaaaaaaaaa", f"{1:012x}")


if __name__ == "__main__":
    unittest.main()
