#!/usr/bin/env python3
"""
Pengambil data pasar tambahan untuk Skor Investasi Jangka Panjang (J2).

Dua kelompok data:
  1. Riwayat harga 5 tahun per emiten
     - bulanan: close MENTAH, dipakai menghitung PER/PBV historis J2
     - harian 260 sesi terakhir: Adj Close, dipakai MA200 / puncak 52 minggu
     - split: riwayat split, dipakai indikator diskon/overprice agar
       PER/PBV historis tidak terdistorsi split
  2. Info ringkas yfinance: mata uang laporan, market cap, saham beredar,
     free float, EPS/BOOK value terkini, total utang, DER.

Kenapa dipisah dari J1?
  J1 hanya mengambil laporan keuangan. Untuk J2 kita butuh harga historis,
  kurs, dan mata uang laporan. Beberapa emiten (ADRO, ITMG, ...) melaporkan
  EPS dalam USD, sedangkan harga dalam IDR. Tanpa `financialCurrency` dan
  kurs, payout/PER historis akan salah ribuan kali.

Prinsip:
  * Tidak menebak. Gagal ambil -> tidak ada di `emiten`, dicatat di `gagal`.
  * Tidak menyimpan API key / cookie.
  * File ditulis atomik.

Jalankan:
    python3 idx/pasar_jp.py --batas 20 --tanpa-tulis
    python3 idx/pasar_jp.py --workers 8
    python3 idx/pasar_jp.py --kode BBCA,ADRO,ITMG
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

_ROOT = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

PATH_FUNDAMENTAL = os.path.join(_ROOT, "data", "fundamental.json")
PATH_HARGA = os.path.join(_ROOT, "data", "harga_jp.json")
PATH_INFO = os.path.join(_ROOT, "data", "info_jp.json")

WIB = timezone(timedelta(hours=7))
VERSI = "1.1.0"
DEFAULT_WORKERS = int(os.environ.get("IDX_JP_WORKERS", "8"))
MAX_RETRY = 2
BASE_BACKOFF = 1.2
SESI_HARIAN = 260

FIELDS_INFO = [
    "financialCurrency", "currency", "exchangeCurrency",
    "marketCap", "sharesOutstanding", "floatShares",
    "trailingEps", "bookValue", "totalDebt", "debtToEquity",
    "sector", "industry",
]


def sekarang_wib() -> datetime:
    return datetime.now(WIB)


def stamp_wib(dt: Optional[datetime] = None) -> str:
    return (dt or sekarang_wib()).replace(microsecond=0).isoformat()


def _angka(v: Any) -> Optional[float]:
    if v is None or isinstance(v, bool):
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(f):
        return None
    return f


def _baca_json(path: str) -> Dict[str, Any]:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _tulis_atomic(path: str, payload: Any) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = f"{path}.tmp.{os.getpid()}"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, separators=(",", ":"))
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def _simbol(kode: str) -> str:
    k = str(kode).strip().upper()
    return k if k.endswith(".JK") else f"{k}.JK"


def _daftar_emiten() -> List[str]:
    """Emiten yang punya minimal satu tahun laporan keuangan.

    Emiten dengan riwayat dividen saja tidak akan pernah lolos J2 (tidak ada
    laba/ekuitas), jadi tidak perlu request harga/info.
    """
    data = _baca_json(PATH_FUNDAMENTAL)
    hasil = []
    for k, p in (data.get("emiten") or {}).items():
        n_tahun = (p.get("cakupan") or {}).get("jumlah_tahun_ada_data") or 0
        if isinstance(n_tahun, (int, float)) and n_tahun >= 1:
            hasil.append(k)
    return sorted(set(hasil))


def _resample_bulanan(seri: Any) -> Tuple[List[str], List[float]]:
    """Seri harian pandas -> month-end (tanggal, close)."""
    try:
        m = seri.resample("ME").last().dropna()
    except ValueError:
        # pandas lama memakai alias "M"
        m = seri.resample("M").last().dropna()
    d, c = [], []
    for idx, val in m.items():
        v = _angka(val)
        if v is None or v <= 0:
            continue
        try:
            d.append(idx.date().isoformat())
        except Exception:
            d.append(str(idx)[:10])
        c.append(v)
    return d, c


def _ambil_satu(kode: str) -> Dict[str, Any]:
    import yfinance as yf

    sym = _simbol(kode)
    t = yf.Ticker(sym)
    out: Dict[str, Any] = {"kode": kode, "simbol": sym}
    galat = []

    # ── harga ────────────────────────────────────────────────────────
    try:
        h = t.history(period="5y", interval="1d", auto_adjust=False,
                      actions=True)
        if h is None or getattr(h, "empty", True):
            galat.append("harga kosong")
        else:
            close = h["Close"].dropna()
            adj = h["Adj Close"].dropna() if "Adj Close" in h.columns else close
            bd, bc = _resample_bulanan(close)
            hd, hc = [], []
            for idx, val in adj.tail(SESI_HARIAN).items():
                v = _angka(val)
                if v is None or v <= 0:
                    continue
                try:
                    hd.append(idx.date().isoformat())
                except Exception:
                    hd.append(str(idx)[:10])
                hc.append(v)
            if bc:
                out["bulanan"] = {"d": bd, "c": bc}
            if hc:
                out["harian"] = {"d": hd, "c": hc}
            if not bc and not hc:
                galat.append("harga tidak valid")
            # Riwayat split untuk penyesuaian PER/PBV historis indikator.
            # J2 tetap memakai harga bulanan mentah.
            try:
                if "Stock Splits" in h.columns:
                    sp = h["Stock Splits"].dropna()
                    events = []
                    for idx, val in sp.items():
                        rasio = _angka(val)
                        if rasio is None or rasio <= 0:
                            continue
                        try:
                            tanggal = idx.date().isoformat()
                        except Exception:
                            tanggal = str(idx)[:10]
                        events.append({"tanggal": tanggal, "rasio": rasio})
                    if events:
                        out["split"] = sorted(events, key=lambda x: x["tanggal"])
            except Exception as e:  # noqa: BLE001
                galat.append(f"split: {type(e).__name__}: {e}")
    except Exception as e:  # noqa: BLE001
        galat.append(f"harga: {type(e).__name__}: {e}")

    # ── info ────────────────────────────────────────────────────────
    info: Dict[str, Any] = {}
    try:
        raw = t.info or {}
        for f in FIELDS_INFO:
            v = raw.get(f)
            if f in ("financialCurrency", "currency", "exchangeCurrency",
                     "sector", "industry"):
                info[f] = str(v).strip() if v else None
            else:
                info[f] = _angka(v)
        out["info"] = info
    except Exception as e:  # noqa: BLE001
        galat.append(f"info: {type(e).__name__}: {e}")

    if galat:
        out["galat"] = galat
    return out


def _ambil_retry(kode: str) -> Dict[str, Any]:
    last: Optional[Dict[str, Any]] = None
    for i in range(MAX_RETRY + 1):
        try:
            r = _ambil_satu(kode)
            if r.get("bulanan") or r.get("info"):
                return r
            last = r
        except Exception as e:  # noqa: BLE001
            last = {"kode": kode, "galat": [f"{type(e).__name__}: {e}"]}
        if i < MAX_RETRY:
            time.sleep(BASE_BACKOFF * (i + 1))
    return last or {"kode": kode, "galat": ["gagal tanpa hasil"]}


def _ambil_kurs() -> Dict[str, Any]:
    """USD/IDR bulanan 5 tahun. Kosong kalau gagal (bukan diisi 1)."""
    try:
        import yfinance as yf
        h = yf.Ticker("USDIDR=X").history(period="5y", interval="1d",
                                           auto_adjust=False, actions=False)
        if h is None or getattr(h, "empty", True):
            return {"d": [], "c": [], "status": "data tidak tersedia"}
        d, c = _resample_bulanan(h["Close"].dropna())
        return {"d": d, "c": c, "status": "ok" if c else "data tidak tersedia"}
    except Exception as e:  # noqa: BLE001
        return {"d": [], "c": [], "status": f"gagal: {type(e).__name__}: {e}"}


def jalankan(kode: Optional[List[str]] = None, batas: int = 0,
             workers: int = DEFAULT_WORKERS,
             tulis: bool = True) -> Dict[str, Any]:
    daftar = kode or _daftar_emiten()
    if batas and batas > 0:
        daftar = daftar[:batas]
    daftar = [k.strip().upper() for k in daftar if k and k.strip()]
    print(f"[pasar_jp] {len(daftar)} emiten | workers {workers}", flush=True)

    hasil: Dict[str, Dict[str, Any]] = {}
    gagal: Dict[str, Any] = {}
    t0 = time.time()
    with ThreadPoolExecutor(max_workers=max(1, workers)) as ex:
        futs = {ex.submit(_ambil_retry, k): k for k in daftar}
        selesai = 0
        for fut in as_completed(futs):
            k = futs[fut]
            selesai += 1
            try:
                r = fut.result()
            except Exception as e:  # noqa: BLE001
                r = {"kode": k, "galat": [f"{type(e).__name__}: {e}"]}
            if r.get("bulanan") or r.get("harian") or r.get("info"):
                hasil[k] = r
            else:
                gagal[k] = r.get("galat") or ["kosong"]
            if selesai % 25 == 0 or selesai == len(daftar):
                print(f"  [{selesai}/{len(daftar)}] ok {len(hasil)} "
                      f"gagal {len(gagal)} | {time.time()-t0:.0f}s",
                      flush=True)

    kurs = _ambil_kurs()
    stamp = stamp_wib()
    harga_payload = {
        "versi": VERSI,
        "diperbarui_pada": stamp,
        "sumber": "yfinance",
        "periode": "5y",
        "interval": "1d",
        "penyesuaian": {
            "bulanan": "Close mentah (untuk PER/PBV historis J2)",
            "harian": "Adj Close (split & dividen, untuk MA200/52 minggu)",
            "split": "riwayat split yfinance (untuk indikator diskon/overprice)",
        },
        "kurs_usd_idr": kurs,
        "jumlah_emiten": len(hasil),
        "emiten": {
            k: {x: r[x] for x in ("bulanan", "harian", "split") if x in r}
            for k, r in sorted(hasil.items())
        },
        "gagal": dict(sorted(gagal.items())),
    }
    info_payload = {
        "versi": VERSI,
        "diperbarui_pada": stamp,
        "sumber": "yfinance",
        "jumlah_emiten": sum(1 for r in hasil.values() if r.get("info")),
        "emiten": {k: r["info"] for k, r in sorted(hasil.items())
                   if r.get("info")},
        "gagal": dict(sorted(gagal.items())),
    }

    if tulis:
        _tulis_atomic(PATH_HARGA, harga_payload)
        _tulis_atomic(PATH_INFO, info_payload)
        print(f"[pasar_jp] ditulis: {PATH_HARGA} "
              f"({len(harga_payload['emiten'])} emiten), {PATH_INFO}",
              flush=True)
    return {
        "ok": bool(hasil),
        "jumlah": len(daftar),
        "berhasil": len(hasil),
        "gagal": len(gagal),
        "harga": PATH_HARGA,
        "info": PATH_INFO,
        "kurs_status": kurs.get("status"),
        "contoh_gagal": dict(list(sorted(gagal.items()))[:5]),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--kode", default="", help="daftar kode dipisah koma")
    ap.add_argument("--batas", type=int, default=0)
    ap.add_argument("--workers", type=int, default=DEFAULT_WORKERS)
    ap.add_argument("--tanpa-tulis", action="store_true")
    args = ap.parse_args()
    daftar = [x for x in args.kode.split(",") if x.strip()] or None
    res = jalankan(kode=daftar, batas=args.batas, workers=args.workers,
                   tulis=not args.tanpa_tulis)
    print(json.dumps(res, ensure_ascii=False, indent=1))
    return 0 if res.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
