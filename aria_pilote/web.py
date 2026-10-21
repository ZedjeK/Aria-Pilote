"""Accès web de l'agent : lecture de pages publiques et recherche via SearXNG."""

import asyncio
import ipaddress
import socket
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit

import httpx

from . import config, searxng

MAX_BYTES = 2 * 1024 * 1024
MAX_REDIRECTS = 5
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AriaPilote/1.0"


class WebError(Exception):
    pass


def check_public(url):
    """Refuse tout ce qui n'est pas une adresse publique.

    Sans ce contrôle, l'agent pourrait appeler l'API d'Aria Pilote elle-même
    (et valider ses propres demandes d'accord) ou des services de ton réseau local.
    """
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https"):
        raise WebError("Seuls les liens http et https sont autorisés.")
    if not parts.hostname:
        raise WebError("Adresse invalide.")
    port = parts.port or (443 if parts.scheme == "https" else 80)
    try:
        infos = socket.getaddrinfo(parts.hostname, port, proto=socket.IPPROTO_TCP)
    except socket.gaierror:
        raise WebError(f"Nom de domaine introuvable : {parts.hostname}")
    for info in infos:
        ip = ipaddress.ip_address(info[4][0].split("%")[0])
        if not ip.is_global:
            raise WebError("Refusé : cette adresse pointe vers ta machine ou ton réseau local.")


class _Extractor(HTMLParser):
    SKIP = {"script", "style", "noscript", "svg", "template", "iframe"}
    BLOCK = {
        "p", "div", "br", "li", "tr", "section", "article", "header", "footer", "main", "pre",
        "blockquote", "table", "ul", "ol", "h1", "h2", "h3", "h4", "h5", "h6", "dt", "dd",
    }

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.title = ""
        self._skip = 0
        self._in_title = False

    def handle_starttag(self, tag, attrs):
        if tag == "title":
            self._in_title = True
        elif tag in self.SKIP:
            self._skip += 1
        elif tag in self.BLOCK:
            self.parts.append("\n")
            if tag in ("h1", "h2", "h3"):
                self.parts.append("## ")
            elif tag == "li":
                self.parts.append("- ")

    def handle_endtag(self, tag):
        if tag == "title":
            self._in_title = False
        elif tag in self.SKIP and self._skip:
            self._skip -= 1
        elif tag in self.BLOCK:
            self.parts.append("\n")

    def handle_data(self, data):
        if self._in_title:
            self.title += data
        elif not self._skip:
            self.parts.append(data)


def html_to_text(html):
    parser = _Extractor()
    parser.feed(html)
    lines, blank = [], False
    for raw in "".join(parser.parts).splitlines():
        line = " ".join(raw.split())
        if line:
            lines.append(line)
            blank = False
        elif not blank and lines:
            lines.append("")
            blank = True
    return " ".join(parser.title.split()), "\n".join(lines).strip()


async def fetch(url):
    """Télécharge une page publique et renvoie {url, title, text}."""
    current = url
    async with httpx.AsyncClient(timeout=20, follow_redirects=False, headers={"User-Agent": USER_AGENT}) as client:
        for _ in range(MAX_REDIRECTS + 1):
            # Chaque redirection est revérifiée : une page publique pourrait rediriger vers 127.0.0.1
            await asyncio.to_thread(check_public, current)
            async with client.stream("GET", current) as resp:
                if resp.is_redirect and "location" in resp.headers:
                    current = urljoin(current, resp.headers["location"])
                    continue
                if resp.status_code >= 400:
                    raise WebError(f"La page a répondu {resp.status_code}.")
                ctype = resp.headers.get("content-type", "").lower()
                body = bytearray()
                async for chunk in resp.aiter_bytes():
                    body.extend(chunk)
                    if len(body) > MAX_BYTES:
                        break
                text = bytes(body).decode(resp.encoding or "utf-8", errors="replace")
                title = ""
                if "html" in ctype or text.lstrip()[:15].lower().startswith(("<!doctype", "<html")):
                    title, text = html_to_text(text)
                elif ctype and not ctype.startswith("text/") and "json" not in ctype and "xml" not in ctype:
                    raise WebError(f"Contenu non textuel ({ctype}).")
                return {"url": current, "title": title, "text": text}
    raise WebError("Trop de redirections.")


async def search(query, limit=8):
    await searxng.manager.ensure_running()
    base = config.get()["searxng_url"].rstrip("/")
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            resp = await client.get(f"{base}/search", params={"q": query, "format": "json"})
    except httpx.HTTPError:
        if not searxng.manager.installed():
            raise WebError("SearXNG n'est pas installé : lance tools/install_searxng.py.")
        raise WebError(f"SearXNG injoignable sur {base} : regarde data/searxng.log.")
    if resp.status_code == 403:
        raise WebError("SearXNG refuse le format JSON : ajoute « json » dans search.formats de son settings.yml.")
    if resp.status_code != 200:
        raise WebError(f"SearXNG a répondu {resp.status_code}.")
    return [
        {"title": r.get("title", ""), "url": r.get("url", ""), "snippet": r.get("content", "")}
        for r in resp.json().get("results", [])[:limit]
    ]
