import json
from pathlib import Path


def load_config(path="config.json"):
    config_path = Path(path)

    if not config_path.exists():
        raise FileNotFoundError(f"{path} not found in current directory")

    with open(config_path, "r", encoding="utf-8") as f:
        return json.load(f)
