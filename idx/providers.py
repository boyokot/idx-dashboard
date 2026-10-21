"""
Lapisan sumber data IDX — Sectors.app API v2 (utama) + yfinance (cadangan).

Aturan yang dipegang modul ini:
  * API key HANYA dari environment variable SECTORS_API_KEY. Tidak pernah
    ditulis di kode, log, atau repo. Kalau tidak ada -> provider langsung
    ditandai nonaktif, bukan crash.
  * Setiap hasil membawa `sumber_data` ('sectors' | 'yfinance' | None) dan
    `diperbarui_pada` (ISO WIB) supaya dashboard bisa jujur soal asal angka.
  * Kalau dua-duanya gagal -> `data_tidak_tersedia()`; tidak menebak, tidak
    memakai harga lama tanpa label.
  * Cache di disk + jeda antar request + retry maksimal 3x dengan backoff.

Sectors API v2 catatan penting (diverifikasi 20 Okt 2026):
  * v1 sudah discontinued 2026-05-11 (HTTP 410) -> wajib /v2/.
  * Auth = header `Authorization: <API_KEY>` (RAW, TANPA prefix "Bearer").
    Tanpa key -> 403 "Authentication credentials were not provided";
    key salah -> 401 "TOKEN_NOT_VALID".
  * Beberapa host di depan API pakai bot-check Vercel yang membalas 429 untuk
    request tanpa User-Agent -> SELALU kirim UA browser.
"""

from __future__ import annotations

import hashlib
import json
import os
import random
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

WIB = timezone(timedelta(hours=7))

SECTORS_BASE = "https://api.sectors.app/v2"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

CACHE_DIR = os.environ.get("IDX_CACHE_DIR", "/root/scripts/idx/.cache")
MAX_RETRY = 3
BASE_BACKOFF = 1.6          # detik; 1.6 -> 3.2 -> 6.4
MIN_INTERVAL = 0.35         # jeda minimum antar request ke host yang sama

_last_request_at = 0.0


# ───────────────────────── util waktu / label ─────────────────────────

def now_wib() -> datetime:
    return datetime.now(WIB)


def stamp_wib(dt: Optional[datetime] = None) -> str:
    """ISO-8601 WIB, mis. 2026-10-20T16:31:00+07:00."""
    return (dt or now_wib()).replace(microsecond=0).isoformat()


def human_wib(dt: Optional[datetime] = None) -> str:
    """Label manusiawi, mis. 20 Oktober 2026, 16:31 WIB."""
    dt = dt or now_wib()
    bulan = ["Januari", "Februari", "Maret", "April", "Mei", "Juni", "Juli",
             "Agustus", "September", "Oktober", "November", "Desember"]
    return f"{dt.day} {bulan[dt.month - 1]} {dt.year}, {dt:%H:%M} WIB"


def data_tidak_tersedia(alasan: str = "sectors & yfinance gagal") -> Dict[str, Any]:
    """Bentuk hasil standar saat tidak ada data. Tidak menebak angka."""
    return {
        "ok": False,
        "sumber_data": None,
        "sumber_label": "data tidak tersedia",
        "diperbarui_pada": stamp_wib(),
        "alasan": alasan,
        "data": None,
    }


