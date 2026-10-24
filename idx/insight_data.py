"""
Modul data pendukung untuk Tab Insight (TAHAP I1).
- History panjang 3-5 tahun via yfinance (fallback, Sectors.app tidak punya history panjang)
- Breadth harian: naik/turun/tetap, % > MA50/MA200, rasio volume vs rata2 20s
- Rotasi sektor: return 5 & 20 sesi vs IHSG per sektor
- Semua disimpan terstruktur di data/insight.json + data/insight_history.json
"""

from __future__ import annotations

import json
import os
import statistics
import sys
import time
import concurrent.futures
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

# -----------------------------------------------------------------------------
# Konfigurasi
# -----------------------------------------------------------------------------
REPO = "/root/idx-dashboard"
DATA_DIR = os.path.join(REPO, "data")
INSIGHT_FILE = os.path.join(DATA_DIR, "insight.json")
INSIGHT_HISTORY_FILE = os.path.join(DATA_DIR, "insight_history.json")
WIB = timezone(timedelta(hours=7))

# Parameter yfinance
YF_PERIOD = "5y"          # ambil 5 tahun
YF_INTERVAL = "1d"
MAX_WORKERS = 8           # parallel download
REQUEST_DELAY = 0.15      # jeda antar request (detik)
MAX_RETRY = 3
BASE_BACKOFF = 1.5

# Ambang minimum bar untuk dihitung
MIN_BARS_BREADTH = 20     # butuh minimal 20 bar untuk MA20/MA50
MIN_BARS_ROTATION = 25    # butuh minimal 25 bar untuk return 20s

# -----------------------------------------------------------------------------
# Util
# -----------------------------------------------------------------------------
def now_wib() -> datetime:
    return datetime.now(WIB)


def stamp_wib(dt: Optional[datetime] = None) -> str:
    dt = dt or now_wib()
    return dt.isoformat()


def _tulis_atomic(path: str, payload: Any) -> None:
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


# -----------------------------------------------------------------------------
# yfinance helper (dengan retry + delay)
# -----------------------------------------------------------------------------
_last_yf_request = 0.0


def _yf_rate_limit() -> None:
    global _last_yf_request
    elapsed = time.time() - _last_yf_request
    if elapsed < REQUEST_DELAY:
        time.sleep(REQUEST_DELAY - elapsed)
    _last_yf_request = time.time()


def _yf_history(symbol: str, period: str = YF_PERIOD) -> Optional[List[Dict[str, Any]]]:
    """
    Ambil history OHLCV dari yfinance.
    Return: list of dict {d: 'YYYY-MM-DD', o, h, l, c, v} atau None kalau gagal.
    """
    import yfinance as yf

    for attempt in range(1, MAX_RETRY + 1):
        try:
            _yf_rate_limit()
            tk = yf.Ticker(symbol)
            df = tk.history(period=period, interval=YF_INTERVAL, auto_adjust=True)
            if df is None or len(df) == 0:
                return None
            df = df.reset_index()
            # kolom: Date, Open, High, Low, Close, Volume, Dividends, Stock Splits
            bars = []
            for _, row in df.iterrows():
                d = row["Date"]
                # Handle both Timestamp and ndarray
                if isinstance(d, str):
                    d_str = d[:10]
                elif hasattr(d, "strftime"):  # Timestamp
                    d_str = d.strftime("%Y-%m-%d")  # type: ignore[attr-defined]
                elif hasattr(d, "item"):  # numpy scalar
                    d_str = d.item().strftime("%Y-%m-%d")
                else:
                    d_str = str(d)[:10]
                # row values can be scalar or numpy types
                def to_float(x):
                    if x is None:
                        return None
                    if hasattr(x, "item"):  # numpy scalar
                        x = x.item()
                    if x == 0:
                        return None
                    return float(x)
                bars.append({
                    "d": d_str,
                    "o": to_float(row.get("Open")),
                    "h": to_float(row.get("High")),
                    "l": to_float(row.get("Low")),
                    "c": to_float(row.get("Close")),
                    "v": to_float(row.get("Volume")),
                })
            return bars
        except Exception as e:
            if attempt == MAX_RETRY:
                return None
            time.sleep(BASE_BACKOFF ** attempt)
    return None


