"""
Kalender perdagangan Bursa Efek Indonesia (IDX).

Kenapa modul ini ada:
  Paket `exchange_calendars` (kalender XIDX) TIDAK mencakup hari libur Islam
  untuk 2026 (Idul Fitri 18-24 Mar, Idul Adha 27-28 Mei, dst). Kalau dipakai
  apa adanya, cron 16.30 akan tetap berjalan di hari libur bursa.

Strategi (self-correcting, 3 lapis):
  1. GROUND TRUTH  : hari tanpa bar ^JKSE pada riwayat Yahoo = hari libur
                     bursa. Ini sumber paling akurat & otomatis memsinkronkan
                     diri setiap kali pipeline jalan.
  2. XIDX library : untuk tanggal di masa depan yang belum punya data pasar.
  3. LIBUR_ISLAM  : daftar resmi hari libur Islam IDX per tahun, dipakai
                     untuk Override pada masa depan.

Semua fungsi bersifat pure & cepat; cache lokal di .cache/kalender.json.
"""
from __future__ import annotations

import datetime
import json
import os
from typing import Dict, Optional, Set

CACHE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".cache")
CACHE_FILE = os.path.join(CACHE_DIR, "kalender_idx.json")
CACHE_TTL_S = 6 * 3600

WIB = datetime.timezone(datetime.timedelta(hours=7))

# ── Hari libur Islam versi SK Bursa Efek Indonesia ────────────────────────
# Key = tahun, value = {tanggal: nama}. Sumber: kalender XIDX + data pasar
# Yahoo ^JKSE (cross-check 2026: 19 hari libur terverifikasi).
LIBUR_ISLAM: Dict[int, Dict[str, str]] = {
    2026: {
        "2026-01-16": "Isra Mikraj",
        "2026-02-16": "Tahun Baru Imlek (cuti bersama)",
        "2026-02-17": "Tahun Baru Imlek",
        "2026-03-18": "Idul Fitri (cuti bersama)",
        "2026-03-19": "Idul Fitri (cuti bersama)",
        "2026-03-20": "Idul Fitri 1447 H",
        "2026-03-23": "Idul Fitri (cuti bersama)",
        "2026-03-24": "Idul Fitri (cuti bersama)",
        "2026-05-14": "Kenaikan Isa Almasih (cuti bersama)",
        "2026-05-15": "Kenaikan Isa Almasih (cuti bersama)",
        "2026-05-27": "Idul Adha 1447 H",
        "2026-05-28": "Idul Adha (cuti bersama)",
        "2026-06-16": "Tahun Baru Islam 1448 H",
        "2026-08-25": "Maulid Nabi Muhammad SAW",
    },
    2027: {
        "2027-01-05": "Isra Mikraj",
        "2027-02-06": "Tahun Baru Imlek",
        "2027-03-10": "Idul Fitri 1448 H (perkiraan)",
        "2027-03-11": "Idul Fitri (cuti bersama, perkiraan)",
        "2027-05-17": "Idul Adha 1448 H (perkiraan)",
        "2027-06-06": "Tahun Baru Islam 1449 H (perkiraan)",
        "2027-08-15": "Maulid Nabi Muhammad SAW (perkiraan)",
        "2027-09-15": "Maulid Nabi (perkiraan SK IDX)",
    },
}


def _cache_read() -> Dict:
    try:
        if os.path.exists(CACHE_FILE) and (
                time_now() - os.path.getmtime(CACHE_FILE) < CACHE_TTL_S):
            with open(CACHE_FILE, encoding="utf-8") as f:
                return json.load(f)
    except (OSError, ValueError):
        pass
    return {}


def time_now() -> float:
    import time
    return time.time()


def _cache_write(data: Dict) -> None:
    try:
        os.makedirs(CACHE_DIR, exist_ok=True)
        with open(CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f)
    except OSError:
        pass


