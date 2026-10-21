"""
Mesin skoring IDX 0-100: Teknikal 40% + Fundamental 40% + Katalis 20%.

Prinsip:
  * Setiap komponen mengembalikan `detail` (list dict) supaya dashboard bisa
    menampilkan kenapa sebuah emiten dapat skor segitu — bukan angka telanjang.
  * Kalau data fundamental tidak ada, komponen itu TIDAK diberi nilai netral
    diam-diam: `tersedia: False` + `skor: None`, dan bobotnya dinormalisasi
    ulang. Dengan begitu skor akhir tidak menipu.
  * Filter `waspada` menandai saham illiquid / gorengan; emiten waspada tidak
    boleh masuk Top 10.
"""

from __future__ import annotations

import statistics
from typing import Any, Dict, List, Optional, Tuple


# ───────────────────────── indikator ─────────────────────────

def sma(vals: List[float], n: int) -> Optional[float]:
    if len(vals) < n:
        return None
    return sum(vals[-n:]) / n


def ema_series(vals: List[float], n: int) -> List[float]:
    if not vals:
        return []
    k = 2.0 / (n + 1)
    out = [vals[0]]
    for v in vals[1:]:
        out.append(v * k + out[-1] * (1 - k))
    return out


def rsi(vals: List[float], n: int = 14) -> Optional[float]:
    """RSI Wilder."""
    if len(vals) < n + 1:
        return None
    gains, losses = [], []
    for i in range(1, len(vals)):
        d = vals[i] - vals[i - 1]
        gains.append(max(d, 0.0))
        losses.append(max(-d, 0.0))
    avg_g = sum(gains[:n]) / n
    avg_l = sum(losses[:n]) / n
    for i in range(n, len(gains)):
        avg_g = (avg_g * (n - 1) + gains[i]) / n
        avg_l = (avg_l * (n - 1) + losses[i]) / n
    if avg_l == 0:
        return 100.0
    rs = avg_g / avg_l
    return 100.0 - (100.0 / (1.0 + rs))


def macd(vals: List[float], fast: int = 12, slow: int = 26, sig: int = 9
         ) -> Tuple[Optional[float], Optional[float], Optional[float]]:
    """return (macd_line, signal_line, histogram) terbaru."""
    if len(vals) < slow + sig:
        return None, None, None
    ef = ema_series(vals, fast)
    es = ema_series(vals, slow)
    line = [a - b for a, b in zip(ef, es)]
    sig_line = ema_series(line, sig)
    return line[-1], sig_line[-1], line[-1] - sig_line[-1]


# ───────────────────────── teknikal (40%) ─────────────────────────

