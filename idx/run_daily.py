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


def _kabari_gagal(judul: str, alasan, stats=None) -> None:
    """Kirim notifikasi kegagalan; kegagalan notifikasi tidak boleh
    mengubah exit code pipeline (kalau tidak, cron melihat sukses jadi gagal)."""
    try:
        from idx import notify
        r = notify.kirim_gagal(judul, alasan, stats)
        print(f"[cron] notifikasi gagal: {json.dumps(r, ensure_ascii=False)}")
    except Exception as e:                   # noqa: BLE001
        print(f"[cron] notifikasi gagal dilewati: {type(e).__name__}: {e}")


def _kumpulkan_eksternal() -> dict:
    """Tahap F1: ambil data eksternal harian SETELAH pipeline sukses.

    Kegagalan di sini TIDAK boleh menggagalkan pipeline (dashboard inti sudah
    terbit) dan TIDAK boleh menaikkan exit code — data eksternal hanya
    pelengkap, dan setiap bagian yang gagal sudah tertulis "data tidak
    tersedia" di dalam payload-nya sendiri.
    """
    try:
        from idx import external as EXT
        payload = EXT.jalankan(tulis=True)
        n_makro = sum(1 for v in (payload.get("makro") or {}).values()
                      if isinstance(v, dict) and isinstance(v.get("nilai"), (int, float)))
        print(f"[cron] external.json diperbarui: makro {n_makro} seri berangka, "
              f"sektor {payload.get('kekuatan_sektor', {}).get('jumlah_sektor')}")
        return {"ok": True, "makro_berangka": n_makro,
                "sektor": payload.get("kekuatan_sektor", {}).get("jumlah_sektor")}
    except Exception as e:                   # noqa: BLE001
        print(f"[cron] data eksternal dilewati: {type(e).__name__}: {e}")
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


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
        _kabari_gagal("Pipeline IDX gagal", f"{type(e).__name__}: {e}")
        return 1

    # Ditolak validasi = data rusak, dashboard TIDAK diperbarui.
    # Ini kegagalan yang WAJIB dikabarkan, bukan sekadar dicatat.
    if res.get("ditolak"):
        alasan = res.get("alasan") or ["validasi prepublish gagal"]
        print(f"[cron] pipeline DITOLAK validasi: {alasan}")
        _kabari_gagal("Publish IDX ditolak validasi", alasan,
                      res.get("validasi"))
        return 1

    if not res.get("ok"):
        print(f"[cron] pipeline tidak ok: {res}")
        _kabari_gagal("Pipeline IDX tidak ok",
                      res.get("alasan") or "tanpa alasan")
        return 1

    print(f"[cron] selesai: {json.dumps(res, ensure_ascii=False)}")

    # 2b) data eksternal harian (Tahap F1) — setelah dashboard inti terbit
    res["eksternal"] = _kumpulkan_eksternal()

    # 3) notifikasi Telegram (opsional)
    try:
        from idx import notify
        notify.kirim_dari_payload(res.get("out"))
    except Exception as e:                   # noqa: BLE001
        print(f"[cron] notifikasi dilewati: {type(e).__name__}: {e}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
