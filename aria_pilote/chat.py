"""Chat : relais en streaming vers llama-server et enregistrement de la réponse dans l'historique."""

import asyncio
import json
import time
import uuid

import httpx

from . import attachments, config, context, conversations, images, persona, skills
from .server import manager

SAMPLING_FIELDS = ("temperature", "top_p", "top_k", "min_p", "presence_penalty", "max_tokens")
SCREENSHOTS_KEPT = 2


class UpstreamError(Exception):
    pass


def build_messages(conv, system_prompt):
    """Messages envoyés au modèle : résumé des anciens échanges, puis la suite de l'historique."""
    summary = conv.get("summary") or {}
    source = conv["messages"][summary.get("upto", 0):]
    keep = summary.get("keep")
    if keep is not None and keep < summary.get("upto", 0):
        # Résumé au milieu d'une tâche : la demande de l'utilisateur reste mot pour mot
        source = [conv["messages"][keep]] + source
    if summary.get("text"):
        system_prompt = (f"{system_prompt}\n\n" if system_prompt else "") + context.SUMMARY_HEADER + summary["text"]
    messages = [{"role": "system", "content": system_prompt}] if system_prompt else []
    cid = conv.get("id")  # absent dans la conversation interne d'un sous-agent (jamais d'image)
    answered = {m["tool_call_id"] for m in source if m["role"] == "tool"}
    # Seuls les derniers résultats d'outils restent entiers : les plus anciens ont déjà servi
    keep_full, limit = context.SQUEEZE_LEVELS[conv.get("squeeze", 0)]
    tool_positions = [i for i, m in enumerate(source) if m["role"] == "tool"]
    old_tools = set(tool_positions[:-keep_full]) if keep_full else set(tool_positions)
    # Captures d'écran : seules les dernières sont renvoyées au modèle (chacune coûte environ 1 000 tokens)
    shots = [i for i, m in enumerate(source) if m["role"] == "tool" and m.get("images")][-SCREENSHOTS_KEPT:]
    pending = []
    for i, m in enumerate(source):
        if pending and m["role"] != "tool":
            messages.append(_screenshots(cid, pending))
            pending = []
        if m["role"] == "tool":
            content = context.shrink(m["content"], limit) if i in old_tools else m["content"]
            messages.append({"role": "tool", "tool_call_id": m["tool_call_id"], "content": content})
            if i in shots:
                pending += m["images"]  # après le dernier résultat du lot : un message ne coupe pas les appels
        elif m["role"] == "assistant" and m.get("tool_calls"):
            messages.append({"role": "assistant", "content": m["content"] or "", "tool_calls": m["tool_calls"]})
            # Un appel resté sans réponse (Stop pendant l'agent) casserait le chat template
            for tc in m["tool_calls"]:
                if tc["id"] not in answered:
                    messages.append({"role": "tool", "tool_call_id": tc["id"], "content": "Action interrompue par l'utilisateur."})
        elif m["role"] == "user":
            text = m["content"]
            if m.get("skill"):
                # « /nom demande » : la compétence, puis le registre de ce mode pour le persona de la conversation
                root = (conv.get("agent") or {}).get("workspace") if (conv.get("agent") or {}).get("enabled") else None
                note = persona.register_note(persona.for_conversation(conv), m["skill"])
                text = skills.expand(m["skill"], m["content"], root) + note
            if m.get("files"):
                text = "\n\n".join(p for p in (text, attachments.for_model(m["files"])) if p)
            messages.append({"role": "user", "content": user_content(cid, text, m.get("images"))})
        else:
            messages.append({"role": m["role"], "content": m["content"]})
    if pending:
        messages.append(_screenshots(cid, pending))
    return messages


def _screenshots(cid, shots):
    """Captures renvoyées par voir_ecran : les modèles ne lisent une image que dans un message, d'où celui-ci."""
    return {"role": "user", "content": user_content(cid, "[Capture d'écran renvoyée par voir_ecran]", shots)}


def user_content(cid, text, attached):
    """Texte du message, précédé de ses images si le modèle les voit (format OpenAI accepté par llama-server)."""
    if not attached:
        return text
    if not manager.vision:
        note = f"[{len(attached)} image(s) jointe(s), que le modèle chargé ne peut pas voir : il n'a pas de projecteur vision]"
        return f"{note}\n{text}" if text else note
    parts = []
    for image in attached:
        try:
            parts.append({"type": "image_url", "image_url": {"url": images.data_url(cid, image["file"])}})
        except images.ImageError:
            parts.append({"type": "text", "text": "[image jointe introuvable]"})
    return parts + ([{"type": "text", "text": text}] if text else [])


def build_payload(conv, preset, model_name, system_prompt=None, tools=None):
    if system_prompt is None:
        system_prompt = preset.get("system_prompt")
    payload = {
        "model": model_name,
        "messages": build_messages(conv, system_prompt),
        "stream": True,
        "stream_options": {"include_usage": True},
    }
    for field in SAMPLING_FIELDS:
        if preset.get(field) is not None:
            payload[field] = preset[field]
    if preset.get("thinking") is not None:
        payload["chat_template_kwargs"] = {"enable_thinking": preset["thinking"]}
    if preset.get("thinking_budget"):
        # llama-server referme la réflexion au bout de ce nombre de tokens, puis le modèle répond
        payload["thinking_budget_tokens"] = preset["thinking_budget"]
    if tools:
        payload["tools"] = tools
    return payload