# -----------------------------------------------------------------------------
# Hitung MA, return, breadth per emiten
# -----------------------------------------------------------------------------
def _ma(values: List[Optional[float]], window: int) -> List[Optional[float]]:
    """Moving average sederhana, return list sama panjang, None di awal."""
    out = []
    for i in range(len(values)):
        if i < window - 1:
            out.append(None)
        else:
            window_vals = [v for v in values[i - window + 1:i + 1] if v is not None]
            out.append(sum(window_vals) / len(window_vals) if window_vals else None)
    return out


def _pct_change(current: Optional[float], past: Optional[float]) -> Optional[float]:
    if current is None or past is None or past == 0:
        return None
    return (current / past - 1) * 100


def compute_emas_from_bars(
    bars: List[Dict[str, Any]],
    ma_windows: Tuple[int, ...] = (20, 50, 200),
) -> Dict[str, Any]:
    """
    Dari bars mentah, hitung MA, return, volume ratio.
    Return dict berisi seri per field (untuk disimpan efisien).
    """
    if not bars or len(bars) < MIN_BARS_BREADTH:
        return {"ok": False, "alasan": "bars terlalu sedikit"}

    dates = [b["d"] for b in bars]
    closes = [b["c"] for b in bars]
    volumes = [b["v"] for b in bars]

    # MA
    mas = {}
    for w in ma_windows:
        mas[f"ma{w}"] = _ma(closes, w)

    # Return 5 & 20 sesi (close sekarang / close N sesi lalu - 1) * 100
    ret5 = [_pct_change(closes[i], closes[i - 5]) if i >= 5 else None for i in range(len(closes))]
    ret20 = [_pct_change(closes[i], closes[i - 20]) if i >= 20 else None for i in range(len(closes))]

    # Volume ratio vs rata2 20 sesi
    vol_ma20 = _ma(volumes, 20)
    vol_ratio = []
    for i in range(len(volumes)):
        vm = vol_ma20[i]
        vi = volumes[i]
        if vm is not None and vm > 0 and vi is not None:
            vol_ratio.append(vi / vm)
        else:
            vol_ratio.append(None)

    return {
        "ok": True,
        "dates": dates,
        "closes": closes,
        "volumes": volumes,
        "mas": mas,
        "ret5": ret5,
        "ret20": ret20,
        "vol_ratio": vol_ratio,
    }


