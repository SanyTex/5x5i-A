import json
import os
from filelock import FileLock

def load_json(path: str, default):
    if not os.path.exists(path) or os.path.getsize(path) == 0:
        return default
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)

def save_json(path: str, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    lock = FileLock(path + ".lock")
    with lock:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
