"""
Pipeline utama IDX Stock Screener.

Alur:
  1. Ambil daftar emiten (Sectors v2 -> yfinance bulk, atau daftar statis
     yang di-cache dari scan sebelumnya).
  2. Untuk setiap emiten: harga + fundamental (Sectors -> yfinance).
  3. Hitung skor 0-100 (teknikal 40 / fundamental 40 / katalis 20).
  4. Tandai emiten gorengan (filter "Waspada").
  5. Simpan JSON untuk dashboard + snapshot harian untuk riwayat.

Aturan: tidak ada data -> "data tidak tersedia" dengan label sumber.
Tidak pernah mengarang angka, tidak pernah memakai data lama tanpa label.
"""

from __future__ import annotations

import json
import os
import sys
import time
from typing import Any, Dict, List, Optional

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from idx import providers as P  # noqa: E402
from idx import scoring as S  # noqa: E402

REPO = "/root/idx-dashboard"
DATA_DIR = os.path.join(REPO, "data")
SNAP_DIR = os.path.join(DATA_DIR, "snapshots")
LIST_CACHE = os.path.join(DATA_DIR, "universe.json")
LOCK_FILE = os.path.join(DATA_DIR, ".pipeline.lock")
UNIVERSE_LIMIT = int(os.environ.get("IDX_UNIVERSE_LIMIT", "0") or 0)  # 0 = semua

# ─────────────────── kontrak data (versi & satuan baku) ───────────────────
# Naikkan versi_skema setiap kali nama field / satuan di latest.json berubah.
VERSI_SKEMA = "1.1.0"

# SATUAN BAKU untuk semua field di latest.json. Semua sumber (Sectors v2,
# yfinance) dinormalisasi ke satuan ini di providers.normalisasi_satuan_fundamental.
# Dokumentasi lengkap: README.md -> "Satuan data".
SATUAN_DATA = {
    "harga": "IDR_per_lembar",
    "open": "IDR_per_lembar",
    "high": "IDR_per_lembar",
    "low": "IDR_per_lembar",
    "close": "IDR_per_lembar",
    "chg_pct": "persen",
    "volume": "lembar_saham",
    "ma20": "IDR_per_lembar",
    "ma50": "IDR_per_lembar",
    "rsi": "indeks_0_100",
    "per": "rasio_x",
    "pbv": "rasio_x",
    "roe": "fraksi_0_1",          # 0.12814 = 12,814%  -> UI multiply x100
    "der": "rasio_x",            # 0.77 = 0,77x hutang atas modal
    "earnings_growth": "fraksi_0_1",
    "revenue_growth": "fraksi_0_1",
    "skor": "poin_0_100",
}

# Fallback daftar emiten: sheet publik "Emiten" (sudah berisi 900+ kode).
SHEET_ID = os.environ.get("IDX_SHEET_ID", "1KRQpGoCFabu7P8Ub3-n_HSERBqb4EAT-D5hLb7HrFU8")
SHEET_URL = (f"https://docs.google.com/spreadsheets/d/{SHEET_ID}/gviz/tq"
             "?tqx=out:csv&sheet=Emiten")


def ensure_dirs() -> None:
    os.makedirs(DATA_DIR, exist_ok=True)
    os.makedirs(SNAP_DIR, exist_ok=True)


def _tulis_atomic(path: str, payload: Any) -> None:
    """
    Tulis JSON lewat file sementara lalu rename. Dashboard tidak pernah membaca
    file setengah tertulis (iblit). rename() di filesystem yang sama bersifat
    atomik.
    """
    tmp = f"{path}.tmp.{os.getpid()}"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=1)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            try:
                os.unlink(tmp)
            except OSError:
                pass


class _PipelineLock:
    """
    Cegah dua pipeline berjalan bersamaan. Tanpa ini, run penuh dan run kecil
    bisa saling menimpa data/latest.json sehingga hasil di dashboard bisa
    setengah jadi.

    Pakai file lock eksklusif (O_CREAT|O_EXCL) — tidak butuh dependency
    eksternal dan otomatis lepas walau proses mati keras (stale lock
    dibersihkan setelah 3 jam).
    """

    def __init__(self, path: str = LOCK_FILE, max_age_s: int = 3 * 3600):
        self.path = path
        self.max_age_s = max_age_s
        self.acquired = False

    def _is_stale(self) -> bool:
        try:
            age = time.time() - os.path.getmtime(self.path)
            return age > self.max_age_s
        except OSError:
            return False

    def __enter__(self) -> "_PipelineLock":
        ensure_dirs()
        for _ in range(2):
            try:
                fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                with os.fdopen(fd, "w") as f:
                    f.write(json.dumps({"pid": os.getpid(),
                                        "mulai": time.time()}))
                self.acquired = True
                return self
            except FileExistsError:
                if self._is_stale():
                    try:
                        os.unlink(self.path)
                        continue
                    except OSError:
                        pass
                raise RuntimeError(
                    "pipeline lain sedang berjalan (lock: %s). "
                    "Tunggu selesai atau hapus lock jika prosesnya sudah mati." % self.path)
        raise RuntimeError("gagal mengambil lock pipeline")

    def __exit__(self, *exc) -> None:
        if self.acquired:
            try:
                os.unlink(self.path)
            except OSError:
                pass
            self.acquired = False


