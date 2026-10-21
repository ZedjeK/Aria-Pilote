"""Gestion du contexte : mesure exacte, résumé des anciens échanges, allègement des vieux résultats d'outils.

Les messages ne sont jamais effacés de l'historique : seul ce qui est envoyé au modèle change.
Le résumé couvre les messages [0, upto[ et remplace ceux-ci dans le prompt.
"""

import json
from datetime import datetime

import httpx

from . import config, conversations
from .server import manager

KEEP_STEPS = 2  # dans une longue tâche, les dernières étapes de l'agent restent intactes

# Niveaux de resserrement des résultats d'outils : (résultats récents gardés entiers, taille des extraits).
# Le niveau monte quand résumer ne suffit plus, par exemple quand l'agent lit beaucoup de fichiers d'un coup.
SQUEEZE_LEVELS = [(4, 1200), (2, 800), (0, 600), (0, 300)]
SUMMARY_TOOL_CHARS = 600  # taille d'un résultat d'outil dans le texte à résumer
SUMMARY_MAX_TOKENS = 900
SUMMARY_INPUT_SHARE = 0.6  # part maximale du contexte pour le texte à résumer

SUMMARY_HEADER = "Résumé des échanges précédents (l'historique complet n'est plus dans ton contexte) :\n"

SUMMARY_PROMPT = """Tu résumes une conversation entre un utilisateur et un assistant (parfois un agent qui \
utilise des outils sur ses fichiers), pour qu'elle puisse continuer sans l'historique complet.
Garde l'essentiel, en liste concise et en français :
- ce que l'utilisateur veut, ses consignes et préférences ;
- les décisions prises et ce qui a été fait (fichiers lus ou modifiés avec leur chemin, commandes lancées
  et leur résultat) ;
- les erreurs rencontrées et ce qui reste à faire.
Si un résumé précédent est fourni, intègre-le. Pas de formule de politesse, pas d'introduction."""


class ContextOverflow(Exception):
    """llama-server refuse la requête : elle dépasse le contexte chargé."""


_tools_tokens_cache = {}


def _url(path):
    return f"http://127.0.0.1:{config.get()['llama_port']}{path}"


def shrink(text, limit):
    """Garde le début et la fin : dans un journal ou un fichier, l'utile est souvent tout en bas."""
    if len(text) <= limit:
        return text
    head, tail = limit * 2 // 3, limit // 3
    return (f"{text[:head]}\n[… {len(text) - head - tail} caractères retirés pour économiser le contexte …]\n"
            f"{text[-tail:]}")


