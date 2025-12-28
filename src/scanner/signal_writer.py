# ===============================================================
# ✍️ signal_writer.py – UTF-8 sicherer Signal-Writer (append-only)
# ===============================================================
# - stabile signal_id (sha1)
# - schreibt CSV mit utf-8 (ä/ö/ü safe)
# - legt Ordner automatisch an
# - schreibt Header nur beim ersten Mal
# - Windows + Linux (Raspberry Pi) kompatibel
# ===============================================================

from __future__ import annotations

import csv
import hashlib
import os
from typing import Dict


def make_signal_id(ts_signal: str, symbol: str, direction: str) -> str:
    """
    Erzeugt eine stabile, kurze Signal-ID (16 chars),
    deterministisch aus Zeit + Symbol + Richtung.
    """
    raw = f"{ts_signal}|{symbol}|{direction}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def write_signal(path: str, row: Dict) -> None:
    """
    Append-only CSV writer:
    - UTF-8 Encoding (ä/ö/ü safe)
    - schreibt Header nur wenn Datei neu ist
    - erstellt Zielordner automatisch
    """

    # Zielordner sicherstellen
    os.makedirs(os.path.dirname(path), exist_ok=True)

    file_exists = os.path.isfile(path)

    with open(path, mode="a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=list(row.keys()),
            extrasaction="ignore",
        )

        # Header nur beim ersten Schreiben
        if not file_exists:
            writer.writeheader()

        writer.writerow(row)