# ───────────────────────── daftar emiten ─────────────────────────

def _hari_libur_bursa() -> List[str]:
    """
    Daftar tanggal libur bursa (ISO) untuk tahun berjalan + tahun depan.
    Dipakai frontend untuk menghitung "hari bursa" dengan benar, bukan
    sekadar hari kerja — supaya warning data basi akurat saat libur IDX.
    """
    out: List[str] = []
    try:
        import datetime
        from idx import calendar_id as C
        tahun = datetime.datetime.now().year
        for y in (tahun, tahun + 1):
            d = datetime.date(y, 1, 1)
            while d.year == y:
                nama = C.hari_libur(d)
                if nama and nama != "Akhir pekan":
                    out.append(d.isoformat())
                d += datetime.timedelta(days=1)
    except Exception:
        return []
    return sorted(set(out))


def load_universe() -> List[Dict[str, Any]]:
    """
    Daftar emiten: Sectors v2 (kalau ada key), kalau tidak -> yfinance bulk
    dari list ticker IDX yang disimpan di cache scan sebelumnya.
    """
    cached: List[Dict[str, Any]] = []
    if os.path.exists(LIST_CACHE):
        try:
            with open(LIST_CACHE, "r", encoding="utf-8") as f:
                cached = json.load(f).get("emiten", [])
        except Exception:
            cached = []

    if P.sectors_available():
        r = P.sectors_companies()
        if r.get("ok"):
            body = r.get("body")
            rows = body if isinstance(body, list) else (body or {}).get("data") or []
            out = []
            for it in rows:
                if not isinstance(it, dict):
                    continue
                sym = (it.get("symbol") or it.get("ticker") or it.get("code") or "").upper()
                if not sym or not sym.isalnum():
                    continue
                out.append({
                    "kode": sym,
                    "nama": (it.get("name") or it.get("company_name") or sym).strip(),
                    "sektor": (it.get("sub_sector") or it.get("sector")
                               or it.get("segment") or "LAINNYA"),
                    "industri": it.get("industry") or it.get("sub_industry") or "",
                })
            if out:
                save_universe(out)
                return out

    if cached:
        return cached
    return _universe_from_sheet()


def _universe_from_sheet() -> List[Dict[str, Any]]:
    """Daftar emiten dari sheet CSV publik (fallback tanpa Sectors key)."""
    import csv
    import io
    import urllib.request
    try:
        req = urllib.request.Request(SHEET_URL, headers={"User-Agent": P.USER_AGENT})
        with urllib.request.urlopen(req, timeout=30) as r:
            text = r.read().decode("utf-8", "replace")
    except Exception as e:
        print(f"[universe] sheet gagal: {type(e).__name__}: {e}", flush=True)
        return []

    out: List[Dict[str, Any]] = []
    seen = set()
    reader = csv.DictReader(io.StringIO(text))
    for row in reader:
        kode = (row.get("Kode") or "").strip().upper()
        if not kode or kode in seen or not kode.isalnum():
            continue
        seen.add(kode)
        out.append({
            "kode": kode,
            "nama": (row.get("Nama Emiten") or kode).strip(),
            "sektor": (row.get("Sektor") or "LAINNYA").strip().upper(),
            "industri": (row.get("Industri") or "").strip(),
        })
    if out:
        print(f"[universe] {len(out)} emiten dari sheet CSV", flush=True)
        save_universe(out)
    return out


def save_universe(emiten: List[Dict[str, Any]]) -> None:
    ensure_dirs()
    with open(LIST_CACHE, "w", encoding="utf-8") as f:
        json.dump({"diperbarui_pada": P.stamp_wib(), "emiten": emiten},
                  f, ensure_ascii=False, indent=1)


# ───────────────────────── pipeline ─────────────────────────

