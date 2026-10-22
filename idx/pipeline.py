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
from idx import rezim as RZ     # noqa: E402
from idx import alert as AL  # noqa: E402
from idx import waspada as WAS  # noqa: E402

try:
    from idx import history_store as HIST     # jalan sebagai modul paket
except ImportError:
    import history_store as HIST              # jalan sebagai script langsung

REPO = "/root/idx-dashboard"
DATA_DIR = os.path.join(REPO, "data")
SNAP_DIR = os.path.join(DATA_DIR, "snapshots")
LIST_CACHE = os.path.join(DATA_DIR, "universe.json")
LOCK_FILE = os.path.join(DATA_DIR, ".pipeline.lock")
UNIVERSE_LIMIT = int(os.environ.get("IDX_UNIVERSE_LIMIT", "0") or 0)  # 0 = semua

# (Review 21 Okt) Syarat likuiditas minimum untuk masuk Top 10.
# Dasar: nilai transaksi harian = volume x harga sesi terakhir.
# Rp1 miliar dipilih karena di bawah itu order Rp10-20 juta sudah
# menggerakkan harga (slippage besar) — bukan angka "enak dilihat".
# Bisa diubah lewat env supaya bisa diuji tanpa menyunting kode.
MIN_NILAI_TRANSAKSI = float(os.environ.get("IDX_MIN_NILAI_TRANSAKSI",
                                           "1000000000") or 0)


def _rupiah_ringkas(v: Optional[float]) -> str:
    """Format rupiah ringkas untuk teks kebijakan (Rp1,0 M / Rp500 jt)."""
    if v is None:
        return "data tidak tersedia"
    try:
        f = float(v)
    except (TypeError, ValueError):
        return "data tidak tersedia"
    if f >= 1e12:
        return f"Rp{f / 1e12:.1f} T".replace(".", ",")
    if f >= 1e9:
        return f"Rp{f / 1e9:.1f} M".replace(".", ",")
    if f >= 1e6:
        return f"Rp{f / 1e6:.0f} jt"
    return f"Rp{f:,.0f}".replace(",", ".")

# ─────────────────── kontrak data (versi & satuan baku) ───────────────────
# Naikkan versi_skema setiap kali nama field / satuan di latest.json berubah.
VERSI_SKEMA = "1.4.0"

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