def score_technical(bars: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    bars: list {date, open, high, low, close, volume} urut naik.
    Bobot mentah 100, lalu dikali 0.4 di agregator.
    """
    detail: List[Dict[str, Any]] = []
    if not bars or len(bars) < 20:
        return {"tersedia": False, "skor": None, "detail": [
            {"sinyal": "Data harga", "nilai": f"{len(bars) if bars else 0} baris",
             "poin": 0, "aktif": False, "catatan": "butuh minimal 20 sesi"}
        ], "indikator": {}}

    closes = [b["close"] for b in bars]
    # Volume None = tidak dilaporkan sumber. Pakai median volume yang ADA
    # sebagai perkiraan wajar untuk rata-rata, tapi volume hari terakhir
    # dibiarkan None supaya tidak diklaim sebagai nol.
    vols_ada = [b["volume"] for b in bars if b.get("volume") is not None]
    vol_ref = (sorted(vols_ada)[len(vols_ada) // 2]) if vols_ada else None
    vols = [b["volume"] if b.get("volume") is not None else (vol_ref or 0)
            for b in bars]
    vol_dilaporkan = bars[-1].get("volume") is not None
    price = closes[-1]

    ma20 = sma(closes, 20)
    ma50 = sma(closes, 50)
    ma20_prev = sma(closes[:-5], 20) if len(closes) >= 25 else None
    ma50_prev = sma(closes[:-5], 50) if len(closes) >= 55 else None

    rsi_v = rsi(closes, 14)
    macd_line, macd_sig, macd_hist = macd(closes)
    macd_hist_prev = None
    if len(closes) >= 36:
        m_prev, s_prev, _ = macd(closes[:-1])
        if m_prev is not None and s_prev is not None:
            macd_hist_prev = m_prev - s_prev

    vol_avg20 = sum(vols[-20:]) / 20 if len(vols) >= 20 else None
    vol_last = vols[-1] if vols else 0.0
    if not vol_dilaporkan:
        vol_last = None

    # resistance/support = high/low 20 sesi TANPA hari ini
    res20 = max(b["high"] for b in bars[-21:-1]) if len(bars) >= 21 else None
    sup20 = min(b["low"] for b in bars[-21:-1]) if len(bars) >= 21 else None

    pts = 0

    def add(sinyal: str, aktif: bool, poin: int, nilai: str, catatan: str = ""):
        nonlocal pts
        if aktif:
            pts += poin
        detail.append({"sinyal": sinyal, "aktif": bool(aktif),
                       "poin": poin if aktif else 0, "maks": poin,
                       "nilai": nilai, "catatan": catatan})

    # 1. harga > MA20
    ok = ma20 is not None and price > ma20
    add("Harga di atas MA20", ok, 15,
        f"Rp{price:,.0f} vs MA20 Rp{ma20:,.0f}" if ma20 else "MA20 n/a")
    # 2. harga > MA50
    ok = ma50 is not None and price > ma50
    add("Harga di atas MA50", ok, 15,
        f"Rp{price:,.0f} vs MA50 Rp{ma50:,.0f}" if ma50 else "MA50 n/a")

    # 3. MA20 memotong ke atas MA50 (golden cross) — event, bukan state
    cross_now = (ma20 is not None and ma50 is not None and ma20 > ma50)
    cross_recent = False
    if ma20_prev is not None and ma50_prev is not None:
        cross_recent = ma20_prev <= ma50_prev and ma20 > ma50
    if cross_recent:
        add("MA20 potong ke atas MA50 (golden cross)", True, 10,
            "terjadi dalam 5 sesi terakhir")
    elif cross_now:
        add("MA20 di atas MA50", True, 6,
            "kondisi bullish, cross > 5 sesi lalu", "parsial")
    else:
        add("MA20 potong ke atas MA50 (golden cross)", False, 10,
            f"MA20 Rp{ma20:,.0f} ≤ MA50 Rp{ma50:,.0f}" if ma20 and ma50 else "MA n/a")

    # 4. breakout resistance 20 hari + volume > 1.5x rata-rata
    is_break = res20 is not None and price > res20
    vol_surge = bool(vol_avg20 and vol_last is not None
                     and vol_last > 1.5 * vol_avg20)
    if is_break and vol_surge:
        add("Breakout 20-hari + volume >1,5×", True, 20,
            f"tembus Rp{res20:,.0f}, vol {vol_last/vol_avg20:.1f}× avg20")
    elif is_break:
        ratio_txt = (f"vol {vol_last/vol_avg20:.1f}× avg20"
                     if vol_last is not None and vol_avg20 else "volume tidak tersedia")
        add("Breakout 20-hari tanpa volume", True, 10,
            f"tembus Rp{res20:,.0f}, {ratio_txt}",
            "volume kurang")
    else:
        add("Breakout 20-hari + volume >1,5×", False, 20,
            f"resistance Rp{res20:,.0f}" if res20 else "n/a")

    # 5. RSI 50-70 (tandai >75)
    if rsi_v is None:
        add("RSI(14) 50–70", False, 20, "n/a")
    elif 50 <= rsi_v <= 70:
        add("RSI(14) 50–70", True, 20, f"RSI {rsi_v:.1f}", "zona ideal")
    elif 45 <= rsi_v < 50 or 70 < rsi_v <= 75:
        add("RSI(14) 50–70", True, 10, f"RSI {rsi_v:.1f}", "zona marginal (parsial)")
    else:
        note = "jenuh beli >75" if rsi_v > 75 else "lemah <45"
        add("RSI(14) 50–70", False, 20, f"RSI {rsi_v:.1f}", note)

    # 6. MACD golden cross
    if macd_hist is None:
        add("MACD golden cross", False, 20, "n/a")
    else:
        cross = macd_hist > 0 and (macd_hist_prev is None or macd_hist_prev <= 0)
        if cross:
            add("MACD golden cross", True, 20, f"hist {macd_hist:+.1f}", "cross baru")
        elif macd_hist > 0:
            add("MACD histogram positif", True, 12, f"hist {macd_hist:+.1f}",
                "bullish, cross > 1 sesi lalu")
        else:
            add("MACD golden cross", False, 20, f"hist {macd_hist:+.1f}")

    indikator = {
        "harga": round(price, 2),
        "ma20": round(ma20, 2) if ma20 else None,
        "ma50": round(ma50, 2) if ma50 else None,
        "rsi": round(rsi_v, 1) if rsi_v is not None else None,
        "macd_hist": round(macd_hist, 2) if macd_hist is not None else None,
        "volume": vol_last,
        "vol_avg20": round(vol_avg20, 0) if vol_avg20 else None,
        "resistance": round(res20, 2) if res20 else None,
        "support": round(sup20, 2) if sup20 else None,
        "breakout": bool(is_break),
        "support_break": bool(sup20 is not None and price < sup20),
        "vol_surge": vol_surge,
    }
    return {"tersedia": True, "skor": min(pts, 100), "detail": detail,
            "indikator": indikator}


def label_sinyal(ind: Optional[Dict[str, Any]]) -> str:
    """Label sinyal ringkas dari indikator teknikal.

    Dipakai BERSAMA oleh pipeline (label hari ini) dan modul backtest
    (label historis). Ditaruh di sini supaya keduanya tidak pernah
    berbeda definisi — kalau logika label diubah, backtest ikut berubah.
    """
    if not ind:
        # tidak ada baris harga sama sekali -> jangan beri label "Normal"
        return "Data tidak tersedia"
    if ind.get("support_break"):
        return "Tembus Support"
    if ind.get("breakout") and ind.get("vol_surge"):
        return "Breakout"
    if ind.get("breakout"):
        return "Breakout (volume tipis)"
    r = ind.get("rsi")
    if r is not None and r > 75:
        return "Waspada (RSI jenuh)"
    return "Normal"


# ───────────────────────── fundamental (40%) ─────────────────────────

def _band(v: Optional[float], bands: List[Tuple[float, int, str]]
          ) -> Tuple[int, str]:
    """
    bands: [(batas_atas, poin, label)] dievaluasi berurutan.

    Nilai di atas batas terakhir TIDAK boleh dianggap 0 — untuk metrik
    "makin besar makin baik" (growth, ROE) itu justru kondisi terbaik.
    Jadi kalau tidak ada band yang cocok, pakai band terakhir.
    """
    if v is None:
        return 0, "n/a"
    for batas, poin, label in bands:
        if v <= batas:
            return poin, label
    _, poin, label = bands[-1]
    return poin, label


def score_fundamental(f: Optional[Dict[str, Any]],
                      sector_pe: Optional[float] = None,
                      sector_pb: Optional[float] = None) -> Dict[str, Any]:
    """Bobot mentah 100: growth 30, ROE 25, DER 15, PER 15, PBV 15."""
    detail: List[Dict[str, Any]] = []
    if not f:
        return {"tersedia": False, "skor": None, "detail": [
            {"sinyal": "Fundamental", "aktif": False, "poin": 0, "maks": 100,
             "nilai": "data tidak tersedia", "catatan": "sumber gagal"}
        ]}

    # dict yang isinya semua None = tidak ada data sama sekali, bukan
    # "nilai 0". Kalau ikut dihitung, emiten tanpa data akan dapat skor 0 dan
    # tercampur dengan emiten yang benar-benar jelek.
    if all(f.get(k) is None for k in
           ("per", "pbv", "roe", "der", "earnings_growth", "revenue_growth")):
        return {"tersedia": False, "skor": None, "detail": [
            {"sinyal": "Fundamental", "aktif": False, "poin": 0, "maks": 100,
             "nilai": "data tidak tersedia",
             "catatan": "tidak ada metrik yang terbaca dari sumber"}
        ]}

    pts = 0

    def add(sinyal, poin, maks, nilai, catatan=""):
        nonlocal pts
        pts += poin
        detail.append({"sinyal": sinyal, "aktif": poin > 0, "poin": poin,
                       "maks": maks, "nilai": nilai, "catatan": catatan})

    # Growth laba + pendapatan (30)
    eg = f.get("earnings_growth")
    rg = f.get("revenue_growth")
    if eg is not None:
        p, lab = _band(eg, [(-0.99, 0, "turun"), (0.0, 5, "flat"),
                            (0.10, 13, "naik sedang"), (99, 18, "naik kuat")])
        add("Pertumbuhan laba", p, 18, f"{eg*100:+.1f}%", lab)
    else:
        add("Pertumbuhan laba", 0, 18, "n/a")
    if rg is not None:
        p, lab = _band(rg, [(-0.99, 0, "turun"), (0.0, 4, "flat"),
                            (0.08, 8, "naik"), (99, 12, "naik kuat")])
        add("Pertumbuhan pendapatan", p, 12, f"{rg*100:+.1f}%", lab)
    else:
        add("Pertumbuhan pendapatan", 0, 12, "n/a")

    # ROE (25)
    roe = f.get("roe")
    if roe is not None:
        p, lab = _band(roe, [(0.0, 0, "negatif"), (0.08, 10, "cukup"),
                             (0.15, 19, "baik"), (0.20, 25, "sangat baik")])
        add("ROE", p, 25, f"{roe*100:.1f}%", lab)
    else:
        add("ROE", 0, 25, "n/a")

    # DER (15) — makin kecil makin baik
    der = f.get("der")
    if der is not None:
        if der < 0.5:
            add("DER", 15, 15, f"{der:.2f}", "konservatif")
        elif der < 1.0:
            add("DER", 12, 15, f"{der:.2f}", "wajar")
        elif der < 2.0:
            add("DER", 6, 15, f"{der:.2f}", "agak tinggi")
        else:
            add("DER", 0, 15, f"{der:.2f}", "tinggi")
    else:
        add("DER", 0, 15, "n/a")

    # PER vs rata-rata sektor (15)
    per = f.get("per")
    if per is not None and per > 0:
        if sector_pe and sector_pe > 0:
            rel = per / sector_pe
            if rel < 0.8:
                add("PER vs sektor", 15, 15,
                    f"{per:.1f} vs {sector_pe:.1f}", f"{(rel-1)*100:+.0f}% lebih murah")
            elif rel < 1.1:
                add("PER vs sektor", 10, 15,
                    f"{per:.1f} vs {sector_pe:.1f}", "sebanding")
            else:
                add("PER vs sektor", 3, 15,
                    f"{per:.1f} vs {sector_pe:.1f}", f"{(rel-1)*100:+.0f}% lebih mahal")
        else:
            p, lab = _band(per, [(10, 15, "murah"), (20, 10, "wajar"), (99, 3, "mahal")])
            add("PER", p, 15, f"{per:.1f}", lab + " (sektor n/a)")
    else:
        add("PER", 0, 15, "n/a" if per is None else "negatif/rugi")

    # PBV vs rata-rata sektor (15)
    pbv = f.get("pbv")
    if pbv is not None and pbv > 0:
        if sector_pb and sector_pb > 0:
            rel = pbv / sector_pb
            if rel < 0.8:
                add("PBV vs sektor", 15, 15, f"{pbv:.2f} vs {sector_pb:.2f}",
                    f"{(rel-1)*100:+.0f}% lebih murah")
            elif rel < 1.1:
                add("PBV vs sektor", 10, 15, f"{pbv:.2f} vs {sector_pb:.2f}", "sebanding")
            else:
                add("PBV vs sektor", 3, 15, f"{pbv:.2f} vs {sector_pb:.2f}",
                    f"{(rel-1)*100:+.0f}% lebih mahal")
        else:
            p, lab = _band(pbv, [(1.5, 15, "murah"), (3, 9, "wajar"), (99, 3, "mahal")])
            add("PBV", p, 15, f"{pbv:.2f}", lab + " (sektor n/a)")
    else:
        add("PBV", 0, 15, "n/a" if pbv is None else "negatif")

    return {"tersedia": True, "skor": min(pts, 100), "detail": detail}


# ───────────────────────── katalis (20%) ─────────────────────────

def score_catalyst(news: Optional[List[Dict[str, Any]]] = None,
                   corporate_actions: Optional[List[Dict[str, Any]]] = None,
                   foreign_net: Optional[float] = None,
                   days_window: int = 14) -> Dict[str, Any]:
    """
    Bobot mentah 100: berita 40, aksi korporasi 30, net asing 30.
    Kalau semua tidak tersedia -> tersedia False (bukan nilai 50 netral).
    """
    from datetime import datetime, timedelta, timezone
    WIB = timezone(timedelta(hours=7))
    detail: List[Dict[str, Any]] = []
    pts = 0
    ada_data = False

    # Berita
    if news is None:
        detail.append({"sinyal": "Berita terbaru", "aktif": False, "poin": 0,
                       "maks": 40, "nilai": "data tidak tersedia", "catatan": ""})
    else:
        ada_data = True
        cut = (datetime.now(WIB) - timedelta(days=days_window)).strftime("%Y-%m-%d")
        recent = [n for n in news if (n.get("date") or "") >= cut]
        if recent:
            poin = min(40, 12 + 7 * len(recent))
            pts += poin
            detail.append({"sinyal": "Berita terbaru", "aktif": True, "poin": poin,
                           "maks": 40, "nilai": f"{len(recent)} berita ≤{days_window} hari",
                           "catatan": (recent[0].get("title") or "")[:90]})
        else:
            detail.append({"sinyal": "Berita terbaru", "aktif": False, "poin": 0,
                           "maks": 40, "nilai": "tidak ada berita baru", "catatan": ""})

    # Aksi korporasi
    if corporate_actions is None:
        detail.append({"sinyal": "Aksi korporasi", "aktif": False, "poin": 0,
                       "maks": 30, "nilai": "data tidak tersedia", "catatan": ""})
    else:
        ada_data = True
        cut = (datetime.now(WIB) - timedelta(days=60)).strftime("%Y-%m-%d")
        recent = [c for c in corporate_actions if (str(c.get("date") or "")) >= cut]
        if recent:
            pts += 30
            detail.append({"sinyal": "Aksi korporasi", "aktif": True, "poin": 30,
                           "maks": 30, "nilai": f"{len(recent)} aksi ≤60 hari",
                           "catatan": str(recent[0].get("type") or "")[:80]})
        else:
            detail.append({"sinyal": "Aksi korporasi", "aktif": False, "poin": 0,
                           "maks": 30, "nilai": "tidak ada", "catatan": ""})

    # Net asing / broker
    if foreign_net is None:
        detail.append({"sinyal": "Net beli asing/broker", "aktif": False, "poin": 0,
                       "maks": 30, "nilai": "data tidak tersedia", "catatan": ""})
    else:
        ada_data = True
        if foreign_net > 0:
            pts += 30
            detail.append({"sinyal": "Net beli asing/broker", "aktif": True, "poin": 30,
                           "maks": 30, "nilai": f"net beli Rp{foreign_net/1e9:,.1f} M",
                           "catatan": ""})
        else:
            detail.append({"sinyal": "Net beli asing/broker", "aktif": False, "poin": 0,
                           "maks": 30, "nilai": f"net jual Rp{abs(foreign_net)/1e9:,.1f} M",
                           "catatan": ""})

    if not ada_data:
        return {"tersedia": False, "skor": None, "detail": detail}
    return {"tersedia": True, "skor": min(pts, 100), "detail": detail}


# ───────────────────────── filter waspada ─────────────────────────

def risk_flags(ind: Dict[str, Any], price: Optional[float],
               chg_pct: Optional[float], news_count: int,
               fund: Optional[Dict[str, Any]] = None) -> List[str]:
    """
    Ciri saham tidak sehat / gorengan. Emiten dengan flag TIDAK masuk Top 10.
    """
    flags: List[str] = []
    vol = ind.get("volume")
    vol_avg = ind.get("vol_avg20") or 0
    rsi_v = ind.get("rsi")

    # Volume tidak dilaporkan sumber: jangan tuduh "volume kecil" — itu
    # ketiadaan data, bukan bukti tidak likuid.
    if vol is None:
        flags.append("volume tidak tersedia")
    else:
        if vol < 100_000:
            flags.append("volume sangat kecil (<100rb)")
        if vol_avg and vol < 0.15 * vol_avg:
            flags.append("volume anjlok vs rata-rata")
    if price is not None and price < 50:
        flags.append("penny stock (<Rp50)")
    if rsi_v is not None and rsi_v > 75:
        flags.append(f"RSI jenuh beli ({rsi_v:.0f})")
    if ind.get("breakout") and not ind.get("vol_surge"):
        flags.append("breakout tanpa volume")
    if ind.get("support_break"):
        flags.append("tembus support 20-hari")

    # lonjakan harga tanpa berita = ciri pump
    if chg_pct is not None and abs(chg_pct) >= 15 and news_count == 0:
        flags.append(f"lonjakan {chg_pct:+.0f}% tanpa berita")
    # fundamental rusak berat
    if fund:
        roe = fund.get("roe")
        if roe is not None and roe < -0.05:
            flags.append("ROE negatif")
        if fund.get("per") is not None and fund.get("per") < 0:
            flags.append("PER negatif (rugi)")
    return flags


# ───────────────────────── agregator ─────────────────────────

W_TECH, W_FUND, W_CAT = 0.40, 0.40, 0.20


def combine(tech: Dict[str, Any], fund: Dict[str, Any],
            cat: Dict[str, Any]) -> Dict[str, Any]:
    """
    Gabung tiga komponen dengan normalisasi bobot: komponen yang datanya
    tidak tersedia dibuang, sisa bobot diskalakan ulang supaya total tetap 1.
    Skor akhir tetap 0-100 dan jujur soal berapa bobot yang benar-benar terisi.
    """
    parts = [("teknikal", W_TECH, tech), ("fundamental", W_FUND, fund),
             ("katalis", W_CAT, cat)]
    aktif = [(n, w, c) for n, w, c in parts if c.get("tersedia") and c.get("skor") is not None]
    total_w = sum(w for _, w, _ in aktif)

    if not aktif or total_w <= 0:
        # Bentuk "komponen" harus sama dengan cabang normal (dict of dict),
        # supaya konsumen (dashboard/snapshot) tidak perlu cek bentuk.
        return {"skor": None, "bobot_terisi": 0.0,
                "komponen": {n: {"skor": None, "bobot": w, "bobot_efektif": 0.0,
                                "kontribusi": 0.0, "tersedia": False,
                                "detail": c.get("detail", [])}
                            for n, w, c in parts},
                "catatan": "tidak ada komponen yang bisa dinilai"}

    skor = sum(c["skor"] * w for _, w, c in aktif) / total_w

    rincian = {}
    for n, w, c in parts:
        rincian[n] = {
            "skor": c.get("skor"),
            "bobot": w,
            "bobot_efektif": (w / total_w) if any(n == a[0] for a in aktif) else 0.0,
            "kontribusi": round((c["skor"] * w / total_w), 1) if any(n == a[0] for a in aktif) else 0.0,
            "tersedia": bool(c.get("tersedia")),
            "detail": c.get("detail", []),
        }

    return {
        "skor": round(skor, 1),
        "bobot_terisi": round(total_w, 2),
        "komponen": rincian,
        "catatan": None if total_w >= 0.99 else f"bobot terisi {total_w:.0%} (sebagian data tidak tersedia)",
    }


def sector_medians(rows: List[Dict[str, Any]]
                   ) -> Tuple[Dict[str, float], Dict[str, float]]:
    """Median PER & PBV per sektor, untuk pembanding relatif.

    Menerima DUA bentuk baris yang dipakai di proyek ini:
      - flat    : {"sektor": ..., "per": ..., "pbv": ...}   (rows pipeline)
      - bersarang: {"sektor": ..., "fundamental": {"per": ...}}
    Sebelumnya hanya bentuk bersarang yang dibaca, sementara pipeline
    mengirim bentuk flat -> median SELALU kosong tanpa error.
    """
    pe_map: Dict[str, List[float]] = {}
    pb_map: Dict[str, List[float]] = {}
    for r in rows:
        f = r.get("fundamental") or {}
        sec = (r.get("sektor") or "LAINNYA").upper()
        pe = r.get("per")
        if pe is None:
            pe = f.get("per")
        pb = r.get("pbv")
        if pb is None:
            pb = f.get("pbv")
        if isinstance(pe, bool):
            pe = None
        if isinstance(pb, bool):
            pb = None
        if isinstance(pe, (int, float)) and pe > 0:
            pe_map.setdefault(sec, []).append(float(pe))
        if isinstance(pb, (int, float)) and pb > 0:
            pb_map.setdefault(sec, []).append(float(pb))
    pe_med = {k: statistics.median(v) for k, v in pe_map.items() if len(v) >= 3}
    pb_med = {k: statistics.median(v) for k, v in pb_map.items() if len(v) >= 3}
    return pe_med, pb_med