def analyze_one(item: Dict[str, Any], dengan_katalis: bool = True) -> Dict[str, Any]:
    """Analisis satu emiten -> baris tabel + rincian skor."""
    kode = item["kode"]
    t0 = time.time()

    hist = P.get_history(kode)
    fund = P.get_fundamental(kode)

    bars = hist.get("data") if hist.get("ok") else None
    tech = S.score_technical(bars) if bars else S.score_technical([])
    fund_d = fund.get("data") if fund.get("ok") else None
    fund_s = S.score_fundamental(fund_d)

    news = None
    ca = None
    net_asing = None
    if dengan_katalis and P.sectors_available():
        n = P.get_news(kode, limit=5)
        news = n.get("data") if n.get("ok") else None
        c = P.sectors_corporate_actions(kode)
        if c.get("ok"):
            body = c.get("body")
            ca = body if isinstance(body, list) else (body or {}).get("data") or None
        ff = P.get_foreign_flow(kode)
        if ff.get("ok"):
            net_asing = _parse_net_asing(ff.get("body"))
    cat = S.score_catalyst(news=news, corporate_actions=ca, foreign_net=net_asing)

    ind = tech.get("indikator", {})
    harga = ind.get("harga")
    chg = None
    if bars and len(bars) >= 2 and bars[-2].get("close"):
        chg = (bars[-1]["close"] / bars[-2]["close"] - 1) * 100

    flags = S.risk_flags(ind, harga, chg, len(news) if news else 0, fund_d)

    # sumber data gabungan per kolom
    sumber_harga = hist.get("sumber_label")
    sumber_fund = fund.get("sumber_label")
    if sumber_harga == sumber_fund:
        sumber = sumber_harga
    else:
        sumber = f"{sumber_harga}/{sumber_fund}"

    return {
        "kode": kode,
        "nama": item.get("nama") or kode,
        "sektor": item.get("sektor") or "LAINNYA",
        "industri": item.get("industri") or "",
        "harga": harga,
        "chg_pct": round(chg, 2) if chg is not None else None,
        "volume": ind.get("volume"),
        "ma20": ind.get("ma20"),
        "ma50": ind.get("ma50"),
        "rsi": ind.get("rsi"),
        "macd_hist": ind.get("macd_hist"),
        "resistance": ind.get("resistance"),
        "support": ind.get("support"),
        "breakout": ind.get("breakout"),
        "support_break": ind.get("support_break"),
        "per": (fund_d or {}).get("per"),
        "pbv": (fund_d or {}).get("pbv"),
        "roe": (fund_d or {}).get("roe"),
        "der": (fund_d or {}).get("der"),
        "earnings_growth": (fund_d or {}).get("earnings_growth"),
        "revenue_growth": (fund_d or {}).get("revenue_growth"),
        "sumber_data": sumber,
        "sumber_harga": sumber_harga,
        "sumber_fundamental": sumber_fund,
        "diperbarui_pada": hist.get("diperbarui_pada"),
        "skor_detail": None,     # diisi setelah median sektor diketahui
        "waspada": bool(flags),
        "flag_waspada": flags,
        "sinyal": _sinyal_label(ind, tech, cat),
        "_bars": bars,
        "_news": news,
        "_detik": round(time.time() - t0, 1),
    }


