"""Mise à jour des moteurs (Réglages › Moteurs) : GitHub et téléchargements simulés, faux zip, jamais de réseau."""

import asyncio
import hashlib
import io
import json
import shutil
import unittest
import zipfile
from unittest import mock

import httpx
from base import HOME, ORIGIN, client, fake_gguf

from aria_pilote import engines
from aria_pilote.server import manager

ROOT = HOME / "engines"
LLAMA_ZIP = "llama-{tag}-bin-win-cuda-12.4-x64.zip"
CUDART_ZIP = "cudart-llama-bin-win-cuda-12.4-x64.zip"


def zip_bytes(files):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name, data in files.items():
            z.writestr(name, data)
    return buf.getvalue()


def asset(name, data, digest=True):
    a = {"name": name, "size": len(data), "browser_download_url": f"https://example.invalid/{name}"}
    if digest:
        a["digest"] = "sha256:" + hashlib.sha256(data).hexdigest()
    return a


def release(tag, assets, draft=False):
    return {"tag_name": tag, "draft": draft, "published_at": "2026-10-30T10:00:00Z", "assets": assets}


def build(tag, server=b"exe"):
    """Une release complète (zip llama + runtime CUDA) et le contenu de ses fichiers, par nom."""
    files = {LLAMA_ZIP.format(tag=tag): zip_bytes({"llama-server.exe": server + tag.encode(), "ggml.dll": b"dll"}),
             CUDART_ZIP: zip_bytes({"cudart64_12.dll": b"cuda"})}
    return release(tag, [asset(n, d) for n, d in files.items()]), files


def serve(files, hits=None):
    """Remplace httpx.AsyncClient par un client qui sert ces fichiers (et compte les requêtes)."""
    real = httpx.AsyncClient

    def handler(request):
        name = request.url.path.rsplit("/", 1)[-1]
        if hits is not None:
            hits.append(name)
        return httpx.Response(200, content=files[name]) if name in files else httpx.Response(404)

    return mock.patch.object(engines.httpx, "AsyncClient", lambda **kw: real(transport=httpx.MockTransport(handler), **kw))


def installed(name="llama.cpp"):
    return (engines._manifest(engines.engine_dir(name)) or {}).get("tag")


class Base(unittest.TestCase):
    def setUp(self):
        shutil.rmtree(ROOT, ignore_errors=True)
        engines._latest.clear()
        engines._job = None
        self.addCleanup(shutil.rmtree, ROOT, True)
        self.addCleanup(engines._latest.clear)
        self.addCleanup(setattr, engines, "_job", None)

    def install(self, tag, name="llama.cpp", hits=None):
        rel, files = build(tag)
        with serve(files, hits):
            asyncio.run(engines.install(name, rel, "cuda-12.4"))
        return rel


class Versions(unittest.TestCase):
    def test_plus_recent(self):
        self.assertTrue(engines.newer("b12007", "b11763"))
        self.assertFalse(engines.newer("b11763", "b11763"))
        self.assertFalse(engines.newer("b9999", "b11763"))  # numérique, pas alphabétique
        self.assertTrue(engines.newer("prism-b10831-9d9efea", "prism-b10826-c9c34bc"))
        self.assertTrue(engines.newer("b12007", None))  # pas encore installé
        self.assertFalse(engines.newer(None, "b11763"))

    def test_choix_de_la_release(self):
        ok, _ = build("b12005")
        no_cudart = release("b12007", [ok["assets"][0]])
        releases = [release("b12009", ok["assets"], draft=True), release("master-abc", ok["assets"]), no_cudart, ok]
        self.assertEqual(engines.pick_release("llama.cpp", releases, "cuda-12.4")["tag_name"], "b12005")
        self.assertIsNone(engines.pick_release("prism", releases, "cuda-12.4"))  # pas de tag prism-b…
        self.assertIsNone(engines.pick_release("llama.cpp", releases, "vulkan"))