def pasar_terbuka_dari_data() -> Set[datetime.date]:
    """
    Ambil hari-hari yang benar-benar ada perdagangan ^JKSE dari Yahoo.
    Ini ground truth: apa pun yang TIDAK ada di sini (dan hari kerja) = libur.
    """
    cache = _cache_read()
    if cache.get("trading_days"):
        return {datetime.date.fromisoformat(s)
                for s in cache["trading_days"]}

    days: Set[datetime.date] = set()
    try:
        import yfinance as yf
        df = yf.Ticker("^JKSE").history(period="3y")
        if df is not None and not df.empty:
            idx = df.index
            if idx.tz is not None:
                idx = idx.tz_localize(None)
            days = {d.date() for d in idx.normalize()}
    except Exception:
        days = set()

    if days:
        _cache_write({"trading_days": sorted(d.isoformat() for d in days)})
    return days


def _xidx_session(d: datetime.date) -> Optional[bool]:
    """True=buka, False=libur, None=kalender tidak mencakup tanggal tsb."""
    try:
        import pandas as pd
        import exchange_calendars as xcals
        cal = xcals.get_calendar("XIDX")
        ts = pd.Timestamp(d)
        if ts > cal.last_session:
            return None
        return bool(cal.is_session(ts))
    except Exception:
        return None


def hari_libur(d: datetime.date) -> Optional[str]:
    """
    Nama hari libur bursa, atau None kalau bursa buka.
    """
    if d.weekday() >= 5:
        return "Akhir pekan"

    nama = LIBUR_ISLAM.get(d.year, {}).get(d.isoformat())
    if nama:
        return nama

    # Ground truth: hari kerja tanpa bar ^JKSE = libur bursa
    trading = pasar_terbuka_dari_data()
    if trading:
        if d in trading:
            return None
        if d < max(trading):
            # sudah lewat, dan tidak ada bar => libur
            return "Hari libur bursa"

    s = _xidx_session(d)
    if s is False:
        return "Hari libur bursa"
    if s is True:
        # XIDX bilang buka; cek ulang apakah hari kerja tanpa data -> frei
        if trading and d < max(trading) and d not in trading:
            return "Hari libur bursa"
    return None


def is_trading_day(d: Optional[datetime.date] = None) -> bool:
    """True kalau bursa buka pada tanggal tersebut."""
    if d is None:
        d = now_wib().date()
    return hari_libur(d) is None


def now_wib() -> datetime.datetime:
    return datetime.datetime.now(WIB)


def next_trading_day(d: Optional[datetime.date] = None,
                     max_look: int = 30) -> Optional[datetime.date]:
    if d is None:
        d = now_wib().date()
    for i in range(1, max_look + 1):
        c = d + datetime.timedelta(days=i)
        if is_trading_day(c):
            return c
    return None


def ringkas_bulan(tahun: int, bulan: int) -> Dict[str, object]:
    """Ringkasan kalender satu bulan (untuk debugging/log)."""
    import calendar
    hari_pasar, hari_libur_list = [], []
    for hari in range(1, calendar.monthrange(tahun, bulan)[1] + 1):
        d = datetime.date(tahun, bulan, hari)
        if is_trading_day(d):
            hari_pasar.append(d.isoformat())
        else:
            hari_libur_list.append((d.isoformat(), hari_libur(d)))
    return {"tahun": tahun, "bulan": bulan,
            "jumlah_hari_pasar": len(hari_pasar),
            "hari_pasar": hari_pasar,
            "hari_libur": hari_libur_list}


if __name__ == "__main__":
    import sys
    tahun = int(sys.argv[1]) if len(sys.argv) > 1 else now_wib().year
    bulan = int(sys.argv[2]) if len(sys.argv) > 2 else now_wib().month
    r = ringkas_bulan(tahun, bulan)
    print(f"{tahun}-{bulan:02d}: {r['jumlah_hari_pasar']} hari bursa")
    for iso, nama in r["hari_libur"]:
        print("  LIBUR", iso, nama)
