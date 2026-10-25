"""Chantier 4, étape 4A : images jointes aux messages, envoyées au modèle quand il a son projecteur vision."""
import base64
import io
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from PIL import Image

from aria_pilote import chat, context, conversations, images
from aria_pilote.server import ServerManager


def data_url(size=(2560, 1440), fmt="PNG"):
    buffer = io.BytesIO()
    Image.new("RGB", size, (30, 120, 200)).save(buffer, fmt)
    mime = "jpeg" if fmt == "JPEG" else fmt.lower()
    return f"data:image/{mime};base64,{base64.b64encode(buffer.getvalue()).decode()}"


class ImagesTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        patcher = mock.patch.object(conversations, "DATA_DIR", Path(self.tmp.name))
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(self.tmp.cleanup)
        self.conv = conversations.create()
        self.cid = self.conv["id"]

    def vision(self, on):
        patcher = mock.patch.object(ServerManager, "vision", new_callable=mock.PropertyMock, return_value=on)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_capture_reduite_en_png(self):
        image = images.save(self.cid, data_url())
        self.assertEqual((image["w"], image["h"]), (1280, 720))
        self.assertTrue(image["file"].endswith(".png"))
        self.assertTrue(images.path(self.cid, image["file"]).is_file())

    def test_photo_reste_en_jpeg_et_petite_image_intacte(self):
        image = images.save(self.cid, data_url((800, 600), "JPEG"))
        self.assertEqual((image["w"], image["h"]), (800, 600))
        self.assertTrue(image["file"].endswith(".jpg"))

    def test_donnees_invalides_refusees(self):
        for bad in ("pas une image", "data:image/png;base64,!!!", "data:image/png;base64," + base64.b64encode(b"texte").decode()):
            with self.assertRaises(images.ImageError):
                images.save(self.cid, bad)

    def test_nom_de_fichier_controle(self):
        for name in ("../config.json", "a.png", "..\\x.png"):
            with self.assertRaises(images.ImageError):
                images.path(self.cid, name)

    def test_envoi_au_modele_avec_vision(self):
        self.vision(True)
        image = images.save(self.cid, data_url())
        parts = chat.user_content(self.cid, "Que vois-tu ?", [image])
        self.assertEqual(parts[0]["type"], "image_url")
        self.assertTrue(parts[0]["image_url"]["url"].startswith("data:image/png;base64,"))
        self.assertEqual(parts[1], {"type": "text", "text": "Que vois-tu ?"})

    def test_modele_sans_vision_previent(self):
        self.vision(False)
        image = images.save(self.cid, data_url())
        text = chat.user_content(self.cid, "Que vois-tu ?", [image])
        self.assertIn("ne peut pas voir", text)
        self.assertTrue(text.endswith("Que vois-tu ?"))

    def test_comptage_estime_les_images(self):
        self.vision(True)
        image = images.save(self.cid, data_url())
        messages = [{"role": "user", "content": chat.user_content(self.cid, "Bonjour", [image])}]
        plain, extra = context._without_images(messages)
        self.assertEqual(plain[0]["content"], "Bonjour")
        self.assertEqual(extra, 40 * 23)  # 1280/32 x ceil(720/32)

    def test_images_supprimees_avec_la_conversation(self):
        images.save(self.cid, data_url())
        self.assertTrue(images.folder(self.cid).is_dir())
        conversations.delete(self.cid)
        self.assertFalse(images.folder(self.cid).exists())

    def test_titre_d_un_message_image_seule(self):
        conversations.add_user_message(self.conv, "", images=[{"file": "x.png", "w": 1, "h": 1}])
        self.assertEqual(self.conv["title"], "🖼 Image")
        self.assertEqual(self.conv["messages"][-1]["images"][0]["file"], "x.png")


if __name__ == "__main__":
    unittest.main()
