import sys

import uvicorn

from . import config


def main():
    if sys.stdout is None:
        # Lancé par pythonw (lanceur Aria Home.exe) : pas de console, on écrit dans un journal
        log_path = config.ROOT / "data" / "aria_home.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        sys.stdout = sys.stderr = open(log_path, "w", encoding="utf-8", buffering=1)

    from . import main as app_module

    port = config.get()["app_port"]
    print(f"Aria Home → http://127.0.0.1:{port}")
    server = uvicorn.Server(uvicorn.Config(app_module.app, host="127.0.0.1", port=port))
    app_module.uvicorn_server = server  # pour le bouton Quitter
    server.run()


if __name__ == "__main__":
    main()
