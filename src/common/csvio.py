import os
import csv
from filelock import FileLock

def append_row_csv(path: str, row: dict) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    lock_path = path + ".lock"

    with FileLock(lock_path):
        file_exists = os.path.exists(path)
        with open(path, "a", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=list(row.keys()))
            if (not file_exists) or os.path.getsize(path) == 0:
                writer.writeheader()
            writer.writerow(row)

def read_csv_rows(path: str):
    if not os.path.exists(path) or os.path.getsize(path) == 0:
        return []
    with open(path, "r", encoding="utf-8") as f:
        return list(csv.DictReader(f))
