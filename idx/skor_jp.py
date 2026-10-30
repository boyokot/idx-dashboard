#!/usr/bin/env python3
"""
J2 — Skor Investasi Jangka Panjang (0-100) untuk dashboard IDX.

Skor ini TERPISAH dari skor momentum/trading yang sudah ada. Pipeline lama
(`skor`, `skor_detail`, `top10`) tidak diubah. Modul ini menambahkan:
  * gerbang likuiditas, kapitalisasi, laba, ekuitas, waspada, cakupan data;
  * enam pilar: kualitas profitabilitas 25%, pertumbuhan 20%,
    kesehatan keuangan 20%, valuasi 20%, dividen 10%,
    risiko/tata kelola 5%;
  * persentil DALAM SEKTOR (sektor <5 emiten -> persentil gabungan);
  * penanganan bank/asuransi/lembaga keuangan (tanpa DER/interest coverage);
  * valuasi relatif sektor + rata-rata 3-5 tahun sendiri;
  * timing sebagai informasi (tidak masuk skor);
  * penanda risiko sebagai informasi (tidak mengurangi skor).

Prinsip:
  * Tidak menebak. Data hilang = null + alasan.
  * Mata uang laporan dinormalisasi. Beberapa emiten (ADRO, ITMG, ...)
    melaporkan EPS dalam USD; harga dalam IDR. Tanpa kurs, payout/PER
    historis salah ribuan kali.
  * Payout >200% dianggap tidak wajar, bukan sinyal bagus.
  * PER sangat rendah akibat laba sekali-jadi dibatasi skornya.

Jalankan:
    python3 idx/skor_jp.py --tanpa-tulis
    python3 idx/skor_jp.py
    python3 idx/skor_jp.py --top 20
"""

from __future__ import annotations

import argparse
import bisect
import json
import math
import os
import statistics
import sys
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

PATH_LATEST = os.path.join(_ROOT, "data", "latest.json")
PATH_FUNDAMENTAL = os.path.join(_ROOT, "data", "fundamental.json")
PATH_HISTORY = os.path.join(_ROOT, "data", "history.json")
PATH_HARGA = os.path.join(_ROOT, "data", "harga_jp.json")
PATH_INFO = os.path.join(_ROOT, "data", "info_jp.json")
PATH_BOBOT = os.path.join(_ROOT, "config", "bobot_jp.json")
PATH_SENS = os.path.join(_ROOT, "config", "sensitivitas_sektor.json")
PATH_RINGKASAN = os.path.join(_ROOT, "data", "skor_jp_ringkasan.json")

WIB = timezone(timedelta(hours=7))
VERSI = "1.0.0"
VERSI_SKEMA_BARU = "1.6.0"
TIDAK_ADA = "data tidak tersedia"
PILAR_DATA = [
    "kualitas_profitabilitas", "pertumbuhan", "kesehatan_keuangan",
    "valuasi", "dividen",
]
PILAR_SEMUA = PILAR_DATA + ["risiko_tata_kelola"]


def sekarang_wib() -> datetime:
    return datetime.now(WIB)


def stamp_wib(dt: Optional[datetime] = None) -> str:
    return (dt or sekarang_wib()).replace(microsecond=0).isoformat()


def _angka(v: Any) -> Optional[float]:
    """Float finite; None/''/bool/NaN/inf -> None (BUKAN 0)."""
    if v is None or isinstance(v, bool):
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(f):
        return None
    return f


def _bulat(v: Optional[float], n: int = 4) -> Optional[float]:
    return None if v is None else round(float(v), n)


def _median(vals: Iterable[Optional[float]]) -> Optional[float]:
    x = [v for v in vals if v is not None and math.isfinite(v)]
    return statistics.median(x) if x else None


def _rp(v: Optional[float]) -> str:
    if v is None:
        return TIDAK_ADA
    if abs(v) >= 1e12:
        return f"Rp{v/1e12:.2f} T"
    if abs(v) >= 1e9:
        return f"Rp{v/1e9:.1f} miliar"
    if abs(v) >= 1e6:
        return f"Rp{v/1e6:.0f} juta"
    return f"Rp{v:.0f}"


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


def _ambil(d: Any, path: str) -> Any:
    cur = d
    for p in path.split("."):
        if not isinstance(cur, dict):
            return None
        cur = cur.get(p)
    return cur


def _persentil(nilai: Optional[float], peers: Sequence[Optional[float]],
               arah: str = "tinggi") -> Optional[float]:
    """Persentil 0-100 berbasis rank, aman untuk outlier.

    arah='tinggi': nilai tinggi -> persentil tinggi.
    arah='rendah': nilai rendah -> persentil tinggi.
    """
    if nilai is None or not math.isfinite(nilai):
        return None
    vals = [x for x in peers if x is not None and math.isfinite(x)]
    if not vals:
        return None
    n = len(vals)
    if n == 1:
        return 50.0
    bawah = sum(1 for x in vals if x < nilai)
    sama = sum(1 for x in vals if x == nilai)
    high = (bawah + 0.5 * sama) / n * 100.0
    out = high if arah == "tinggi" else 100.0 - high
    return max(0.0, min(100.0, out))


