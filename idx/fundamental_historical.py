#!/usr/bin/env python3
"""
J1 — Pengumpul DATA FUNDAMENTAL HISTORIS untuk Skor Investasi Jangka Panjang.

Yang dikumpulkan per emiten (yfinance .JK, laporan tahunan 3–5 tahun):
  laba bersih, pendapatan, ekuitas, total utang, arus kas operasi,
  beban bunga, dividen per saham.

Yang dihitung:
  ROE rata-rata 3 tahun, CAGR laba & pendapatan 3 tahun, margin laba,
  konsistensi laba, DER, interest coverage, OCF/laba, dividend yield,
  payout ratio, konsistensi dividen.

Prinsip:
  * TIDAK ada tebakan. Nilai yang tidak tersedia = null + label
    "data tidak tersedia" (lihat `metrik.tidak_tersedia`), bukan 0.
  * Setiap emiten menyimpan periode laporan (tanggal fiskal) dan sumber.
  * Modul ini HANYA mengumpulkan & menghitung metrik — TIDAK menghitung skor.
  * Sectors.app tidak dipakai kalau SECTORS_API_KEY tidak diset.

Jalankan:
    python3 -m idx.fundamental_historical
    python3 idx/fundamental_historical.py --batas 20 --tanpa-tulis
    python3 idx/fundamental_historical.py --kode BBCA,TLKM
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

# ───────────────────────── path / constants ─────────────────────────

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

PATH_LATEST = os.path.join(_ROOT, "data", "latest.json")
PATH_FUNDAMENTAL = os.path.join(_ROOT, "data", "fundamental.json")
PATH_BOBOT_JP = os.path.join(_ROOT, "config", "bobot_jp.json")

WIB = timezone(timedelta(hours=7))
VERSI_FUNDAMENTAL_HISTORIS = "1.1.0"
TIDAK_ADA = "data tidak tersedia"

MAX_TAHUN = 5                 # ambil maksimal 5 tahun fiskal terakhir
MAX_RETRY = 3
BASE_BACKOFF = 1.5            # detik
DEFAULT_WORKERS = int(os.environ.get("IDX_FUND_WORKERS", "6"))
DIVIDEN_TAHUN_DISIMPAN = 10   # riwayat dividen kalender yang disimpan

# Urutan tahun pada ringkasan
_FIELD_INTI_5 = [
    "laba_bersih", "pendapatan", "ekuitas", "total_utang", "arus_kas_operasi",
]
_FIELD_INTI_6 = _FIELD_INTI_5 + ["beban_bunga"]

_METRIK_KUNCI = [
    ("profitabilitas", "roe_rata2_3th"),
    ("pertumbuhan", "cagr_laba_3th"),
    ("pertumbuhan", "cagr_pendapatan_3th"),
    ("profitabilitas", "margin_laba_rata2_3th"),
    ("kesehatan_keuangan", "der_rata2_3th"),
    ("kesehatan_keuangan", "interest_coverage_rata2_3th"),
    ("kesehatan_keuangan", "ocf_laba_rata2_3th"),
    ("dividen", "dividend_yield_ttm"),
    ("dividen", "payout_ratio_rata2_3th"),
    ("dividen", "konsistensi_dividen_3th"),
    ("konsistensi", "laba_positif_3th"),
]

# Key camelCase fundamentals-timeseries -> label yang dipakai _pilih_baris.
# Satu request bisa mengambil semuanya; ini jauh lebih cepat daripada
# memanggil income_stmt + balance_sheet + cashflow terpisah.
_KUNCI_INCOME = {
    "NetIncome": "Net Income",
    "NetIncomeCommonStockholders": "Net Income Common Stockholders",
    "TotalRevenue": "Total Revenue",
    "OperatingRevenue": "Operating Revenue",
    "InterestExpense": "Interest Expense",
    "PretaxIncome": "Pretax Income",
    "EBIT": "EBIT",
    "DilutedEPS": "Diluted EPS",
    "BasicEPS": "Basic EPS",
}
_KUNCI_BALANCE = {
    "StockholdersEquity": "Stockholders Equity",
    "CommonStockEquity": "Common Stock Equity",
    "TotalEquityGrossMinorityInterest": "Total Equity Gross Minority Interest",
    "TotalDebt": "Total Debt",
    "LongTermDebtAndCapitalLeaseObligation": "Long Term Debt And Capital Lease Obligation",
    "OrdinarySharesNumber": "Ordinary Shares Number",
    "ShareIssued": "Share Issued",
}
_KUNCI_CASHFLOW = {
    "OperatingCashFlow": "Operating Cash Flow",
    "CashFlowsfromusedinOperatingActivitiesDirect":
        "Cash Flowsfromusedin Operating Activities Direct",
}
_KUNCI_SEMUA = (
    list(_KUNCI_INCOME) + list(_KUNCI_BALANCE) + list(_KUNCI_CASHFLOW)
)

# ───────────────────────── util waktu / angka ─────────────────────────

def sekarang_wib() -> datetime:
    return datetime.now(WIB)


def stamp_wib(dt: Optional[datetime] = None) -> str:
    return (dt or sekarang_wib()).replace(microsecond=0).isoformat()


def _angka(v: Any) -> Optional[float]:
    """Konversi ke float tanpa melempar; None kalau tidak masuk akal."""
    if v is None or isinstance(v, bool):
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(f):
        return None
    return f


def _rata2(values: Iterable[Optional[float]]) -> Tuple[Optional[float], int]:
    """Rata-rata mengabaikan None. Kembalikan (nilai, jumlah_nilai_valid)."""
    valid = [v for v in values if v is not None]
    if not valid:
        return None, 0
    return sum(valid) / len(valid), len(valid)


def _cagr(awal: Optional[float], akhir: Optional[float], tahun: int) -> Optional[float]:
    if awal is None or akhir is None or awal <= 0 or akhir <= 0 or tahun <= 0:
        return None
    try:
        return (akhir / awal) ** (1.0 / tahun) - 1.0
    except (ZeroDivisionError, OverflowError, ValueError):
        return None


def _tulis_atomic(path: str, payload: Any) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = f"{path}.tmp.{os.getpid()}"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=1)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


# ───────────────────────── ekstraksi laporan ─────────────────────────

def _tanggal_kolom(col: Any) -> Optional[date]:
    """Kolom yfinance -> date. None kalau tidak bisa diparse."""
    if isinstance(col, str):
        try:
            return date.fromisoformat(col[:10])
        except ValueError:
            return None
    try:
        d = col.date()
        if isinstance(d, date):
            return d
    except Exception:
        pass
    try:
        return date.fromisoformat(str(col)[:10])
    except ValueError:
        return None


def _pilih_baris(df: Any, names: Sequence[str]) -> Optional[Any]:
    """
    Pilih label baris laporan dengan urutan:
      1. exact match (paling aman, mis. 'Net Income' BUKAN
         'Net Income From Continuing Operation Net Minority Interest')
      2. startswith
      3. substring
    """
    if df is None or getattr(df, "empty", True):
        return None
    try:
        labels = list(df.index)
    except Exception:
        return None
    if not labels:
        return None

    teks = [str(x).strip().lower() for x in labels]
    for nama in names:
        target = nama.strip().lower()
        for i, t in enumerate(teks):
            if t == target:
                return labels[i]
    for nama in names:
        target = nama.strip().lower()
        for i, t in enumerate(teks):
            if t.startswith(target):
                return labels[i]
    for nama in names:
        target = nama.strip().lower()
        for i, t in enumerate(teks):
            if target in t:
                return labels[i]
    return None


def _ekstrak_tahunan(
    df: Any,
    names: Sequence[str],
    hari_ini: date,
) -> Tuple[Dict[int, Optional[float]], Optional[str]]:
    """
    Ekstrak satu baris laporan menjadi {tahun_fiskal: nilai}.
    Kolom dengan tanggal > hari ini diabaikan (bukan laporan aktual).
    """
    out: Dict[int, Optional[float]] = {}
    label = _pilih_baris(df, names)
    if label is None:
        return out, None

    try:
        row = df.loc[label]
        # Kalau index duplikat, df.loc bisa mengembalikan DataFrame.
        if getattr(row, "ndim", 1) > 1:
            row = row.iloc[0]
    except Exception:
        return out, None

    kolom = []
    for col in row.index:
        d = _tanggal_kolom(col)
        if d is not None and d <= hari_ini:
            kolom.append((d, col))
    # Kolom terbaru menang kalau ada duplikat tahun (mis. restatement).
    for d, col in sorted(kolom, key=lambda x: x[0]):
        out[d.year] = _angka(row.get(col))
    return out, label


def _peta_periode(df: Any, hari_ini: date) -> Dict[int, str]:
    """Peta tahun fiskal -> tanggal periode laporan (YYYY-MM-DD)."""
    out: Dict[int, str] = {}
    if df is None or getattr(df, "empty", True):
        return out
    for col in df.columns:
        d = _tanggal_kolom(col)
        if d is not None and d <= hari_ini:
            out[d.year] = d.isoformat()
    return out


def _simbol_yfinance(kode: str) -> str:
    k = str(kode).strip().upper()
    return k if k.endswith(".JK") else f"{k}.JK"


# ───────────────────────── pengambilan data ─────────────────────────

def _potong_statement(df: Any, mapping: Dict[str, str]) -> Any:
    """Ambil subset baris dari tabel gabungan dan beri label yang mudah dibaca."""
    if df is None or getattr(df, "empty", True):
        return df
    rows = [k for k in mapping if k in df.index]
    if not rows:
        return df.iloc[0:0]
    out = df.loc[rows].copy()
    out.index = [mapping[k] for k in rows]
    return out


def _ambil_satu(kode: str) -> Dict[str, Any]:
    """Ambil 3 laporan + dividen dari yfinance, dengan retry."""
    try:
        import yfinance as yf
    except ImportError:
        return {"ok": False, "error": "yfinance belum terpasang"}

    sim = _simbol_yfinance(kode)
    last_err: Optional[str] = None
    for attempt in range(MAX_RETRY):
        try:
            tk = yf.Ticker(sim)
            galat_bagian: List[str] = []

            def _ambil(nama: str) -> Any:
                try:
                    return getattr(tk, nama)
                except Exception as e:  # noqa: BLE001
                    galat_bagian.append(f"{nama}: {type(e).__name__}: {e}")
                    return None

            # Jalur cepat: satu request fundamentals-timeseries untuk semua
            # baris laporan yang dibutuhkan. Kalau gagal, fallback ke properti
            # standar (3 request terpisah).
            gabungan = None
            try:
                gabungan = tk._fundamentals.financials._get_financials_time_series(
                    "yearly", _KUNCI_SEMUA
                )
            except Exception as e:  # noqa: BLE001
                galat_bagian.append(f"gabungan: {type(e).__name__}: {e}")

            if gabungan is not None and not getattr(gabungan, "empty", True):
                inc = _potong_statement(gabungan, _KUNCI_INCOME)
                bal = _potong_statement(gabungan, _KUNCI_BALANCE)
                cf = _potong_statement(gabungan, _KUNCI_CASHFLOW)
            else:
                inc = _ambil("income_stmt")
                bal = _ambil("balance_sheet")
                cf = _ambil("cashflow")
            div = _ambil("dividends")

            def _kosong(x: Any) -> bool:
                return x is None or getattr(x, "empty", True)

            if _kosong(inc) and _kosong(bal) and _kosong(cf) and _kosong(div):
                # Bukan error jaringan: Yahoo memang tidak punya laporan untuk
                # emiten ini. Jangan retry 3x — itu hanya memperlambat run.
                return {
                    "ok": False,
                    "symbol": sim,
                    "error": "yfinance tidak menyediakan laporan/dividen untuk emiten ini",
                    "kosong": True,
                }

            return {
                "ok": True,
                "symbol": sim,
                "income_stmt": inc,
                "balance_sheet": bal,
                "cashflow": cf,
                "dividends": div,   # None = gagal, Series kosong = tidak ada dividen
                "galat_bagian": galat_bagian,
                "diambil_pada": stamp_wib(),
            }
        except Exception as e:  # noqa: BLE001
            last_err = f"{type(e).__name__}: {e}"
            if attempt < MAX_RETRY - 1:
                time.sleep(BASE_BACKOFF * (2 ** attempt))

    return {
        "ok": False,
        "symbol": sim,
        "error": f"yfinance gagal setelah {MAX_RETRY}x ({last_err})",
    }


# ───────────────────────── hitung metrik ─────────────────────────

def _hitung_dari_raw(
    kode: str,
    nama: str,
    sektor: str,
    raw: Dict[str, Any],
    harga: Optional[float],
    tanggal_harga: Optional[str],
    hari_ini: Optional[date] = None,
) -> Dict[str, Any]:
    """
    Hitung metrik dari raw yfinance. Dipisah dari fetch supaya bisa dites
    offline dengan DataFrame sintetis.
    """
    hari_ini = hari_ini or sekarang_wib().date()
    inc = raw.get("income_stmt")
    bal = raw.get("balance_sheet")
    cf = raw.get("cashflow")
    divs = raw.get("dividends")

    # ── data mentah per tahun fiskal ──
    laba, _ = _ekstrak_tahunan(inc, ["Net Income", "Net Income Common Stockholders"], hari_ini)
    pendapatan, _ = _ekstrak_tahunan(
        inc, ["Total Revenue", "Operating Revenue", "Revenue"], hari_ini
    )
    ekuitas, _ = _ekstrak_tahunan(
        bal,
        ["Stockholders Equity", "Common Stock Equity",
         "Total Equity Gross Minority Interest", "Total Equity"],
        hari_ini,
    )
    total_utang, _ = _ekstrak_tahunan(
        bal, ["Total Debt", "Long Term Debt And Capital Lease Obligation"], hari_ini
    )
    arus_kas, _ = _ekstrak_tahunan(
        cf, ["Operating Cash Flow", "Cash Flowsfromusedin Operating Activities Direct"], hari_ini
    )
    beban_bunga, _ = _ekstrak_tahunan(inc, ["Interest Expense"], hari_ini)
    pretax, _ = _ekstrak_tahunan(
        inc, ["Pretax Income", "Income Before Tax", "Earnings Before Tax"], hari_ini
    )
    ebit, _ = _ekstrak_tahunan(inc, ["EBIT"], hari_ini)
    eps, _ = _ekstrak_tahunan(inc, ["Diluted EPS", "Basic EPS"], hari_ini)
    saham, _ = _ekstrak_tahunan(
        bal, ["Ordinary Shares Number", "Share Issued", "Common Shares Outstanding"], hari_ini
    )

    # ── tahun fiskal yang dipakai (3–5 tahun terakhir) ──
    semua_tahun = set()
    for d in (laba, pendapatan, ekuitas, total_utang, arus_kas,
              beban_bunga, pretax, ebit, eps):
        semua_tahun.update(d.keys())
    tahun = sorted(semua_tahun, reverse=True)[:MAX_TAHUN]

    # ── periode laporan (tanggal fiskal asli) ──
    periode: Dict[int, str] = {}
    for df in (bal, cf, inc):          # income_stmt menang kalau bentrok
        for t, tgl in _peta_periode(df, hari_ini).items():
            periode[t] = tgl
    periode = {t: periode[t] for t in tahun if t in periode}

    # ── EPS fallback: laba bersih / jumlah saham ──
    for t in tahun:
        if eps.get(t) is None and laba.get(t) is not None and saham.get(t) not in (None, 0):
            eps[t] = laba[t] / saham[t]

    # ── dividen per tahun kalender (ex-date) ──
    catatan_dividen = divs is not None
    dps_kalender: Dict[int, float] = {}
    if divs is not None:
        try:
            for idx, val in divs.items():
                d = _tanggal_kolom(idx)
                v = _angka(val)
                if d is not None and v is not None:
                    dps_kalender[d.year] = dps_kalender.get(d.year, 0.0) + v
        except Exception:
            catatan_dividen = False

    dps_ttm = 0.0
    if divs is not None:
        batas_ttm = hari_ini - timedelta(days=365)
        try:
            for idx, val in divs.items():
                d = _tanggal_kolom(idx)
                v = _angka(val)
                if d is not None and d > batas_ttm and v is not None:
                    dps_ttm += v
        except Exception:
            catatan_dividen = False

    # ── ROE per tahun (ekuitas rata-rata awal-akhir kalau tersedia) ──
    roe_tahunan: Dict[int, Optional[float]] = {}
    for t in tahun:
        ni = laba.get(t)
        eq = ekuitas.get(t)
        if ni is None or eq is None or eq <= 0:
            continue
        eq_awal = ekuitas.get(t - 1)
        dasar = (eq + eq_awal) / 2.0 if eq_awal is not None and eq_awal > 0 else eq
        if dasar > 0:
            roe_tahunan[t] = ni / dasar

    # ── margin laba ──
    margin_tahunan: Dict[int, Optional[float]] = {}
    for t in tahun:
        ni = laba.get(t)
        rev = pendapatan.get(t)
        if ni is not None and rev is not None and rev > 0:
            margin_tahunan[t] = ni / rev

    # ── DER ──
    der_tahunan: Dict[int, Optional[float]] = {}
    for t in tahun:
        d = total_utang.get(t)
        e = ekuitas.get(t)
        if d is not None and e is not None and e > 0:
            der_tahunan[t] = d / e

    # ── interest coverage = EBIT / beban bunga ──
    ic_tahunan: Dict[int, Optional[float]] = {}
    for t in tahun:
        b = beban_bunga.get(t)
        if b is None or abs(b) < 1e-9:
            continue
        b = abs(b)
        if ebit.get(t) is not None:
            ic_tahunan[t] = ebit[t] / b
        elif pretax.get(t) is not None:
            ic_tahunan[t] = (pretax[t] + b) / b

    # ── OCF / laba ──
    ocf_laba_tahunan: Dict[int, Optional[float]] = {}
    for t in tahun:
        ni = laba.get(t)
        o = arus_kas.get(t)
        if ni is not None and ni > 0 and o is not None:
            ocf_laba_tahunan[t] = o / ni

    # ── payout ratio = DPS / EPS ──
    payout_tahunan: Dict[int, Optional[float]] = {}
    for t in tahun:
        e = eps.get(t)
        d = dps_kalender.get(t)
        if e is not None and e > 0:
            if d is None:
                d = 0.0       # tidak ada catatan dividen pada tahun itu = 0
            payout_tahunan[t] = d / e

    # ── rata-rata 3 tahun ──
    # Nilai berlabel "_3th" HANYA diisi kalau benar-benar ada 3 tahun valid.
    # Rata-rata parsial tetap disimpan di "_tersedia" supaya tidak hilang,
    # tetapi tidak dipakai sebagai "rata-rata 3 tahun".
    tiga = tahun[:3]
    roe_avg_raw, roe_n = _rata2(roe_tahunan.get(t) for t in tiga)
    margin_avg_raw, margin_n = _rata2(margin_tahunan.get(t) for t in tiga)
    der_avg_raw, der_n = _rata2(der_tahunan.get(t) for t in tiga)
    ic_avg_raw, ic_n = _rata2(ic_tahunan.get(t) for t in tiga)
    ocf_avg_raw, ocf_n = _rata2(ocf_laba_tahunan.get(t) for t in tiga)
    payout_avg_raw, payout_n = _rata2(payout_tahunan.get(t) for t in tiga)

    roe_avg = roe_avg_raw if roe_n >= 3 else None
    margin_avg = margin_avg_raw if margin_n >= 3 else None
    der_avg = der_avg_raw if der_n >= 3 else None
    ic_avg = ic_avg_raw if ic_n >= 3 else None
    ocf_avg = ocf_avg_raw if ocf_n >= 3 else None
    payout_avg = payout_avg_raw if payout_n >= 3 else None

    # ── CAGR 3 tahun: wajib tepat 3 tahun fiskal, bukan "jarak yang tersedia" ──
    def _cagr_3(d: Dict[int, Optional[float]]) -> Optional[float]:
        tersedia = [t for t in d if d.get(t) is not None]
        if not tersedia:
            return None
        t_akhir = max(tersedia)
        return _cagr(d.get(t_akhir - 3), d.get(t_akhir), 3)

    cagr_laba = _cagr_3(laba)
    cagr_pendapatan = _cagr_3(pendapatan)

    # ── konsistensi laba & dividen ──
    def _positif(d: Dict[int, Optional[float]], n: int) -> Tuple[Optional[int], int]:
        vals = [d.get(t) for t in tahun[:n]]
        valid = [v for v in vals if v is not None]
        if not valid:
            return None, 0
        return sum(1 for v in valid if v > 0), len(valid)

    laba_pos3, laba_pos3_n = _positif(laba, 3)
    laba_pos4, laba_pos4_n = _positif(laba, 4)
    laba_pos5, laba_pos5_n = _positif(laba, 5)
    # Jangan melaporkan "3 dari 4" kalau tahun ke-4 memang tidak ada.
    if laba_pos3_n < 3:
        laba_pos3 = None
    if laba_pos4_n < 4:
        laba_pos4 = None
    if laba_pos5_n < 5:
        laba_pos5 = None

    def _konsisten_dividen(n: int) -> Tuple[Optional[int], int]:
        if not catatan_dividen or len(tahun) < n:
            return None, len(tahun[:n])
        vals = [dps_kalender.get(t, 0.0) for t in tahun[:n]]
        return sum(1 for v in vals if v > 0), len(vals)

    div_3, div_3_n = _konsisten_dividen(3)
    div_5, div_5_n = _konsisten_dividen(5)

    # ── dividend yield (harga terakhir latest.json) ──
    harga_f = _angka(harga)
    yield_ttm = None
    yield_akhir = None
    if harga_f is not None and harga_f > 0:
        if catatan_dividen:
            yield_ttm = dps_ttm / harga_f
        t_akhir = tahun[0] if tahun else None
        if t_akhir is not None:
            yield_akhir = dps_kalender.get(t_akhir, 0.0) / harga_f

    # ── kumpulan metrik ──
    metrik: Dict[str, Any] = {
        "profitabilitas": {
            "roe_tahunan": roe_tahunan,
            "roe_rata2_3th": roe_avg,
            "roe_rata2_tersedia": roe_avg_raw,
            "roe_n_tahun": roe_n,
            "margin_laba_tahunan": margin_tahunan,
            "margin_laba_rata2_3th": margin_avg,
            "margin_laba_rata2_tersedia": margin_avg_raw,
            "margin_n_tahun": margin_n,
        },
        "pertumbuhan": {
            "cagr_laba_3th": cagr_laba,
            "cagr_pendapatan_3th": cagr_pendapatan,
        },
        "kesehatan_keuangan": {
            "der_tahunan": der_tahunan,
            "der_rata2_3th": der_avg,
            "der_rata2_tersedia": der_avg_raw,
            "der_n_tahun": der_n,
            "interest_coverage_tahunan": ic_tahunan,
            "interest_coverage_rata2_3th": ic_avg,
            "interest_coverage_rata2_tersedia": ic_avg_raw,
            "interest_coverage_n_tahun": ic_n,
            "ocf_laba_tahunan": ocf_laba_tahunan,
            "ocf_laba_rata2_3th": ocf_avg,
            "ocf_laba_rata2_tersedia": ocf_avg_raw,
            "ocf_laba_n_tahun": ocf_n,
        },
        "dividen": {
            "dividen_per_saham_tahunan": {
                str(y): v
                for y, v in sorted(dps_kalender.items(), reverse=True)[:DIVIDEN_TAHUN_DISIMPAN]
            },
            "dividen_ttm": dps_ttm if catatan_dividen else None,
            "dividend_yield_ttm": yield_ttm,
            "dividend_yield_tahun_terakhir": yield_akhir,
            "payout_ratio_tahunan": payout_tahunan,
            "payout_ratio_rata2_3th": payout_avg,
            "payout_ratio_rata2_tersedia": payout_avg_raw,
            "payout_n_tahun": payout_n,
            "konsistensi_dividen_3th": div_3,
            "konsistensi_dividen_5th": div_5,
            "konsistensi_dividen_3th_n": div_3_n,
            "konsistensi_dividen_5th_n": div_5_n,
            "ada_catatan_dividen": catatan_dividen,
        },
        "konsistensi": {
            "laba_positif_3th": laba_pos3,
            "laba_positif_4th": laba_pos4,
            "laba_positif_5th": laba_pos5,
            "laba_positif_3th_n": laba_pos3_n,
            "laba_positif_4th_n": laba_pos4_n,
            "laba_positif_5th_n": laba_pos5_n,
        },
    }

    # ── daftar metrik yang tidak tersedia + alasan ──
    tidak_tersedia: List[str] = []
    catatan_metrik: Dict[str, str] = {}
    for kat, field in _METRIK_KUNCI:
        nilai = metrik.get(kat, {}).get(field)
        if nilai is None:
            path = f"{kat}.{field}"
            tidak_tersedia.append(path)
            if field.startswith("cagr_"):
                catatan_metrik[path] = "butuh dua tahun fiskal berjarak 3 tahun dengan nilai positif"
            elif field.startswith("interest_coverage"):
                catatan_metrik[path] = "beban bunga tidak tersedia/EBIT tidak tersedia"
            elif field.startswith("dividend_yield"):
                catatan_metrik[path] = "harga acuan atau catatan dividen tidak tersedia"
            elif field.startswith("payout"):
                catatan_metrik[path] = "EPS tidak positif/tidak tersedia"
            elif field.startswith("ocf_laba"):
                catatan_metrik[path] = "laba tidak positif/arus kas operasi tidak tersedia"
            elif field.startswith("der"):
                catatan_metrik[path] = "ekuitas tidak positif/total utang tidak tersedia"
            elif field.startswith("roe") or field.startswith("margin"):
                catatan_metrik[path] = "laporan laba/ekuitas/pendapatan tidak lengkap"
            else:
                catatan_metrik[path] = "data tidak tersedia di sumber"
    metrik["tidak_tersedia"] = tidak_tersedia
    metrik["catatan_metrik"] = catatan_metrik

    # ── data mentah (maks 5 tahun fiskal) ──
    def _peta_lima(d: Dict[int, Optional[float]]) -> Dict[str, Optional[float]]:
        return {str(t): d.get(t) for t in tahun}

    data_mentah = {
        "laba_bersih": _peta_lima(laba),
        "pendapatan": _peta_lima(pendapatan),
        "ekuitas": _peta_lima(ekuitas),
        "total_utang": _peta_lima(total_utang),
        "arus_kas_operasi": _peta_lima(arus_kas),
        "beban_bunga": _peta_lima(beban_bunga),
        "pretax_income": _peta_lima(pretax),
        "ebit": _peta_lima(ebit),
        "eps": _peta_lima(eps),
        "jumlah_saham": _peta_lima(saham),
        "dividen_per_saham": {
            str(y): v for y, v in sorted(dps_kalender.items(), reverse=True)[:DIVIDEN_TAHUN_DISIMPAN]
        },
        "dividen_ttm": dps_ttm if catatan_dividen else None,
        "harga_acuan": harga_f,
        "tanggal_harga_acuan": tanggal_harga,
    }

    # ── cakupan ──
    tahun_ada = [
        t for t in tahun
        if any(d.get(t) is not None for d in
               (laba, pendapatan, ekuitas, total_utang, arus_kas))
    ]
    tahun_lengkap5 = [
        t for t in tahun
        if all((d.get(t) is not None) for d in
               (laba, pendapatan, ekuitas, total_utang, arus_kas))
    ]
    tahun_lengkap6 = [
        t for t in tahun
        if all((d.get(t) is not None) for d in
               (laba, pendapatan, ekuitas, total_utang, arus_kas, beban_bunga))
    ]

    detail_metrik = {}
    for kat, field in _METRIK_KUNCI:
        detail_metrik[f"{kat}.{field}"] = metrik.get(kat, {}).get(field) is not None
    tersedia_n = sum(1 for v in detail_metrik.values() if v)
    cakupan_persen = round(tersedia_n / len(_METRIK_KUNCI) * 100, 1)

    if not tahun_ada:
        status_cakupan = "kosong"
    elif len(tahun_lengkap5) < 3:
        status_cakupan = "kurang"
    elif len(tahun_lengkap6) >= 3 and cakupan_persen >= 70:
        status_cakupan = "lengkap"
    else:
        status_cakupan = "sebagian"

    cakupan = {
        "persen_metrik": cakupan_persen,
        "metrik_tersedia": tersedia_n,
        "metrik_total": len(_METRIK_KUNCI),
        "status": status_cakupan,
        "tahun_ada_data": tahun_ada,
        "tahun_lengkap_5_field": tahun_lengkap5,
        "tahun_lengkap_6_field": tahun_lengkap6,
        "jumlah_tahun_ada_data": len(tahun_ada),
        "jumlah_tahun_lengkap_5_field": len(tahun_lengkap5),
        "jumlah_tahun_lengkap_6_field": len(tahun_lengkap6),
        "detail_metrik": detail_metrik,
    }

    if tahun:
        ketersediaan = (
            "laporan_keuangan" if len(tahun_lengkap5) >= 3 else "laporan_sebagian"
        )
        alasan_data = None
    elif dps_kalender:
        ketersediaan = "dividen_saja"
        alasan_data = "laporan keuangan tidak tersedia; hanya catatan dividen"
    else:
        ketersediaan = "tidak_ada"
        alasan_data = "laporan keuangan dan dividen tidak tersedia"

    return {
        "kode": kode,
        "nama": nama,
        "sektor": sektor,
        "status": "ok",
        "status_data": status_cakupan,
        "ketersediaan": ketersediaan,
        "alasan": alasan_data,
        "sumber": "yfinance",
        "sumber_dividen": "yfinance",
        "galat_bagian": raw.get("galat_bagian") or [],
        "diambil_pada": raw.get("diambil_pada") or stamp_wib(),
        "periode_laporan": {str(t): periode[t] for t in sorted(periode, reverse=True)},
        "tahun_keuangan": tahun,
        "jumlah_tahun_tersedia": len(tahun),
        "metrik": metrik,
        "data_mentah": data_mentah,
        "cakupan": cakupan,
    }


def _payload_gagal(kode: str, nama: str, sektor: str, alasan: str) -> Dict[str, Any]:
    return {
        "kode": kode,
        "nama": nama,
        "sektor": sektor,
        "status": "gagal",
        "status_data": "kosong",
        "ketersediaan": "tidak_ada",
        "alasan": alasan,
        "sumber": None,
        "sumber_dividen": None,
        "diambil_pada": stamp_wib(),
        "periode_laporan": {},
        "tahun_keuangan": [],
        "metrik": {
            "tidak_tersedia": [f"{k}.{f}" for k, f in _METRIK_KUNCI],
            "catatan_metrik": {"*": "pengambilan data gagal; tidak ada nilai yang ditebak"},
        },
        "data_mentah": {},
        "cakupan": {
            "persen_metrik": 0.0,
            "metrik_tersedia": 0,
            "metrik_total": len(_METRIK_KUNCI),
            "status": "kosong",
            "tahun_ada_data": [],
            "tahun_lengkap_5_field": [],
            "tahun_lengkap_6_field": [],
            "jumlah_tahun_ada_data": 0,
            "jumlah_tahun_lengkap_5_field": 0,
            "jumlah_tahun_lengkap_6_field": 0,
            "detail_metrik": {f"{k}.{f}": False for k, f in _METRIK_KUNCI},
        },
    }


def _bangun_payload_emiten(
    em: Dict[str, str],
    harga: Optional[float],
    tanggal_harga: Optional[str],
) -> Dict[str, Any]:
    kode = em["kode"]
    nama = em.get("nama", "")
    sektor = em.get("sektor", "")
    raw = _ambil_satu(kode)
    if not raw.get("ok"):
        return _payload_gagal(kode, nama, sektor, raw.get("error", "tidak diketahui"))
    return _hitung_dari_raw(kode, nama, sektor, raw, harga, tanggal_harga)


# ───────────────────────── ringkasan ─────────────────────────

def _ringkas(hasil_emiten: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    total = len(hasil_emiten)
    status_count = {"lengkap": 0, "sebagian": 0, "kurang": 0, "kosong": 0, "gagal": 0}
    ketersediaan_count = {
        "laporan_keuangan": 0, "laporan_sebagian": 0,
        "dividen_saja": 0, "tidak_ada": 0,
    }
    distribusi_5 = {str(i): 0 for i in range(MAX_TAHUN + 1)}
    per_metrik: Dict[str, int] = {f"{k}.{f}": 0 for k, f in _METRIK_KUNCI}
    per_raw: Dict[str, int] = {f: 0 for f in _FIELD_INTI_6}
    per_sektor: Dict[str, Dict[str, int]] = {}

    for p in hasil_emiten.values():
        status = p.get("status_data") or "kosong"
        if status not in status_count:
            status = "kosong"
        status_count[status] += 1
        if p.get("status") == "gagal":
            status_count["gagal"] += 1

        ket = p.get("ketersediaan") or "tidak_ada"
        if ket not in ketersediaan_count:
            ket = "tidak_ada"
        ketersediaan_count[ket] += 1

        n5 = int((p.get("cakupan") or {}).get("jumlah_tahun_lengkap_5_field") or 0)
        distribusi_5[str(min(n5, MAX_TAHUN))] += 1

        detail = (p.get("cakupan") or {}).get("detail_metrik") or {}
        for key in per_metrik:
            if not detail.get(key):
                per_metrik[key] += 1

        dm = p.get("data_mentah") or {}
        for field in per_raw:
            vals = dm.get(field) or {}
            if not any(v is not None for v in vals.values()):
                per_raw[field] += 1

        sektor = p.get("sektor") or "(tanpa sektor)"
        s = per_sektor.setdefault(sektor, {
            "total": 0, "lengkap": 0, "sebagian": 0, "kurang": 0,
            "kosong": 0, "punya_3th_5_field": 0,
        })
        s["total"] += 1
        s[status] += 1
        if n5 >= 3:
            s["punya_3th_5_field"] += 1

    per_metrik = dict(sorted(per_metrik.items(), key=lambda x: (-x[1], x[0])))
    per_raw = dict(sorted(per_raw.items(), key=lambda x: (-x[1], x[0])))

    return {
        "total_emiten": total,
        "status_cakupan": status_count,
        "ketersediaan": ketersediaan_count,
        # Kompatibilitas dengan skema lama: empat kategori datar.
        "lengkap": status_count["lengkap"],
        "sebagian": status_count["sebagian"],
        "kurang": status_count["kurang"],
        "kosong": status_count["kosong"],
        "gagal": status_count["gagal"],
        "punya_3th_lengkap": status_count["lengkap"],
        "punya_3th_6_field": sum(
            1 for p in hasil_emiten.values()
            if int((p.get("cakupan") or {}).get("jumlah_tahun_lengkap_6_field") or 0) >= 3
        ),
        "punya_3th_lengkap_5_field": sum(
            1 for p in hasil_emiten.values()
            if int((p.get("cakupan") or {}).get("jumlah_tahun_lengkap_5_field") or 0) >= 3
        ),
        "punya_4th_lengkap_5_field": sum(
            1 for p in hasil_emiten.values()
            if int((p.get("cakupan") or {}).get("jumlah_tahun_lengkap_5_field") or 0) >= 4
        ),
        "punya_5th_lengkap_5_field": sum(
            1 for p in hasil_emiten.values()
            if int((p.get("cakupan") or {}).get("jumlah_tahun_lengkap_5_field") or 0) >= 5
        ),
        "distribusi_tahun_lengkap_5_field": distribusi_5,
        "per_metrik_bolong": per_metrik,
        "per_data_mentah_bolong": per_raw,
        "per_sektor": dict(sorted(per_sektor.items())),
    }


# ───────────────────────── orkestrasi ─────────────────────────

def _muat_latest() -> Dict[str, Any]:
    with open(PATH_LATEST, "r", encoding="utf-8") as f:
        return json.load(f)


def jalankan(
    emiten_list: Optional[List[Dict[str, str]]] = None,
    tulis: bool = True,
    workers: int = DEFAULT_WORKERS,
    batas: Optional[int] = None,
) -> Dict[str, Any]:
    """
    Kumpulkan data fundamental historis untuk semua emiten.

    `emiten_list` None -> ambil dari latest.json.
    `batas` hanya untuk uji cepat; default None (semua).
    """
    mulai = time.time()
    latest = _muat_latest()

    if emiten_list is None:
        emiten_list = [
            {"kode": e["kode"], "nama": e.get("nama", ""), "sektor": e.get("sektor", "")}
            for e in latest.get("emiten", [])
        ]
    if batas is not None:
        emiten_list = emiten_list[:batas]

    harga_map = {
        e.get("kode"): e.get("harga")
        for e in latest.get("emiten", [])
        if e.get("kode")
    }
    tanggal_harga = latest.get("tanggal_data") or latest.get("diperbarui_pada")

    print(f"[fundamental_historical] Memproses {len(emiten_list)} emiten "
          f"({workers} worker)...")

    hasil_emiten: Dict[str, Dict[str, Any]] = {}
    selesai = 0
    with ThreadPoolExecutor(max_workers=max(1, workers)) as ex:
        futures = {
            ex.submit(
                _bangun_payload_emiten,
                em,
                _angka(harga_map.get(em["kode"])),
                tanggal_harga,
            ): em["kode"]
            for em in emiten_list
        }
        for fut in as_completed(futures):
            kode = futures[fut]
            try:
                payload = fut.result()
            except Exception as e:  # noqa: BLE001
                em = next(x for x in emiten_list if x["kode"] == kode)
                payload = _payload_gagal(
                    kode, em.get("nama", ""), em.get("sektor", ""),
                    f"{type(e).__name__}: {e}",
                )
            hasil_emiten[kode] = payload
            selesai += 1
            if selesai % 25 == 0 or selesai == len(emiten_list):
                print(f"  [{selesai}/{len(emiten_list)}] terakhir: {kode}")

    # Pertahankan urutan sesuai latest.json
    urut = {em["kode"]: i for i, em in enumerate(emiten_list)}
    hasil_emiten = dict(sorted(hasil_emiten.items(), key=lambda kv: urut.get(kv[0], 10**9)))

    ringkasan = _ringkas(hasil_emiten)
    payload: Dict[str, Any] = {
        "versi": VERSI_FUNDAMENTAL_HISTORIS,
        "diperbarui_pada": stamp_wib(),
        "tanggal_data_utama": latest.get("tanggal_data"),
        "sumber": "yfinance",
        "sectors": {
            "aktif": False,
            "alasan": "SECTORS_API_KEY tidak diset; data historis memakai yfinance",
        },
        "jumlah_emiten": len(hasil_emiten),
        "emiten": hasil_emiten,
        "ringkasan_cakupan": ringkasan,
        "metrik_bolong": ringkasan["per_metrik_bolong"],
        "satuan": {
            "roe_rata2_3th": "fraksi_0_1",
            "margin_laba_rata2_3th": "fraksi_0_1",
            "cagr_laba_3th": "fraksi_0_1",
            "cagr_pendapatan_3th": "fraksi_0_1",
            "der_rata2_3th": "rasio_x",
            "interest_coverage_rata2_3th": "rasio_x",
            "ocf_laba_rata2_3th": "rasio_x",
            "dividend_yield_ttm": "fraksi_0_1",
            "payout_ratio_rata2_3th": "fraksi_0_1",
            "konsistensi_dividen_3th": "jumlah_tahun",
            "laba_positif_4th": "jumlah_tahun",
            "dividen_ttm": "IDR_per_lembar",
        },
        "metode": {
            "periode": f"maks {MAX_TAHUN} tahun fiskal terakhir yang tersedia",
            "roe": "laba bersih / rata-rata ekuitas awal-akhir; fallback ekuitas akhir",
            "cagr": "tepat 3 tahun fiskal (t-3 ke t), hanya bila kedua nilai positif",
            "interest_coverage": "EBIT / beban bunga; fallback (pretax + beban bunga) / beban bunga",
            "dividend_yield": "dividen 12 bulan terakhir (ex-date) / harga terakhir latest.json",
            "payout": "DPS tahun kalender / EPS tahun fiskal; DPS tanpa catatan = 0",
            "dividen": "dikelompokkan per tahun kalender ex-date, bukan tahun fiskal",
        },
        "catatan": (
            "Nilai yang tidak tersedia disimpan null dan didaftarkan di "
            "metrik.tidak_tersedia; tidak diisi 0 dan tidak ditebak. "
            "Dividen memakai tahun kalender ex-date; laporan keuangan memakai "
            "tahun fiskal. Dividend yield memakai harga terakhir latest.json."
        ),
    }

    if tulis:
        _tulis_atomic(PATH_FUNDAMENTAL, payload)
        print(f"[fundamental_historical] Tersimpan ke {PATH_FUNDAMENTAL}")

    durasi = round(time.time() - mulai, 1)
    payload["ok"] = True
    payload["durasi_detik"] = durasi
    print(f"[fundamental_historical] Selesai {len(hasil_emiten)} emiten "
          f"dalam {durasi:.1f} detik")
    print(f"  Lengkap (>=3 th, 6 field): {ringkasan['status_cakupan']['lengkap']}")
    print(f"  Punya >=3 th (5 field):     {ringkasan['punya_3th_lengkap_5_field']}")
    print(f"  Sebagian:                   {ringkasan['status_cakupan']['sebagian']}")
    print(f"  Kurang:                     {ringkasan['status_cakupan']['kurang']}")
    print(f"  Kosong/gagal:               {ringkasan['status_cakupan']['kosong']} "
          f"(gagal ambil: {ringkasan['status_cakupan']['gagal']})")
    if ringkasan["per_metrik_bolong"]:
        print("  Metrik paling sering bolong:")
        for k, v in list(ringkasan["per_metrik_bolong"].items())[:5]:
            print(f"    {k}: {v}")
    return payload


def _pilih_emiten(kode_csv: str) -> List[Dict[str, str]]:
    latest = _muat_latest()
    semua = {e["kode"]: e for e in latest.get("emiten", [])}
    out = []
    for k in [x.strip().upper() for x in kode_csv.split(",") if x.strip()]:
        e = semua.get(k)
        if not e:
            raise SystemExit(f"Kode tidak ditemukan di latest.json: {k}")
        out.append({"kode": e["kode"], "nama": e.get("nama", ""), "sektor": e.get("sektor", "")})
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="J1 — data fundamental historis IDX")
    ap.add_argument("--kode", help="daftar kode dipisah koma, mis. BBCA,TLKM")
    ap.add_argument("--batas", type=int, default=None, help="batasi jumlah emiten (uji)")
    ap.add_argument("--workers", type=int, default=DEFAULT_WORKERS)
    ap.add_argument("--tanpa-tulis", action="store_true", help="jangan tulis data/fundamental.json")
    args = ap.parse_args()

    emiten = _pilih_emiten(args.kode) if args.kode else None
    res = jalankan(
        emiten_list=emiten,
        tulis=not args.tanpa_tulis,
        workers=args.workers,
        batas=args.batas,
    )
    if not res.get("ok"):
        print(f"GAGAL: {res.get('error')}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
