"""
Backtest sinyal vs IHSG (Kelompok C).

Tujuan: menjawab "kalau sinyal X muncul, apa yang terjadi 5/10/20 sesi
bursa kemudian, dan apakah mengalahkan IHSG pada horizon yang sama?"

Prinsip yang dipakai:
  * HORIZON = SESI BURSA, bukan hari kalender. N sesi ke depan = indeks
    bar ke-(i+N) pada seri emiten itu sendiri. Akhir pekan & libur IDX
    otomatis terlewati karena tidak ada bar-nya.
  * Pembanding IHSG dihitung pada TANGGAL yang sama (bukan offset bar),
    supaya excess return benar-benar "atas/bawah pasar" di periode yang
    sama.
  * Tidak ada look-ahead: label sinyal di titik i hanya memakai bar
    <= i, dan return dihitung dari close i ke close i+N.
  * Emiten/ titik tanpa harga pembanding yang lengkap DIBUANG dari
    agregat, tidak diisi nol.

Output: data/backtest.json
"""

from __future__ import annotations

import json
import os
import sys
import time
from typing import Any, Dict, List, Optional, Tuple

try:  # dipakai sebagai paket (idx.backtest) maupun script langsung
    from . import providers as P
    from . import scoring as S
except ImportError:  # pragma: no cover
    import providers as P  # type: ignore
    import scoring as S  # type: ignore

REPO = "/root/idx-dashboard"
DATA = os.path.join(REPO, "data")
HIST = os.path.join(DATA, "history.json")
OUT = os.path.join(DATA, "backtest.json")

HORIZON = (5, 10, 20)      # sesi bursa
MIN_SESI = 25              # minimal bar supaya skor teknikal tersedia & ada ruang horizon
HORIZON_MAKS = max(HORIZON)

# Pengelompokan skor. Batas ini HARUS sama dengan yang dipakai UI supaya
# angka di dashboard dan di backtest tidak saling bertentangan.
KELOMPOK_SKOR = (
    ("tinggi", 70.0, 100.01, "Skor ≥ 70"),
    ("sedang", 50.0, 70.0, "Skor 50–69"),
    ("rendah", 0.0, 50.0, "Skor < 50"),
)


# ───────────────────────── util ─────────────────────────

def _tulis_atomic(path: str, payload: Dict[str, Any]) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = f"{path}.tmp.{os.getpid()}"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, separators=(",", ":"))
    os.replace(tmp, path)


def _median(xs: List[float]) -> Optional[float]:
    if not xs:
        return None
    s = sorted(xs)
    n = len(s)
    m = n // 2
    return s[m] if n % 2 else (s[m - 1] + s[m]) / 2


def _pct(xs: List[float], q: float) -> Optional[float]:
    """Kuantil q (0..1) dengan interpolasi linear; None kalau kosong."""
    if not xs:
        return None
    s = sorted(xs)
    if len(s) == 1:
        return s[0]
    pos = q * (len(s) - 1)
    lo = int(pos)
    hi = min(lo + 1, len(s) - 1)
    frac = pos - lo
    return s[lo] * (1 - frac) + s[hi] * frac


def _kelompok(skor: Optional[float]) -> Optional[str]:
    if skor is None:
        return None
    for nama, lo, hi, _ in KELOMPOK_SKOR:
        if lo <= skor < hi:
            return nama
    return None


def _peta_harga_ihsg(bars: List[Dict[str, Any]]) -> Dict[str, float]:
    """tanggal -> close IHSG (untuk pembanding pada tanggal yang sama)."""
    return {b["date"]: b["close"] for b in bars if b.get("close") is not None}


# ───────────────────────── inti ─────────────────────────