def _ringkas_sumber(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Hitung asal data per field (Kelompok E).

    `sumber_utama` hanya menyebut satu nilai untuk seluruh run. Itu
    menyembunyikan fakta penting: sebagian emiten bisa punya harga tapi
    tidak punya fundamental, atau tidak punya data sama sekali. Fungsi ini
    melaporkan sebaran nyatanya supaya UI tidak bisa mengklaim lebih dari
    yang sebenarnya ada.
    """
    def hitung(field: str) -> Dict[str, int]:
        c: Dict[str, int] = {}
        for r in rows:
            k = r.get(field) or "tidak diketahui"
            c[str(k)] = c.get(str(k), 0) + 1
        return dict(sorted(c.items(), key=lambda kv: -kv[1]))

    n = len(rows)
    tanpa_data = sum(1 for r in rows
                     if r.get("harga") is None and r.get("per") is None
                     and r.get("roe") is None)
    return {
        "harga": hitung("sumber_harga"),
        "fundamental": hitung("sumber_fundamental"),
        "gabungan": hitung("sumber_data"),
        "emiten_tanpa_data": tanpa_data,
        "emiten_total": n,
    }


def snapshot_layak_timpa(snap_path: str,
                         snap_rows: List[Dict[str, Any]]) -> tuple:
    """Putuskan apakah snapshot harian boleh ditimpa (Kelompok E).

    Aturan: snapshot yang SUDAH ada di tanggal ini hanya boleh ditimpa bila
    run baru punya cakupan harga >= run lama. Alasannya: `run_penuh` (975
    emiten tersapu) tetap bisa menghasilkan separuh harga gagal diambil —
    menimpanya akan membuang snapshot pagi yang lebih lengkap, dan riwayat
    skor yang sudah tersimpan jadi tidak bisa dipulihkan.

    Fungsi murni (hanya membaca file snapshot lama) supaya bisa diuji tanpa
    menjalankan pipeline penuh.
    """
    baru = sum(1 for x in snap_rows if x.get("harga") is not None)
    lama_data = _baca_json(snap_path)
    if not (isinstance(lama_data, dict) and lama_data.get("emiten")):
        return True, {"baru": baru, "lama": 0}
    lama = sum(1 for x in lama_data["emiten"]
               if isinstance(x, dict) and x.get("harga") is not None)
    return (baru >= lama), {"baru": baru, "lama": lama}


def _baca_json(path: str) -> Optional[Any]:
    """Baca JSON; None kalau file tidak ada atau rusak.

    Dipakai untuk memastikan file lama benar-benar bisa dibaca sebelum kita
    memutuskan 'data lama dipertahankan'. Kalau file lama ternyata rusak,
    pemanggil tahu bahwa tidak ada data sehat yang tersisa.
    """
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


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

def _tanggal_data_hist(bars_per_kode):
    """Tanggal SESI BURSA terbaru dari bar harga per emiten (YYYY-MM-DD).

    Berbeda dari `diperbarui_pada` (jam pipeline jalan). Pipeline yang
    jalan 07:47 pagi hari kerja belum punya bar sesi hari itu, sehingga
    `diperbarui_pada[:10]` lebih maju 1 hari dari tanggal data terakhir.
    """
    tgl = []
    for b in bars_per_kode.values():
        if b:
            d = b[-1].get("date") or b[-1].get("tanggal")
            if d:
                tgl.append(str(d)[:10])
    return max(tgl) if tgl else None


def _f(v: Any) -> Optional[float]:
    """Konversi aman ke float; bool & non-angka -> None (bukan 0)."""
    if isinstance(v, bool) or v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


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

    # (Review 21 Okt) Nilai transaksi harian dalam rupiah = volume x harga.
    # Hanya diisi kalau KEDUA angka benar-benar dilaporkan; kalau salah satu
    # None, hasilnya None (bukan 0) supaya tidak terbaca "transaksi nol".
    vol_last = ind.get("volume")
    nilai_trx = (float(vol_last) * float(harga)
                 if (vol_last is not None and harga is not None) else None)

    # Klasifikasi bertingkat: TINGGI mendiskualifikasi dari Top 10,
    # RINGAN hanya jadi catatan. Tidak ada flag yang dibuang.
    wlevel = WAS.klasifikasi(flags)

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
        # (Review 21 Okt) Nilai transaksi harian dalam rupiah (volume x harga).
        "nilai_transaksi": nilai_trx,
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
        # Waspada dua tingkat: `waspada` tetap berarti "ada flag risiko apa
        # pun" (kompatibel dengan konsumen lama); `waspada_tingkat` yang
        # menentukan hak masuk Top 10.
        "waspada": bool(flags),
        "waspada_tingkat": wlevel["tingkat"],
        "waspada_diskualifikasi": wlevel["diskualifikasi"],
        "flag_waspada": flags,
        "sinyal": _sinyal_label(ind, tech, cat),
        "_bars": bars,
        "_news": news,
        "_detik": round(time.time() - t0, 1),
    }


def _alerts_payload(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    """(Kelompok D) Alert pasar. Kegagalan alert tidak boleh menggagalkan
    publish data utama — kalau modul alert error, kirim struktur kosong
    yang jujur (tersedia: false), bukan menghentikan pipeline."""
    try:
        a = AL.kumpulkan(rows)
        a["tersedia"] = True
        return a
    except Exception as e:
        print(f"[pipeline] alert gagal: {type(e).__name__}: {e}", flush=True)
        return {"tersedia": False, "total": 0, "per_jenis": {},
                "per_severity": {"kritis": 0, "perhatian": 0, "info": 0},
                "dipotong": False, "daftar": [],
                "catatan": f"alert tidak dapat dihitung: {type(e).__name__}"}


def _median_valuasi_sektor(rows: List[Dict[str, Any]],
                           pe_med: Dict[str, float],
                           pb_med: Dict[str, float]) -> Dict[str, Any]:
    """(Kelompok D) Median valuasi per sektor + posisi tiap emiten
    terhadap median sektornya sendiri.

    Pembanding HARUS median sektor yang sama, bukan median seluruh pasar:
    PER 12 itu murah untuk bank tapi biasa untuk ritel.
    Sektor dengan < 3 emiten berdata tidak diberi median (terlalu tipis
    untuk disebut "median" — itu hanya rata-rata 1-2 angka).
    """
    sec_rows: Dict[str, List[Dict[str, Any]]] = {}
    for r in rows:
        s = (r.get("sektor") or "LAINNYA").upper()
        sec_rows.setdefault(s, []).append(r)

    out_sector: List[Dict[str, Any]] = []
    for s, rs in sorted(sec_rows.items()):
        pe = _f(pe_med.get(s))
        pb = _f(pb_med.get(s))
        n_pe = sum(1 for r in rs if _f(r.get("per")) is not None
                   and (_f(r.get("per")) or 0) > 0)
        n_pb = sum(1 for r in rs if _f(r.get("pbv")) is not None
                   and (_f(r.get("pbv")) or 0) > 0)
        out_sector.append({
            "sektor": s,
            "jumlah_emiten": len(rs),
            "median_per": round(pe, 2) if pe is not None else None,
            "median_pbv": round(pb, 2) if pb is not None else None,
            "n_per": n_pe,
            "n_pbv": n_pb,
            "cukup_data": bool(pe is not None or pb is not None),
            "catatan": None if (pe is not None or pb is not None)
            else f"data < 3 emiten (PER {n_pe}, PBV {n_pb}) — median tidak dihitung",
        })

    out_sector.sort(key=lambda x: (-x["jumlah_emiten"], x["sektor"]))
    return {
        "per_sektor": out_sector,
        "jumlah_sektor": len(out_sector),
        "sektor_dengan_median": sum(1 for x in out_sector if x["cukup_data"]),
        "ambang_min_emiten": 3,
        "catatan": ("Median dihitung per sektor dengan minimal 3 emiten "
                    "berdata; sektor tipis ditandai, bukan diisi angka."),
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
    # (Review 21 Okt) Ditambah periode: `chg_pct` = 1 sesi (hari ini),
    # `chg_5h_pct` = 5 sesi. Tanpa label periode, angka persen di kartu KPI
    # tidak bisa ditafsirkan. `_bars` masih ada di sini (di-pop setelah
    # ringkasan pasar dibuat), jadi tidak perlu request tambahan.
    sec_chg: Dict[str, List[float]] = defaultdict(list)
    sec_chg5: Dict[str, List[float]] = defaultdict(list)
    for r in rows:
        ch = r.get("chg_pct")
        if isinstance(ch, (int, float)):
            sec_chg[(r.get("sektor") or "LAINNYA").upper()].append(float(ch))
        b = r.get("_bars") or []
        if len(b) >= 6:
            try:
                c1, c0 = float(b[-1]["close"]), float(b[-6]["close"])
                if c0:
                    sec_chg5[(r.get("sektor") or "LAINNYA").upper()].append(
                        (c1 / c0 - 1) * 100)
            except (KeyError, TypeError, ValueError):
                pass
    sec_stats = [
        {
            "sektor": s,
            "chg_pct": round(sum(v) / len(v), 2),
            "periode_chg": "1 sesi (hari ini)",
            "jumlah": len(v),
            "chg_5h_pct": (round(sum(sec_chg5[s]) / len(sec_chg5[s]), 2)
                           if sec_chg5.get(s) else None),
            "periode_chg_5h": "5 sesi",
            "jumlah_5h": len(sec_chg5.get(s) or []),
        }
        for s, v in sec_chg.items() if len(v) >= 1
    ]
    sec_stats.sort(key=lambda x: x["chg_pct"], reverse=True)

    # net asing total (hanya kalau Sectors aktif & angkanya terkumpul)
    net_total = None
    if P.sectors_available():
        ff = P.get_foreign_flow()
        if ff.get("ok"):
            net_total = _parse_net_asing(ff.get("data"))

    # Rezim pasar: sumber tunggal idx/rezim.py. Dihitung dari bar IHSG yang
    # sama dengan ringkasan di atas (bukan fetch kedua -> tidak bisa beda).
    ihsg_bars = idx.get("data") if idx.get("ok") and isinstance(idx.get("data"), list) else []
    rezim = RZ.deteksi_rezim(ihsg_bars or [])

    return {
        "ihsg": ihsg,
        "sektor_terkuat": sec_stats[0] if sec_stats else None,
        "sektor_terlemah": sec_stats[-1] if sec_stats else None,
        "sektor": sec_stats,
        "net_asing_total": net_total,
        "net_asing_tersedia": net_total is not None,
        "rezim_pasar": rezim,
        "konteks_pasar": RZ.ringkas_konteks(rezim),
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
    """Label sinyal ringkas. Delegasi ke S.label_sinyal supaya definisinya
    SATU dengan modul backtest (tidak bisa melenceng)."""
    return S.label_sinyal(ind)


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

    # (Kelompok B) Kumpulkan baris harga per emiten untuk history.json.
    # diambil dari _bars yang SUDAH diunduh analyze_one — tanpa request
    # tambahan, jadi history tidak menambah beban jaringan sama sekali.
    bars_untuk_history: Dict[str, List[Dict[str, Any]]] = {}
    for r in rows:
        b = r.get("_bars")
        if b:
            bars_untuk_history[r["kode"]] = b

    # (D) Tanggal sesi bursa terbaru dari bar yang benar-benar ada.
    # Harus dihitung DI SINI: `_bars` di-pop di loop di bawah.
    tanggal_data = _tanggal_data_hist(bars_untuk_history)

    # Konteks pasar dihitung SEBELUM skoring akhir supaya flag "pasar
    # stres" bisa menempel ke tiap baris (tanpa mengubah angka skor).
    ringkasan_pasar = _market_summary(rows)
    konteks_pasar = ringkasan_pasar.get("konteks_pasar") or {}
    flag_konteks = S.flag_konteks_pasar(konteks_pasar)

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
            "konteks_pasar": flag_konteks,
        }
        r.pop("_bars", None)
        r.pop("_news", None)

    # (Review 21 Okt) Rincian skor parsial harus TERSIMPAN DI BARIS EMITEN,
    # bukan cuma di payload terpisah. Tanpa ini, tabel screener tidak bisa
    # menandai emiten yang skornya dihitung dari data sebagian — dan
    # "skor 88" terlihat setara dengan "skor 88 dari data 100%".
    for r in rows:
        det = r.get("skor_detail") or {}
        komp = det.get("komponen") or {}
        hilang = [n for n, c in komp.items()
                  if isinstance(c, dict) and not c.get("tersedia")]
        r["bobot_terisi"] = det.get("bobot_terisi")
        r["komponen_hilang"] = hilang
        # "Parsial" berarti skornya ADA tapi dihitung dari data sebagian.
        # Emiten tanpa skor bukan parsial — mereka tidak punya skor sama
        # sekali, dan menandainya "parsial" akan membuat pembaca salah paham.
        r["skor_parsial"] = bool(hilang) and r.get("skor") is not None

    # urutkan: skor desc, emiten gorengan selalu di bawah
    # (Review 21 Okt) Yang didorong ke bawah hanya waspada TINGGI. Waspada
    # RINGAN tetap ikut urutan skor — sebelumnya satu flag ringan seperti
    # "volume tidak tersedia" membuat emiten 81,0 terlempar di bawah skor 12.
    rows.sort(key=lambda r: (bool(r.get("waspada_diskualifikasi")),
                             -(r["skor"] if r.get("skor") is not None else -1)))

    # Top 10 Emiten: skor ada, bukan waspada TINGGI, dan likuid.
    # (Review 21 Okt) Tanpa syarat likuiditas, Top 10 bisa diisi emiten
    # dengan nilai transaksi Rp0,09 M yang tidak bisa dibeli tanpa
    # menggerakkan harga.
    def _kandidat(x: Dict[str, Any]) -> bool:
        if x.get("skor") is None or x.get("waspada_diskualifikasi"):
            return False
        n = x.get("nilai_transaksi")
        return n is not None and n >= MIN_NILAI_TRANSAKSI

    def _top_row(i: int, r: Dict[str, Any]) -> Dict[str, Any]:
        det = r.get("skor_detail") or {}
        komp = det.get("komponen") or {}
        hilang = [n for n, c in komp.items()
                  if isinstance(c, dict) and not c.get("tersedia")]
        return {
            "peringkat": i + 1, "kode": r["kode"], "nama": r.get("nama"),
            "skor": r.get("skor"), "sinyal": r.get("sinyal"),
            "harga": r.get("harga"), "chg_pct": r.get("chg_pct"),
            "sektor": r.get("sektor"),
            # transparansi kelayakan
            "nilai_transaksi": r.get("nilai_transaksi"),
            "bobot_terisi": det.get("bobot_terisi"),
            "komponen_hilang": hilang,
            "skor_parsial": bool(hilang),
            "waspada_tingkat": r.get("waspada_tingkat"),
            "flag_waspada": r.get("flag_waspada") or [],
        }

    top10 = [_top_row(i, r) for i, r in
             enumerate(x for x in rows if _kandidat(x))][:10]

    # Gugur karena likuiditas: kandidat skor-tertinggi yang TIDAK diskualifikasi
    # waspada tetapi nilai transaksinya di bawah ambang. Dilaporkan eksplisit
    # supaya angka "gugur" bisa diaudit, bukan angka tersembunyi.
    _gugur_liq = [{
        "kode": r["kode"], "skor": r.get("skor"),
        "nilai_transaksi": r.get("nilai_transaksi"),
    } for r in rows
        if r.get("skor") is not None and not r.get("waspada_diskualifikasi")
        and (r.get("nilai_transaksi") is None
             or float(r["nilai_transaksi"]) < MIN_NILAI_TRANSAKSI)]
    _gugur_liq.sort(key=lambda x: -(x["skor"] or 0))

    _gugur_waspada = [{
        "kode": r["kode"], "skor": r.get("skor"),
        "flag_waspada": r.get("flag_waspada") or [],
    } for r in rows
        if r.get("skor") is not None and r.get("waspada_diskualifikasi")]
    _gugur_waspada.sort(key=lambda x: -(x["skor"] or 0))

    # Kandidat teratas TANPA syarat likuiditas — pembanding agar terlihat
    # apa yang berubah karena syarat itu.
    _tanpa_syarat = [_top_row(i, r) for i, r in enumerate(
        x for x in rows if x.get("skor") is not None
        and not x.get("waspada_diskualifikasi"))][:10]

    kriteria_top10 = {
        "syarat_likuiditas": {
            "nilai_transaksi_min": MIN_NILAI_TRANSAKSI,
            "nilai_transaksi_min_teks": _rupiah_ringkas(MIN_NILAI_TRANSAKSI),
            "dasar": "volume x harga sesi terakhir (nilai transaksi harian)",
        },
        "waspada": "hanya waspada TINGGI yang mendiskualifikasi",
        "jumlah_gugur_likuiditas": len(_gugur_liq),
        "jumlah_gugur_waspada": len(_gugur_waspada),
        "gugur_likuiditas": _gugur_liq[:15],
        "gugur_waspada": _gugur_waspada[:15],
        "top10_tanpa_syarat_likuiditas": _tanpa_syarat,
        "yang_gugur_dari_tanpa_syarat": [
            x["kode"] for x in _tanpa_syarat
            if x["kode"] not in {t["kode"] for t in top10}],
    }

    payload = {
        "diperbarui_pada": P.stamp_wib(),
        "diperbarui_pada_teks": P.human_wib(),
        "versi_skema": VERSI_SKEMA,
        "satuan": SATUAN_DATA,
        "hari_libur_bursa": _hari_libur_bursa(),
        "sumber_utama": "sectors" if P.sectors_available() else "yfinance",
        "sectors_aktif": P.sectors_available(),
        # (E) Metadata sumber: dari mana tiap field berasal, dihitung per
        # emiten. Dashboard generik "yfinance" saja menyembunyikan fakta
        # bahwa 59 emiten tidak punya data sama sekali.
        "sumber_data_ringkas": _ringkas_sumber(rows),
        "jumlah_emiten": len(rows),
        "jumlah_ada_harga": sum(1 for r in rows if r.get("harga") is not None),
        "jumlah_ada_fundamental": sum(1 for r in rows if r.get("per") is not None
                                      or r.get("roe") is not None),
        "jumlah_waspada": sum(1 for r in rows if r.get("waspada")),
        # (Review 21 Okt) Rincian waspada per kriteria + per tingkat, supaya
        # angka "507" bisa diaudit dan tidak lagi terlihat seperti satu
        # gerombolan emiten yang sama buruknya.
        "waspada_ringkasan": WAS.ringkas(rows),
        # Tanggal SESI BURSA data ini berasal -- BUKAN jam pipeline jalan.
        # Pipeline jalan 16:30 WIB (sesi sudah tutup) atau pagi hari kerja
        # (sesi belum buka), jadi `diperbarui_pada` bisa lebih maju 1 hari
        # dari tanggal bar terakhir. Bandingkan tanggal data dengan ini.
        "tanggal_data": tanggal_data,
        "median_per_sektor": pe_med,
        "median_pbv_sektor": pb_med,
        "ringkasan_pasar": ringkasan_pasar,
        "konteks_pasar": konteks_pasar,
        "alerts": _alerts_payload(rows),
        "skor_parsial": AL.skor_parsial(rows),
        "median_valuasi_sektor": _median_valuasi_sektor(rows, pe_med, pb_med),
        "top10": top10,
        "kriteria_top10": kriteria_top10,
        "emiten": rows,
        "disclaimer": "Hasil screening otomatis, bukan rekomendasi beli.",
    }
    out_path = os.path.join(DATA_DIR, out_name)

    # ── (Kelompok E) Validasi prepublish ──────────────────────────────
    # Sebelum ini latest.json ditulis tanpa pemeriksaan apa pun: run yang
    # menghasilkan harga kosong / satuan kacau langsung merusak dashboard.
    # Sekarang data divalidasi dulu; kalau GAGAL, file lama DIPERTAHANKAN
    # dan pipeline melaporkan gagal (bukan diam-diam menimpa data sehat).
    run_penuh_e = len(emiten) >= total_universe
    try:
        from idx import validate as VALID
    except ImportError:
        import validate as VALID
    vres = VALID.validasi(payload, total_universe=total_universe,
                          run_penuh=run_penuh_e)
    payload["validasi"] = {
        "ok": vres["ok"],
        "errors": vres["errors"],
        "warnings": vres["warnings"],
        "stats": vres["stats"],
    }
    print(VALID.ringkas(vres), flush=True)
    for w in vres["warnings"]:
        print(f"[validasi] warning: {w}", flush=True)

    if not vres["ok"]:
        for e in vres["errors"]:
            print(f"[validasi] ERROR: {e}", flush=True)
        lama = _baca_json(out_path)
        print(f"[pipeline] publish DITOLAK — {out_name} lama "
              f"({'ada' if lama else 'tidak ada'}) tidak ditimpa", flush=True)
        return {"ok": False, "out": None, "ditolak": True,
                "alasan": vres["errors"],
                "warnings": vres["warnings"],
                "validasi": vres["stats"],
                "jumlah": len(rows),
                "ada_harga": payload["jumlah_ada_harga"],
                "ada_fundamental": payload["jumlah_ada_fundamental"]}

    _tulis_atomic(out_path, payload)

    # ── (Kelompok B) history.json: SATU file riwayat harga untuk semua emiten ──
    # Aman ditulis pada run parsial juga: merge_lama mempertahankan riwayat
    # emiten yang gagal diambil pada run ini, jadi file tidak pernah bolong.
    try:
        hist_payload = HIST.build(
            bars_untuk_history,
            max_sesi=HIST.SESI_MAKS,
            sumber=payload.get("sumber_utama", ""),
        )
        lama = HIST.load()
        hist_payload, dipertahankan = HIST.merge_lama(hist_payload, lama)
        # Kalau hasil akhirnya kosong TIDAK BOLEH menimpa history yang ada:
        # lebih baik file lama (utuh) daripada file kosong.
        if not hist_payload["emiten"] and lama and lama.get("emiten"):
            print("[pipeline] history.json: run ini tidak menghasilkan baris "
                  "-> file lama dipertahankan, tidak ditimpa", flush=True)
            history_info = {"file": HIST.HISTORY_FILE,
                            "emiten": len(lama.get("emiten", {})),
                            "sesi": sum(len(x.get("d", []))
                                       for x in lama.get("emiten", {}).values()),
                            "dipertahankan": True}
        else:
            HIST.publish(hist_payload)
            print(f"[pipeline] history.json: {hist_payload['jumlah_emiten']} emiten, "
                  f"{hist_payload['jumlah_sesi']} sesi"
                  + (f" ({dipertahankan} emiten dipertahankan dari run lama)"
                     if dipertahankan else ""), flush=True)
            history_info = {"file": HIST.HISTORY_FILE,
                            "emiten": hist_payload["jumlah_emiten"],
                            "sesi": hist_payload["jumlah_sesi"]}
    except Exception as e:
        print(f"[pipeline] history.json GAGAL: {type(e).__name__}: {e}", flush=True)
        history_info = None

    # Snapshot harian hanya ditulis oleh run LENGAP. Run uji/partial akan
    # menimpa snapshot harian dengan data sebagian dan merusak riwayat skor.
    run_penuh = len(emiten) >= total_universe
    snap = os.path.join(SNAP_DIR, P.now_wib().strftime("%Y-%m-%d.json"))
    if not run_penuh:
        print(f"[pipeline] run parsial ({len(emiten)}/{total_universe} emiten) "
              f"-> snapshot harian tidak ditimpa", flush=True)
        return {"ok": True, "out": out_path, "snapshot": None,
                "jumlah": len(rows),
                "history": history_info,
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

    # ── (Kelompok E) Perlindungan snapshot harian ─────────────────────
    # `run_penuh` saja tidak cukup: run bisa menyapu seluruh universe tapi
    # separuh harganya gagal diambil. Menimpanya di tanggal yang sama akan
    # membuang snapshot pagi yang lebih lengkap. Jadi bandingkan cakupan
    # harga dengan snapshot yang SUDAH ada di tanggal ini.
    layak, info_snap = snapshot_layak_timpa(snap, snap_rows)
    if not layak:
        print(f"[pipeline] snapshot {os.path.basename(snap)} TIDAK ditimpa: "
              f"run ini {info_snap['baru']} emiten berharga, "
              f"snapshot lama {info_snap['lama']}", flush=True)
        return {"ok": True, "out": out_path, "snapshot": snap,
                "snapshot_dipertahankan": True,
                "snapshot_harga_lama": info_snap["lama"],
                "snapshot_harga_baru": info_snap["baru"],
                "jumlah": len(rows),
                "history": history_info,
                "ada_harga": payload["jumlah_ada_harga"],
                "ada_fundamental": payload["jumlah_ada_fundamental"]}

    snap_baru_harga = info_snap["baru"]
    _tulis_atomic(snap, {"tanggal": P.now_wib().strftime("%Y-%m-%d"),
                         "tanggal_data": tanggal_data,
                         "diperbarui_pada": P.stamp_wib(),
                         "versi_skema": VERSI_SKEMA,
                         "jumlah_emiten": len(snap_rows),
                         "jumlah_ada_harga": snap_baru_harga,
                         "emiten": snap_rows})
    # index snapshot dibuat SETELAH snapshot hari ini ditulis
    try:
        from idx import snapshot_index          # jalan sebagai modul paket
    except ImportError:
        import snapshot_index                    # jalan sebagai script langsung
    snapshot_index.build()

    # ── backtest vs IHSG (Kelompok C) ──────────────────────────────
    # Dihitung dari history.json yang baru saja dipublish, jadi selalu
    # konsisten dengan data yang dilihat dashboard. Kegagalan backtest
    # TIDAK boleh menggagalkan pipeline: latest.json sudah aman ditulis,
    # backtest lama (kalau ada) dibiarkan apa adanya.
    backtest_info: Dict[str, Any] = {"ok": False, "alasan": "belum dijalankan"}
    try:
        try:
            from idx import backtest as BT          # sebagai paket
        except ImportError:
            import backtest as BT                    # sebagai script langsung
        bt = BT.build(tulis=True)
        if bt.get("ok"):
            c = bt.get("cakupan") or {}
            backtest_info = {"ok": True,
                             "emiten": c.get("emiten_dipakai"),
                             "titik": c.get("titik_dihitung"),
                             "ihsg_bar": bt.get("ihsg_bar")}
        else:
            backtest_info = {"ok": False, "alasan": bt.get("error")}
            print(f"[pipeline] backtest dilewati: {bt.get('error')}", flush=True)
    except Exception as e:  # jangan sampai mematikan publish utama
        backtest_info = {"ok": False, "alasan": f"{type(e).__name__}: {e}"}
        print(f"[pipeline] backtest error: {type(e).__name__}: {e}", flush=True)

    return {"ok": True, "out": out_path, "snapshot": snap,
            "jumlah": len(rows),
            "history": history_info,
            "backtest": backtest_info,
            "ada_harga": payload["jumlah_ada_harga"],
            "ada_fundamental": payload["jumlah_ada_fundamental"]}


if __name__ == "__main__":
    # (Review 21 Okt) Argumen harus diparse dengan benar: `python3 -m
    # idx.pipeline --out uji.json` sebelumnya crash karena `int(sys.argv[1])`
    # mencoba mengubah "--out" jadi integer. Sekarang flag non-numerik dilewati.
    lim = UNIVERSE_LIMIT
    out = "latest.json"
    kat = True
    i = 1
    while i < len(sys.argv):
        a = sys.argv[i]
        if a == "--out" and i + 1 < len(sys.argv):
            out = sys.argv[i + 1]
            i += 2
            continue
        if a == "--no-katalis":
            kat = False
            i += 1
            continue
        try:
            lim = int(a)
        except ValueError:
            pass
        i += 1
    res = run(universe_limit=lim, dengan_katalis=kat, out_name=out)
    print(json.dumps(res, ensure_ascii=False, indent=1))
