"""
Buat data/snapshots/index.json — daftar snapshot harian untuk dashboard
(grafik riwayat harga & backtest sinyal).

Dijalankan otomatis di akhir pipeline (`python3 idx/pipeline.py`).
"""

from __future__ import annotations

import json
import os
import sys

REPO = "/root/idx-dashboard"
SNAP_DIR = os.path.join(REPO, "data", "snapshots")
OUT = os.path.join(SNAP_DIR, "index.json")


def build() -> dict:
    os.makedirs(SNAP_DIR, exist_ok=True)
    files = sorted(f for f in os.listdir(SNAP_DIR)
                   if f.endswith(".json") and f != "index.json")
    payload = {"files": files, "jumlah": len(files)}
    if files:
        payload["terlama"] = files[0][:10]
        payload["terbaru"] = files[-1][:10]
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=1)
    return payload


if __name__ == "__main__":
    p = build()
    print(f"index.json: {p['jumlah']} snapshot")