def _market_summary(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Ringkasan pasar: IHSG, sektor terkuat/terlemah (dari rata-rata Chg% emiten
    yang datanya ada), dan net asing total kalau tersedia.
    Sektor dihitung dari data yang benar-benar ada — bukan tebakan.
    """
    from collections import defaultdict

    idx = P.get_market_index("IHSG")
    ihsg: Dict[str, Any] = {"tersedia": False, "nilai": None, "chg_pct": None,
                            "sumber_data": idx.get("sumber_label"),
                            "diperbarui_pada": idx.get("diperbarui_pada")}
    if idx.get("ok") and isinstance(idx.get("data"), list) and idx["data"]:
        bars = idx["data"]
        try:
            last = float(bars[-1]["close"])
            prev = float(bars[-2]["close"]) if len(bars) >= 2 else None
            ihsg.update({
                "tersedia": True,
                "nilai": round(last, 2),
                "chg_pct": round((last / prev - 1) * 100, 2) if prev else None,
            })
        except (KeyError, TypeError, ValueError, IndexError):
            pass

    # sektor: rata-rata chg% + jumlah emiten
    sec_chg: Dict[str, List[float]] = defaultdict(list)
    for r in rows:
        ch = r.get("chg_pct")
        if isinstance(ch, (int, float)):
            sec_chg[(r.get("sektor") or "LAINNYA").upper()].append(float(ch))
    sec_stats = [
        {"sektor": s, "chg_pct": round(sum(v) / len(v), 2), "jumlah": len(v)}
        for s, v in sec_chg.items() if len(v) >= 1
    ]
    sec_stats.sort(key=lambda x: x["chg_pct"], reverse=True)

    # net asing total (hanya kalau Sectors aktif & angkanya terkumpul)
    net_total = None
    if P.sectors_available():
        ff = P.get_foreign_flow()
        if ff.get("ok"):
            net_total = _parse_net_asing(ff.get("data"))

    return {
        "ihsg": ihsg,
        "sektor_terkuat": sec_stats[0] if sec_stats else None,
        "sektor_terlemah": sec_stats[-1] if sec_stats else None,
        "sektor": sec_stats,
        "net_asing_total": net_total,
        "net_asing_tersedia": net_total is not None,
    }


def _parse_net_asing(body: Any) -> Optional[float]:
    """Coba ambil net foreign flow dari berbagai bentuk respons Sectors."""
    if body is None:
        return None
    if isinstance(body, (int, float)):
        return float(body)
    if isinstance(body, dict):
        for k in ("net_foreign_flow", "net_flow", "net", "value", "net_buy",
                  "foreign_net", "net_buy_sell", "value_net"):
            v = body.get(k)
            if isinstance(v, (int, float)):
                return float(v)
        for k in ("data", "latest", "summary"):
            if k in body:
                return _parse_net_asing(body[k])
    if isinstance(body, list) and body:
        last = body[-1]
        if isinstance(last, dict):
            return _parse_net_asing(last)
    return None


def _komponen_skor(r: Dict[str, Any], nama: str) -> Optional[float]:
    """Ambil skor satu komponen dari skor_detail; aman untuk semua bentuk."""
    k = ((r.get("skor_detail") or {}).get("komponen") or {}).get(nama)
    if isinstance(k, dict):
        v = k.get("skor")
        return v if isinstance(v, (int, float)) else None
    return k if isinstance(k, (int, float)) else None


def _sinyal_label(ind: Dict[str, Any], tech: Dict[str, Any],
                  cat: Dict[str, Any]) -> str:
    """Label sinyal ringkas: Breakout / Tembus Support / Waspada / Normal."""
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


def run(universe_limit: int = 0, dengan_katalis: bool = True,
        out_name: str = "latest.json") -> Dict[str, Any]:
    with _PipelineLock():
        return _run_locked(universe_limit, dengan_katalis, out_name)


def _run_locked(universe_limit: int, dengan_katalis: bool,
                out_name: str) -> Dict[str, Any]:
    ensure_dirs()
    emiten = load_universe()
    if not emiten:
        return {"ok": False, "error": "daftar emiten kosong (Sectors & cache tidak tersedia)"}
    total_universe = len(emiten)
    if universe_limit:
        emiten = emiten[:universe_limit]

    print(f"[pipeline] {len(emiten)} emiten | Sectors: {'AKTIF' if P.sectors_available() else 'tidak ada key (yfinance)'}", flush=True)

    rows: List[Dict[str, Any]] = []
    for i, it in enumerate(emiten, 1):
        try:
            rows.append(analyze_one(it, dengan_katalis=dengan_katalis))
        except Exception as e:
            print(f"  [{i}/{len(emiten)}] {it.get('kode')} ERROR {type(e).__name__}: {e}", flush=True)
            continue
        if i % 10 == 0 or i == len(emiten):
            ok = sum(1 for r in rows if r.get("harga") is not None)
            print(f"  [{i}/{len(emiten)}] harga terambil {ok}/{len(rows)}", flush=True)

    # median sektor untuk pembanding PER/PBV
    pe_med, pb_med = S.sector_medians(rows)
    for r in rows:
        fund_d = {"per": r.get("per"), "pbv": r.get("pbv"), "roe": r.get("roe"),
                  "der": r.get("der"), "earnings_growth": r.get("earnings_growth"),
                  "revenue_growth": r.get("revenue_growth")}
        tech = S.score_technical(r.get("_bars") or [])
        sec = (r.get("sektor") or "LAINNYA").upper()
        fund_s = S.score_fundamental(fund_d, pe_med.get(sec), pb_med.get(sec))
        # katalis tidak dihitung ulang (biaya), ambil dari skoring ulang ringkas
        cat_s = S.score_catalyst(news=r.get("_news"), corporate_actions=None,
                                 foreign_net=None)
        comb = S.combine(tech, fund_s, cat_s)
        r["skor"] = comb["skor"]
        r["skor_detail"] = {
            "total": comb["skor"],
            "bobot_terisi": comb["bobot_terisi"],
            "catatan": comb["catatan"],
            "komponen": comb["komponen"],
        }
        r.pop("_bars", None)
        r.pop("_news", None)

    # urutkan: skor desc, emiten gorengan selalu di bawah
    rows.sort(key=lambda r: (r.get("waspada", False),
                             -(r["skor"] if r.get("skor") is not None else -1)))

    # Top 10 Emiten: hanya yang skornya ada & TIDAK bertanda waspada
    top10 = [{"peringkat": i + 1, "kode": r["kode"], "nama": r.get("nama"),
              "skor": r.get("skor"), "sinyal": r.get("sinyal"),
              "harga": r.get("harga"), "chg_pct": r.get("chg_pct"),
              "sektor": r.get("sektor")}
             for i, r in enumerate(x for x in rows
                                   if x.get("skor") is not None
                                   and not x.get("waspada"))][:10]

    payload = {
        "diperbarui_pada": P.stamp_wib(),
        "diperbarui_pada_teks": P.human_wib(),
        "versi_skema": VERSI_SKEMA,
        "satuan": SATUAN_DATA,
        "hari_libur_bursa": _hari_libur_bursa(),
        "sumber_utama": "sectors" if P.sectors_available() else "yfinance",
        "sectors_aktif": P.sectors_available(),
        "jumlah_emiten": len(rows),
        "jumlah_ada_harga": sum(1 for r in rows if r.get("harga") is not None),
        "jumlah_ada_fundamental": sum(1 for r in rows if r.get("per") is not None
                                      or r.get("roe") is not None),
        "jumlah_waspada": sum(1 for r in rows if r.get("waspada")),
        "median_per_sektor": pe_med,
        "median_pbv_sektor": pb_med,
        "ringkasan_pasar": _market_summary(rows),
        "top10": top10,
        "emiten": rows,
        "disclaimer": "Hasil screening otomatis, bukan rekomendasi beli.",
    }
    out_path = os.path.join(DATA_DIR, out_name)
    _tulis_atomic(out_path, payload)

    # Snapshot harian hanya ditulis oleh run LENGAP. Run uji/partial akan
    # menimpa snapshot harian dengan data sebagian dan merusak riwayat skor.
    run_penuh = len(emiten) >= total_universe
    snap = os.path.join(SNAP_DIR, P.now_wib().strftime("%Y-%m-%d.json"))
    if not run_penuh:
        print(f"[pipeline] run parsial ({len(emiten)}/{total_universe} emiten) "
              f"-> snapshot harian tidak ditimpa", flush=True)
        return {"ok": True, "out": out_path, "snapshot": None,
                "jumlah": len(rows),
                "ada_harga": payload["jumlah_ada_harga"],
                "ada_fundamental": payload["jumlah_ada_fundamental"]}

    snap_rows = [{
        "kode": r["kode"], "nama": r["nama"], "skor": r.get("skor"),
        "sinyal": r.get("sinyal"), "waspada": r.get("waspada"),
        "harga": r.get("harga"), "chg_pct": r.get("chg_pct"),
        "rsi": r.get("rsi"), "per": r.get("per"), "pbv": r.get("pbv"),
        "roe": r.get("roe"), "der": r.get("der"),
        "teknikal": _komponen_skor(r, "teknikal"),
        "fundamental": _komponen_skor(r, "fundamental"),
        "katalis": _komponen_skor(r, "katalis"),
        "sumber_data": r.get("sumber_data"),
    } for r in rows]
    _tulis_atomic(snap, {"tanggal": P.now_wib().strftime("%Y-%m-%d"),
                         "diperbarui_pada": P.stamp_wib(), "emiten": snap_rows})
    # index snapshot dibuat SETELAH snapshot hari ini ditulis
    try:
        from idx import snapshot_index          # jalan sebagai modul paket
    except ImportError:
        import snapshot_index                    # jalan sebagai script langsung
    snapshot_index.build()

    return {"ok": True, "out": out_path, "snapshot": snap,
            "jumlah": len(rows),
            "ada_harga": payload["jumlah_ada_harga"],
            "ada_fundamental": payload["jumlah_ada_fundamental"]}


if __name__ == "__main__":
    lim = int(sys.argv[1]) if len(sys.argv) > 1 else UNIVERSE_LIMIT
    kat = "--no-katalis" not in sys.argv
    # --out namafile : tulis ke file lain, untuk run uji tanpa menimpa
    # data/latest.json yang dipakai dashboard.
    out = "latest.json"
    if "--out" in sys.argv:
        i = sys.argv.index("--out")
        if i + 1 < len(sys.argv):
            out = sys.argv[i + 1]
    res = run(universe_limit=lim, dengan_katalis=kat, out_name=out)
    print(json.dumps(res, ensure_ascii=False, indent=1))
