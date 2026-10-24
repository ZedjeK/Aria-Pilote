"""Banc d'essai de l'agent : rejoue des tâches types sur un ou plusieurs modèles et mesure leur fiabilité.

Chaque tâche est un dossier de bench/tasks/ :
    task.json   demande, réponses aux questions de l'agent, mots pièges ; "folder": false pour une pure
                discussion (aucun dossier attaché) ; "approve_plan": false pour ne pas approuver le plan proposé
    fixture/    fichiers de départ, copiés dans un dossier temporaire neuf à chaque passage (facultatif sans dossier)
    check.py    check(ws, run) -> (ok, raison) : vérifie le résultat réel (fichiers, tests, réponse)
                setup(ws) facultatif : prépare le dossier après la copie

Le script passe par l'API de l'appli de dev, comme l'interface (il la démarre si besoin), et joue
ton rôle : il valide les accords, sauf les commandes qui contiennent un mot piège, répond aux questions
et approuve les plans proposés (sauf si la tâche dit le contraire).

    py -3.11 tools/bench.py --models qwen,bonsai
    py -3.11 tools/bench.py --models qwen --tasks 2,6 --runs 3
"""

import argparse
import importlib.util
import json
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
BENCH = ROOT / "bench"
TASKS_DIR = BENCH / "tasks"
RESULTS_DIR = BENCH / "results"
try:  # le port de l'appli de dev, lu dans sa config (8710 une fois séparée de la version installée)
    _PORT = json.loads((ROOT / "config.json").read_text(encoding="utf-8")).get("app_port", 8700)
except (OSError, ValueError):
    _PORT = 8700
BASE = f"http://127.0.0.1:{_PORT}"
ORIGIN = {"Origin": BASE}
MAX_ANSWERS = 3  # questions de l'agent auxquelles on répond au plus, par passage
MAX_PLANS = 2  # plans approuvés au plus, par passage
PLAN_APPROVED = "Le plan est approuvé : exécute-le étape par étape, en tenant ton plan à jour."
INCIDENTS = {"tourne en rond": "boucle", "Réponse coupée": "coupée", "Limite de": "limite"}

sys.path.insert(0, str(BENCH))  # check.py importe les utilitaires de bench/checks.py


# --- Appli et modèles ----------------------------------------------------------

def alive(client):
    try:
        return client.get("/api/status").status_code == 200
    except httpx.HTTPError:
        return False


def start_app(client):
    """Démarre l'appli de dev si elle ne tourne pas ; renvoie le processus lancé (ou None)."""
    if alive(client):
        return None
    flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
    proc = subprocess.Popen([sys.executable, "-m", "aria_pilote"], cwd=ROOT, creationflags=flags,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(60):
        if alive(client):
            return proc
        time.sleep(0.5)
    proc.kill()
    sys.exit("L'appli de dev ne démarre pas : regarde data/aria_pilote.log.")


def find_model(client, pattern):
    pattern = pattern.lower()
    found = [m for m in client.get("/api/models").json()
             if pattern in m["name"].lower() or pattern in m["path"].lower()]
    if not found:
        sys.exit(f"Aucun modèle ne correspond à « {pattern} ».")
    # Projecteurs vision (mmproj) exclus : on veut le modèle lui-même
    found = [m for m in found if "mmproj" not in Path(m["path"]).name.lower()] or found
    return found[0]


def load_model(client, model, ctx):
    # ctx = 0 : les réglages mémorisés du modèle (ceux de « Optimiser mes modèles », par exemple)
    settings = model.get("settings") or {}
    label = f"contexte {ctx}" if ctx else f"réglages mémorisés : {settings.get('ctx_size')}, cache {settings.get('cache_type')}"
    print(f"\n=== Chargement de {Path(model['path']).name} ({label})")
    body = {"model_id": model["id"], **({"ctx_size": ctx} if ctx else {})}
    resp = client.post("/api/load", json=body, headers=ORIGIN)
    if resp.status_code != 200:
        sys.exit(f"Chargement refusé : {resp.text[:300]}")
    errors = 0
    for _ in range(300):
        status = client.get("/api/status").json()
        if status["state"] == "ready":
            return
        # Une erreur passagère peut précéder la bascule automatique vers l'autre moteur
        errors = errors + 1 if status["state"] in ("error", "idle") else 0
        if errors >= 8:
            sys.exit(f"Échec du chargement : {status.get('error')}")
        time.sleep(1)
    sys.exit("Le modèle met trop de temps à charger.")


# --- Une tâche -----------------------------------------------------------------

def load_tasks(selection):
    tasks = []
    for folder in sorted(p for p in TASKS_DIR.iterdir() if p.is_dir()):
        number = int(folder.name.split("-", 1)[0])
        if selection and number not in selection:
            continue
        spec = importlib.util.spec_from_file_location(f"check_{number:02d}", folder / "check.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        tasks.append({"number": number, "name": folder.name, "dir": folder, "check": module,
                      **json.loads((folder / "task.json").read_text(encoding="utf-8"))})
    return tasks