class Installation(Base):
    def test_installe_puis_garde_la_precedente(self):
        self.install("b100")
        exe = engines.find_server("llama.cpp")
        self.assertEqual(exe.read_bytes(), b"exeb100")
        self.assertTrue((exe.parent / "cudart64_12.dll").exists())  # runtime CUDA à côté de l'exe
        self.assertEqual(engines._manifest(engines.engine_dir("llama.cpp")),
                         {"repo": "ggml-org/llama.cpp", "tag": "b100", "backend": "cuda-12.4"})
        self.assertIsNone(engines.info("llama.cpp")["previous"])

        self.install("b200")
        self.install("b300")
        info = engines.info("llama.cpp")
        self.assertEqual((info["release"]["tag"], info["previous"]), ("b300", "b200"))
        self.assertEqual(sorted(p.name for p in ROOT.iterdir()), ["_downloads", "llama.cpp", "llama.cpp.prev"])
        # Le zip de la build est effacé, le runtime CUDA gardé pour la prochaine fois
        self.assertEqual([p.name for p in (ROOT / "_downloads").iterdir()], [CUDART_ZIP])

    def test_runtime_cuda_reutilise(self):
        self.install("b100")
        hits = []
        self.install("b200", hits=hits)
        self.assertEqual(hits, [LLAMA_ZIP.format(tag="b200")])
        # Même runtime publié par PrismML (même empreinte) : réutilisé aussi
        hits.clear()
        self.install("prism-b1-abc", name="prism", hits=hits)
        self.assertEqual(hits, [LLAMA_ZIP.format(tag="prism-b1-abc")])

    def test_empreinte_differente_refusee(self):
        self.install("b100")
        rel, files = build("b200")
        files[LLAMA_ZIP.format(tag="b200")] = files[LLAMA_ZIP.format(tag="b200")][:-1] + b"X"  # même taille
        with serve(files), self.assertRaisesRegex(engines.UpdateError, "SHA-256"):
            asyncio.run(engines.install("llama.cpp", rel, "cuda-12.4"))
        self.assertEqual(installed(), "b100")  # rien n'a bougé
        self.assertFalse(list((ROOT / "_downloads").rglob("*.part")))

    def test_zip_sans_llama_server(self):
        files = {LLAMA_ZIP.format(tag="b200"): zip_bytes({"autre.exe": b"x"}), CUDART_ZIP: zip_bytes({"c.dll": b"c"})}
        rel = release("b200", [asset(n, d) for n, d in files.items()])
        with serve(files), self.assertRaisesRegex(engines.UpdateError, "llama-server.exe introuvable"):
            asyncio.run(engines.install("llama.cpp", rel, "cuda-12.4"))
        self.assertFalse(engines._side("llama.cpp", ".new").exists())

    def test_modele_charge_entre_temps(self):
        self.install("b100")
        rel, files = build("b200")
        with serve(files), self.assertRaisesRegex(engines.UpdateError, "entre-temps"):
            asyncio.run(engines.install("llama.cpp", rel, "cuda-12.4", in_use=lambda name: True))
        self.assertEqual(installed(), "b100")
        self.assertFalse(engines._side("llama.cpp", ".new").exists())

    def test_echange_rate_tout_remis_en_place(self):
        self.install("b100")
        self.install("b200")
        missing = engines._side("llama.cpp", ".new")  # le renommage de la version neuve échoue : elle n'existe pas
        with self.assertRaises(OSError):
            engines._swap("llama.cpp", missing)
        info = engines.info("llama.cpp")
        self.assertEqual((info["release"]["tag"], info["previous"]), ("b200", "b100"))
        self.assertFalse(engines._side("llama.cpp", ".old").exists())

    def test_revenir_puis_revenir_encore(self):
        self.install("b100")
        self.install("b200")
        free = lambda name: False  # noqa: E731
        self.assertEqual(engines.rollback("llama.cpp", free)["release"]["tag"], "b100")
        self.assertEqual(engines.info("llama.cpp")["previous"], "b200")
        self.assertEqual(engines.rollback("llama.cpp", free)["release"]["tag"], "b200")
        with self.assertRaisesRegex(engines.UpdateError, "utilisé"):
            engines.rollback("llama.cpp", lambda name: True)
        with self.assertRaisesRegex(engines.UpdateError, "Aucune version précédente"):
            engines.rollback("prism", free)


