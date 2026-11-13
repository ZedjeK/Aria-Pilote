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
import re

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
        self.evenements = {}  # conversation -> ce que le site a tenté (alertes, choix de fichier) depuis la dernière action
        self.bloquees = []  # dernières requêtes refusées par la garde du réseau local

    @property
    def ouvert(self):
        return self._context is not None

    def status(self):
        exe = executable()
        tabs = sum(1 for p in self._pages.values() if not p.is_closed()) if self.ouvert else 0
        return {"installe": exe is not None, "ouvert": self.ouvert, "profil": str(PROFILE_DIR),
                "profil_existe": PROFILE_DIR.exists(), "chromium": exe.parts[-3] if exe else None, "onglets": tabs}

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
        self._watch(cid, current)
        return current

    def _watch(self, cid, page):
        """Alertes et confirmations du site : refermées sans accepter. Choix de fichier : rien n'est envoyé (avec un
        écouteur, Playwright n'ouvre pas la fenêtre de Windows). L'agent en est prévenu après son action."""
        def noted(text):
            self.evenements.setdefault(cid, []).append(text)
            del self.evenements[cid][:-10]

        async def dialog(d):
            noted(f"fenêtre « {d.type} » du site refermée sans l'accepter : {d.message[:300]}")
            try:
                await d.dismiss()
            except Exception:  # déjà refermée
                pass

        page.on("dialog", lambda d: asyncio.ensure_future(dialog(d)))
        page.on("filechooser", lambda _: noted("le site a demandé un fichier : refusé, l'agent n'envoie aucun fichier"))

    def page_ouverte(self, cid):
        """L'onglet de la conversation s'il est encore ouvert, sans rien ouvrir ; None sinon."""
        current = self._pages.get(cid)
        return current if current is not None and not current.is_closed() and self.ouvert else None

    async def fermer_page(self, cid):
        """Ferme l'onglet de la conversation (conversation supprimée) ; le navigateur reste ouvert pour les autres."""
        current = self._pages.pop(cid, None)
        self.vues.pop(cid, None)
        self.evenements.pop(cid, None)
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
        self.evenements.clear()
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
# Ports d'Aria Pilote, version installée et version de dev : jamais une exception, même écrite à la main dans
# config.json (une page pourrait sinon appeler l'API d'Aria, ou son moteur)
ARIA_PORTS = {8700, 8701, 8888, 8710, 8711, 8898}
MAX_EXCEPTIONS = 20
EXCEPTION_RE = re.compile(r"^(?:\[(?P<ip6>[0-9a-f:.]+)\]|(?P<host>[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?)):(?P<port>\d{1,5})$",
                          re.IGNORECASE)


def aria_ports():
    """Ports d'Aria : ceux de la config en cours, plus ceux des deux versions par défaut."""
    from urllib.parse import urlsplit

    cfg = config.get()
    ports = set(ARIA_PORTS) | {cfg.get("app_port"), cfg.get("llama_port")}
    try:
        ports.add(urlsplit(cfg.get("searxng_url") or "").port)
    except ValueError:
        pass
    return {p for p in ports if p}


def _parse_exception(entry):
    """(hôte, port) d'une entrée « adresse:port », ou ValueError avec un message lisible."""
    m = EXCEPTION_RE.match(entry.strip())
    if not m:
        raise ValueError(f"« {entry} » : écris une adresse suivie de son port, par exemple 127.0.0.1:3000 ou "
                         "192.168.1.20:8080.")
    port = int(m["port"])
    if not 1 <= port <= 65535:
        raise ValueError(f"« {entry} » : port invalide.")
    if port in aria_ports():
        raise ValueError(f"« {entry} » : le port {port} est celui d'Aria Pilote, jamais ouvert au navigateur.")
    return (m["ip6"] or m["host"]).lower(), port


def check_settings(patch):
    """Réglages du navigateur vérifiés : ValueError avec un message lisible si une valeur ne va pas."""
    out = dict(patch)
    entries = out.get("locaux_autorises")
    if entries is not None:
        if isinstance(entries, str):  # « 127.0.0.1:3000, 192.168.1.20:8080 » tapé dans le champ
            entries = re.split(r"[\s,;]+", entries)
        cleaned = []
        for entry in (e for e in entries if e and e.strip()):
            host, port = _parse_exception(entry)
            cleaned.append(f"[{host}]:{port}" if ":" in host else f"{host}:{port}")
        cleaned = list(dict.fromkeys(cleaned))
        if len(cleaned) > MAX_EXCEPTIONS:
            raise ValueError(f"{MAX_EXCEPTIONS} exceptions au plus.")
        out["locaux_autorises"] = cleaned
    return out


def exception_locale(host, port):
    """L'adresse:port est dans tes exceptions (et n'est pas un port d'Aria, relu à chaque fois)."""
    if not host or not port or port in aria_ports():
        return False
    allowed = set()
    for entry in (config.get().get("navigateur") or {}).get("locaux_autorises") or []:
        try:
            allowed.add(_parse_exception(entry))
        except ValueError:  # une entrée abîmée à la main dans config.json : ignorée
            continue
    return (host.lower(), port) in allowed


def exception_url(url):
    """L'adresse visée par `url` est une de tes exceptions locales."""
    from urllib.parse import urlsplit

    try:
        parts = urlsplit(url)
        port = parts.port or {"http": 80, "ws": 80, "https": 443, "wss": 443}.get(parts.scheme)
    except ValueError:
        return False
    return exception_locale(parts.hostname, port)
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
    if exception_url(url):  # avant le cache : retirer une exception prend effet tout de suite
        return None
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
