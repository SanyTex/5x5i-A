# ===============================================================
# 📄 csvio.py – Robust CSV IO (UTF-8, append-only, lock-safe)
# ===============================================================
# Ziele:
# - UTF-8 immer (ä/ö/ü korrekt)
# - append-only mit Header-Handling
# - Datei-Lock via filelock (cross-platform)
# - tolerant bei fehlenden/leerem File
# - stable fieldnames: einmal Header gesetzt -> danach konsistent
# ===============================================================

from __future__ import annotations

import csv
import os
from typing import Dict, List, Optional

from filelock import FileLock


def _ensure_parent_dir(path: str) -> None:
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)


def _read_header(path: str) -> Optional[List[str]]:
    """Liest die Header-Zeile (fieldnames) aus einer bestehenden CSV."""
    if not os.path.exists(path) or os.path.getsize(path) == 0:
        return None

    with open(path, "r", newline="", encoding="utf-8") as f:
        reader = csv.reader(f)
        try:
            header = next(reader)
        except StopIteration:
            return None

    header = [h.strip() for h in header if h is not None]
    return header or None


def append_row_csv(path: str, row: Dict) -> None:
    """
    Hängt eine Zeile an eine CSV an (append-only), thread-/process-safe via Lock.

    Verhalten:
    - Legt Ordner an
    - Lock: <path>.lock
    - Wenn Datei nicht existiert oder leer: schreibt Header (keys aus row)
    - Wenn Datei existiert: nutzt bestehenden Header und schreibt Werte passend
      (fehlende Keys -> leer, zusätzliche Keys -> ignoriert)
    """
    _ensure_parent_dir(path)
    lock_path = path + ".lock"

    with FileLock(lock_path):
        existing_header = _read_header(path)
        file_exists_and_has_content = existing_header is not None

        # Header festlegen: existing wenn vorhanden, sonst aktuelle row.keys()
        fieldnames = existing_header if existing_header else list(row.keys())

        with open(path, "a", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(
                f,
                fieldnames=fieldnames,
                extrasaction="ignore",  # zusätzliche Keys ignorieren
            )

            if not file_exists_and_has_content:
                writer.writeheader()

            # Nur Keys, die im Header sind (fehlende Keys werden als "" geschrieben)
            writer.writerow({k: row.get(k, "") for k in fieldnames})


def read_csv_rows(path: str) -> List[Dict[str, str]]:
    """
    Liest eine CSV als List[Dict]. Gibt [] zurück wenn Datei fehlt oder leer ist.
    """
    if not os.path.exists(path) or os.path.getsize(path) == 0:
        return []

    with open(path, "r", newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))
