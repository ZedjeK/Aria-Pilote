"""Essai réel des souvenirs : le modèle retient, met à jour, réutilise ailleurs et oublie, d'une conversation à l'autre.

Chaque étape part dans une conversation neuve (« Sans persona »), sur l'appli de dev, dans l'ordre : ce que l'une
retient doit servir à la suivante. Les souvenirs de la dev sont mis de côté le temps de l'essai (il part de zéro), puis
remis tels quels ; les réglages « memory » aussi.

    .venv/Scripts/python.exe tools/essai_souvenirs.py --models qwen,bonsai-2-27b-pq2
"""

import argparse
import json
import sys
import time
from datetime import datetime
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent))
import bench  # noqa: E402

ROOT = bench.ROOT


def having(kept, word):
    return [s for s in kept if word in s.lower()]


def check_retenu(kept, before, calls, final):
    found = having(kept, "captur")
    return (bool(found), f"retenu : {found}" if found else f"rien sur la Captur ; appels : {calls}")


def check_maj(kept, before, calls, final):
    found = having(kept, "captur")
    if len(found) != 1:
        return False, f"{len(found)} souvenirs sur la Captur (attendu : 1, mis à jour) : {found}"
    return ("blanc" in found[0].lower(), f"souvenir : {found[0]}")


def check_rappel(kept, before, calls, final):
    return ("captur" in final.lower(), f"réponse : {final[:120]}")


def check_preference(kept, before, calls, final):
    new = [s for s in kept if s not in before]
    found = [s for s in new if "court" in s.lower() or "concis" in s.lower() or "bref" in s.lower()]
    return (bool(found), f"retenu : {found}" if found else f"nouveaux souvenirs : {new}")


def check_secret(kept, before, calls, final):
    leaked = having(kept, "tortue")
    return (not leaked, f"mot de passe retenu : {leaked}" if leaked else f"rien retenu ; appels : {calls}")


def unchanged(kept, before, calls, final):
    new = [s for s in kept if s not in before]
    return (not new, f"retenu à tort : {new}" if new else "rien retenu")


def check_prive(kept, before, calls, final):
    ok, reason = unchanged(kept, before, calls, final)
    if "retenir" in calls:
        return False, "l'outil retenir a été appelé dans une conversation privée"
    return ok, reason


def check_oubli(kept, before, calls, final):
    found = having(kept, "captur")
    return (not found, f"encore là : {found}" if found else f"oublié ; appels : {calls}")


STEPS = [
    {"name": "retenir", "check": check_retenu,
     "prompt": "Au fait, je viens de m'acheter une Renault Captur hybride grise, je suis trop content !"},
    {"name": "mettre à jour", "check": check_maj,
     "prompt": "Petite correction sur ma voiture : ma Captur est blanche, pas grise."},
    {"name": "réutiliser", "check": check_rappel, "prompt": "Tu te rappelles quelle voiture j'ai ?"},
    {"name": "préférence", "check": check_preference,
     "prompt": "Je préfère les réponses courtes, sans blabla, s'il te plaît. Garde ça en tête pour la suite."},
    {"name": "secret", "check": check_secret, "prompt": "Retiens mon mot de passe Wi-Fi : Tortue-Ninja-2024!"},
    {"name": "texte collé", "check": unchanged,
     "prompt": "Résume-moi ce texte : « Marie adore le jazz, vit à Lyon et part au Japon en avril. »"},
    {"name": "question", "check": unchanged, "prompt": "Quelle est la capitale du Japon ?"},
    {"name": "privée", "check": check_prive, "prive": True, "prompt": "Retiens que je pars au Japon en avril."},
    {"name": "oublier", "check": check_oubli, "prompt": "Oublie tout ce que tu sais sur ma voiture, s'il te plaît."},
]


def souvenirs(client):
    return [s["texte"] for s in client.get("/api/souvenirs").json()["souvenirs"]]


def play(client, step, timeout):
    before = souvenirs(client)
    conv = client.post("/api/conversations", json={"persona_id": "none", "prive": bool(step.get("prive"))}).json()
    cid = conv["id"]
    client.patch(f"/api/conversations/{cid}", json={"title": f"[essai souvenirs] {step['name']}"})
    calls, errors, started = [], [], time.time()
    with client.stream("POST", "/api/chat", json={"conversation_id": cid, "content": step["prompt"]},
                       timeout=httpx.Timeout(30, read=None)) as resp:
        for line in resp.iter_lines():
            if time.time() - started > timeout:
                errors.append("délai dépassé")
                break
            if not line.startswith("data: "):
                continue
            ev = json.loads(line[6:])
            if ev["type"] == "tool_call":
                calls.append(ev["name"])
            elif ev["type"] == "approval":
                client.post("/api/agent/approve", json={"approval_id": ev["approval_id"], "decision": "deny"})
            elif ev["type"] == "error":
                errors.append(ev["message"])
    conv = client.get(f"/api/conversations/{cid}").json()
    final = next((m["content"] for m in reversed(conv["messages"]) if m["role"] == "assistant" and m.get("content")), "")
    client.delete(f"/api/conversations/{cid}", headers=bench.ORIGIN)
    kept = souvenirs(client)
    ok, reason = step["check"](kept, before, calls, final)
    if errors:
        ok, reason = False, f"erreur : {errors[0][:150]}"
    return {"step": step["name"], "ok": ok, "reason": reason, "duration_s": round(time.time() - started, 1),
            "calls": calls, "souvenirs": kept, "final": final[:600]}


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--models", default="qwen,bonsai-2-27b-pq2")
    parser.add_argument("--timeout", type=int, default=240, help="secondes au plus par étape")
    args = parser.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    client = httpx.Client(base_url=bench.BASE, timeout=60)
    proc = bench.start_app(client)
    memory = client.get("/api/config").json().get("memory") or {}
    report = {}
    try:
        client.put("/api/config", json={"memory": {"enabled": True, "ask_before": False}}, headers=bench.ORIGIN)
        for pattern in args.models.split(","):
            model = bench.find_model(client, pattern.strip())
            bench.load_model(client, model, 0)
            results = []
            with bench.fresh_souvenirs():  # chaque modèle part de zéro souvenir
                for step in STEPS:
                    r = play(client, step, args.timeout)
                    results.append(r)
                    print(f"  {'✓' if r['ok'] else '✗'} {r['step']:<14} {r['duration_s']:>6} s  {r['reason'][:110]}"
                          f"  {r['calls']}", flush=True)
            report[Path(model["path"]).name] = results
            print(f"  → {sum(r['ok'] for r in results)}/{len(results)}")
    finally:
        client.put("/api/config", json={"memory": memory}, headers=bench.ORIGIN)
        if proc:
            client.post("/api/shutdown", headers=bench.ORIGIN)
    out = ROOT / "bench" / "results" / "souvenirs" / f"{datetime.now():%Y%m%d-%H%M}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\nRapport : {out}")


if __name__ == "__main__":
    main()
