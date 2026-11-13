"""Une page web en texte compact pour le modèle, à partir de l'arbre d'accessibilité de Playwright (mode « ai »).

La vue brute d'une page Wikipédia pèse près d'un million de caractères : conteneurs sans nom imbriqués, adresses
des liens, curseurs, une référence sur chaque nœud. Ici on garde ce qui sert :
- les éléments sur lesquels on peut agir (liens, boutons, champs, cases…), chacun avec son numéro : « [37] lien
  « Se connecter » ». Le numéro est celui de Playwright sans le « e » (e37), le même que sur la capture de
  voir_page, et celui que viseront les actions ;
- les titres, précédés de « # » selon leur niveau ;
- le texte, tel quel ;
- les images qui ont une description.
"""

import re

PART_CHARS = 6000  # une partie de lire_page : de quoi lire sans saturer le contexte d'un petit modèle
MAX_LINE = 400  # un paragraphe très long est coupé : chercher_page retrouve le reste

# Rôles sur lesquels on peut agir, et leur nom pour le modèle
INTERACTIVE = {
    "link": "lien", "button": "bouton", "textbox": "champ", "searchbox": "champ de recherche",
    "checkbox": "case", "radio": "bouton radio", "combobox": "liste déroulante", "listbox": "liste",
    "option": "option", "menuitem": "menu", "menuitemcheckbox": "menu à cocher", "menuitemradio": "menu à choix",
    "tab": "onglet", "switch": "interrupteur", "slider": "curseur", "spinbutton": "champ numérique",
    "treeitem": "élément d'arbre",
}
# États utiles au modèle (les autres annotations, comme [cursor=pointer], sont retirées)
STATES = {"checked": "coché", "disabled": "désactivé", "expanded": "déplié", "selected": "choisi",
          "pressed": "enfoncé", "required": "obligatoire"}

# « - role "nom" [attr] [attr=val]: texte » ; le nom et le texte sont facultatifs
LINE_RE = re.compile(r'^\s*- (?P<role>[a-z][\w-]*)(?: "(?P<name>(?:[^"\\]|\\.)*)")?(?P<attrs>(?: \[[^\]]*\])*)'
                     r'(?::\s*(?P<text>.*))?$')
ATTR_RE = re.compile(r"\[([\w-]+)(?:=([^\]]*))?\]")
# « - text: … » et autres lignes de texte, « - /url: … » et autres propriétés
TEXT_RE = re.compile(r'^\s*- text: (?P<text>.*)$')
PROP_RE = re.compile(r'^\s*- /(?P<prop>[\w-]+): (?P<value>.*)$')


def _unquote(text):
    text = (text or "").strip()
    if len(text) >= 2 and text[0] == text[-1] == '"':
        text = text[1:-1].replace('\\"', '"')
    return text


def _clip(text):
    return text if len(text) <= MAX_LINE else text[:MAX_LINE] + "…"


def compact(snapshot):
    """Lignes de texte compact, et les références des éléments numérotés : ({numéro: « eNN »}, lignes)."""
    lines, refs = [], {}
    for raw in snapshot.splitlines():
        prop = PROP_RE.match(raw)
        if prop:
            # Le texte d'aide d'un champ dit à quoi il sert ; une adresse de lien n'apprend presque rien au modèle
            if prop["prop"] == "placeholder" and lines and lines[-1].startswith("[") and "«" not in lines[-1]:
                lines[-1] += f" « {_unquote(prop['value'])} »"
            continue
        text_line = TEXT_RE.match(raw)
        if text_line:
            text = _unquote(text_line["text"])
            if text:
                lines.append(_clip(text))
            continue
        m = LINE_RE.match(raw)
        if not m:
            continue
        role, name, text = m["role"], (m["name"] or "").replace('\\"', '"'), _unquote(m["text"])
        attrs = {k: v for k, v in ATTR_RE.findall(m["attrs"] or "")}
        ref = attrs.get("ref")
        if role in INTERACTIVE and ref and ref[1:].isdigit():
            number = int(ref[1:])
            refs[number] = ref
            label = f"[{number}] {INTERACTIVE[role]}"
            shown = name or text
            if shown:
                label += f" « {_clip(shown)} »"
            states = [STATES[k] for k in attrs if k in STATES and attrs[k] in ("", "true", None)]
            if attrs.get("checked") == "mixed":
                states.append("partiellement coché")
            if states:
                label += f" ({', '.join(states)})"
            if text and name and text != name:
                label += f" : {_clip(text)}"
            lines.append(label)
        elif role == "heading":
            level = int(attrs.get("level") or 2) if (attrs.get("level") or "2").isdigit() else 2
            title = name or text
            if title:
                lines.append(f"{'#' * max(1, min(6, level))} {_clip(title)}")
        elif role == "img":
            if name:
                lines.append(f"(image « {_clip(name)} »)")
        elif text:
            lines.append(_clip(text))
        elif name and role in ("cell", "columnheader", "rowheader", "listitem", "paragraph", "term", "definition",
                               "caption", "status", "alert", "dialog", "figure", "blockquote", "code"):
            lines.append(_clip(name))
    return refs, _dedupe(lines)


def roles(snapshot):
    """Rôle de chaque élément numéroté : {numéro: « textbox », « button »…}."""
    found = {}
    for raw in snapshot.splitlines():
        m = LINE_RE.match(raw)
        if not m or m["role"] not in INTERACTIVE:
            continue
        ref = dict(ATTR_RE.findall(m["attrs"] or "")).get("ref")
        if ref and ref[1:].isdigit():
            found[int(ref[1:])] = m["role"]
    return found


def _dedupe(lines):
    """Retire une ligne de texte qui répète le libellé de l'élément juste au-dessus (un lien et son texte)."""
    kept = []
    for line in lines:
        if kept and not line.startswith(("[", "#", "(")) and f"« {line} »" in kept[-1]:
            continue
        kept.append(line)
    return kept


def parts(lines, size=PART_CHARS):
    """Découpe les lignes en parties d'environ `size` caractères, sans couper une ligne."""
    chunks, current, length = [], [], 0
    for line in lines:
        if current and length + len(line) + 1 > size:
            chunks.append("\n".join(current))
            current, length = [], 0
        current.append(line)
        length += len(line) + 1
    if current or not chunks:
        chunks.append("\n".join(current))
    return chunks


def search(lines, query, limit=30):
    """Lignes qui contiennent `query` (sans tenir compte de la casse ni des accents), avec leur partie."""
    needle = _fold(query)
    found, part, length = [], 1, 0
    for line in lines:
        if length and length + len(line) + 1 > PART_CHARS:
            part, length = part + 1, 0
        length += len(line) + 1
        if needle and needle in _fold(line):
            found.append((part, line))
            if len(found) >= limit:
                break
    return found


def _fold(text):
    import unicodedata
    return "".join(c for c in unicodedata.normalize("NFD", text.casefold()) if unicodedata.category(c) != "Mn")


BOX_RE = re.compile(r"\[ref=(e\d+)\].*?\[box=(-?[\d.]+),(-?[\d.]+),([\d.]+),([\d.]+)\]")


def boxes(snapshot, refs):
    """Rectangles (x0, y0, x1, y1), en pixels CSS de la fenêtre, des éléments numérotés : {numéro: rect}."""
    wanted = {ref: number for number, ref in refs.items()}
    found = {}
    for m in BOX_RE.finditer(snapshot):
        number = wanted.get(m[1])
        if number is None:
            continue
        x, y, w, h = (float(v) for v in m.groups()[1:])
        if w > 0 and h > 0:
            found[number] = (x, y, x + w, y + h)
    return found
