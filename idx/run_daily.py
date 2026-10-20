#!/usr/bin/env python3
"""
Wrapper untuk cron IDX.

Tugas:
  1. Cek hari ini bursa buka atau libur -> kalau libur, KELUAR tanpa menjalankan
     pipeline (hemat kuota, tidak kirim pesan bodoh).
  2. Jalankan pipeline penuh (Sectors v2 -> yfinance fallback).
  3. Kirim ringkasan ke Telegram (kalau token tersedia).

Exit code:
  0 = sukses (atau libur bursa, yang dianggap sukses-sengaja)
  1 = gagal
"""
from __future__ import annotations

import datetime
import json
import os
import sys

sys.path.insert(0, "/root/scripts")

from idx import calendar_id as CAL          # noqa: E402
from idx import pipeline as PIPE           # noqa: E402


def main() -> int:
    # 1) cek hari bursa
    now = CAL.now_wib()
    d = now.date()
    libur = CAL.hari_libur(d)
    if libur:
        print(f"[cron] {d} ({d.strftime('%A')}) LIBUR BURSA: {libur} "
              f"-> pipeline tidak dijalankan")
        next_buka = CAL.next_trading_day(d)
        if next_buka:
            print(f"[cron] hari bursa berikutnya: {next_buka}")
        return 0

    print(f"[cron] {d} ({d.strftime('%A')}) bursa buka -> jalankan pipeline")

    # 2) jalankan pipeline
    try:
        res = PIPE.run(universe_limit=0, dengan_katalis=True)
    except Exception as e:                   # noqa: BLE001
        print(f"[cron] pipeline GAGAL: {type(e).__name__}: {e}")
        return 1

    if not res.get("ok"):
        print(f"[cron] pipeline tidak ok: {res}")
        return 1

    print(f"[cron] selesai: {json.dumps(res, ensure_ascii=False)}")

    # 3) notifikasi Telegram (opsional)
    try:
        from idx import notify
        notify.kirim_dari_payload(res.get("out"))
    except Exception as e:                   # noqa: BLE001
        print(f"[cron] notifikasi dilewati: {type(e).__name__}: {e}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
