"""Service de supervision : lit config.json au démarrage."""

import json
from pathlib import Path

CONFIG = json.loads((Path(__file__).parent / "config.json").read_text(encoding="utf-8"))