class Verification(Base):
    def test_derniere_version_et_erreur(self):
        self.install("b100")
        rel, _ = build("b200")

        async def github(path):
            if "PrismML" in path:
                raise RuntimeError("GitHub limite les requêtes pour l'instant : réessaie plus tard.")
            return [rel]

        with mock.patch.object(engines, "_github", github):
            result = {e["name"]: e for e in asyncio.run(engines.check())}
        self.assertEqual((result["llama.cpp"]["latest"], result["llama.cpp"]["newer"]), ("b200", True))
        self.assertIsNone(result["prism"]["latest"])
        self.assertIn("limite", result["prism"]["latest_error"])
        self.assertFalse(result["prism"]["newer"])
        json.dumps(result)  # la release brute de GitHub reste côté serveur : la réponse est du JSON simple


class MiseAJour(Base):
    def run_update(self, install, in_use=lambda name: False):
        rel, _ = build("b200")
        engines._latest["llama.cpp"] = {"tag": "b200", "error": None, "release": rel}

        async def go():
            with mock.patch.object(engines, "install", install):
                job = await engines.update("llama.cpp", in_use)
                self.assertTrue(engines.busy("llama.cpp"))
                for _ in range(50):
                    await asyncio.sleep(0.01)
                    if not engines.busy("llama.cpp"):
                        break
            return job
        return asyncio.run(go())

    def test_tache_de_fond(self):
        job = self.run_update(mock.AsyncMock())
        self.assertEqual((job["tag"], job["status"]), ("b200", "downloading"))
        self.assertEqual(engines.info("llama.cpp")["update"]["status"], "done")
        self.assertIsNone(engines.info("prism")["update"])

    def test_echec_affiche(self):
        self.run_update(mock.AsyncMock(side_effect=engines.UpdateError("fichier abîmé")))
        self.assertEqual(engines.info("llama.cpp")["update"]["error"], "fichier abîmé")

    def test_refus_si_le_moteur_tourne(self):
        with self.assertRaisesRegex(engines.UpdateError, "éjecte"):
            self.run_update(mock.AsyncMock(), in_use=lambda name: True)


class Api(Base):
    def setUp(self):
        super().setUp()
        self.client = client()

    def test_moteur_inconnu(self):
        self.assertEqual(self.client.post("/api/engines/vllm/update", headers=ORIGIN).status_code, 404)
        self.assertEqual(self.client.post("/api/engines/vllm/rollback", headers=ORIGIN).status_code, 404)

    def test_refus_pendant_que_le_modele_tourne(self):
        with mock.patch.object(manager, "engine", "llama.cpp"), mock.patch.object(manager, "state", "ready"):
            r = self.client.post("/api/engines/llama.cpp/update", headers=ORIGIN)
        self.assertEqual(r.status_code, 409)
        self.assertIn("éjecte", r.json()["detail"])

    def test_pas_de_chargement_pendant_la_mise_a_jour(self):
        fake_gguf(HOME / "models")
        model = self.client.get("/api/models?refresh=true").json()[0]
        engines._job = {"name": "llama.cpp", "tag": "b200", "status": "installing", "done_bytes": 1, "total_bytes": 1,
                        "error": None}
        r = self.client.post("/api/load", json={"model_id": model["id"]}, headers=ORIGIN)
        self.assertEqual(r.status_code, 409)
        self.assertIn("mise à jour", r.json()["detail"])

    def test_liste_avec_searxng(self):
        names = [e["name"] for e in self.client.get("/api/engines").json()]
        self.assertEqual(names[:2], ["llama.cpp", "prism"])


if __name__ == "__main__":
    unittest.main()
