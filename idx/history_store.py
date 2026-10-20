"""
Penyimpan riwayat harga per emiten (Kelompok B).

SATU FILE untuk semua emiten: data/history.json
Format kolom (columnar) supaya tetap kecil walau 975 emiten x 120 sesi:

  {
    "versi_skema": "1.2.0",
    "sesi_maks": 120,
    "diperbarui_pada": "2026-10-20T21:58:02+07:00",
    "sumber": "yfinance",
    "emiten": {
      "KINO": {
        "d": ["2026-05-01", ...],       # tanggal sesi, urut naik
        "c": [1400.0, ...],            # harga close (IDR per lembar)
        "v": [107300.0, null, ...]     # volume (lembar saham; null = tidak dilaporkan)
      }
    }
  }

Kenapa MA20/MA50 TIDAK disimpan di sini:
  MA20/MA50 dihitung ulang di frontend dari seri `c` yang sama persis dengan
  pipeline. Kalau disimpan dua kali, angka bisa berbeda antara dashboard dan
  latest.json — persis masalah yang Group A sudah bereskan untuk satuan.
  Jadi: pipeline dan frontend menghitung dari satu sumber angka yang sama.

Satuan (lihat README.md -> "Satuan data"):
  close  -> IDR per lembar
  volume -> jumlah lembar saham (null = sumber tidak melapor, BUKAN 0)
"""

from __future__ import annotations

import json
import os
import sys
from typing import Any, Dict, List, Optional, Tuple

REPO = "/root/idx-dashboard"
DATA_DIR = os.path.join(REPO, "data")
HISTORY_FILE = os.path.join(DATA_DIR, "history.json")

SESI_MAKS = 120          # jumlah sesi maksimum per emiten
VERSI_SKEMA_HISTORY = "1.2.0"


def _tulis_atomic(path: str, payload: Any) -> None:
    """Tulis JSON lewat file sementara lalu rename (atomik)."""
    tmp = f"{path}.tmp.{os.getpid()}"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, separators=(",", ":"))
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            try:
                os.unlink(tmp)
            except OSError:
                pass


def load(path: str = HISTORY_FILE) -> Optional[Dict[str, Any]]:
    """Baca history.json lama. None kalau file tidak ada / rusak."""
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def build(bars_by_kode: Dict[str, List[Dict[str, Any]]],
          max_sesi: int = SESI_MAKS,
          sumber: str = "") -> Dict[str, Any]:
    """
    Bangun payload history dari {kode: [baris OHLCV]}.

    Baris: {date, open, high, low, close, volume}
    Hanya `close` dan `volume` yang disimpan — open/high/low tidak dipakai
    chart garis + MA + volume, jadi tidak perlu menambah ukuran file.
    """
    emiten: Dict[str, Any] = {}
    n_baris = 0
    for kode, bars in (bars_by_kode or {}).items():
        if not bars:
            continue

        # defensif: pastikan urut naik berdasarkan tanggal & tanggal unik.
        # Provider memang sudah mengurutkan, tapi modul ini tidak boleh
        # bergantung pada itu — kalau tidak urut, MA di frontend salah.
        urut = sorted(
            enumerate(bars),
            key=lambda kv: (str(kv[1].get("date") or "")[:10], kv[0]),
        )
        # tanggal sama -> ambil entri terakhir (data paling baru)
        uniq: Dict[str, Dict[str, Any]] = {}
        for _, b in urut:
            dt = str(b.get("date") or "")[:10]
            if dt:
                uniq[dt] = b

        d: List[str] = []
        c: List[Optional[float]] = []
        v: List[Optional[float]] = []
        for dt in sorted(uniq):
            b = uniq[dt]
            close = b.get("close")
            if close is None:
                continue
            d.append(dt)
            try:
                c.append(round(float(close), 2))
            except (TypeError, ValueError):
                c.append(None)
            vol = b.get("volume")
            try:
                v.append(int(vol) if vol is not None else None)
            except (TypeError, ValueError):
                v.append(None)
        if not d:
            continue
        # potong ke max_sesi TERAKHIR
        if len(d) > max_sesi:
            d, c, v = d[-max_sesi:], c[-max_sesi:], v[-max_sesi:]
        emiten[kode] = {"d": d, "c": c, "v": v}
        n_baris += len(d)

    return {
        "versi_skema": VERSI_SKEMA_HISTORY,
        "sesi_maks": max_sesi,
        "diperbarui_pada": _stamp(),
        "sumber": sumber,
        "jumlah_emiten": len(emiten),
        "jumlah_sesi": n_baris,
        "emiten": emiten,
    }


def merge_lama(baru: Dict[str, Any],
               lama: Optional[Dict[str, Any]]) -> Tuple[Dict[str, Any], int]:
    """
    Gabungkan history baru dengan history lama.

    Aturan: kalau sebuah emiten gagal diambil pada run ini (tidak ada baris
    sama sekali), baris lamanya DIPERTAHANKAN supaya grafik tidak bolong
    gara-gara satu request yang gagal. Run penuh tetap menulis ulang file,
    tapi tidak menghapus riwayat emiten yang sedang bermasalah.
    """
    if not lama or not isinstance(lama.get("emiten"), dict):
        return baru, 0

    lama_emiten = lama["emiten"]
    baru_emiten = baru.get("emiten", {})
    dipertahankan = 0
    for kode, entry in lama_emiten.items():
        if kode in baru_emiten:
            continue
        if isinstance(entry, dict) and entry.get("d"):
            baru_emiten[kode] = entry
            dipertahankan += 1
    baru["emiten"] = baru_emiten
    baru["jumlah_emiten"] = len(baru_emiten)
    baru["jumlah_sesi"] = sum(len(e.get("d", [])) for e in baru_emiten.values())
    baru["emiten_diperahankan_dari_run_lama"] = dipertahankan
    return baru, dipertahankan


def publish(payload: Dict[str, Any], path: str = HISTORY_FILE) -> str:
    """Tulis history.json secara atomik. Kembalikan path tujuan."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    _tulis_atomic(path, payload)
    return path


def _stamp() -> str:
    """Timestamp WIB ISO-8601 (tanpa import providers -> bebas circular)."""
    from datetime import datetime, timedelta, timezone
    wib = timezone(timedelta(hours=7))
    return datetime.now(wib).replace(microsecond=0).isoformat()


if __name__ == "__main__":
    p = load()
    if not p:
        print("history.json belum ada")
    else:
        print(f"history.json: {p.get('jumlah_emiten')} emiten, "
              f"{p.get('jumlah_sesi')} sesi, diperbarui {p.get('diperbarui_pada')}")
