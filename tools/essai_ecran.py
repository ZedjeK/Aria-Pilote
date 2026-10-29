"""Essai réel du contrôle du PC : l'agent pilote vraiment l'écran (mode PC entier) sur quatre tâches sans risque.

    calculatrice      ouvrir la Calculatrice par le menu Démarrer, calculer 12 × 34 en cliquant ses touches
    enregistrer-sous  Bloc-notes, taper un texte, Fichier › Enregistrer sous (boîte de dialogue) dans le dossier d'essai
    menu-contextuel   clic droit sur ce fichier dans l'Explorateur, « Copier en tant que chemin d'accès »
    restaurer         rouvrir le Bloc-notes réduit dans la barre des tâches

⚠ Ta souris et ton clavier sont pilotés pendant l'essai (environ 4 minutes par modèle) : ne touche à rien. Le script
joue ton rôle : il accepte les actions à l'écran, refuse le reste. Le contrôle du PC est activé le temps de l'essai puis
remis comme avant ; les applis ouvertes par l'essai sont refermées, le dossier d'essai est temporaire.

    .venv/Scripts/python.exe tools/essai_ecran.py --models qwen38,27b-pq2_0
"""
import argparse
import ctypes
import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
import bench  # noqa: E402

user32 = ctypes.windll.user32


def log(*a):
    print(*a, flush=True)


def pids(name):
    out = subprocess.run(["tasklist", "/FI", f"IMAGENAME eq {name}", "/FO", "CSV", "/NH"], capture_output=True, text=True).stdout
    return {int(line.split('","')[1]) for line in out.splitlines() if line.count('","') >= 2}


def ask(client, cid, content, timeout=420):
    calls, asked, errors = [], [], []
    started = time.time()
    with client.stream("POST", "/api/chat", json={"conversation_id": cid, "content": content},
                       timeout=httpx.Timeout(30, read=None)) as resp:
        if resp.status_code != 200:
            return {"errors": [resp.read().decode()[:300]], "calls": [], "asked": [], "final": "", "duration": 0}
        for line in resp.iter_lines():
            if time.time() - started > timeout:
                errors.append("délai")
                break
            if not line.startswith("data: "):
                continue
            ev = json.loads(line[6:])
            if ev["type"] == "tool_call":
                calls.append({"name": ev["name"], "args": ev["args"]})
            elif ev["type"] == "tool_result" and calls:
                calls[-1]["status"] = ev["status"]
                calls[-1]["out"] = (ev.get("output") or "")[:160]
            elif ev["type"] == "approval":
                ok = ev["kind"] == "screen_action"  # tout le reste (web, commandes) est refusé
                asked.append({"kind": ev["kind"], "preview": ev["preview"].split("\n")[0][:120], "ordinary": ev.get("ordinary")})
                client.post("/api/agent/approve", json={"approval_id": ev["approval_id"], "decision": "allow" if ok else "deny"})
            elif ev["type"] == "error":
                errors.append(ev["message"][:200])
    conv = client.get(f"/api/conversations/{cid}").json()
    final = next((m["content"] for m in reversed(conv["messages"]) if m["role"] == "assistant" and m.get("content")), "")
    return {"calls": calls, "asked": asked, "errors": errors, "final": final[:400], "duration": round(time.time() - started, 1)}


def new_conv(client):
    cid = client.post("/api/conversations", json={"persona_id": "none"}).json()["id"]
    client.patch(f"/api/conversations/{cid}", json={"title": "[essai écran]"})
    r = client.patch(f"/api/conversations/{cid}", json={"screen_control": True, "screen_mode": "pc"}, headers=bench.ORIGIN)
    assert r.status_code == 200, r.text
    return cid


def close_new(name, before):
    for pid in pids(name) - before:
        subprocess.run(["taskkill", "/PID", str(pid), "/F"], capture_output=True)


def close_explorer_windows(title_part):
    found = []

    @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
    def collect(hwnd, _):
        n = user32.GetWindowTextLengthW(hwnd)
        buf = ctypes.create_unicode_buffer(n + 1)
        user32.GetWindowTextW(hwnd, buf, n + 1)
        if title_part.lower() in buf.value.lower() and user32.IsWindowVisible(hwnd):
            found.append(hwnd)
        return True
    user32.EnumWindows(collect, 0)
    for hwnd in found:
        user32.PostMessageW(hwnd, 0x0010, 0, 0)  # WM_CLOSE