def compute_breadth_snapshot(
    emas_data: Dict[str, Dict[str, Any]],
    ihsg_data: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Hitung breadth untuk tanggal terbaru yang umum (latest common date).
    emas_data: {kode: {mas: {ma20:[...], ma50:[...], ma200:[...]}, closes:[...], volumes:[...], ...}}
    """
    # Cari tanggal terbaru yang ada di SEMUA emiten (atau mayoritas)
    # Pakai approach: ambil latest date per emiten, lalu pilih yang paling sering muncul
    date_counts: Dict[str, int] = {}
    for kode, ed in emas_data.items():
        if not ed.get("ok") or not ed.get("dates"):
            continue
        latest_d = ed["dates"][-1]
        date_counts[latest_d] = date_counts.get(latest_d, 0) + 1

    if not date_counts:
        return {"ok": False, "alasan": "tidak ada data tanggal"}

    # Tanggal dengan count tertinggi (biasanya hari ini / kemarin)
    target_date = max(date_counts.items(), key=lambda x: x[1])[0]
    target_count = date_counts[target_date]

    # Hitung breadth pada target_date
    naik = turun = tetap = 0
    above_ma50 = above_ma200 = 0
    total_ma50 = total_ma200 = 0
    vol_ratios = []

    for kode, ed in emas_data.items():
        if not ed.get("ok"):
            continue
        dates = ed["dates"]
        try:
            idx = dates.index(target_date)
        except ValueError:
            continue

        closes = ed["closes"]
        mas = ed.get("mas", {})
        ma50 = mas.get("ma50", [])
        ma200 = mas.get("ma200", [])
        vol_ratio = ed.get("vol_ratio", [])

        c = closes[idx]
        if c is None:
            continue

        # Naik/turun/tetap vs hari sebelumnya
        if idx > 0 and closes[idx - 1] is not None:
            prev = closes[idx - 1]
            if c > prev:
                naik += 1
            elif c < prev:
                turun += 1
            else:
                tetap += 1

        # Above MA50/MA200
        if idx < len(ma50) and ma50[idx] is not None:
            total_ma50 += 1
            if c > ma50[idx]:
                above_ma50 += 1
        if idx < len(ma200) and ma200[idx] is not None:
            total_ma200 += 1
            if c > ma200[idx]:
                above_ma200 += 1

        # Volume ratio
        if idx < len(vol_ratio) and vol_ratio[idx] is not None:
            vol_ratios.append(vol_ratio[idx])

    total = naik + turun + tetap
    median_vol_ratio = None
    if vol_ratios:
        vol_ratios.sort()
        median_vol_ratio = vol_ratios[len(vol_ratios) // 2]

    return {
        "ok": True,
        "tanggal": target_date,
        "emiten_tercakup": target_count,
        "total_emiten": total,
        "naik": naik,
        "turun": turun,
        "tetap": tetap,
        "pct_naik": round(naik / total * 100, 2) if total else None,
        "above_ma50": above_ma50,
        "total_ma50": total_ma50,
        "pct_above_ma50": round(above_ma50 / total_ma50 * 100, 2) if total_ma50 else None,
        "above_ma200": above_ma200,
        "total_ma200": total_ma200,
        "pct_above_ma200": round(above_ma200 / total_ma200 * 100, 2) if total_ma200 else None,
        "median_volume_ratio": round(median_vol_ratio, 3) if median_vol_ratio else None,
        "ihsg_close": ihsg_data["closes"][-1] if ihsg_data and ihsg_data.get("closes") else None,
        "ihsg_chg_pct": ihsg_data["ret5"][-1] if ihsg_data and ihsg_data.get("ret5") else None,
    }


def compute_sector_rotation(
    emas_data: Dict[str, Dict[str, Any]],
    latest_rows: List[Dict[str, Any]],
    ihsg_ret5: Optional[float],
    ihsg_ret20: Optional[float],
) -> List[Dict[str, Any]]:
    """
    Hitung return 5s & 20s per sektor vs IHSG.
    Return list of {sektor, jumlah_emiten, ret5, ret20, excess5, excess20}
    """
    # Kumpulkan emiten per sektor
    sektor_emiten: Dict[str, List[str]] = {}
    for r in latest_rows:
        sec = (r.get("sektor") or "LAINNYA").upper()
        kode = r.get("kode")
        if kode and kode in emas_data:
            sektor_emiten.setdefault(sec, []).append(kode)

    out = []
    for sec, kodes in sektor_emiten.items():
        if len(kodes) < 3:  # minimal 3 emiten untuk representatif
            continue

        ret5_vals = []
        ret20_vals = []
        for kode in kodes:
            ed = emas_data[kode]
            if not ed.get("ok"):
                continue
            # Ambil latest valid ret5/ret20
            for val in reversed(ed.get("ret5", [])):
                if val is not None:
                    ret5_vals.append(val)
                    break
            for val in reversed(ed.get("ret20", [])):
                if val is not None:
                    ret20_vals.append(val)
                    break

        if not ret5_vals or not ret20_vals:
            continue

        # Median return sektor (statistics.median: benar untuk n genap)
        sec_ret5 = statistics.median(ret5_vals)
        sec_ret20 = statistics.median(ret20_vals)
        diam20 = sum(1 for v in ret20_vals if v == 0.0)

        out.append({
            "sektor": sec,
            "jumlah_emiten": len(kodes),
            "jumlah_sampel": len(ret20_vals),
            "jumlah_diam20": diam20,
            "ret5": round(sec_ret5, 2),
            "ret20": round(sec_ret20, 2),
            "excess5": round(sec_ret5 - ihsg_ret5, 2) if ihsg_ret5 is not None else None,
            "excess20": round(sec_ret20 - ihsg_ret20, 2) if ihsg_ret20 is not None else None,
        })

    # Urutkan: excess20 desc (sektor paling outperform 20s di atas)
    out.sort(key=lambda x: (x["excess20"] is None, -(x["excess20"] or -999)))
    return out


# -----------------------------------------------------------------------------
# Fungsi utama: build insight data
# -----------------------------------------------------------------------------
def build_insight(
    universe: List[Dict[str, Any]],
    latest_rows: List[Dict[str, Any]],
    force_refresh: bool = False,
    out_file: Optional[str] = None,
    history_file: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Bangun data insight dari universe.
    - Download history 5y untuk semua emiten (parallel, pakai cache insight_history.json)
    - Hitung breadth, rotasi sektor
    - Simpan ke insight.json + insight_history.json

    out_file / history_file: override path output (dipakai smoke test agar tidak
    menimpa artifact produksi).
    """
    out_path = out_file or INSIGHT_FILE
    hist_path = history_file or INSIGHT_HISTORY_FILE
    print(f"[insight] Mulai build: {len(universe)} emiten", flush=True)
    t0 = time.time()

    # Load cache history lama
    cache: Dict[str, Any] = {}
    if not force_refresh and os.path.exists(hist_path):
        try:
            with open(hist_path, "r", encoding="utf-8") as f:
                cache = json.load(f)
            print(f"[insight] Cache dimuat: {len(cache.get('emiten', {}))} emiten", flush=True)
        except Exception as e:
            print(f"[insight] Cache gagal dibaca: {e}", flush=True)
            cache = {}

    # Identifikasi emiten yang perlu di-fetch
    to_fetch = []
    for item in universe:
        kode = item["kode"]
        sym = f"{kode}.JK"
        cached = cache.get("emiten", {}).get(kode)
        if cached and cached.get("ok") and len(cached.get("dates", [])) >= 200:
            continue  # sudah cukup panjang
        to_fetch.append((kode, sym))

    print(f"[insight] Perlu fetch: {len(to_fetch)} / {len(universe)}", flush=True)

    # Parallel fetch
    new_data = {}
    done = 0
    with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
        fut = {ex.submit(_yf_history, sym): (kode, sym) for kode, sym in to_fetch}
        for f in concurrent.futures.as_completed(fut):
            kode, sym = fut[f]
            try:
                bars = f.result()
                if bars:
                    emas = compute_emas_from_bars(bars)
                    if emas.get("ok"):
                        new_data[kode] = emas
            except Exception as e:
                print(f"[insight] {kode} error: {e}", flush=True)
            done += 1
            if done % 50 == 0:
                print(f"[insight] Progress: {done}/{len(to_fetch)}", flush=True)

    # Gabungkan dengan cache
    all_emas = {}
    for kode, ed in cache.get("emiten", {}).items():
        if ed.get("ok"):
            all_emas[kode] = ed
    for kode, ed in new_data.items():
        all_emas[kode] = ed

    print(f"[insight] Total emas siap: {len(all_emas)}", flush=True)

    # IHSG
    ihsg_cache = cache.get("emiten", {}).get("^JKSE") or cache.get("emiten", {}).get("IHSG")
    ihsg_emas = None
    if ihsg_cache and ihsg_cache.get("ok"):
        ihsg_emas = ihsg_cache
    else:
        print("[insight] Fetch IHSG...", flush=True)
        ihsg_bars = _yf_history("^JKSE")
        if ihsg_bars:
            ihsg_emas = compute_emas_from_bars(ihsg_bars)
            if ihsg_emas.get("ok"):
                all_emas["^JKSE"] = ihsg_emas

    # Breadth snapshot
    breadth = compute_breadth_snapshot(all_emas, ihsg_emas)
    print(f"[insight] Breadth: {breadth.get('tanggal')}, naik={breadth.get('naik')}, turun={breadth.get('turun')}, >MA50={breadth.get('pct_above_ma50')}%", flush=True)

    # Rotasi sektor
    ihsg_ret5 = ihsg_emas["ret5"][-1] if ihsg_emas and ihsg_emas.get("ret5") else None
    ihsg_ret20 = ihsg_emas["ret20"][-1] if ihsg_emas and ihsg_emas.get("ret20") else None
    rotation = compute_sector_rotation(all_emas, latest_rows, ihsg_ret5, ihsg_ret20)
    print(f"[insight] Rotasi sektor: {len(rotation)} sektor", flush=True)

    # Simpan insight_history.json (cache untuk run berikutnya)
    history_payload = {
        "versi_skema": "1.0.0",
        "diperbarui_pada": stamp_wib(),
        "emiten": all_emas,
    }
    _tulis_atomic(hist_path, history_payload)
    print(f"[insight] Cache disimpan: {hist_path}", flush=True)

    # Insight.json (payload ringkas untuk dashboard)
    # Hitung cakupan hanya untuk emiten universe (bukan IHSG)
    emiten_berhistory = sum(1 for u in universe if u["kode"] in all_emas)
    emiten_bolong = len(universe) - emiten_berhistory
    insight_payload = {
        "versi_skema": "1.0.0",
        "diperbarui_pada": stamp_wib(),
        "sumber": "yfinance (5y, 1d)",
        "cakupan": {
            "total_universe": len(universe),
            "emiten_berhistory": emiten_berhistory,
            "emiten_fetch_baru": len(new_data),
            "emiten_bolong": emiten_bolong,
        },
        "breadth": breadth,
        "rotasi_sektor": rotation,
        "ihsg": {
            "ret5": round(ihsg_ret5, 2) if ihsg_ret5 is not None else None,
            "ret20": round(ihsg_ret20, 2) if ihsg_ret20 is not None else None,
            "close": ihsg_emas["closes"][-1] if ihsg_emas and ihsg_emas.get("closes") else None,
        },
    }
    _tulis_atomic(out_path, insight_payload)
    print(f"[insight] Insight disimpan: {out_path} ({time.time() - t0:.1f}s)", flush=True)

    return insight_payload


# -----------------------------------------------------------------------------
# Load / merge history
# -----------------------------------------------------------------------------
def load_insight() -> Optional[Dict[str, Any]]:
    if not os.path.exists(INSIGHT_FILE):
        return None
    try:
        with open(INSIGHT_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def load_insight_history() -> Optional[Dict[str, Any]]:
    if not os.path.exists(INSIGHT_HISTORY_FILE):
        return None
    try:
        with open(INSIGHT_HISTORY_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


# -----------------------------------------------------------------------------
# CLI test
# -----------------------------------------------------------------------------
if __name__ == "__main__":
    sys.path.insert(0, REPO)
    from idx import pipeline as PL

    universe = PL.load_universe() or []
    latest = json.load(open(os.path.join(DATA_DIR, "latest.json")))
    latest_rows = latest.get("emiten", [])

    # Argumen: --test N  -> hanya N emiten (smoke test, JANGAN menimpa insight.json)
    #           (default) -> build penuh atas seluruh universe
    argv = sys.argv[1:]
    test_n = None
    force = "--force" in argv
    if "--test" in argv:
        i = argv.index("--test")
        test_n = int(argv[i + 1]) if i + 1 < len(argv) else 5

    if test_n is not None:
        # Smoke test: SEMUA output diarahkan ke file uji terpisah supaya
        # tidak menimpa artifact produksi (insight.json) MAUPUN cache history.
        res = build_insight(
            universe[:test_n], latest_rows, force_refresh=True,
            out_file=os.path.join(DATA_DIR, "insight_uji.json"),
            history_file=os.path.join(DATA_DIR, "insight_history_uji.json"),
        )
        print(json.dumps(res, ensure_ascii=False, indent=2)[:2000])
    else:
        res = build_insight(universe, latest_rows, force_refresh=force)
        print(f"[insight] selesai: {res['breadth'].get('total_emiten')} emiten, "
              f"{len(res.get('rotasi_sektor') or [])} sektor")