def reserve(preset, total):
    """Place gardée pour la réponse du modèle."""
    return min(preset.get("max_tokens") or 4096, total // 4)


async def _tokenize(client, text):
    resp = await client.post(_url("/tokenize"), json={"content": text})
    resp.raise_for_status()
    return len(resp.json()["tokens"])


async def count_tokens(messages, tools=None):
    """Nombre exact de tokens du prompt (gabarit de chat compris), outils inclus."""
    async with httpx.AsyncClient(timeout=30, headers=manager.auth_headers()) as client:
        try:
            resp = await client.post(_url("/apply-template"), json={"messages": messages})
            resp.raise_for_status()
            total = await _tokenize(client, resp.json()["prompt"])
            if tools:
                key = (manager.model.id if manager.model else None, len(tools))
                if key not in _tools_tokens_cache:
                    _tools_tokens_cache[key] = await _tokenize(client, json.dumps(tools, ensure_ascii=False))
                total += _tools_tokens_cache[key]
            return total
        except (httpx.HTTPError, KeyError, ValueError):
            # Estimation grossière si le serveur ne sait pas compter
            return len(json.dumps(messages, ensure_ascii=False)) // 3 + (len(json.dumps(tools)) // 3 if tools else 0)


def _transcript(messages, skip=None):
    lines = []
    for i, m in enumerate(messages):
        if i == skip:
            continue
        if m["role"] == "user":
            lines.append(f"Utilisateur : {m['content']}")
        elif m["role"] == "assistant":
            if m.get("content"):
                lines.append(f"Assistant : {m['content']}")
            for tc in m.get("tool_calls") or []:
                lines.append(f"[Appel d'outil {tc['function']['name']} {tc['function']['arguments'][:300]}]")
        elif m["role"] == "tool":
            status = m.get("status", "ok")
            lines.append(f"[Résultat {m.get('name', '?')} ({status})] {shrink(m['content'], SUMMARY_TOOL_CHARS)}")
    return lines


async def _summarize(conv, upto, cut, model_name, keep=None):
    """Résume les messages [upto, cut[ (sauf `keep`, la demande en cours, gardée telle quelle)."""
    previous = (conv.get("summary") or {}).get("text")
    lines = _transcript(conv["messages"][upto:cut], skip=keep - upto if keep is not None and keep >= upto else None)
    if keep is not None:
        lines.insert(0, f"(Demande en cours de l'utilisateur, conservée à part : {conv['messages'][keep]['content'][:500]})")
    total = manager.ctx_size
    async with httpx.AsyncClient(timeout=httpx.Timeout(10, read=None), headers=manager.auth_headers()) as client:
        # Le texte à résumer doit lui-même tenir dans le contexte : on écarte le plus ancien si besoin
        dropped = 0
        while lines and await _tokenize(client, "\n".join(lines)) > total * SUMMARY_INPUT_SHARE:
            cut_lines = max(1, len(lines) // 5)
            lines, dropped = lines[cut_lines:], dropped + cut_lines
        body = "\n".join(lines)
        if dropped:
            body = f"[{dropped} lignes les plus anciennes omises]\n" + body
        if previous:
            body = f"Résumé précédent :\n{previous}\n\nSuite de la conversation :\n{body}"
        resp = await client.post(_url("/v1/chat/completions"), json={
            "model": model_name,
            "messages": [{"role": "system", "content": SUMMARY_PROMPT}, {"role": "user", "content": body}],
            "max_tokens": SUMMARY_MAX_TOKENS,
            "temperature": 0.3,
            "chat_template_kwargs": {"enable_thinking": False},
        })
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"].strip()


async def ensure_room(cid, preset, system_prompt, tools=None, force=False):
    """Résume les anciens échanges si le prochain prompt ne laisse plus assez de place pour la réponse.

    Renvoie un événement à transmettre à l'interface : « context » (mesure) ou « compacted ».
    """
    from . import chat  # import tardif : chat utilise aussi ce module

    total = manager.ctx_size
    if not total:
        return None
    conv = conversations.get(cid)
    used = await count_tokens(chat.build_messages(conv, system_prompt), tools)
    budget = total - reserve(preset, total)
    if used <= budget and not force:
        return {"type": "context", "used": used, "max": total}

    before, compacted, after = used, 0, used
    # Deux passes au plus : d'abord les tours précédents, puis, si ça ne suffit pas,
    # les anciennes étapes de la tâche en cours (la demande et les dernières étapes restent)
    for _ in range(2):
        cut = _cut_point(conv)
        if cut is None:
            break
        upto, keep = cut["upto"], cut["keep"]
        text = await _summarize(conv, (conv.get("summary") or {}).get("upto", 0), upto, manager.model.name, keep)
        summary = {"text": text, "upto": upto, "keep": keep, "created_at": datetime.now().isoformat(timespec="seconds")}
        compacted += upto - (conv.get("summary") or {}).get("upto", 0)
        conv = conversations.update(cid, lambda c: c.__setitem__("summary", summary))
        after = await count_tokens(chat.build_messages(conv, system_prompt), tools)
        if after <= budget:
            break
    # Dernier recours : resserrer les résultats d'outils, niveau par niveau
    squeezed = False
    while after > budget and conv.get("squeeze", 0) < len(SQUEEZE_LEVELS) - 1:
        conv = conversations.update(cid, lambda c: c.__setitem__("squeeze", c.get("squeeze", 0) + 1))
        after = await count_tokens(chat.build_messages(conv, system_prompt), tools)
        squeezed = True
    if not compacted and not squeezed:
        return {"type": "context", "used": used, "max": total, "saturated": used > budget}
    return {"type": "compacted", "before": before, "used": after, "max": total, "messages": compacted,
            "summary": (conv.get("summary") or {}).get("text", ""), "squeeze": conv.get("squeeze", 0),
            "saturated": after > budget}


def _cut_point(conv):
    """Où couper pour résumer : {upto, keep} ou None s'il ne reste rien à résumer.

    `keep` est l'indice de la demande en cours quand on coupe au milieu d'une tâche :
    elle est alors renvoyée telle quelle au modèle, juste avant les étapes gardées.
    """
    messages = conv["messages"]
    upto = (conv.get("summary") or {}).get("upto", 0)
    last_user = max((i for i, m in enumerate(messages) if m["role"] == "user"), default=0)
    if last_user > upto:
        return {"upto": last_user, "keep": None}
    steps = [i for i in range(last_user + 1, len(messages)) if messages[i]["role"] == "assistant"]
    steps = [i for i in steps if i > upto]
    if len(steps) > KEEP_STEPS:
        return {"upto": steps[-KEEP_STEPS], "keep": last_user}
    return None


def record_usage(cid, stats):
    """Mémorise l'occupation du contexte après une réponse, pour la jauge."""
    total = manager.ctx_size
    used = (stats.get("prompt_tokens") or 0) + (stats.get("completion_tokens") or 0)
    if not total or not used:
        return None
    conversations.update(cid, lambda c: c.__setitem__("context", {"used": used, "max": total}))
    return {"type": "context", "used": used, "max": total}


def is_overflow(body):
    return "exceed_context_size_error" in body or "exceeds the available context size" in body