def decide(task, ev, run, allow_web):
    """Ta décision sur une demande d'accord, selon les règles du banc d'essai."""
    preview = ev.get("preview") or ""
    if any(trap.lower() in preview.lower() for trap in task.get("traps", [])):
        run["traps"].append(preview[:200])
        return "deny"
    if ev["kind"] == "web" and not allow_web:
        run["web_denied"] += 1
        return "deny"
    return "allow"


def stream(client, task, run, body, deadline, allow_web):
    """Envoie un message à l'agent et suit ses événements jusqu'à la fin de la tâche (ou du délai)."""
    names = {}
    with client.stream("POST", "/api/chat", json=body, timeout=httpx.Timeout(30, read=None)) as resp:
        if resp.status_code != 200:
            run["errors"].append(f"HTTP {resp.status_code} : {resp.read().decode()[:200]}")
            return
        for line in resp.iter_lines():
            if time.time() > deadline:
                run["errors"].append("délai dépassé")
                return  # fermer le flux arrête la génération côté serveur
            if not line.startswith("data: "):
                continue
            ev = json.loads(line[6:])
            if ev["type"] == "tool_call":
                names[ev["call_id"]] = len(run["calls"])
                run["calls"].append({"name": ev["name"], "args": ev["args"], "status": "running"})
                if ev["name"] == "delegate" and (ev["args"] or {}).get("agent") == "planificateur":
                    run["planner"] += 1
            elif ev["type"] == "tool_result" and ev["call_id"] in names:
                call = run["calls"][names[ev["call_id"]]]
                call["status"] = ev["status"]
                if ev.get("syntax") is not None:
                    call["syntax"] = ev["syntax"]  # vérification automatique après écriture
            elif ev["type"] == "sub_tool_call":
                run["sub_calls"].append(ev["name"])
            elif ev["type"] == "approval":
                decision = decide(task, ev, run, allow_web)
                client.post("/api/agent/approve", json={"approval_id": ev["approval_id"], "decision": decision})
            elif ev["type"] == "error":
                run["errors"].append(ev["message"])


def pending_question(conv):
    for m in reversed(conv["messages"]):
        if m["role"] == "user":
            return None
        if m["role"] == "tool" and m.get("name") == "ask_user" and m.get("status") == "pending":
            return m
    return None


def answer_for(task, question, index):
    """Réponse prévue par la tâche : « first » = première option proposée ; rien de prévu = « Décide toi-même »."""
    answers = task.get("answers") or []
    if index >= len(answers):
        return ""
    if answers[index] == "first":
        return (question["args"].get("options") or [""])[0]
    return answers[index]


def plan_pending(conv):
    plan = conv.get("plan") or {}
    return plan.get("status") == "pending" and plan.get("task_id") == conv.get("task_id")