def iter_titik(seri: Dict[str, Any], ihsg: Dict[str, float],
               horizon: Tuple[int, ...] = HORIZON):
    """Yield satu titik evaluasi backtest per sesi bursa untuk satu emiten.

    Dipisah dari `hitung` supaya bisa diuji langsung (mis. bukti tidak ada
    look-ahead) dan dipakai ulang untuk debug.

    Setiap hasil: dict dengan kunci
      i, tanggal, sinyal, skor, kelompok_skor,
      return {N: pct|None}, return_ihsg {N: pct|None}, alasan|None
    alasan: None (dipakai) | "tanpa_ihsg" | "seri_pendek" | "close_kosong"
    """
    d = seri.get("d") or []
    c = seri.get("c") or []
    v = seri.get("v") or []
    hz_maks = max(horizon)
    if len(d) != len(c) or len(c) < MIN_SESI:
        yield {"alasan": "seri_pendek"}
        return

    bars = [{"date": d[i], "open": c[i], "high": c[i], "low": c[i],
             "close": c[i], "volume": v[i] if i < len(v) else None}
            for i in range(len(d))]

    # butuh MIN_SESI untuk skor, dan hz_maks sesi ke depan untuk return
    for i in range(MIN_SESI - 1, len(bars) - hz_maks):
        tech = S.score_technical(bars[:i + 1])   # <= i, tanpa look-ahead
        if not tech.get("tersedia"):
            continue
        ind = tech.get("indikator") or {}
        skor = tech.get("skor")
        kel = _kelompok(skor)
        if kel is None:
            continue

        tgl0 = bars[i]["date"]
        close0 = bars[i]["close"]
        if not close0:
            yield {"i": i, "tanggal": tgl0, "alasan": "close_kosong"}
            continue

        # IHSG pada tanggal acuan. Kalau tidak ada, titik ini tidak bisa
        # dihitung excess return -> dibuang (bukan diisi nol).
        ih0 = ihsg.get(tgl0)
        if ih0 is None:
            yield {"i": i, "tanggal": tgl0, "alasan": "tanpa_ihsg"}
            continue

        r_emit: Dict[int, Optional[float]] = {}
        r_ihsg: Dict[int, Optional[float]] = {}
        for N in horizon:
            j = i + N                      # N SESI bursa ke depan
            tglN = bars[j]["date"]
            closeN = bars[j]["close"]
            ihN = ihsg.get(tglN)
            if not closeN or ihN is None:
                r_emit[N] = None
                r_ihsg[N] = None
                continue
            r_emit[N] = round((closeN / close0 - 1) * 100, 6)
            r_ihsg[N] = round((ihN / ih0 - 1) * 100, 6)

        yield {"i": i, "tanggal": tgl0, "alasan": None,
               "sinyal": S.label_sinyal(ind), "skor": skor,
               "kelompok_skor": kel,
               "return": r_emit, "return_ihsg": r_ihsg}


def _statistik(returns: List[float]) -> Dict[str, Any]:
    n = len(returns)
    if not n:
        return {"n": 0, "rata2": None, "median": None, "menang": None,
                "p25": None, "p75": None, "terburuk": None, "terbaik": None}
    menang = sum(1 for r in returns if r > 0)
    return {
        "n": n,
        "rata2": round(sum(returns) / n, 4),
        "median": round(_median(returns), 4),
        "menang": round(menang / n * 100, 2),   # % return positif
        "p25": round(_pct(returns, 0.25), 4),
        "p75": round(_pct(returns, 0.75), 4),
        "terburuk": round(min(returns), 4),
        "terbaik": round(max(returns), 4),
    }


def hitung(hist: Dict[str, Any], ihsg_bars: List[Dict[str, Any]],
           horizon: Tuple[int, ...] = HORIZON) -> Dict[str, Any]:
    """Hitung agregat backtest dari history emiten + bar IHSG.

    Tidak menyentuh jaringan — murni fungsi data, supaya bisa diuji.
    """
    emiten = hist.get("emiten") or {}
    ihsg = _peta_harga_ihsg(ihsg_bars)

    # kumpulan return per (kelompok sinyal, horizon) dan per (kelompok skor, horizon)
    per_sinyal: Dict[str, Dict[int, List[float]]] = {}
    per_sinyal_ihsg: Dict[str, Dict[int, List[float]]] = {}
    per_skor: Dict[str, Dict[int, List[float]]] = {}
    per_skor_ihsg: Dict[str, Dict[int, List[float]]] = {}

    dipakai_emiten = 0
    titik_total = 0
    titik_tanpa_ihsg = 0

    for kode, seri in emiten.items():
        dipakai = False
        for t in iter_titik(seri, ihsg, horizon):
            if t["alasan"] == "tanpa_ihsg":
                titik_tanpa_ihsg += 1
                continue
            if t["alasan"] is not None:
                continue
            titik_total += 1
            dipakai = True
            sisi = t["sinyal"]
            kel = t["kelompok_skor"]
            for N, r in t["return"].items():
                if r is None or t["return_ihsg"].get(N) is None:
                    continue
                per_sinyal.setdefault(sisi, {}).setdefault(N, []).append(r)
                per_sinyal_ihsg.setdefault(sisi, {}).setdefault(N, []).append(
                    t["return_ihsg"][N])
                per_skor.setdefault(kel, {}).setdefault(N, []).append(r)
                per_skor_ihsg.setdefault(kel, {}).setdefault(N, []).append(
                    t["return_ihsg"][N])

        if dipakai:
            dipakai_emiten += 1

    def rangkum(grup: Dict[str, Dict[int, List[float]]],
                grup_ihsg: Dict[str, Dict[int, List[float]]]) -> Dict[str, Any]:
        out: Dict[str, Any] = {}
        for nama in sorted(grup):
            out[nama] = {}
            for N in horizon:
                r = grup.get(nama, {}).get(N, [])
                ri = grup_ihsg.get(nama, {}).get(N, [])
                st = _statistik(r)
                sti = _statistik(ri)
                # excess return = rata2 emiten - rata2 IHSG pada titik yang sama
                if st["n"] and sti["n"] and st["rata2"] is not None \
                        and sti["rata2"] is not None:
                    st["ihsg_rata2"] = sti["rata2"]
                    st["excess"] = round(st["rata2"] - sti["rata2"], 4)
                    st["ihsg_menang"] = sti["menang"]
                else:
                    st["ihsg_rata2"] = None
                    st["excess"] = None
                    st["ihsg_menang"] = None
                out[nama][str(N)] = st
        return out

    return {
        "horizon_sesi": list(horizon),
        "min_sesi": MIN_SESI,
        "kelompok_skor": [{"nama": n, "batas_bawah": lo, "batas_atas": hi,
                           "label": lab} for n, lo, hi, lab in KELOMPOK_SKOR],
        "per_sinyal": rangkum(per_sinyal, per_sinyal_ihsg),
        "per_skor": rangkum(per_skor, per_skor_ihsg),
        "cakupan": {
            "emiten_history": len(emiten),
            "emiten_dipakai": dipakai_emiten,
            "titik_dihitung": titik_total,
            "titik_tanpa_ihsg": titik_tanpa_ihsg,
        },
    }


