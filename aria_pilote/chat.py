"""Chat : relais en streaming vers llama-server et enregistrement de la réponse dans l'historique."""

import json
import time
import uuid

import httpx

from . import config, conversations
from .server import manager

SAMPLING_FIELDS = ("temperature", "top_p", "top_k", "min_p", "presence_penalty", "max_tokens")


class UpstreamError(Exception):
    pass


def build_messages(conv, system_prompt):
    messages = [{"role": "system", "content": system_prompt}] if system_prompt else []
    answered = {m["tool_call_id"] for m in conv["messages"] if m["role"] == "tool"}
    for m in conv["messages"]:
        if m["role"] == "tool":
            messages.append({"role": "tool", "tool_call_id": m["tool_call_id"], "content": m["content"]})
        elif m["role"] == "assistant" and m.get("tool_calls"):
            messages.append({"role": "assistant", "content": m["content"] or "", "tool_calls": m["tool_calls"]})
            # Un appel resté sans réponse (Stop pendant l'agent) casserait le chat template
            for tc in m["tool_calls"]:
                if tc["id"] not in answered:
                    messages.append({"role": "tool", "tool_call_id": tc["id"], "content": "Action interrompue par l'utilisateur."})
        else:
            messages.append({"role": m["role"], "content": m["content"]})
    return messages


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
        "completion_tokens": timings.get("predicted_n") or usage.get("completion_tokens"),
        "prompt_tokens": timings.get("prompt_n") or usage.get("prompt_tokens"),
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


async def stream(conv, preset, model_name):
    """Chat simple : une complétion, puis « done ».

    Si le client coupe la connexion (bouton Stop), la génération est annulée
    et la réponse partielle est quand même enregistrée.
    """
    out, message = {}, None
    try:
        async for ev in completion(build_payload(conv, preset, model_name), out):
            yield ev
    except (httpx.HTTPError, UpstreamError) as exc:
        yield event({"type": "error", "message": f"Échec de la génération : {exc}"})
    finally:
        if has_output(out):
            message = assistant_message(out, model_name, preset)
            conversations.append_message(conv["id"], message)
    if out.get("finished"):
        yield event({"type": "done", "message": message})