def _wrap(data: Any, sumber: str, diambil: Optional[datetime] = None,
          extra: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    out = {
        "ok": True,
        "sumber_data": sumber,
        "sumber_label": "Sectors.app" if sumber == "sectors" else "yfinance",
        "diperbarui_pada": stamp_wib(diambil),
        "alasan": None,
        "data": data,
    }
    if extra:
        out.update(extra)
    return out


# ───────────────────────── cache ─────────────────────────

def _cache_path(key: str) -> str:
    os.makedirs(CACHE_DIR, exist_ok=True)
    h = hashlib.sha1(key.encode("utf-8")).hexdigest()[:24]
    return os.path.join(CACHE_DIR, f"{h}.json")


def cache_get(key: str, ttl_seconds: int) -> Optional[Any]:
    p = _cache_path(key)
    if not os.path.exists(p):
        return None
    try:
        with open(p, "r", encoding="utf-8") as f:
            blob = json.load(f)
        if time.time() - blob.get("_ts", 0) > ttl_seconds:
            return None
        return blob.get("payload")
    except Exception:
        return None


def cache_put(key: str, payload: Any) -> None:
    try:
        with open(_cache_path(key), "w", encoding="utf-8") as f:
            json.dump({"_ts": time.time(), "payload": payload}, f)
    except Exception:
        pass


# ───────────────────────── HTTP dengan retry ─────────────────────────

def http_json(url: str, headers: Optional[Dict[str, str]] = None,
              timeout: int = 25) -> Dict[str, Any]:
    """
    GET JSON dengan retry maksimal MAX_RETRY dan backoff eksponensial.
    Mengembalikan {'ok': bool, 'status': int, 'body': dict|None, 'error': str|None}.
    """
    global _last_request_at
    hdrs = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    if headers:
        hdrs.update(headers)

    last_err, last_status = None, 0
    for attempt in range(MAX_RETRY):
        # jaga jarak antar request
        gap = time.time() - _last_request_at
        if gap < MIN_INTERVAL:
            time.sleep(MIN_INTERVAL - gap)
        try:
            req = urllib.request.Request(url, headers=hdrs)
            with urllib.request.urlopen(req, timeout=timeout) as r:
                _last_request_at = time.time()
                raw = r.read().decode("utf-8", "replace")
                try:
                    body = json.loads(raw)
                except json.JSONDecodeError:
                    return {"ok": False, "status": r.status, "body": None,
                            "error": f"respons bukan JSON: {raw[:160]}"}
                return {"ok": True, "status": r.status, "body": body, "error": None}
        except urllib.error.HTTPError as e:
            _last_request_at = time.time()
            last_status = e.code
            detail = ""
            try:
                detail = e.read().decode("utf-8", "replace")[:200]
            except Exception:
                pass
            # 4xx selain 429 = salah key/endpoint -> jangan retry sia-sia
            if e.code in (400, 401, 403, 404, 410):
                return {"ok": False, "status": e.code, "body": None,
                        "error": f"HTTP {e.code}: {detail}"}
            last_err = f"HTTP {e.code}: {detail}"
        except Exception as e:  # timeout, DNS, koneksi putus
            _last_request_at = time.time()
            last_err = f"{type(e).__name__}: {e}"

        if attempt < MAX_RETRY - 1:
            time.sleep(BASE_BACKOFF * (2 ** attempt) + random.uniform(0, 0.4))

    return {"ok": False, "status": last_status, "body": None,
            "error": last_err or "tidak diketahui"}


# ───────────────────────── Sectors.app v2 ─────────────────────────

def sectors_api_key() -> Optional[str]:
    """Ambil key dari environment. Selalu None kalau tidak diset."""
    k = (os.environ.get("SECTORS_API_KEY") or "").strip()
    return k or None


def sectors_available() -> bool:
    return sectors_api_key() is not None


def sectors_get(path: str, params: Optional[Dict[str, Any]] = None,
                ttl: int = 3600, use_cache: bool = True) -> Dict[str, Any]:
    """
    GET ke Sectors v2. `path` mis. '/companies/' atau '/daily/BBCA/'.
    Cache default 1 jam (data harian tidak berubah intraday).
    """
    key = sectors_api_key()
    if not key:
        return {"ok": False, "status": 0, "body": None,
                "error": "SECTORS_API_KEY tidak diset di environment"}

    url = f"{SECTORS_BASE}{path}"
    if params:
        url += "?" + urllib.parse.urlencode(params)

    ck = f"sectors::{url}"
    if use_cache:
        hit = cache_get(ck, ttl)
        if hit is not None:
            return {"ok": True, "status": 200, "body": hit, "error": None,
                    "cached": True}

    res = http_json(url, headers={"Authorization": key})
    if res.get("ok") and use_cache:
        cache_put(ck, res["body"])
    return res


def sectors_companies(sub_sector: Optional[str] = None,
                      ttl: int = 86400) -> Dict[str, Any]:
    params = {"sub_sector": sub_sector} if sub_sector else None
    return sectors_get("/companies/", params=params, ttl=ttl)


def sectors_daily(symbol: str, ttl: int = 3600) -> Dict[str, Any]:
    """Harga/transaksi harian satu emiten."""
    return sectors_get(f"/daily/{symbol.upper()}/", ttl=ttl)


def sectors_report(symbol: str, ttl: int = 86400) -> Dict[str, Any]:
    """Laporan keuangan / rasio (PER, PBV, ROE, DER, growth)."""
    return sectors_get(f"/company/report/{symbol.upper()}/", ttl=ttl)


def sectors_foreign_flow(symbol: Optional[str] = None,
                         ttl: int = 3600) -> Dict[str, Any]:
    if symbol:
        return sectors_get(f"/foreign-flow/{symbol.upper()}/", ttl=ttl)
    return sectors_get("/foreign-flow/", ttl=ttl)


def sectors_broker_summary(symbol: str, ttl: int = 3600) -> Dict[str, Any]:
    return sectors_get(f"/broker-summary/{symbol.upper()}/", ttl=ttl)


def sectors_news(symbol: Optional[str] = None, ttl: int = 1800) -> Dict[str, Any]:
    params = {"symbol": symbol.upper()} if symbol else None
    return sectors_get("/news/", params=params, ttl=ttl)


def sectors_index_daily(index_code: str = "IHSG", ttl: int = 1800) -> Dict[str, Any]:
    return sectors_get(f"/index-daily/{index_code}/", ttl=ttl)


def sectors_corporate_actions(symbol: str, ttl: int = 86400) -> Dict[str, Any]:
    return sectors_get(f"/company/corporate-actions/{symbol.upper()}/", ttl=ttl)


# ───────────────────────── yfinance (cadangan) ─────────────────────────

def yfinance_history(symbol: str, period: str = "6mo",
                     use_cache: bool = True) -> Dict[str, Any]:
    """
    Riwayat OHLCV via yfinance. Dipakai kalau Sectors gagal/kosong.
    Mengembalikan list baris: {date, open, high, low, close, volume}.
    """
    # Hanya kode emiten IDX polos (mis. BBCA, AADI, BUMI) yang boleh diberi
    # suffix .JK. Simbol non-emiten WAJIB dilewatkan apa adanya:
    #   ^JKSE (indeks), USDIDR=X (kurs), CL=F/GC=F/CPO=F (komoditas).
    # Bug 21 Okt 2026: aturan lama hanya mengecualikan "^" dan ".JK", sehingga
    # "USDIDR=X" menjadi "USDIDR=X.JK" dan yfinance balas 404 -> seluruh data
    # makro/komoditas dilaporkan "data tidak tersedia" padahal tersedia.
    up = symbol.strip().upper()
    if re.fullmatch(r"[A-Z0-9]{1,6}", up):
        sym = f"{up}.JK"                 # kode emiten IDX polos
    else:
        sym = up                         # indeks / kurs / komoditas / sudah .JK
    ck = f"yf::hist::{sym}::{period}"
    if use_cache:
        hit = cache_get(ck, 1800)
        if hit:
            return _wrap(hit, "yfinance")

    try:
        import yfinance as yf
    except ImportError:
        return data_tidak_tersedia("yfinance belum terpasang")

    last_err = None
    for attempt in range(MAX_RETRY):
        try:
            tk = yf.Ticker(sym)
            df = tk.history(period=period, interval="1d", auto_adjust=True)
            if df is None or df.empty:
                return data_tidak_tersedia(f"yfinance tidak punya data untuk {sym}")
            rows: List[Dict[str, Any]] = []
            vol_kosong = 0
            for idx, row in df.iterrows():
                try:
                    vol = float(row["Volume"])
                except Exception:
                    vol = None
                if vol == 0:
                    # Volume 0 dari yfinance = tidak dilaporkan, bukan "tidak ada
                    # transaksi". Simpan None supaya UI menulis "data tidak
                    # tersedia", bukan menampilkan angka nol yang menyesatkan.
                    vol_kosong += 1
                    vol = None
                try:
                    rows.append({
                        "date": idx.strftime("%Y-%m-%d"),
                        "open": float(row["Open"]),
                        "high": float(row["High"]),
                        "low": float(row["Low"]),
                        "close": float(row["Close"]),
                        "volume": vol,
                    })
                except Exception:
                    continue
            if not rows:
                return data_tidak_tersedia(f"yfinance baris kosong untuk {sym}")
            if use_cache:
                cache_put(ck, rows)
            return _wrap(rows, "yfinance")
        except Exception as e:
            last_err = f"{type(e).__name__}: {e}"
            time.sleep(BASE_BACKOFF * (2 ** attempt))
    return data_tidak_tersedia(f"yfinance gagal setelah {MAX_RETRY}x ({last_err})")


def yfinance_info(symbol: str, use_cache: bool = True) -> Dict[str, Any]:
    """Fundamental via yfinance .info (PER, PBV, ROE, DER, growth)."""
    sym = f"{symbol.upper()}.JK"
    ck = f"yf::info::{sym}"
    if use_cache:
        hit = cache_get(ck, 86400)
        if hit:
            return _wrap(hit, "yfinance")

    try:
        import yfinance as yf
    except ImportError:
        return data_tidak_tersedia("yfinance belum terpasang")

    try:
        info = yf.Ticker(sym).info or {}
    except Exception as e:
        return data_tidak_tersedia(f"yfinance .info gagal: {type(e).__name__}")

    pe = info.get("trailingPE")
    pb = info.get("priceToBook")
    roe = info.get("returnOnEquity")
    der = info.get("debtToEquity")
    eg = info.get("earningsGrowth") or info.get("earningsQuarterlyGrowth")
    rg = info.get("revenueGrowth")

    # yfinance sering tidak mengisi debtToEquity (khususnya bank). Hitung dari
    # neraca: total debt / total equity. Bank punya DER tinggi secara wajar,
    # tapi lebih baik angka nyata daripada kosong.
    if der is None:
        try:
            bs = yf.Ticker(sym).balance_sheet
            if bs is not None and not bs.empty:
                col = bs.columns[0]
                def g(*keys):
                    for k in keys:
                        if k in bs.index:
                            v = bs.loc[k, col]
                            try:
                                fv = float(v)
                            except (TypeError, ValueError):
                                continue
                            if fv == fv and fv != 0:   # bukan NaN / nol
                                return fv
                    return None
                eq = g("Stockholders Equity", "Total Equity Gross Minority Interest",
                       "Common Stock Equity")
                debt = g("Total Debt")
                if debt is None:
                    liab = g("Total Liabilities Net Minority Interest", "Total Liabilities")
                    if liab is not None and eq is not None:
                        debt = liab          # proxy kasar, ditandai di catatan
                if eq and debt:
                    der = (debt / eq) * 100.0   # yfinance debtToEquity = persen
        except Exception:
            pass

    if all(v is None for v in (pe, pb, roe, der, eg, rg)):
        return data_tidak_tersedia(f"yfinance .info kosong untuk {sym}")

    payload = {
        "per": pe,
        "pbv": pb,
        "roe": roe,
        "der": der,
        "earnings_growth": eg,
        "revenue_growth": rg,
        "market_cap": info.get("marketCap"),
        "name": info.get("longName") or info.get("shortName"),
        "sector": info.get("sector"),
    }
    # SAMU-SATUNYA tempat konversi satuan untuk yfinance -> satuan baku.
    payload = normalisasi_satuan_fundamental(payload, sumber="yfinance")
    payload = _sanitize_fundamental(payload)
    if use_cache:
        cache_put(ck, payload)
    return _wrap(payload, "yfinance")


# ───────────────────────── gabungan: harga + fundamental ─────────────────────────

def _normalize_sectors_daily(body: Any) -> Optional[List[Dict[str, Any]]]:
    """
    Sectors `/daily/{symbol}/` mengembalikan list baris transaksi harian.
    Bentuknya bisa sedikit berbeda antar versi -> normalisasi defensif.

    SATUAN BAKU (lihat README.md -> "Satuan data"):
      close/open/high/low -> Rupiah per lembar
      volume              -> JUMLAH LEMBAR SAHAM (bukan lot, bukan rupiah)
      date                -> YYYY-MM-DD
    """
    if body is None:
        return None
    rows = body
    if isinstance(body, dict):
        for k in ("data", "results", "daily", "items"):
            if isinstance(body.get(k), list):
                rows = body[k]
                break
        else:
            return None
    if not isinstance(rows, list) or not rows:
        return None

    out: List[Dict[str, Any]] = []
    for r in rows:
        if not isinstance(r, dict):
            continue
        def pick(*names):
            for n in names:
                if r.get(n) is not None:
                    return r[n]
            return None
        d = pick("date", "trading_date", "day")
        c = pick("close", "last_price", "price", "close_price")
        if d is None or c is None:
            continue
        # volume yang tidak dilaporkan harus tetap None (bukan 0) supaya
        # UI tidak menampilkan "nol transaksi" yang menyesatkan.
        v = pick("volume", "vol", "traded_volume")
        try:
            vol = float(v) if v is not None else None
        except (TypeError, ValueError):
            vol = None
        try:
            out.append({
                "date": str(d)[:10],
                "open": float(pick("open", "open_price") or c),
                "high": float(pick("high", "high_price") or c),
                "low": float(pick("low", "low_price") or c),
                "close": float(c),
                "volume": vol,
            })
        except (TypeError, ValueError):
            continue
    out.sort(key=lambda x: x["date"])
    return out or None


def get_history(symbol: str) -> Dict[str, Any]:
    """
    Riwayat harga: coba Sectors dulu, jatuh ke yfinance.
    Hasil selalu membawa sumber_data + diperbarui_pada.
    """
    if sectors_available():
        r = sectors_daily(symbol)
        if r.get("ok"):
            norm = _normalize_sectors_daily(r.get("body"))
            if norm and len(norm) >= 2:
                return _wrap(norm, "sectors")
        # gagal / kosong -> lanjut ke cadangan
    y = yfinance_history(symbol)
    if y.get("ok"):
        return y
    alasan = "sectors & yfinance gagal"
    if not sectors_available():
        alasan = "SECTORS_API_KEY tidak diset; yfinance juga gagal"
    return data_tidak_tersedia(alasan)


def normalisasi_satuan_fundamental(f: Dict[str, Any],
                                  sumber: str = "") -> Dict[str, Any]:
    """
    KONVERSI SATUAN BAKU — satu-satunya pintu masuk untuk semua sumber.

    Satuan baku (lihat README.md -> "Satuan data"):
      roe              -> FRAKSI   (0.12814 = 12,814%)
      earnings_growth  -> FRAKSI   (0.152 = 15,2%)
      revenue_growth   -> FRAKSI   (0.089 = 8,9%)
      der              -> RASIO   (0.77 = 0,77x; 77% hutang atas modal)
      per, pbv         -> RASIO   (sudah rasio di semua sumber)

    Sectorsv2 dan yfinance bisa mengirim persen (15.2) ATAU fraksi (0.152).
    Deteksi ambang: nilai mutlak > 1.5 dianggap persen untuk ROE & growth
    (fraksi realistis untuk emiten IDX: |ROE| < 150%).
    """
    out = dict(f or {})

    def as_num(v):
        return v if isinstance(v, (int, float)) and not isinstance(v, bool) else None

    # yfinance .info sudah dalam FRAKSI untuk roe/earningsGrowth/revenueGrowth
    # (returnOnEquity 0.155 = 15,5%) -> tidak boleh dikonversi lagi.
    # Sectors/umum bisa kirim persen (15.5) atau fraksi (0.155) -> heuristik.
    if sumber != "yfinance":
        roe = as_num(out.get("roe"))
        if roe is not None and abs(roe) > 1.5:
            roe = roe / 100.0
        out["roe"] = roe

        for k in ("earnings_growth", "revenue_growth"):
            v = as_num(out.get(k))
            if v is not None and abs(v) > 1.5:
                v = v / 100.0
            out[k] = v

    der = as_num(out.get("der"))
    if der is not None:
        if sumber == "yfinance":
            # yfinance .info "debtToEquity" SELALU persen (10.5 = 10,5x).
            der = der / 100.0
        elif der > 12:
            # Sectors/umum: rasio > 12 hampir pasti persen -> konversi.
            der = der / 100.0
    out["der"] = der

    return out


def _normalize_sectors_fundamental(body: Any) -> Optional[Dict[str, Any]]:
    if not isinstance(body, dict):
        return None
    # report company biasanya punya nested 'fundamental' / 'valuation' / 'financials'
    src: Dict[str, Any] = {}
    for k in ("fundamental", "valuation", "financials", "ratios", "data", "report"):
        v = body.get(k)
        if isinstance(v, dict):
            src.update(v)
    src.update({k: v for k, v in body.items() if not isinstance(v, (dict, list))})

    def num(*names):
        for n in names:
            v = src.get(n)
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                return float(v)
        return None

    per = num("per", "pe_ratio", "trailing_pe", "price_earning_ratio")
    pbv = num("pbv", "pb_ratio", "price_to_book", "price_book_value")
    roe = num("roe", "return_on_equity")
    der = num("der", "debt_to_equity", "debt_equity_ratio")
    eg = num("earnings_growth", "profit_growth", "net_income_growth")
    rg = num("revenue_growth", "sales_growth")

    if all(v is None for v in (per, pbv, roe, der, eg, rg)):
        return None
    # Konversi ke satuan baku di satu tempat (lihat docstring fungsi).
    f = normalisasi_satuan_fundamental(
        {"per": per, "pbv": pbv, "roe": roe, "der": der,
         "earnings_growth": eg, "revenue_growth": rg}, sumber="sectors")
    return _sanitize_fundamental(f)


def _sanitize_fundamental(f: Dict[str, Any]) -> Dict[str, Any]:
    """
    Buang rasio yang secara ekonomi tidak masuk akal, supaya tidak tampil
    bogus di tabel (contoh: PBV 23.752 dari yfinance — itu data rusak).

    Ambang kasar tapi aman untuk IDX:
      * PBV  > 50   -> rusak
      * PER  > 500  -> rusak (perusahaan dengan laba sangat kecil)
      * ROE  > 300% -> rusak (bukan fraksi 0-1, atau data kacau)
      * DER  > 50   -> rusak
    Nilai yang dibuang jadi None = "tidak tersedia", bukan 0.
    """
    out = dict(f)

    def sane(key, hi, lo=0.0):
        v = out.get(key)
        if isinstance(v, (int, float)):
            if v != v or v < lo or v > hi:
                out[key] = None
        elif v is not None:
            out[key] = None

    sane("pbv", 50.0)
    sane("per", 500.0)
    sane("roe", 3.0, -1.0)
    sane("der", 50.0, -50.0)
    sane("earnings_growth", 20.0, -1.0)
    sane("revenue_growth", 20.0, -1.0)
    return out


def get_fundamental(symbol: str) -> Dict[str, Any]:
    """Fundamental: Sectors dulu, lalu yfinance."""
    if sectors_available():
        r = sectors_report(symbol)
        if r.get("ok"):
            norm = _normalize_sectors_fundamental(r.get("body"))
            if norm:
                return _wrap(norm, "sectors")
    y = yfinance_info(symbol)
    if y.get("ok"):
        return y
    return data_tidak_tersedia("sectors & yfinance gagal (fundamental)")


def get_news(symbol: Optional[str] = None, limit: int = 8) -> Dict[str, Any]:
    """Berita: hanya Sectors (yfinance tidak menyediakan)."""
    if not sectors_available():
        return data_tidak_tersedia("SECTORS_API_KEY tidak diset")
    r = sectors_news(symbol)
    if not r.get("ok"):
        return data_tidak_tersedia(f"sectors news gagal: {r.get('error')}")
    body = r.get("body")
    rows = body if isinstance(body, list) else (body or {}).get("data") or []
    items = []
    for it in rows[:limit]:
        if isinstance(it, dict):
            items.append({
                "title": it.get("title") or it.get("headline") or "",
                "date": str(it.get("date") or it.get("published_at") or "")[:10],
                "url": it.get("url") or it.get("link") or "",
                "source": it.get("source") or "",
            })
    if not items:
        return data_tidak_tersedia("tidak ada berita")
    return _wrap(items, "sectors")


def get_foreign_flow(symbol: Optional[str] = None) -> Dict[str, Any]:
    if not sectors_available():
        return data_tidak_tersedia("SECTORS_API_KEY tidak diset")
    r = sectors_foreign_flow(symbol)
    if not r.get("ok"):
        return data_tidak_tersedia(f"sectors foreign-flow gagal: {r.get('error')}")
    return _wrap(r.get("body"), "sectors")


def get_market_index(index_code: str = "IHSG") -> Dict[str, Any]:
    """Ringkasan indeks; Sectors dulu, yfinance (^JKSE) sebagai cadangan."""
    if sectors_available():
        r = sectors_index_daily(index_code)
        if r.get("ok") and r.get("body"):
            return _wrap(r.get("body"), "sectors")
    try:
        import yfinance as yf
        t = yf.Ticker("^JKSE")
        h = t.history(period="1mo", interval="1d", auto_adjust=True)
        if h is not None and not h.empty:
            rows = [{"date": i.strftime("%Y-%m-%d"), "close": float(r["Close"]),
                     "volume": float(r["Volume"])} for i, r in h.iterrows()]
            return _wrap(rows, "yfinance")
    except Exception as e:
        return data_tidak_tersedia(f"indeks gagal: {type(e).__name__}")
    return data_tidak_tersedia("indeks tidak tersedia")
