"""
Periode anomali pasar — sumber tunggal untuk penandaan & backtest.

Periode dengan pergerakan ekstrem (crash sharp, lonjakan tak wajar, dll.)
meng distort statistik backtest. Menandainya secara eksplisit
membuat statistik bisa dilihat DENGAN dan TANPA periode tersebut, supaya
user bisa menilai apakah angka jangka panjang (structural) atau memang ada
efek kejadian sesaat.

Definisi periode TIDAK boleh diubah diam-diam: kalau ditambah/diubah,
`diperbarui_pada` ikut diperbarui dan perubahan tercatat di `catatan`.

Periode 15-21 Okt 2026:
  IHSG 5.863 (15 Okt) -> 5.185 (16 Okt) -> 4.996 (19 Okt) -> 5.282 (21 Okt)
  = turun ~11,1% dalam 5 sesi, disertai pengumuman usulan reklasifikasi
  MSCI Indonesia (Emerging -> Standalone) pada 15 Okt 2026 (belum final).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

VERSI_SKEMA_ANOMALI = "1.0.0"
WIB = timezone(timedelta(hours=7))

PERIODE_ANOMALI: List[Dict[str, Any]] = [
    {
        "id": "okt2026_guncangan",
        "mulai": "2026-10-15",
        "selesai": "2026-10-21",
        "sebab": ("IHSG turun ~11,1% dalam 5 sesi (5.863 -> 5.185 -> 4.996 -> "
                  "5.282), disertai pengumuman usulan reklasifikasi MSCI "
                  "15 Okt 2026 (Emerging -> Standalone, belum final)"),
        "bukti": {
            "ihsg_15okt": 5863.0,
            "ihsg_16okt": 5185.0,
            "ihsg_19okt": 4996.0,
            "ihsg_21okt": 5282.0,
            "sumber": "^JKSE (yfinance)",
        },
        "dipakai_backtest": True,
        "catatan": ("Statistik backtest harus dilaporkan DENGAN dan TANPA "
                    "periode ini. Jangan hanya salah satu."),
    },
]


def daftar_periode() -> List[Dict[str, Any]]:
    """Salinan daftar periode (supaya pemanggil tak bisa mengubah sumber)."""
    return [dict(p) for p in PERIODE_ANOMALI]


def tanggal_dalam_anomali(tanggal_iso: Optional[str]) -> bool:
    """True kalau tanggal masuk salah satu periode anomali."""
    if not tanggal_iso:
        return False
    t = str(tanggal_iso)[:10]
    return any(p["mulai"] <= t <= p["selesai"] for p in PERIODE_ANOMALI)


def filter_anomali(tanggal_iso: Optional[str], sertakan: bool = False) -> bool:
    """True kalau titik backtest layak dipakai.

    CATATAN SEMANTIK: ini versi "sederhana" — hanya melihat TANGGAL SINYAL.
    Untuk backtest yang benar, pakai `jendela_beririsan()`: sebuah titik
    dihitung anomali kalau JENDELA evaluasinya (tanggal sinyal s.d. T+h
    sesi) beririsan dengan periode anomali, bukan hanya kalau tanggal
    sinyalnya jatuh di dalam periode.

    sertakan=False -> buang titik yang jatuh di periode anomali.
    sertakan=True  -> keep semuanya (statistik "dengan anomali").
    """
    if sertakan:
        return True
    return not tanggal_dalam_anomali(tanggal_iso)


def jendela_beririsan(tanggal_sinyal: Optional[str],
                      tanggal_akhir_jendela: Optional[str]) -> bool:
    """True kalau JENDELA evaluasi titik beririsan dengan periode anomali.

    Definisi (dipakai backtest & UI):
      titik dianggap "dalam anomali" kalau interval
      [tanggal_sinyal .. tanggal_akhir_jendela] beririsan dengan
      [periode.mulai .. periode.selesai] untuk salah satu periode.

    Konsekuensi yang harus dinyatakan di UI:
      * titik sinyal 15-21 Okt 2026 sendiri belum bisa dievaluasi
        (jendelanya belum selesai pada horizon 5/10/20 sesi), sehingga
        TIDAK boleh dihitung sebagai "anomali" — hanya sinyal SEBELUM
        periode itu yang jendelanya menabrak periode anomali.
      * satu titik bisa beririsan untuk horizon 5 tapi tidak untuk 20
        (kalau ujung jendelanya masuk sebelum periode mulai).

    Argumen boleh None/kosong -> dianggap tidak beririsan (bukan error).
    """
    if not tanggal_sinyal:
        return False
    t0 = str(tanggal_sinyal)[:10]
    t1 = str(tanggal_akhir_jendela)[:10] if tanggal_akhir_jendela else t0
    if t1 < t0:            # jendela rusak -> anggap tidak beririsan
        return False
    for p in PERIODE_ANOMALI:
        if t0 <= p["selesai"] and p["mulai"] <= t1:
            return True
    return False


def ringkas(hari_ini: Optional[str] = None) -> Dict[str, Any]:
    """Ringkasan periode anomali untuk disimpan di external.json."""
    if hari_ini is None:
        # Hitung sendiri (WIB) supaya modul ini tidak bergantung pada
        # idx.external -> avoids circular import & bisa dipakai sebagai script.
        hari_ini = datetime.now(WIB).date().isoformat()
    aktif = [p for p in PERIODE_ANOMALI
             if p["mulai"] <= hari_ini <= p["selesai"]]
    return {
        "versi_skema": VERSI_SKEMA_ANOMALI,
        "periode": daftar_periode(),
        "sedang_aktif": aktif,
        "ada_anomali_aktif": bool(aktif),
        "jumlah_periode": len(PERIODE_ANOMALI),
        "catatan": ("Backtest wajib melaporkan statistik DENGAN dan TANPA "
                    "periode anomali; jangan hanya salah satu."),
        "fungsi_filter": "idx.anomali.filter_anomali(tanggal, sertakan=False)",
        "fungsi_jendela": ("idx.anomali.jendela_beririsan(tanggal_sinyal, "
                           "tanggal_akhir_jendela) — dipakai backtest: "
                           "titik dihitung anomali kalau JENDELA evaluasinya "
                           "beririsan dengan periode, bukan hanya tanggal "
                           "sinyalnya"),
    }
