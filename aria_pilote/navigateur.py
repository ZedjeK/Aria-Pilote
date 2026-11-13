"""Navigateur piloté : un Chromium embarqué, lancé par Playwright, visible, sur un profil à part.

- Le navigateur est celui d'Aria (chromium\\), jamais celui de l'utilisateur : même version pour tout le monde, et
  Opera, Firefox ou Edge ne sont jamais touchés. Il fait partie du code de l'appli, remplacé à chaque mise à jour :
  Playwright exige la version de Chromium qui va avec la sienne.
- Le profil (data\\navigateur\\profil) n'a ni les cookies, ni les mots de passe, ni l'historique de l'utilisateur.
- Playwright parle à Chromium par un tube, pas par un port réseau : aucun autre programme du PC ne peut le piloter.
- Un seul navigateur pour toute l'appli ; il s'arrête avec Aria, même si elle est tuée (job object).
"""

import asyncio
import os

from . import config, jobs, trash

BROWSERS_DIR = config.ROOT / "chromium"
PROFILE_DIR = config.ROOT / "data" / "navigateur" / "profil"


class NavigateurError(Exception):
    pass


def _revision(exe):
    """Numéro de build de Playwright dans « chromium-1254\\chrome-win64\\chrome.exe » (0 s'il est illisible)."""
    suffix = exe.parts[-3].rpartition("-")[2]
    return int(suffix) if suffix.isdigit() else 0


def executable():
    """Le chrome.exe embarqué (le plus récent s'il y en a plusieurs), ou None s'il n'est pas installé."""
    found = sorted(BROWSERS_DIR.glob("chromium-*/chrome-win64/chrome.exe"), key=_revision)
    return found[-1] if found else None