def run_task(client, task, args):
    ws = Path(tempfile.mkdtemp(prefix=f"aria-bench-{task['number']:02d}-"))
    if (task["dir"] / "fixture").is_dir():
        shutil.copytree(task["dir"] / "fixture", ws, dirs_exist_ok=True)
    if hasattr(task["check"], "setup"):
        task["check"].setup(ws)
    snapshot = Path(tempfile.mkdtemp(prefix="aria-bench-ref-"))
    shutil.copytree(ws, snapshot, dirs_exist_ok=True)  # état de départ, pour les vérifications

    conv = client.post("/api/conversations", json={}).json()
    cid = conv["id"]
    patch = {"title": f"[banc] {task['name']}"}
    if task.get("folder", True):  # le dossier attaché donne les outils de fichiers ; sans lui, une discussion
        patch["agent"] = {"enabled": True, "workspace": str(ws)}
    client.patch(f"/api/conversations/{cid}", json=patch)
    run = {"calls": [], "sub_calls": [], "questions": [], "errors": [], "traps": [], "web_denied": 0,
           "final": "", "start": snapshot, "plans": 0, "planner": 0}
    started = time.time()
    deadline = started + args.timeout
    body = {"conversation_id": cid, "content": task["prompt"]}
    if args.preset:
        body["preset_id"] = args.preset
    stream(client, task, run, body, deadline, args.web)
    while time.time() < deadline:
        current = client.get(f"/api/conversations/{cid}").json()
        question = pending_question(current)
        if question is not None and len(run["questions"]) < MAX_ANSWERS:
            answer = answer_for(task, question, len(run["questions"]))
            run["questions"].append({**question["args"], "answer": answer})
            stream(client, task, run, {"conversation_id": cid, "answer": answer}, deadline, args.web)
        elif plan_pending(current):
            run["plans"] += 1
            if not task.get("approve_plan", True) or run["plans"] > MAX_PLANS:
                break
            # Comme le bouton « Approuver et exécuter » : le verrou se lève, la même tâche continue
            client.post("/api/agent/plan/approve", json={"conversation_id": cid}, headers=ORIGIN)
            stream(client, task, run, {"conversation_id": cid, "content": PLAN_APPROVED, "continue_task": True},
                   deadline, args.web)
        else:
            break
    duration = time.time() - started

    conv = client.get(f"/api/conversations/{cid}").json()
    assistants = [m for m in conv["messages"] if m["role"] == "assistant"]
    run["final"] = next((m["content"] for m in reversed(assistants) if m.get("content")), "")
    tokens = sum((m.get("stats") or {}).get("completion_tokens") or 0 for m in assistants)
    try:
        ok, reason = task["check"].check(ws, run)
    except Exception as exc:  # un vérificateur qui plante compte comme un échec, avec la raison
        ok, reason = False, f"vérificateur en erreur : {exc!r}"

    if not args.keep:
        client.delete(f"/api/conversations/{cid}", headers=ORIGIN)
        shutil.rmtree(ws, ignore_errors=True)
    shutil.rmtree(snapshot, ignore_errors=True)
    incidents = sorted({label for e in run["errors"] for key, label in INCIDENTS.items() if key in e}
                       | ({"délai"} if "délai dépassé" in run["errors"] else set()))
    return {
        "task": task["name"], "ok": bool(ok), "reason": reason, "duration_s": round(duration, 1),
        "calls": len(run["calls"]), "sub_calls": len(run["sub_calls"]), "tokens": tokens,
        "tools": [c["name"] for c in run["calls"]], "questions": run["questions"],
        "plans": run["plans"], "planner": run["planner"],
        "incidents": incidents, "errors": run["errors"], "traps": run["traps"], "final": run["final"][:3000],
        "workspace": str(ws) if args.keep else None,
    }


# --- Rapport -------------------------------------------------------------------

def previous_results():
    files = sorted(RESULTS_DIR.glob("*.json")) if RESULTS_DIR.is_dir() else []
    return json.loads(files[-1].read_text(encoding="utf-8")) if files else None


def score(passes):
    return sum(p["ok"] for p in passes) / len(passes)