def scenario(name, client, prompt, check, setup=None):
    before = {p: pids(p) for p in ("notepad.exe", "CalculatorApp.exe")}
    if setup:
        setup()
    cid = new_conv(client)
    r = ask(client, cid, prompt)
    try:
        ok, reason = check(r)
    except Exception as exc:
        ok, reason = False, f"vérification en erreur : {exc!r}"
    actions = [c for c in r["calls"] if c["name"] == "agir_ecran"]
    log(f"  {'✓' if ok else '✗'} {name:<18} {r['duration']:>6} s · {len(r['calls'])} appels · {len(actions)} actions · "
        f"{len(r['asked'])} accords · {reason}")
    if r["errors"]:
        log(f"      erreurs : {r['errors'][:2]}")
    client.delete(f"/api/conversations/{cid}", headers=bench.ORIGIN)
    for p, b in before.items():
        close_new(p, b)
    time.sleep(1)
    return {"name": name, "ok": ok, "reason": reason, **r}


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--models", default="qwen38,27b-pq2_0", help="motifs des modèles, séparés par des virgules")
    args = parser.parse_args()
    work = Path(tempfile.mkdtemp(prefix="aria-essai-ecran-"))
    out = ROOT / "bench" / "results" / "ecran" / f"{time.strftime('%Y%m%d-%H%M')}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    report = {}
    client = httpx.Client(base_url=bench.BASE, timeout=60)
    proc = bench.start_app(client)
    saved = {k: client.get("/api/config").json().get(k) for k in ("screen_control", "max_tool_calls")}
    client.put("/api/config", json={"screen_control": True, "max_tool_calls": 50}, headers=bench.ORIGIN)
    target = work / "essai-ecran.txt"
    try:
        for pattern in args.models.split(","):
            model = bench.find_model(client, pattern)
            bench.load_model(client, model, 0)
            results = []
            target.unlink(missing_ok=True)

            results.append(scenario("calculatrice", client,
                "Ouvre la Calculatrice de Windows et calcule 12 × 34 en cliquant sur ses touches, puis donne-moi le résultat affiché.",
                lambda r: (("408" in r["final"]) and any(c["args"].get("action") == "ouvrir" for c in r["calls"] if c["name"] == "agir_ecran"),
                           "ouverte par le menu Démarrer, 408 annoncé" if "408" in r["final"] else f"réponse : {r['final'][:80]!r}")))

            def saved_ok(r):
                exists = target.exists()
                text = target.read_text(encoding="utf-8", errors="replace") if exists else ""
                return exists and "Essai Aria écran" in text, (f"fichier créé ({len(text)} caractères)" if exists else "fichier absent")
            results.append(scenario("enregistrer-sous", client,
                f"Ouvre le Bloc-notes, écris « Essai Aria écran », puis enregistre le fichier sous {target} (utilise Enregistrer sous).",
                saved_ok))

            def explorer_setup():
                subprocess.Popen(["explorer.exe", str(work)])
                time.sleep(3)
                subprocess.run(["powershell", "-NoProfile", "-Command", "Set-Clipboard -Value 'vide'"], capture_output=True)

            def clip_ok(r):
                clip = subprocess.run(["powershell", "-NoProfile", "-Command", "Get-Clipboard"], capture_output=True,
                                      text=True).stdout.strip()
                return target.name in clip.lower(), f"presse-papiers : {clip[-60:]!r}"
            if target.exists():
                results.append(scenario("menu-contextuel", client,
                    f"Dans la fenêtre de l'Explorateur ouverte sur le dossier {work.name}, fais un clic droit sur le fichier "
                    f"{target.name} et choisis « Copier en tant que chemin d'accès ».", clip_ok, explorer_setup))
                close_explorer_windows(work.name)

            def minimized_setup():
                subprocess.Popen(["notepad.exe", str(target)])
                time.sleep(2.5)
                user32.ShowWindow(user32.GetForegroundWindow(), 6)  # SW_MINIMIZE : le Bloc-notes vient d'arriver devant
                time.sleep(1)

            def restored_ok(r):
                used = [c for c in r["calls"] if c["name"] == "agir_ecran" and c["args"].get("action") == "restaurer"]
                return bool(used) and all(c.get("status") == "ok" for c in used), f"restaurer : {len(used)} appel(s)"
            if target.exists():
                results.append(scenario("restaurer", client, "Rouvre la fenêtre du Bloc-notes qui est réduite dans la barre des tâches.",
                                        restored_ok, minimized_setup))
            report[model["name"]] = results
            log(f"  → {sum(r['ok'] for r in results)}/{len(results)}")
    finally:
        client.put("/api/config", json={k: v for k, v in saved.items() if v is not None}, headers=bench.ORIGIN)
        out.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
        log(f"\nRapport : {out}")
        client.post("/api/unload", headers=bench.ORIGIN)
        if proc:
            client.post("/api/shutdown", headers=bench.ORIGIN)


if __name__ == "__main__":
    main()