class Navigateur:
    def __init__(self):
        self._lock = asyncio.Lock()
        self._manager = None  # gestionnaire de contexte de Playwright (son pilote Node)
        self._playwright = None
        self._context = None
        self._pages = {}  # conversation -> son onglet
        self.vues = {}  # conversation -> dernière lecture de son onglet (adresse, numéros des éléments)
        self.bloquees = []  # dernières requêtes refusées par la garde du réseau local

    @property
    def ouvert(self):
        return self._context is not None

    def status(self):
        exe = executable()
        return {"installe": exe is not None, "ouvert": self.ouvert, "profil": str(PROFILE_DIR),
                "chromium": exe.parts[-3] if exe else None}

    async def ouvrir(self):
        """Ouvre le navigateur s'il ne l'est pas déjà ; renvoie son contexte (onglets, cookies du profil)."""
        async with self._lock:
            if self._context is not None:
                return self._context
            if executable() is None:
                raise NavigateurError("Navigateur absent : lance tools/install_navigateur.py (version de dev), "
                                      "ou réinstalle Aria Pilote.")
            from playwright.async_api import async_playwright  # importé à l'usage : le démarrage n'en dépend pas

            # Playwright cherche ses navigateurs là, et pas dans %LOCALAPPDATA%\\ms-playwright
            os.environ["PLAYWRIGHT_BROWSERS_PATH"] = str(BROWSERS_DIR)
            PROFILE_DIR.mkdir(parents=True, exist_ok=True)
            self._manager = async_playwright()
            try:
                self._playwright = await self._manager.start()
                self._attach_driver()
                self._context = await self._playwright.chromium.launch_persistent_context(
                    str(PROFILE_DIR), headless=False, accept_downloads=False,
                    no_viewport=True)  # la page suit la taille de la fenêtre, que l'utilisateur peut changer
            except Exception as exc:
                await self._stop()
                raise NavigateurError(f"Navigateur impossible à ouvrir : {exc}") from exc
            # Chaque requête de chaque page passe par la garde du réseau local (voir refus_reseau)
            await self._context.route("**/*", self._garde)
            await self._context.route_web_socket("**/*", self._garde_ws)
            # L'utilisateur peut fermer la fenêtre lui-même : on l'oublie, et le pilote s'arrête
            self._context.on("close", lambda _: asyncio.ensure_future(self._closed_by_user()))
            return self._context

    # --- garde du réseau local ---------------------------------------------------------------------

    async def _garde(self, route):
        reason = await refus_reseau(route.request.url)
        if reason:
            self.bloquees.append(f"{route.request.url[:200]} ({reason})")
            del self.bloquees[:-20]
            await route.abort("blockedbyclient")
        else:
            await route.continue_()

    async def _garde_ws(self, ws):
        reason = await refus_reseau(ws.url)
        if reason:
            self.bloquees.append(f"{ws.url[:200]} ({reason})")
            del self.bloquees[:-20]
            await ws.close(reason="Refusé par Aria Pilote")
        else:
            ws.connect_to_server()

    # --- un onglet par conversation ------------------------------------------------------------------

    async def page(self, cid):
        """L'onglet de la conversation : celui qu'elle a déjà, sinon l'onglet vide du démarrage, sinon un nouveau."""
        context = await self.ouvrir()
        current = self._pages.get(cid)
        if current is not None and not current.is_closed():
            return current
        taken = set(map(id, self._pages.values()))
        blank = [p for p in context.pages if id(p) not in taken and not p.is_closed() and p.url == "about:blank"]
        current = blank[0] if blank else await context.new_page()
        self._pages[cid] = current
        return current

    def page_ouverte(self, cid):
        """L'onglet de la conversation s'il est encore ouvert, sans rien ouvrir ; None sinon."""
        current = self._pages.get(cid)
        return current if current is not None and not current.is_closed() and self.ouvert else None

    async def fermer_page(self, cid):
        """Ferme l'onglet de la conversation (conversation supprimée) ; le navigateur reste ouvert pour les autres."""
        current = self._pages.pop(cid, None)
        self.vues.pop(cid, None)
        if current is not None and not current.is_closed():
            try:
                await current.close()
            except Exception:  # navigateur fermé entre-temps : rien à faire
                pass
            return True
        return False

    def _attach_driver(self):
        """Rattache le pilote Node de Playwright au job d'Aria : il meurt avec elle, et Chromium, lancé ensuite par
        lui, hérite du rattachement. L'attribut est interne à Playwright : s'il change, on perd seulement ce filet
        (la fermeture normale reste assurée par fermer()) ; un test le vérifie pour la version épinglée."""
        proc = getattr(getattr(getattr(self._manager, "_client", None), "_transport", None), "_proc", None)
        return jobs.attach_pid(proc.pid) if proc is not None else False

    async def _closed_by_user(self):
        async with self._lock:
            if self._context is not None:
                self._context = None
                await self._stop()

    async def fermer(self):
        async with self._lock:
            closed = self._context is not None
            await self._stop()
            return closed

    async def _stop(self):
        self._pages.clear()
        self.vues.clear()
        context, self._context = self._context, None
        if context is not None:
            try:
                await context.close()
            except Exception:  # déjà fermé (fenêtre fermée, pilote arrêté) : rien à faire
                pass
        manager, self._manager, self._playwright = self._manager, None, None
        if manager is not None:
            try:
                await manager.__aexit__()
            except Exception:
                pass

    async def vider_profil(self):
        """Envoie le profil à la corbeille (cookies, historique, cache) ; le navigateur doit être fermé."""
        async with self._lock:
            if self._context is not None:
                raise NavigateurError("Ferme d'abord le navigateur.")
            if not PROFILE_DIR.exists():
                return False
            await asyncio.to_thread(trash.to_recycle_bin, [PROFILE_DIR])
            return True


manager = Navigateur()


# --- garde du réseau local ---------------------------------------------------------------------------
# Sans elle, une page (ou une redirection, une image, un script) pourrait faire appeler par le navigateur l'API
# d'Aria Pilote sur 127.0.0.1, ou un service de ton réseau local. Le nom est résolu ici, avant que Chromium ne le
# résolve lui-même : un serveur DNS qui changerait sa réponse entre les deux pourrait passer (« DNS rebinding ») ;
# l'API d'Aria refuse de toute façon un hôte qui n'est pas le sien.

CACHE_S = 60
_verdicts = {}  # (hôte, port) -> (échéance, raison du refus ou None)
NON_RESEAU = ("data:", "blob:", "about:")  # contenu fabriqué par la page elle-même, sans requête réseau


async def refus_reseau(url):
    """Raison du refus si `url` vise ta machine, ton réseau local ou un schéma non pris en charge ; sinon None."""
    from urllib.parse import urlsplit

    from . import web

    if url.startswith(NON_RESEAU):
        return None
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https", "ws", "wss"):
        return f"schéma « {parts.scheme} » non pris en charge"
    try:
        key = (parts.hostname, parts.port)
    except ValueError:
        return "adresse invalide"
    now = asyncio.get_running_loop().time()
    cached = _verdicts.get(key)
    if cached and cached[0] > now:
        return cached[1]
    http_url = url.replace("ws://", "http://", 1).replace("wss://", "https://", 1)
    try:
        await asyncio.to_thread(web.check_public, http_url)
        reason = None
    except web.WebError as exc:
        reason = str(exc)
    _verdicts[key] = (now + CACHE_S, reason)
    return reason