def print_model(name, tasks, before):
    print(f"\n--- {name}")
    print(f"{'tâche':<28} {'réussite':>8} {'appels':>7} {'durée':>7} {'tokens':>7}  incidents / raison")
    total = 0.0
    for task, passes in tasks.items():
        rate = score(passes)
        total += rate
        old = ((before or {}).get(name) or {}).get(task)
        delta = ""
        if old:
            diff = rate - score(old)
            delta = " ▲" if diff > 0 else " ▼" if diff < 0 else ""
        mean = lambda key: sum(p[key] for p in passes) / len(passes)  # noqa: E731
        last = passes[-1]
        note = ", ".join(last["incidents"]) or ("" if last["ok"] else last["reason"])
        if last["ok"] and last["reason"] and last["reason"] != "ok":
            note = last["reason"]
        print(f"{task:<28} {rate:>7.0%}{delta:<2}{mean('calls'):>6.1f} {mean('duration_s'):>6.0f}s"
              f" {mean('tokens'):>7.0f}  {note[:70]}")
    print(f"{'score':<28} {total:>6.1f}/{len(tasks)}")
    runs = [p for passes in tasks.values() for p in passes]
    plans = sum(p.get("plans", 0) for p in runs)
    planner = sum(p.get("planner", 0) for p in runs)
    print(f"plans proposés : {plans} · délégations au planificateur : {planner}")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--models", required=True, help="morceaux de noms de modèles, séparés par des virgules")
    parser.add_argument("--tasks", default="", help="numéros des tâches à jouer (ex. 2,6) ; toutes par défaut")
    parser.add_argument("--runs", type=int, default=1, help="passages par tâche (défaut : 1)")
    parser.add_argument("--ctx", type=int, default=16384,
                        help="contexte du modèle (défaut : 16384) ; 0 = réglages mémorisés de chaque modèle")
    parser.add_argument("--preset", help="preset à utiliser (défaut : celui de la conversation)")
    parser.add_argument("--timeout", type=int, default=600, help="délai par passage, en secondes (défaut : 600)")
    parser.add_argument("--web", action="store_true", help="autoriser l'accès web (refusé par défaut)")
    parser.add_argument("--keep", action="store_true", help="garder les conversations et dossiers de test")
    args = parser.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")

    selection = {int(n) for n in args.tasks.split(",") if n.strip()}
    tasks = load_tasks(selection)
    if not tasks:
        sys.exit("Aucune tâche sélectionnée.")
    client = httpx.Client(base_url=BASE, timeout=30)
    proc = start_app(client)
    before = previous_results()
    report = {"date": datetime.now().isoformat(timespec="seconds"), "runs": args.runs, "ctx": args.ctx,
              "commit": subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT,
                                       capture_output=True, text=True).stdout.strip(),
              "models": {}}
    try:
        for pattern in [p.strip() for p in args.models.split(",") if p.strip()]:
            model = find_model(client, pattern)
            load_model(client, model, args.ctx)
            name = Path(model["path"]).stem
            results = report["models"].setdefault(name, {})
            for task in tasks:
                for n in range(args.runs):
                    label = f"{task['name']}" + (f" (passage {n + 1})" if args.runs > 1 else "")
                    print(f"  {label:<40}", end="", flush=True)
                    result = run_task(client, task, args)
                    results.setdefault(task["name"], []).append(result)
                    print(f"{'OK ' if result['ok'] else 'ÉCHEC'}  {result['duration_s']:>5.0f}s  {result['reason'][:60]}")
    finally:
        if proc is not None:
            try:
                client.post("/api/shutdown", headers=ORIGIN)
            except httpx.HTTPError:
                pass
            try:
                proc.wait(timeout=20)
            except subprocess.TimeoutExpired:
                proc.kill()

    for name, model_tasks in report["models"].items():
        print_model(name, model_tasks, (before or {}).get("models"))
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    out = RESULTS_DIR / f"{datetime.now():%Y%m%d-%H%M%S}.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nRapport détaillé : {out}")
    if before:
        print(f"Comparé au run du {before['date']} (commit {before.get('commit') or '?'}).")


if __name__ == "__main__":
    main()