def build(tulis: bool = True, period_ihsg: str = "2y") -> Dict[str, Any]:
    """Ambil history + IHSG, hitung, tulis data/backtest.json."""
    t0 = time.time()
    if not os.path.exists(HIST):
        return {"ok": False, "error": "data/history.json belum ada"}
    with open(HIST, encoding="utf-8") as f:
        hist = json.load(f)

    r = P.yfinance_history("^JKSE", period=period_ihsg, use_cache=True)
    if not r.get("ok") or not r.get("data"):
        return {"ok": False,
                "error": f"IHSG tidak tersedia: {r.get('error') or 'kosong'}"}
    ihsg_bars = r["data"]

    hasil = hitung(hist, ihsg_bars)
    payload = {
        "versi_skema": 1,
        "metode": ("Return dihitung close->close pada horizon SESI BURSA "
                   "(bukan hari kalender). Excess = return emiten - return "
                   "IHSG pada tanggal yang sama."),
        "pembanding": "IHSG (^JKSE)",
        "sumber_ihsg": r.get("sumber_label") or "yfinance",
        "periode_ihsg": period_ihsg,
        "ihsg_bar": len(ihsg_bars),
        "ihsg_terakhir": ihsg_bars[-1]["date"] if ihsg_bars else None,
        "diperbarui_pada": P.stamp_wib(),
        "catatan_risiko": [
            "Backtest memakai harga historis, bukan jaminan hasil ke depan.",
            "Tidak memperhitungkan biaya transaksi, spread, slippage, atau pajak.",
            "Sinyal dihitung dari data yang sudah tutup; eksekusi nyata bisa beda harga.",
            "Emiten tidak likuid membuat return mudah terdistorsi.",
            "IHSG adalah rata-rata pasar; saham kecil bisa bergerak jauh dari IHSG.",
        ],
        **hasil,
        "durasi_detik": round(time.time() - t0, 2),
        "ok": True,
    }
    if tulis:
        _tulis_atomic(OUT, payload)
    return payload


if __name__ == "__main__":
    p = build()
    if not p.get("ok"):
        print(f"[backtest] GAGAL: {p.get('error')}")
        sys.exit(1)
    c = p["cakupan"]
    print(f"[backtest] {c['emiten_dipakai']}/{c['emiten_history']} emiten, "
          f"{c['titik_dihitung']} titik, IHSG {p['ihsg_bar']} bar "
          f"({p['durasi_detik']}s) -> {OUT}")
    for sisi, per_h in p["per_sinyal"].items():
        for N, st in per_h.items():
            if st["n"]:
                print(f"  {sisi:<24} {N:>2} sesi  n={st['n']:<6} "
                      f"rata2={st['rata2']:+.2f}%  IHSG={st['ihsg_rata2']:+.2f}%  "
                      f"excess={st['excess']:+.2f}%  menang={st['menang']:.0f}%")