def event(data):
    return f"data: {json.dumps(data, ensure_ascii=False)}\n\n"


def _summarize(out, stopped):
    timings = out["stats"].get("timings") or {}
    usage = out["stats"].get("usage") or {}
    tok_s = timings.get("predicted_per_second")
    first = out["first_token_at"]
    return {
        "completion_tokens": usage.get("completion_tokens") or timings.get("predicted_n"),
        # usage compte tout le prompt ; timings.prompt_n ne compte que la partie hors cache
        "prompt_tokens": usage.get("prompt_tokens") or timings.get("prompt_n"),
        "tok_s": round(tok_s, 1) if tok_s else None,
        "ttft_s": round(first - out["started"], 2) if first else None,
        "duration_s": round(time.time() - out["started"], 1),
        "finish_reason": out["stats"].get("finish_reason"),
        "stopped": stopped,
    }


def has_output(out):
    return bool(out) and (any(out["content"]) or any(out["reasoning"]) or bool(out["tool_calls"]))


def assistant_message(out, model_name, preset):
    message = {
        "role": "assistant",
        "content": "".join(out["content"]),
        "reasoning": "".join(out["reasoning"]) or None,
        "model": model_name,
        "preset_id": preset["id"],
        "stats": _summarize(out, stopped=not out["finished"]),
    }
    calls = [tc for _, tc in sorted(out["tool_calls"].items())]
    for tc in calls:
        tc["id"] = tc["id"] or f"call_{uuid.uuid4().hex[:12]}"
    if calls:
        message["tool_calls"] = calls
    return message


async def completion(payload, out):
    """Streame une complétion en émettant des événements « delta ».

    `out` est rempli au fil de l'eau (contenu, réflexion, appels d'outils, stats)
    pour que l'appelant puisse enregistrer même une réponse interrompue.
    """
    out.update(content=[], reasoning=[], tool_calls={}, stats={}, started=time.time(), first_token_at=None, finished=False)
    url = f"http://127.0.0.1:{config.get()['llama_port']}/v1/chat/completions"
    async with httpx.AsyncClient(timeout=httpx.Timeout(10, read=None)) as client:
        async with client.stream("POST", url, json=payload, headers=manager.auth_headers()) as resp:
            if resp.status_code != 200:
                body = (await resp.aread()).decode("utf-8", errors="replace")
                if context.is_overflow(body):
                    raise context.ContextOverflow(body[:300])
                raise UpstreamError(f"llama-server a répondu {resp.status_code} : {body[:500]}")
            async for line in resp.aiter_lines():
                if not line.startswith("data: "):
                    continue
                data = line[6:].strip()
                if data == "[DONE]":
                    break
                chunk = json.loads(data)
                if chunk.get("timings"):
                    out["stats"]["timings"] = chunk["timings"]
                if chunk.get("usage"):
                    out["stats"]["usage"] = chunk["usage"]
                for choice in chunk.get("choices") or []:
                    delta = choice.get("delta") or {}
                    for tc in delta.get("tool_calls") or []:
                        slot = out["tool_calls"].setdefault(
                            tc.get("index", 0), {"id": None, "type": "function", "function": {"name": "", "arguments": ""}}
                        )
                        if tc.get("id"):
                            slot["id"] = tc["id"]
                        fn = tc.get("function") or {}
                        slot["function"]["name"] += fn.get("name") or ""
                        slot["function"]["arguments"] += fn.get("arguments") or ""
                    c = delta.get("content") or ""
                    r = delta.get("reasoning_content") or ""
                    if c or r:
                        out["first_token_at"] = out["first_token_at"] or time.time()
                        out["content"].append(c)
                        out["reasoning"].append(r)
                        yield event({"type": "delta", "content": c, "reasoning": r})
                    if choice.get("finish_reason"):
                        out["stats"]["finish_reason"] = choice["finish_reason"]
    out["finished"] = True


SATURATED = ("La conversation dépasse le contexte chargé, même après résumé des anciens échanges : "
             "recharge le modèle avec un contexte plus grand, ou commence une nouvelle conversation.")


ENGINE_CRASHED = ("Le moteur s'est arrêté pendant la génération. Cause fréquente : un autre logiciel "
                  "(génération d'images, jeu…) a pris la mémoire de la carte graphique. "
                  "Libère-la, puis recharge le modèle dans l'onglet Modèles.")


async def failure_message(exc):
    """Message clair pour une génération interrompue, y compris quand le moteur a planté."""
    await asyncio.sleep(1)  # laisse au superviseur le temps de constater un plantage
    if manager.state == "error":
        return ENGINE_CRASHED
    return f"Échec de la génération : {exc or type(exc).__name__}"


async def make_room(cid, preset, system_prompt, tools=None, force=False):
    """Mesure le contexte et résume si besoin ; une panne de mesure ne bloque pas la génération."""
    try:
        return await context.ensure_room(cid, preset, system_prompt, tools, force)
    except (httpx.HTTPError, KeyError, ValueError):
        return None