def _indeks_peer(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    idx: Dict[str, Any] = {
        "sektor": defaultdict(list),
        "kelompok": defaultdict(list),
        "semua": defaultdict(list),
    }
    for r in rows:
        sec = r.get("sektor") or "LAINNYA"
        kel = r.get("kelompok") or "nonkeuangan"
        for key, m in (r.get("_metrik") or {}).items():
            v = m.get("nilai")
            if v is None:
                continue
            idx["sektor"][(key, sec)].append(v)
            idx["kelompok"][(key, kel)].append(v)
            idx["semua"][key].append(v)
    return idx


def _pilih_peer(key: str, r: Dict[str, Any], idx: Dict[str, Any]
                ) -> Tuple[List[float], str, bool, int]:
    """Pilih pembanding: sektor dulu; <5 -> gabungan kelompok/semua."""
    sec = r.get("sektor") or "LAINNYA"
    kel = r.get("kelompok") or "nonkeuangan"
    kunci_kelompok = key in ("roe_keuangan", "pbv_keuangan") or key in (
        "der_rata2_3th", "interest_coverage_rata2_3th")
    peer_sec = idx["sektor"].get((key, sec), [])
    if len(peer_sec) >= 5:
        return list(peer_sec), "sektor", False, len(peer_sec)
    if kunci_kelompok:
        peer_kel = idx["kelompok"].get((key, kel), [])
        if len(peer_kel) >= 5:
            return list(peer_kel), f"gabungan_{kel}", True, len(peer_sec)
    peer_all = idx["semua"].get(key, [])
    return list(peer_all), "gabungan_semua", True, len(peer_sec)


def _nilai_pada(seri: Optional[Dict[str, Any]], target: str
                ) -> Optional[float]:
    """Close terakhir <= target dari seri {d:[ISO], c:[float]}."""
    if not seri:
        return None
    d = seri.get("d") or []
    c = seri.get("c") or []
    if not d or not c:
        return None
    i = bisect.bisect_right(d, target) - 1
    if i < 0:
        return None
    v = _angka(c[i]) if i < len(c) else None
    return v if v is not None and v > 0 else None


def _tambah_hari(tanggal: str, hari: int) -> Optional[str]:
    try:
        return (date.fromisoformat(tanggal[:10]) + timedelta(days=hari)).isoformat()
    except ValueError:
        return None


def _tahun_terurut(fund: Optional[Dict[str, Any]]) -> List[int]:
    if not fund:
        return []
    raw = fund.get("tahun_keuangan")
    if isinstance(raw, list):
        out = []
        for y in raw:
            try:
                out.append(int(y))
            except (TypeError, ValueError):
                pass
        return sorted(set(out), reverse=True)
    out = []
    for y in (fund.get("periode_laporan") or {}):
        try:
            out.append(int(y))
        except (TypeError, ValueError):
            pass
    return sorted(set(out), reverse=True)


def _nilai_tahun(raw: Dict[str, Any], tahun: int,
                 field: str) -> Optional[float]:
    d = raw.get(field) or {}
    if not isinstance(d, dict):
        return None
    return _angka(d.get(str(tahun)))


def _saham_tahun(fund: Dict[str, Any], tahun: int) -> Optional[float]:
    raw = fund.get("data_mentah") or {}
    sh = _nilai_tahun(raw, tahun, "jumlah_saham")
    if sh is not None and sh > 0:
        return sh
    # Fallback: laba bersih / EPS (keduanya mata uang laporan yang sama).
    ni = _nilai_tahun(raw, tahun, "laba_bersih")
    eps = _nilai_tahun(raw, tahun, "eps")
    if ni is not None and eps is not None and ni > 0 and eps > 0:
        return ni / eps
    return None


def _saham_terakhir(fund: Dict[str, Any]) -> Tuple[Optional[float], Optional[int], str]:
    for y in _tahun_terurut(fund):
        sh = _saham_tahun(fund, y)
        if sh is not None and sh > 0:
            return sh, y, "jumlah_saham"
    return None, None, TIDAK_ADA


def _ekuitas_terakhir(fund: Dict[str, Any]) -> Tuple[Optional[float], Optional[int]]:
    for y in _tahun_terurut(fund):
        v = _nilai_tahun(fund.get("data_mentah") or {}, y, "ekuitas")
        if v is not None:
            return v, y
    return None, None


def _laba_positif(fund: Dict[str, Any]) -> Dict[str, Any]:
    k = _ambil(fund, "metrik.konsistensi") or {}
    p3 = _angka(k.get("laba_positif_3th"))
    n3 = _angka(k.get("laba_positif_3th_n")) or 0
    p4 = _angka(k.get("laba_positif_4th"))
    n4 = _angka(k.get("laba_positif_4th_n")) or 0
    if p3 is None and p4 is None:
        # fallback hitung dari laba mentah
        vals = []
        for y in _tahun_terurut(fund)[:4]:
            v = _nilai_tahun(fund.get("data_mentah") or {}, y, "laba_bersih")
            if v is not None:
                vals.append(v)
        p3 = sum(1 for v in vals[:3] if v > 0)
        n3 = min(3, len(vals))
        p4 = sum(1 for v in vals[:4] if v > 0)
        n4 = min(4, len(vals))
    return {"positif_3th": p3, "n_3th": n3, "positif_4th": p4, "n_4th": n4}


def _kurs_pada(kurs: Optional[Dict[str, Any]], target: str) -> Optional[float]:
    return _nilai_pada(kurs, target)


def _harga_pada(harga: Optional[Dict[str, Any]], target: str) -> Optional[float]:
    return _nilai_pada(harga, target)


def _eps_idr(fund: Dict[str, Any], info: Optional[Dict[str, Any]],
             kurs: Optional[Dict[str, Any]], tahun: int) -> Optional[float]:
    """EPS tahun fiskal dalam IDR; None kalau mata uang tidak bisa dipastikan."""
    raw = fund.get("data_mentah") or {}
    eps = _nilai_tahun(raw, tahun, "eps")
    if eps is None:
        return None
    fin = (info or {}).get("financialCurrency")
    if fin == "IDR":
        return eps
    if fin == "USD":
        periode = (fund.get("periode_laporan") or {}).get(str(tahun))
        if not periode:
            return None
        target = _tambah_hari(periode, 120)
        fx = _kurs_pada(kurs, target) if target else None
        return eps * fx if fx else None
    return None


def _sejarah_valuasi(fund: Optional[Dict[str, Any]],
                     info: Optional[Dict[str, Any]],
                     harga: Optional[Dict[str, Any]],
                     kurs: Optional[Dict[str, Any]],
                     cfg: Dict[str, Any]) -> Dict[str, Any]:
    """PER/PBV rata-rata 3-5 tahun memakai harga sekitar 120 hari setelah tutup buku."""
    out: Dict[str, Any] = {
        "status": TIDAK_ADA, "per_rata2": None, "pbv_rata2": None,
        "n_per": 0, "n_pbv": 0, "tahun": [], "mata_uang_laporan": None,
        "catatan": [],
    }
    if not fund:
        return out
    fin = (info or {}).get("financialCurrency")
    out["mata_uang_laporan"] = fin or TIDAK_ADA
    if fin not in ("IDR", "USD"):
        out["catatan"].append("mata uang laporan tidak diketahui")
        return out
    h = (harga or {}).get("bulanan")
    if not h:
        out["catatan"].append("riwayat harga bulanan tidak tersedia")
        return out
    min_n = int(cfg.get("histori_tahun_min", 2) or 2)
    max_n = int(cfg.get("histori_tahun_maks", 5) or 5)
    per_vals, pbv_vals = [], []
    for y in _tahun_terurut(fund)[:max_n]:
        periode = (fund.get("periode_laporan") or {}).get(str(y))
        if not periode:
            continue
        target = _tambah_hari(periode, 120)
        if not target:
            continue
        px = _harga_pada(h, target)
        if px is None:
            continue
        raw = fund.get("data_mentah") or {}
        eps = _nilai_tahun(raw, y, "eps")
        eq = _nilai_tahun(raw, y, "ekuitas")
        sh = _saham_tahun(fund, y)
        fx = 1.0
        if fin == "USD":
            fx = _kurs_pada(kurs, target)
            if fx is None:
                out["catatan"].append(f"kurs USD/IDR {target} tidak tersedia")
                continue
        eps_idr = eps * fx if eps is not None else None
        eq_idr = eq * fx if eq is not None else None
        per_t = px / eps_idr if (eps_idr is not None and eps_idr > 0) else None
        bvps = (eq_idr / sh) if (eq_idr is not None and sh and sh > 0) else None
        pbv_t = px / bvps if (bvps is not None and bvps > 0) else None
        item = {"tahun": y, "periode": periode, "target_harga": target,
                "harga": _bulat(px, 2), "eps_idr": _bulat(eps_idr, 2),
                "per": _bulat(per_t, 3), "pbv": _bulat(pbv_t, 3),
                "kurs_usd_idr": _bulat(fx, 2) if fin == "USD" else None}
        out["tahun"].append(item)
        if per_t is not None:
            per_vals.append(per_t)
        if pbv_t is not None:
            pbv_vals.append(pbv_t)
    out["n_per"] = len(per_vals)
    out["n_pbv"] = len(pbv_vals)
    if len(per_vals) >= min_n:
        out["per_rata2"] = sum(per_vals) / len(per_vals)
    if len(pbv_vals) >= min_n:
        out["pbv_rata2"] = sum(pbv_vals) / len(pbv_vals)
    if out["n_per"] or out["n_pbv"]:
        out["status"] = "ok"
    return out


def _median_likuiditas(kode: str, bars: Optional[Dict[str, Any]],
                       history: Optional[Dict[str, Any]],
                       sesi: int = 60) -> Dict[str, Any]:
    """Median close x volume. bars (run ini) diprioritaskan atas history lama."""
    data = None
    sumber = TIDAK_ADA
    if bars and kode in bars:
        data = bars.get(kode)
        sumber = "bars_run_ini"
    elif history:
        data = (history.get("emiten") or {}).get(kode)
        sumber = "history.json"
    if not data:
        return {"nilai": None, "n_sesi": 0, "sumber": TIDAK_ADA,
                "tanggal_akhir": None}
    vals: List[float] = []
    tanggal: List[str] = []
    if isinstance(data, list):
        for b in data[-sesi:]:
            if not isinstance(b, dict):
                continue
            c = _angka(b.get("close"))
            v = _angka(b.get("volume"))
            if c and v and c > 0 and v > 0:
                vals.append(c * v)
                tanggal.append(str(b.get("date") or "")[:10])
    else:
        cs = data.get("c") or []
        vs = data.get("v") or []
        ds = data.get("d") or []
        for c, v, d in zip(cs[-sesi:], vs[-sesi:], ds[-sesi:]):
            c2, v2 = _angka(c), _angka(v)
            if c2 and v2 and c2 > 0 and v2 > 0:
                vals.append(c2 * v2)
                tanggal.append(str(d)[:10])
    return {
        "nilai": statistics.median(vals) if vals else None,
        "n_sesi": len(vals), "sumber": sumber,
        "tanggal_akhir": tanggal[-1] if tanggal else None,
    }


def _market_cap(kode: str, latest: Dict[str, Any], fund: Optional[Dict[str, Any]],
                info: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    mc = _angka((info or {}).get("marketCap"))
    if mc is not None and mc > 0:
        return {"nilai": mc, "sumber": "yfinance.info.marketCap",
                "saham": _angka((info or {}).get("sharesOutstanding")),
                "tahun_saham": None}
    harga = _angka(latest.get("harga"))
    sh, tahun, src = _saham_terakhir(fund) if fund else (None, None, TIDAK_ADA)
    if harga is not None and sh is not None and harga > 0 and sh > 0:
        return {"nilai": harga * sh, "sumber": f"harga x {src}",
                "saham": sh, "tahun_saham": tahun}
    return {"nilai": None, "sumber": TIDAK_ADA, "saham": sh,
            "tahun_saham": tahun}


def _zona_timing(latest: Dict[str, Any], harga: Optional[Dict[str, Any]]
                 ) -> Dict[str, Any]:
    out: Dict[str, Any] = {
        "status": TIDAK_ADA, "zona": TIDAK_ADA, "harga": _angka(latest.get("harga")),
        "ma200": None, "harga_vs_ma200_pct": None,
        "puncak_52m": None, "jarak_puncak_pct": None,
        "n_sesi": 0, "dipakai_skor": False, "catatan": [],
    }
    h = (harga or {}).get("harian") or {}
    c = [x for x in (h.get("c") or []) if _angka(x) is not None and _angka(x) > 0]
    if len(c) < 20:
        out["catatan"].append("riwayat harga harian <20 sesi")
        return out
    c = [float(x) for x in c]
    harga_akhir = c[-1]
    n_ma = min(200, len(c))
    ma200 = sum(c[-n_ma:]) / n_ma
    n_peak = min(250, len(c))
    puncak = max(c[-n_peak:])
    vs_ma = (harga_akhir / ma200 - 1.0) * 100.0 if ma200 > 0 else None
    jarak = (harga_akhir / puncak - 1.0) * 100.0 if puncak > 0 else None
    if len(c) < 200:
        out["catatan"].append("MA200 dihitung dari <200 sesi")
    if len(c) < 250:
        out["catatan"].append("puncak 52 minggu dihitung dari <250 sesi")
    if vs_ma is not None and jarak is not None:
        if harga_akhir >= ma200 and jarak >= -10:
            zona = "Tren naik, dekat puncak"
        elif harga_akhir >= ma200:
            zona = "Tren naik, terkoreksi"
        elif jarak <= -20:
            zona = "Tren turun, koreksi dalam"
        else:
            zona = "Netral / transisi"
    else:
        zona = TIDAK_ADA
    out.update({
        "status": "ok", "zona": zona, "harga": _bulat(harga_akhir, 2),
        "ma200": _bulat(ma200, 2), "harga_vs_ma200_pct": _bulat(vs_ma, 2),
        "puncak_52m": _bulat(puncak, 2), "jarak_puncak_pct": _bulat(jarak, 2),
        "n_sesi": len(c),
    })
    return out


def _payout_sehat(p: Optional[float], cfg: Dict[str, Any]) -> Optional[float]:
    """0-1; 0,2-0,8 ideal. >200% -> 0 (tidak wajar)."""
    if p is None or not math.isfinite(p) or p < 0:
        return None
    lo = float(cfg.get("payout_ideal_min", 0.2))
    hi = float(cfg.get("payout_ideal_maks", 0.8))
    maks = float(cfg.get("payout_maks_wajar", 2.0))
    if p <= lo:
        return 0.5 + 0.5 * (p / lo) if lo > 0 else 1.0
    if p <= hi:
        return 1.0
    if p <= maks:
        return 1.0 - 0.8 * ((p - hi) / (maks - hi)) if maks > hi else 0.2
    return 0.0


def _laba_once(fund: Optional[Dict[str, Any]], cfg: Dict[str, Any]
               ) -> Dict[str, Any]:
    """Deteksi laba terakhir jauh di atas median 2-3 tahun sebelumnya."""
    out = {"flag": False, "rasio": None, "median_sebelumnya": None,
           "laba_terakhir": None, "catatan": None}
    if not fund:
        return out
    raw = fund.get("data_mentah") or {}
    ni = raw.get("laba_bersih") or {}
    years = []
    for y, v in ni.items():
        if _angka(v) is not None:
            try:
                years.append(int(y))
            except (TypeError, ValueError):
                pass
    years = sorted(set(years), reverse=True)
    if len(years) < 3:
        return out
    terakhir = _angka(ni.get(str(years[0])))
    prev = [_angka(ni.get(str(y))) for y in years[1:4]]
    prev = [x for x in prev if x is not None]
    if terakhir is None or not prev:
        return out
    med = statistics.median(prev)
    out["laba_terakhir"] = terakhir
    out["median_sebelumnya"] = med
    if terakhir > 0 and med > 0:
        rasio = terakhir / med
        out["rasio"] = rasio
        if rasio >= float(cfg.get("laba_once_rasio", 2.0)):
            out["flag"] = True
            out["catatan"] = f"laba terakhir {rasio:.1f}x median sebelumnya"
    return out


def _penanda_risiko(sektor: str, info: Optional[Dict[str, Any]],
                    sens: Optional[Dict[str, Any]], cfg: Dict[str, Any]
                    ) -> Dict[str, Any]:
    info = info or {}
    fin = info.get("financialCurrency")
    total_utang = _angka(info.get("totalDebt"))
    der = _angka(info.get("debtToEquity"))
    if der is not None:
        der = der / 100.0
    if fin == "USD":
        if total_utang is not None and total_utang <= 0:
            utang = False
            catatan_utang = "laporan USD; tidak ada utang"
        elif der is not None:
            utang = der >= float(cfg.get("utang_usd_der_min", 0.5))
            catatan_utang = f"laporan USD; DER {der:.2f}x"
        else:
            utang = TIDAK_ADA
            catatan_utang = "laporan USD; DER tidak tersedia"
    elif fin:
        utang = TIDAK_ADA
        catatan_utang = f"laporan {fin}; rincian mata uang utang tidak tersedia"
    else:
        utang = TIDAK_ADA
        catatan_utang = "mata uang laporan/utang tidak tersedia"

    fs = _angka(info.get("floatShares"))
    so = _angka(info.get("sharesOutstanding"))
    ff_pct = (fs / so * 100.0) if (fs and so and fs > 0 and so > 0) else None
    if ff_pct is None:
        ff = TIDAK_ADA
    else:
        ff = ff_pct < float(cfg.get("free_float_rendah_pct", 15.0))

    s = None
    if sens:
        s = (sens.get("sektor") or {}).get(sektor)
    if s:
        sens_out = {
            "status": "ok",
            "faktor": {k: v for k, v in s.items() if k != "alasan"},
            "alasan": s.get("alasan"),
            "dipakai_skor": False,
            "sumber": "config/sensitivitas_sektor.json",
            "status_peta": sens.get("status"),
        }
    else:
        sens_out = {"status": TIDAK_ADA, "dipakai_skor": False,
                    "catatan": "sektor tidak ada di peta sensitivitas"}
    return {
        "utang_usd_tinggi": utang,
        "catatan_utang": catatan_utang,
        "sektor_siklikal": sektor in set(cfg.get("sektor_siklikal") or []),
        "free_float_rendah": ff,
        "free_float_pct": _bulat(ff_pct, 2),
        "ketergantungan_komoditas": sektor in set(cfg.get("sektor_komoditas") or []),
        "sensitivitas_sektor": sens_out,
        "dipakai_skor": False,
    }


def _current_per_pbv(latest: Dict[str, Any], fund: Optional[Dict[str, Any]],
                     info: Optional[Dict[str, Any]],
                     kurs: Optional[Dict[str, Any]],
                     hist_val: Dict[str, Any]) -> Dict[str, Any]:
    harga = _angka(latest.get("harga"))
    per = _angka(latest.get("per"))
    pbv = _angka(latest.get("pbv"))
    sumber_per, sumber_pbv = "latest.per", "latest.pbv"
    fin = (info or {}).get("financialCurrency")
    if per is None and harga:
        eps = _angka((info or {}).get("trailingEps"))
        if eps is not None and eps > 0:
            per, sumber_per = harga / eps, "harga / trailingEps (IDR)"
        elif fund and fin in ("IDR", "USD"):
            years = _tahun_terurut(fund)
            if years:
                eps_idr = _eps_idr(fund, info, kurs, years[0])
                if eps_idr is not None and eps_idr > 0:
                    per, sumber_per = harga / eps_idr, "harga / EPS tahunan (IDR)"
    if pbv is None and harga:
        bv = _angka((info or {}).get("bookValue"))
        if bv is not None and bv > 0 and fin == "IDR":
            pbv, sumber_pbv = harga / bv, "harga / bookValue (IDR)"
        elif fund:
            eq, y = _ekuitas_terakhir(fund)
            sh = _saham_tahun(fund, y) if y else None
            if eq is not None and sh and sh > 0:
                fx = 1.0
                if fin == "USD" and y:
                    periode = (fund.get("periode_laporan") or {}).get(str(y))
                    target = _tambah_hari(periode, 120) if periode else None
                    fx = (_kurs_pada(kurs, target) if target else None) or 0
                if fx:
                    bvps = eq * fx / sh
                    if bvps > 0:
                        pbv, sumber_pbv = harga / bvps, "harga / BVPS (IDR)"
    return {"per": per if per and per > 0 else None, "sumber_per": sumber_per,
            "pbv": pbv if pbv and pbv > 0 else None, "sumber_pbv": sumber_pbv}


def _bangun_baris(latest: Dict[str, Any], fund: Optional[Dict[str, Any]],
                  info: Optional[Dict[str, Any]], history: Optional[Dict[str, Any]],
                  harga: Optional[Dict[str, Any]], kurs: Optional[Dict[str, Any]],
                  bars: Optional[Dict[str, Any]], median_per: Dict[str, float],
                  median_pbv: Dict[str, float], config: Dict[str, Any],
                  sens: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    kode = str(latest.get("kode") or "").upper()
    sektor = str(latest.get("sektor") or (fund or {}).get("sektor") or "LAINNYA").upper()
    kel = "keuangan" if sektor in set(config.get("sektor_keuangan") or []) else "nonkeuangan"
    fund_ada = bool(fund and ((fund.get("cakupan") or {}).get("jumlah_tahun_ada_data")
                              or fund.get("tahun_keuangan")))
    vcfg = config.get("valuasi") or {}

    lik = _median_likuiditas(kode, bars, history,
                             int((config.get("gerbang") or {}).get("likuiditas", {}).get("median_sesi", 60)))
    mc = _market_cap(kode, latest, fund, info)
    harga_emiten = ((harga or {}).get("emiten") or {}).get(kode)
    hv = _sejarah_valuasi(fund, info, harga_emiten, kurs, vcfg)
    cp = _current_per_pbv(latest, fund, info, kurs, hv)
    ekuitas, tahun_ekuitas = _ekuitas_terakhir(fund) if fund else (None, None)
    laba = _laba_positif(fund) if fund else {"positif_3th": None, "n_3th": 0,
                                             "positif_4th": None, "n_4th": 0}
    once = _laba_once(fund, vcfg)

    raw = fund.get("data_mentah") or {} if fund else {}
    years = _tahun_terurut(fund) if fund else []
    tahun_now = years[0] if years else None
    eps_now = _eps_idr(fund, info, kurs, tahun_now) if (fund and tahun_now) else None
    dps = _angka(raw.get("dividen_ttm"))
    payout = None
    payout_sumber = TIDAK_ADA
    if eps_now is not None and eps_now <= 0:
        payout_sumber = "tidak tersedia: laba/EPS <= 0"
    elif dps is not None and eps_now is not None and eps_now > 0:
        payout, payout_sumber = dps / eps_now, "DPS TTM / EPS (IDR)"
    elif dps is not None and dps == 0:
        payout, payout_sumber = 0.0, "tidak membagikan dividen"
    if payout is None and eps_now is not None and eps_now > 0:
        j1 = _ambil(fund, "metrik.dividen.payout_ratio_rata2_3th")
        j1 = _angka(j1)
        if j1 is not None and 0 <= j1 <= float(vcfg.get("payout_maks_wajar", 2.0)):
            payout, payout_sumber = j1, "J1 (mata uang belum diverifikasi)"
    payout_mentah = payout
    payout_s = _payout_sehat(payout, vcfg)
    payout_catatan = None
    if payout_mentah is not None and payout_mentah > float(vcfg.get("payout_maks_wajar", 2.0)):
        payout_catatan = "payout >200%: tidak wajar, tidak diberi skor tinggi"
    elif eps_now is not None and eps_now <= 0:
        payout_catatan = "payout tidak tersedia karena EPS <= 0"
    div_yield = _ambil(fund, "metrik.dividen.dividend_yield_ttm")
    div_yield = _angka(div_yield)
    kons_div = _ambil(fund, "metrik.dividen.konsistensi_dividen_3th")
    kons_div = _angka(kons_div)
    if kons_div is not None:
        kons_div = kons_div / 3.0

    # ── metrik mentah untuk persentil ──────────────────────────────
    metrik: Dict[str, Dict[str, Any]] = {}

    def add(key: str, val: Optional[float], label: str, sumber: str,
            catatan: Optional[str] = None) -> None:
        v = _angka(val)
        if v is None:
            return
        metrik[key] = {"nilai": v, "label": label, "sumber": sumber,
                       "catatan": catatan}

    add("roe_rata2_3th", _ambil(fund, "metrik.profitabilitas.roe_rata2_3th"),
        "ROE rata-rata 3 tahun", "J1")
    add("margin_laba_rata2_3th",
        _ambil(fund, "metrik.profitabilitas.margin_laba_rata2_3th"),
        "Margin laba rata-rata 3 tahun", "J1")
    add("ocf_laba_rata2_3th",
        _ambil(fund, "metrik.kesehatan_keuangan.ocf_laba_rata2_3th"),
        "OCF / laba rata-rata 3 tahun", "J1")
    if laba.get("positif_3th") is not None and laba.get("n_3th"):
        add("konsistensi_laba", float(laba["positif_3th"]) / 3.0,
            "Konsistensi laba 3 tahun", "J1")
    elif laba.get("positif_4th") is not None and laba.get("n_4th"):
        add("konsistensi_laba", float(laba["positif_4th"]) / 4.0,
            "Konsistensi laba 4 tahun", "J1")
    add("cagr_laba_3th", _ambil(fund, "metrik.pertumbuhan.cagr_laba_3th"),
        "CAGR laba 3 tahun", "J1")
    add("cagr_pendapatan_3th",
        _ambil(fund, "metrik.pertumbuhan.cagr_pendapatan_3th"),
        "CAGR pendapatan 3 tahun", "J1")
    der = _ambil(fund, "metrik.kesehatan_keuangan.der_rata2_3th")
    der = _angka(der)
    if der is not None and der >= 0:
        add("der_rata2_3th", der, "DER rata-rata 3 tahun", "J1")
    ic = _ambil(fund, "metrik.kesehatan_keuangan.interest_coverage_rata2_3th")
    ic = _angka(ic)
    if ic is not None and kel != "keuangan":
        add("interest_coverage_rata2_3th", ic,
            "Interest coverage rata-rata 3 tahun", "J1")
    # PBV/ROE versi lembaga keuangan memakai pembanding kelompok keuangan.
    add("roe_keuangan", _ambil(fund, "metrik.profitabilitas.roe_rata2_3th"),
        "ROE rata-rata 3 tahun (keuangan)", "J1")
    add("pbv_keuangan", cp["pbv"], "PBV (keuangan)", cp["sumber_pbv"])

    mper = median_per.get(sektor)
    mpbv = median_pbv.get(sektor)
    if cp["per"] and mper and mper > 0:
        add("per_vs_median_sektor", cp["per"] / mper,
            "PER relatif median sektor", "latest / median sektor")
    if cp["pbv"] and mpbv and mpbv > 0:
        add("pbv_vs_median_sektor", cp["pbv"] / mpbv,
            "PBV relatif median sektor", "latest / median sektor")
    if cp["per"] and hv.get("per_rata2") and hv["per_rata2"] > 0:
        add("per_vs_sejarah", cp["per"] / hv["per_rata2"],
            "PER relatif rata-rata historis", "harga / EPS historis")
    if cp["pbv"] and hv.get("pbv_rata2") and hv["pbv_rata2"] > 0:
        add("pbv_vs_sejarah", cp["pbv"] / hv["pbv_rata2"],
            "PBV relatif rata-rata historis", "harga / BVPS historis")
    add("dividend_yield_ttm", div_yield, "Dividend yield TTM", "J1")
    if payout_s is not None:
        add("payout_ratio_sehat", payout_s * 100.0, "Payout ratio sehat (0-100)",
            payout_sumber, payout_catatan)
    add("konsistensi_dividen", kons_div, "Konsistensi dividen 3 tahun", "J1")

    # Emiten tanpa laporan keuangan tidak boleh mendapat skor dari
    # "konsistensi 0" atau sisa metrik kosong. Skor JP harus None.
    if not fund_ada:
        metrik = {}

    waspada_tingkat = latest.get("waspada_tingkat")
    if waspada_tingkat == "tinggi" or latest.get("waspada_diskualifikasi"):
        waspada_skor = 0.0
    elif waspada_tingkat == "ringan":
        waspada_skor = 60.0
    else:
        waspada_skor = 100.0

    timing = _zona_timing(latest, harga_emiten)
    risiko = _penanda_risiko(sektor, info, sens, config)

    total_utang = _nilai_tahun(raw, tahun_now, "total_utang") if tahun_now else None
    beban_bunga = _nilai_tahun(raw, tahun_now, "beban_bunga") if tahun_now else None
    ic_relevan = True
    if total_utang is not None and total_utang <= 0:
        ic_relevan = False
    if beban_bunga is not None and beban_bunga <= 0:
        ic_relevan = False

    return {
        "kode": kode, "asli": latest, "fund": fund, "fund_ada": fund_ada,
        "info": info, "sektor": sektor, "kelompok": kel, "_metrik": metrik, "_raw": {
            "fund_ada": fund_ada,
            "likuiditas": lik, "market_cap": mc, "ekuitas": ekuitas,
            "tahun_ekuitas": tahun_ekuitas, "laba": laba,
            "total_utang": total_utang, "beban_bunga": beban_bunga,
            "ic_relevan": ic_relevan,
            "per": cp["per"], "pbv": cp["pbv"],
            "sumber_per": cp["sumber_per"], "sumber_pbv": cp["sumber_pbv"],
            "per_sejarah": hv.get("per_rata2"), "pbv_sejarah": hv.get("pbv_rata2"),
            "valuasi_historis": hv, "payout": payout_mentah,
            "payout_sumber": payout_sumber, "payout_sehat": payout_s,
            "eps_idr": eps_now, "laba_once": once,
            "waspada_skor": waspada_skor, "waspada_tingkat": waspada_tingkat,
            "timing": timing, "risiko": risiko,
        },
    }


def _config_pilar(pilar: str, kelompok: str, config: Dict[str, Any]
                  ) -> Dict[str, Any]:
    cfg = (config.get("metrik") or {}).get(pilar) or {}
    if pilar == "kesehatan_keuangan":
        return cfg.get("keuangan" if kelompok == "keuangan" else "non_keuangan") or {}
    return cfg


def _hitung_pilar(r: Dict[str, Any], pilar: str, config: Dict[str, Any]
                  ) -> Dict[str, Any]:
    cfg = _config_pilar(pilar, r.get("kelompok") or "nonkeuangan", config)
    total_w = 0.0
    total_cfg = 0.0
    jumlah = 0.0
    detail: List[Dict[str, Any]] = []
    catatan: List[str] = []
    alias = {"roe_rata2_3th": "roe_keuangan", "pbv": "pbv_keuangan"}
    for key, mc in cfg.items():
        bobot = float(mc.get("bobot", 0.0) or 0.0)
        kkey = alias.get(key, key) if pilar == "kesehatan_keuangan" else key
        m = (r.get("_metrik") or {}).get(kkey)
        alasan = None
        tersedia = bool(m and m.get("persentil") is not None)
        aplikabel = True
        if bobot <= 0:
            alasan = "data tidak tersedia / bobot opsional 0"
            aplikabel = False
        elif key == "interest_coverage_rata2_3th" and not r["_raw"].get("ic_relevan", True):
            alasan = "tidak relevan: tanpa utang berbunga"
            aplikabel = False
        elif not tersedia:
            alasan = TIDAK_ADA
        if aplikabel:
            total_cfg += bobot
        if tersedia and aplikabel:
            total_w += bobot
            jumlah += float(m["persentil"]) * bobot
        detail.append({
            "kunci": key,
            "label": mc.get("label") or key,
            "nilai": _bulat(m.get("nilai"), 6) if m else None,
            "persentil": _bulat(m.get("persentil"), 2) if m else None,
            "bobot": bobot,
            "tersedia": bool(tersedia and aplikabel),
            "arah": mc.get("arah"),
            "sumber": mc.get("sumber"),
            "basis_persentil": m.get("basis") if m else None,
            "n_pembanding": m.get("n_pembanding") if m else None,
            "persentil_gabungan": m.get("persentil_gabungan") if m else None,
            "catatan": m.get("catatan") if m else None,
            "alasan": alasan,
        })
        if m and m.get("persentil_gabungan"):
            catatan.append(f"{mc.get('label') or key}: persentil gabungan "
                           f"(sektor <5 emiten)")
    skor = (jumlah / total_w) if total_w > 0 else None
    cakupan = (total_w / total_cfg * 100.0) if total_cfg > 0 else 0.0
    if pilar == "kesehatan_keuangan" and r.get("kelompok") == "keuangan":
        catatan.append("bank/lembaga keuangan: DER & interest coverage tidak dipakai")
        if not any(d.get("tersedia") for d in detail if d["kunci"] in ("npl", "car")):
            catatan.append("NPL/CAR tidak tersedia; hanya ROE & PBV")
    return {"skor": _bulat(skor, 2), "bobot": config["bobot_pilar"][pilar],
            "cakupan": _bulat(cakupan, 2), "metrik": detail,
            "catatan": catatan or None}


def _cakupan_bobot(pilars: Dict[str, Any], config: Dict[str, Any]) -> float:
    denom = sum(float(config["bobot_pilar"][p]) for p in PILAR_DATA)
    num = sum(float(pilars[p]["cakupan"]) * float(config["bobot_pilar"][p])
              for p in PILAR_DATA)
    return (num / denom) if denom > 0 else 0.0


def _hitung_risiko(r: Dict[str, Any], cakupan: float, config: Dict[str, Any]
                   ) -> Dict[str, Any]:
    cfg = (config.get("metrik") or {}).get("risiko_tata_kelola") or {}
    nilai_manual = {
        "cakupan_data": cakupan,
        "waspada": r["_raw"].get("waspada_skor"),
        "likuiditas": r["_raw"].get("likuiditas_pct"),
    }
    total_w = 0.0
    total_cfg = 0.0
    jumlah = 0.0
    detail = []
    for key, mc in cfg.items():
        bobot = float(mc.get("bobot", 0.0) or 0.0)
        total_cfg += bobot
        v = _angka(nilai_manual.get(key))
        tersedia = v is not None
        if tersedia:
            total_w += bobot
            jumlah += v * bobot
        detail.append({
            "kunci": key, "label": mc.get("label") or key,
            "nilai": _bulat(v, 2), "persentil": _bulat(v, 2),
            "bobot": bobot, "tersedia": tersedia, "arah": mc.get("arah"),
            "sumber": mc.get("sumber"), "alasan": None if tersedia else TIDAK_ADA,
        })
    skor = (jumlah / total_w) if total_w > 0 else None
    cakupan_p = (total_w / total_cfg * 100.0) if total_cfg > 0 else 0.0
    return {"skor": _bulat(skor, 2),
            "bobot": config["bobot_pilar"]["risiko_tata_kelola"],
            "cakupan": _bulat(cakupan_p, 2), "metrik": detail,
            "catatan": ["penanda risiko tidak mengurangi skor"]}


def _skor_total(pilars: Dict[str, Any], config: Dict[str, Any],
                cakupan: float) -> Tuple[Optional[float], Dict[str, Any]]:
    # Skor JP hanya bermakna kalau minimal satu pilar DATA tersedia.
    # Tanpa ini, emiten tanpa fundamental bisa mendapat skor dari pilar
    # risiko (waspada + likuiditas) saja — jelas menyesatkan.
    ada_data = any(pilars[p].get("skor") is not None for p in PILAR_DATA)
    total_w = 0.0
    jumlah = 0.0
    if ada_data:
        for p in PILAR_SEMUA:
            s = pilars[p].get("skor")
            if s is not None:
                w = float(config["bobot_pilar"][p])
                total_w += w
                jumlah += float(s) * w
    for p in PILAR_SEMUA:
        s = pilars[p].get("skor") if ada_data else None
        w = float(config["bobot_pilar"][p])
        eff = (w / total_w) if (total_w > 0 and s is not None) else 0.0
        pilars[p]["bobot_efektif"] = _bulat(eff, 4)
        pilars[p]["kontribusi"] = _bulat(float(s) * eff, 2) if s is not None else None
    skor = (jumlah / total_w) if total_w > 0 else None
    return _bulat(skor, 2), pilars


def _gerbang(r: Dict[str, Any], cakupan: float, config: Dict[str, Any]
             ) -> Dict[str, Any]:
    g = config.get("gerbang") or {}
    alasan: List[str] = []
    detail: Dict[str, Any] = {}

    lik = r["_raw"].get("likuiditas") or {}
    lv = _angka(lik.get("nilai"))
    lmin = float((g.get("likuiditas") or {}).get("nilai_transaksi_min", 1e9))
    sesi_min = int((g.get("likuiditas") or {}).get("min_sesi", 20))
    n_sesi = int(lik.get("n_sesi") or 0)
    if lv is None:
        lolos_lik = False
        alasan.append("likuiditas: data tidak tersedia")
    elif n_sesi < sesi_min:
        lolos_lik = False
        alasan.append(f"likuiditas: riwayat {n_sesi} sesi < {sesi_min}")
    elif lv < lmin:
        lolos_lik = False
        alasan.append(f"likuiditas: median {_rp(lv)} < {_rp(lmin)}")
    else:
        lolos_lik = True
    detail["likuiditas"] = {"lolos": lolos_lik, "nilai": lv, "ambang": lmin,
                            "n_sesi": n_sesi, "sumber": lik.get("sumber"),
                            "satuan": "IDR_per_hari"}

    mc = r["_raw"].get("market_cap") or {}
    mv = _angka(mc.get("nilai"))
    mmin = float((g.get("kapitalisasi") or {}).get("market_cap_min", 2e11))
    if mv is None:
        lolos_mc = False
        alasan.append("kapitalisasi: data tidak tersedia")
    elif mv < mmin:
        lolos_mc = False
        alasan.append(f"kapitalisasi: {_rp(mv)} < {_rp(mmin)}")
    else:
        lolos_mc = True
    detail["kapitalisasi"] = {"lolos": lolos_mc, "nilai": mv, "ambang": mmin,
                              "sumber": mc.get("sumber"), "satuan": "IDR"}

    laba = r["_raw"].get("laba") or {}
    p4, n4 = _angka(laba.get("positif_4th")), int(laba.get("n_4th") or 0)
    p3, n3 = _angka(laba.get("positif_3th")), int(laba.get("n_3th") or 0)
    if n4 >= 4:
        lolos_laba = p4 is not None and p4 >= int((g.get("laba") or {}).get("min_positif_4th", 3))
        syarat = "3 dari 4 tahun"
    elif n3 >= 3:
        lolos_laba = p3 is not None and p3 >= int((g.get("laba") or {}).get("min_positif_3th", 2))
        syarat = "2 dari 3 tahun"
    else:
        lolos_laba = False
        syarat = "butuh minimal 3 tahun"
    if not lolos_laba:
        alasan.append(f"laba: gagal syarat {syarat} (3th {p3}/{n3}, 4th {p4}/{n4})")
    detail["laba"] = {"lolos": lolos_laba, "positif_3th": p3, "n_3th": n3,
                      "positif_4th": p4, "n_4th": n4, "syarat": syarat}

    eq = _angka(r["_raw"].get("ekuitas"))
    if eq is None:
        lolos_eq = False
        alasan.append("ekuitas: data tidak tersedia")
    elif eq <= 0:
        lolos_eq = False
        alasan.append(f"ekuitas: {_rp(eq)} <= 0")
    else:
        lolos_eq = True
    detail["ekuitas"] = {"lolos": lolos_eq, "nilai": eq,
                         "tahun": r["_raw"].get("tahun_ekuitas")}

    wt = r["_raw"].get("waspada_tingkat")
    lolos_w = not (wt == "tinggi" or r["asli"].get("waspada_diskualifikasi"))
    if not lolos_w:
        alasan.append("waspada: tingkat tinggi")
    detail["waspada"] = {"lolos": lolos_w, "tingkat": wt}

    cmin = float(g.get("cakupan_bobot_min", 70.0))
    lolos_c = cakupan >= cmin
    if not lolos_c:
        alasan.append(f"data kurang: cakupan bobot {cakupan:.1f}% < {cmin:.0f}%")
    detail["cakupan_bobot"] = {"lolos": lolos_c, "nilai": _bulat(cakupan, 2),
                               "ambang": cmin}

    return {"lolos_gerbang": all(x["lolos"] for x in detail.values()),
            "alasan_gagal_gerbang": alasan, "gerbang_detail": detail,
            "data_kurang": not lolos_c}


def _arah_map(config: Dict[str, Any]) -> Dict[str, str]:
    out: Dict[str, str] = {}
    for p, cfg in (config.get("metrik") or {}).items():
        if p == "kesehatan_keuangan":
            for sub in ("non_keuangan", "keuangan"):
                for k, mc in (cfg.get(sub) or {}).items():
                    out[k] = mc.get("arah", "tinggi")
        else:
            for k, mc in cfg.items():
                out[k] = mc.get("arah", "tinggi")
    out["roe_keuangan"] = "tinggi"
    out["pbv_keuangan"] = "rendah"
    return out


def _median_sektor(rows: List[Dict[str, Any]], field: str
                   ) -> Dict[str, float]:
    by: Dict[str, List[float]] = defaultdict(list)
    for e in rows:
        v = _angka(e.get(field))
        if v is not None and v > 0:
            by[str(e.get("sektor") or "LAINNYA").upper()].append(v)
    semua = [v for xs in by.values() for v in xs]
    overall = statistics.median(semua) if semua else None
    out: Dict[str, float] = {}
    for sec, vals in by.items():
        out[sec] = statistics.median(vals) if len(vals) >= 5 else (overall or statistics.median(vals))
    return out


def _dist(vals: Iterable[Optional[float]]) -> Dict[str, Any]:
    x = sorted(v for v in vals if v is not None and math.isfinite(v))
    n = len(x)
    if not n:
        return {"n": 0, "min": None, "p25": None, "median": None,
                "p75": None, "p90": None, "max": None}
    def q(p: float) -> float:
        return x[min(n - 1, int(p * n))]
    return {"n": n, "min": _bulat(x[0], 2), "p25": _bulat(q(.25), 2),
            "median": _bulat(statistics.median(x), 2),
            "p75": _bulat(q(.75), 2), "p90": _bulat(q(.90), 2),
            "max": _bulat(x[-1], 2)}


def _baris_output(r: Dict[str, Any]) -> Dict[str, Any]:
    g = r["_gerbang"]
    return {
        "kode": r["kode"],
        "skor_jp": r.get("_skor"),
        "peringkat_jp": r.get("_peringkat"),
        "diperingkat": bool(g.get("lolos_gerbang") and r.get("_skor") is not None),
        "lolos_gerbang": bool(g.get("lolos_gerbang")),
        "alasan_gagal_gerbang": g.get("alasan_gagal_gerbang") or [],
        "gerbang_detail": g.get("gerbang_detail") or {},
        "cakupan_bobot": _bulat(r.get("_cakupan"), 2),
        "data_kurang": bool(g.get("data_kurang")),
        # Rincian pilar hanya untuk emiten yang benar-benar punya skor.
        # Emiten tanpa laporan tetap null, bukan objek berisi angka 0.
        "skor_jp_rincian": (r.get("_pilar") or {}) if r.get("_skor") is not None else None,
        "zona_timing": r["_raw"].get("timing") or {},
        "penanda_risiko": r["_raw"].get("risiko") or {},
        "valuasi_historis": (r["_raw"].get("valuasi_historis") or {}) if r.get("fund_ada") else None,
    }


def _ringkas_top(r: Dict[str, Any], i: int) -> Dict[str, Any]:
    f = r.get("fund") or {}
    raw = r.get("_raw") or {}
    return {
        "peringkat": i, "kode": r["kode"],
        "nama": r["asli"].get("nama"),
        "sektor": r.get("sektor"),
        "skor_jp": r.get("_skor"),
        "cakupan_bobot": _bulat(r.get("_cakupan"), 2),
        "harga": r["asli"].get("harga"),
        "per": _bulat(raw.get("per"), 4),
        "pbv": _bulat(raw.get("pbv"), 4),
        "roe": _bulat(r["asli"].get("roe") or
                      _ambil(f, "metrik.profitabilitas.roe_rata2_3th"), 6),
        "dividend_yield": _bulat(_ambil(f, "metrik.dividen.dividend_yield_ttm"), 6),
        "nilai_transaksi_median": _bulat((raw.get("likuiditas") or {}).get("nilai"), 2),
        "market_cap": _bulat((raw.get("market_cap") or {}).get("nilai"), 2),
        "zona_timing": (raw.get("timing") or {}).get("zona"),
        "pilar": {p: (r.get("_pilar") or {}).get(p, {}).get("skor")
                  for p in PILAR_SEMUA},
        "penanda_risiko": raw.get("risiko") or {},
    }


def hitung(latest: Dict[str, Any], fundamental: Dict[str, Any],
           history: Optional[Dict[str, Any]] = None,
           harga: Optional[Dict[str, Any]] = None,
           info: Optional[Dict[str, Any]] = None,
           config: Optional[Dict[str, Any]] = None,
           sens: Optional[Dict[str, Any]] = None,
           bars: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    config = config or _baca_json(PATH_BOBOT)
    latest_rows = latest.get("emiten") or []
    fund_map = (fundamental or {}).get("emiten") or {}
    info_map = (info or {}).get("emiten") or {}
    mp = _median_sektor(latest_rows, "per")
    mb = _median_sektor(latest_rows, "pbv")
    n_sektor = defaultdict(int)
    for e in latest_rows:
        n_sektor[str(e.get("sektor") or "LAINNYA").upper()] += 1
    # Median sektor hanya dipakai bila sektor punya >=5 emiten.
    # Sektor kecil memakai median gabungan (sesuai aturan persentil).
    for k, v in (latest.get("median_per_sektor") or {}).items():
        sec = str(k).upper()
        if n_sektor.get(sec, 0) >= 5 and _angka(v) is not None and _angka(v) > 0:
            mp[sec] = float(v)
    for k, v in (latest.get("median_pbv_sektor") or {}).items():
        sec = str(k).upper()
        if n_sektor.get(sec, 0) >= 5 and _angka(v) is not None and _angka(v) > 0:
            mb[sec] = float(v)

    rows: List[Dict[str, Any]] = []
    for lr in latest_rows:
        k = str(lr.get("kode") or "").upper()
        rows.append(_bangun_baris(lr, fund_map.get(k), info_map.get(k),
                                  history, harga, (harga or {}).get("kurs_usd_idr"),
                                  bars, mp, mb, config, sens))
    idx = _indeks_peer(rows)
    arah = _arah_map(config)
    for r in rows:
        for key, m in (r.get("_metrik") or {}).items():
            peers, basis, gabung, nsec = _pilih_peer(key, r, idx)
            m["persentil"] = _bulat(_persentil(m.get("nilai"), peers,
                                               arah.get(key, "tinggi")), 2)
            m["basis"] = basis
            m["n_pembanding"] = len(peers)
            m["persentil_gabungan"] = gabung
            m["n_sektor"] = nsec

    # Hindari skor valuasi tinggi dari PER rendah akibat laba sekali-jadi.
    cap_per = float((config.get("valuasi") or {}).get("laba_once_cap_skor_per", 40.0))
    for r in rows:
        if not (r["_raw"].get("laba_once") or {}).get("flag"):
            continue
        for key in ("per_vs_median_sektor", "per_vs_sejarah"):
            m = (r.get("_metrik") or {}).get(key)
            if m and m.get("persentil") is not None:
                m["persentil"] = min(float(m["persentil"]), cap_per)
                m["catatan"] = "dibatasi: laba sekali-jadi / PER rendah tidak wajar"

    # Persentil likuiditas dipakai pilar risiko (informasi, bukan gerbang).
    liq_all = [_angka((r["_raw"].get("likuiditas") or {}).get("nilai")) for r in rows]
    liq_all = [v for v in liq_all if v is not None and v > 0]
    for r in rows:
        lv = _angka((r["_raw"].get("likuiditas") or {}).get("nilai"))
        r["_raw"]["likuiditas_pct"] = _bulat(_persentil(lv, liq_all, "tinggi"), 2)

    for r in rows:
        pilars: Dict[str, Any] = {}
        for p in PILAR_DATA:
            pilars[p] = _hitung_pilar(r, p, config)
        cak = _cakupan_bobot(pilars, config)
        pilars["risiko_tata_kelola"] = _hitung_risiko(r, cak, config)
        skor, pilars = _skor_total(pilars, config, cak)
        g = _gerbang(r, cak, config)
        r["_pilar"] = pilars
        r["_cakupan"] = cak
        r["_skor"] = skor
        r["_gerbang"] = g
        r["_peringkat"] = None

    ranked = [r for r in rows
              if r["_gerbang"].get("lolos_gerbang") and r.get("_skor") is not None]
    ranked.sort(key=lambda r: (-(r.get("_skor") or 0),
                               -(r.get("_cakupan") or 0), r["kode"]))
    for i, r in enumerate(ranked, 1):
        r["_peringkat"] = i

    outputs = [_baris_output(r) for r in rows]
    top20 = [_ringkas_top(r, i) for i, r in enumerate(ranked[:20], 1)]

    # ── ringkasan / audit ──────────────────────────────────────────
    n_fund = sum(1 for r in rows if r.get("fund_ada"))
    gagal = {k: 0 for k in ("likuiditas", "kapitalisasi", "laba", "ekuitas",
                            "waspada", "cakupan_bobot")}
    for r in rows:
        gd = r["_gerbang"].get("gerbang_detail") or {}
        for k in gagal:
            if gd.get(k) and not gd[k].get("lolos"):
                gagal[k] += 1
    semua_kunci = {
        "roe_rata2_3th", "margin_laba_rata2_3th", "ocf_laba_rata2_3th",
        "konsistensi_laba", "cagr_laba_3th", "cagr_pendapatan_3th",
        "der_rata2_3th", "interest_coverage_rata2_3th",
        "roe_keuangan", "pbv_keuangan",
        "per_vs_median_sektor", "pbv_vs_median_sektor",
        "per_vs_sejarah", "pbv_vs_sejarah",
        "dividend_yield_ttm", "payout_ratio_sehat", "konsistensi_dividen",
    }
    bolong = {}
    for k in sorted(semua_kunci):
        n = 0
        for r in rows:
            if not r.get("fund_ada"):
                continue
            if k == "interest_coverage_rata2_3th" and not r["_raw"].get("ic_relevan", True):
                continue
            if k not in (r.get("_metrik") or {}):
                n += 1
        if n:
            bolong[k] = n
    sekunder = defaultdict(int)
    for r in rows:
        if r.get("fund_ada"):
            sekunder[r.get("sektor") or "LAINNYA"] += 1
    sektor_kecil = sorted([s for s, n in sekunder.items() if n < 5])
    usd = sum(1 for r in rows if ((r.get("info") or {}).get("financialCurrency") == "USD"))
    once = sum(1 for r in rows if (r["_raw"].get("laba_once") or {}).get("flag"))
    payout_aneh = sum(1 for r in rows
                      if (_angka(r["_raw"].get("payout")) or 0) > float(
                          (config.get("valuasi") or {}).get("payout_maks_wajar", 2.0)))
    tgl_latest = str(latest.get("tanggal_data") or "")
    hj_date = str((harga or {}).get("diperbarui_pada") or "")[:10]
    fd_date = str((fundamental or {}).get("diperbarui_pada") or "")[:10]
    catatan_data = []
    if hj_date and tgl_latest and hj_date < tgl_latest:
        catatan_data.append(
            f"harga_jp.json diperbarui {hj_date}, lebih lama dari data utama "
            f"{tgl_latest}; MA200/timing memakai harga terakhir yang tersedia.")
    if fd_date and tgl_latest and fd_date < tgl_latest:
        catatan_data.append(
            f"fundamental.json diperbarui {fd_date}, lebih lama dari data utama "
            f"{tgl_latest}; skor JP memakai laporan terbaru yang tersedia.")
    meta = {
        "versi": VERSI,
        "status": "ok",
        "dihitung_pada": stamp_wib(),
        "tanggal_data": latest.get("tanggal_data"),
        "sumber": ["data/fundamental.json", "data/history.json",
                   "data/harga_jp.json", "data/info_jp.json",
                   "config/bobot_jp.json", "config/sensitivitas_sektor.json"],
        "sumber_data": {
            "fundamental_diperbarui_pada": (fundamental or {}).get("diperbarui_pada"),
            "fundamental_tanggal_data_utama": (fundamental or {}).get("tanggal_data_utama"),
            "harga_jp_diperbarui_pada": (harga or {}).get("diperbarui_pada"),
            "info_jp_diperbarui_pada": (info or {}).get("diperbarui_pada"),
            "history_diperbarui_pada": (history or {}).get("diperbarui_pada"),
            "harga_jp_jumlah_emiten": (harga or {}).get("jumlah_emiten"),
            "info_jp_jumlah_emiten": (info or {}).get("jumlah_emiten"),
        },
        "bobot_pilar": config.get("bobot_pilar"),
        "gerbang": config.get("gerbang"),
        "status_ambang": config.get("status_ambang"),
        "dikonfirmasi_pada": config.get("dikonfirmasi_pada"),
        "dasar_ambang": config.get("dasar_ambang"),
        "metode": "persentil dalam sektor; sektor <5 emiten memakai persentil gabungan; bank/lembaga keuangan tanpa DER/interest coverage",
        "jumlah": {
            "total": len(rows), "punya_fundamental": n_fund,
            "lolos_gerbang": len(ranked), "diperingkat": len(ranked),
            "data_kurang": sum(1 for r in rows if r["_gerbang"].get("data_kurang")),
            "tanpa_skor": sum(1 for r in rows if r.get("_skor") is None),
            "gagal": gagal,
        },
        "distribusi_skor": _dist([r.get("_skor") for r in ranked]),
        "distribusi_cakupan": _dist([r.get("_cakupan") for r in rows if r.get("fund_ada")]),
        "metrik_bolong": bolong,
        "sektor_kecil": sektor_kecil,
        "catatan": [
            "Skor JP terpisah dari skor momentum/trading; field lama tidak diubah.",
            "Valuasi historis memakai harga sekitar 120 hari setelah tutup buku; laporan USD dikonversi kurs bulanan.",
            f"{usd} emiten melaporkan keuangan dalam USD (dikonversi ke IDR untuk EPS/BVPS).",
            f"{once} emiten terdeteksi laba sekali-jadi (PER dibatasi).",
            f"{payout_aneh} emiten payout >200% (tidak diberi skor tinggi).",
            "Penanda risiko (utang USD, siklikal, free float, komoditas, sensitivitas) tidak mengurangi skor.",
            "NPL/CAR tidak tersedia dari yfinance; bank/lembaga keuangan hanya memakai ROE & PBV.",
            "Zona timing hanya informasi, tidak masuk skor.",
        ] + catatan_data,
    }
    ringkasan = {
        "versi": VERSI, "dihitung_pada": meta["dihitung_pada"],
        "tanggal_data": meta["tanggal_data"], "jumlah": meta["jumlah"],
        "distribusi_skor": meta["distribusi_skor"],
        "distribusi_cakupan": meta["distribusi_cakupan"],
        "metrik_bolong": bolong, "sektor_kecil": sektor_kecil,
        "top20": top20, "catatan": meta["catatan"],
    }
    return {"outputs": outputs, "meta": meta, "top20": top20,
            "ringkasan": ringkasan, "rows": rows}


def muat_pendukung() -> Dict[str, Any]:
    """Data pendukung J2; tidak membaca latest.json (dipakai pipeline)."""
    def ada(path: str) -> bool:
        return os.path.exists(path)
    return {
        "fundamental": _baca_json(PATH_FUNDAMENTAL) if ada(PATH_FUNDAMENTAL) else {},
        "history": _baca_json(PATH_HISTORY) if ada(PATH_HISTORY) else {},
        "harga": _baca_json(PATH_HARGA) if ada(PATH_HARGA) else {},
        "info": _baca_json(PATH_INFO) if ada(PATH_INFO) else {},
        "config": _baca_json(PATH_BOBOT),
        "sens": _baca_json(PATH_SENS) if ada(PATH_SENS) else {},
    }


def muat_semua() -> Dict[str, Any]:
    data = muat_pendukung()
    data["latest"] = _baca_json(PATH_LATEST)
    return data


def gabung_ke_latest(latest: Dict[str, Any], hasil: Dict[str, Any]) -> Dict[str, Any]:
    by = {x["kode"]: x for x in hasil.get("outputs") or []}
    for row in latest.get("emiten") or []:
        k = str(row.get("kode") or "").upper()
        add = by.get(k)
        if not add:
            continue
        for key, val in add.items():
            if key == "kode":
                continue
            row[key] = val
    latest["versi_skema"] = VERSI_SKEMA_BARU
    latest["skor_jp_meta"] = hasil.get("meta")
    latest["top20_jp"] = hasil.get("top20") or []
    return latest


def tulis_ringkasan_payload(payload: Dict[str, Any]) -> bool:
    """Tulis file audit dari payload latest yang SUDAH lolos validasi."""
    meta = payload.get("skor_jp_meta") or {}
    if not meta or meta.get("status") == "gagal":
        return False
    ringkasan = {
        "versi": meta.get("versi"), "dihitung_pada": meta.get("dihitung_pada"),
        "tanggal_data": meta.get("tanggal_data"), "jumlah": meta.get("jumlah"),
        "distribusi_skor": meta.get("distribusi_skor"),
        "distribusi_cakupan": meta.get("distribusi_cakupan"),
        "metrik_bolong": meta.get("metrik_bolong"),
        "sektor_kecil": meta.get("sektor_kecil"),
        "top20": payload.get("top20_jp") or [],
        "catatan": meta.get("catatan"),
    }
    _tulis_atomic(PATH_RINGKASAN, ringkasan)
    return True


def sisipkan(payload: Dict[str, Any], bars: Optional[Dict[str, Any]] = None,
             tulis_ringkasan: bool = False) -> Dict[str, Any]:
    """Dipanggil pipeline sebelum latest.json ditulis.

    Kegagalan J2 TIDAK boleh menggagalkan pipeline momentum. Kalau gagal,
    payload tetap terbit dan hanya diberi meta error yang jelas.
    """
    try:
        data = muat_pendukung()
        hasil = hitung(payload, data["fundamental"], data["history"],
                       data["harga"], data["info"], data["config"],
                       data["sens"], bars=bars)
        gabung_ke_latest(payload, hasil)
        if tulis_ringkasan:
            _tulis_atomic(PATH_RINGKASAN, hasil["ringkasan"])
        return payload
    except Exception as e:  # noqa: BLE001
        payload["skor_jp_meta"] = {
            "versi": VERSI, "status": "gagal",
            "dihitung_pada": stamp_wib(),
            "error": f"{type(e).__name__}: {e}",
            "catatan": ["Skor JP tidak dihitung; skor momentum tetap berlaku."],
        }
        return payload


def jalankan_tulis() -> Dict[str, Any]:
    data = muat_semua()
    hasil = hitung(data["latest"], data["fundamental"], data["history"],
                   data["harga"], data["info"], data["config"], data["sens"])
    latest = gabung_ke_latest(data["latest"], hasil)
    _tulis_atomic(PATH_LATEST, latest)
    _tulis_atomic(PATH_RINGKASAN, hasil["ringkasan"])
    return hasil


def _cetak_ringkas(hasil: Dict[str, Any], top_n: int = 20) -> None:
    meta = hasil.get("meta") or {}
    print(f"[skor_jp] {meta.get('jumlah', {}).get('total')} emiten | "
          f"lolos gerbang {meta.get('jumlah', {}).get('lolos_gerbang')} | "
          f"data kurang {meta.get('jumlah', {}).get('data_kurang')}", flush=True)
    d = meta.get("distribusi_skor") or {}
    print(f"[skor_jp] skor: n={d.get('n')} median={d.get('median')} "
          f"p90={d.get('p90')} max={d.get('max')}", flush=True)
    print(f"[skor_jp] gagal: {json.dumps(meta.get('jumlah', {}).get('gagal'), ensure_ascii=False)}",
          flush=True)
    print(f"[skor_jp] metrik bolong: {json.dumps(meta.get('metrik_bolong'), ensure_ascii=False)}",
          flush=True)
    for t in (hasil.get("top20") or [])[:top_n]:
        p = t.get("pilar") or {}
        print(f"  {t['peringkat']:2d}. {t['kode']:5s} {t['skor_jp']:6.2f} "
              f"cakupan {t['cakupan_bobot']:5.1f}% | {t.get('sektor')} | "
              f"K{t.get('kualitas_profitabilitas') or p.get('kualitas_profitabilitas')} "
              f"G{p.get('pertumbuhan')} H{p.get('kesehatan_keuangan')} "
              f"V{p.get('valuasi')} D{p.get('dividen')} R{p.get('risiko_tata_kelola')}",
              flush=True)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tanpa-tulis", action="store_true")
    ap.add_argument("--top", type=int, default=20)
    ap.add_argument("--json", action="store_true",
                    help="cetak meta lengkap sebagai JSON")
    args = ap.parse_args()
    if args.tanpa_tulis:
        data = muat_semua()
        hasil = hitung(data["latest"], data["fundamental"], data["history"],
                       data["harga"], data["info"], data["config"], data["sens"])
    else:
        hasil = jalankan_tulis()
    _cetak_ringkas(hasil, top_n=args.top)
    if args.json:
        print(json.dumps({"meta": hasil.get("meta"),
                          "top20": hasil.get("top20"),
                          "ringkasan": hasil.get("ringkasan")},
                         ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
