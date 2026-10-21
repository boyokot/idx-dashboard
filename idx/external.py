"""
Tahap 1 (Kelompok F) — pengumpul DATA EKSTERNAL harian.

Menyimpan hasil ke data/external.json (atomic write + timestamp WIB).

Prinsip modul ini:
  * TIDAK ada tebakan. Sumber gagal -> string "data tidak tersedia" + alasan.
  * Setiap nilai membawa sumber + tanggal, supaya bisa dilacak.
  * Modul ini TIDAK mengubah skor apa pun. Skor eksternal baru dipakai pada
    Tahap 4; Tahap 1 hanya mengumpulkan & menyimpan.

Jalankan:
    python3 -m idx.external          (dari /root/idx-dashboard)
    python3 idx/external.py
"""
from __future__ import annotations

import json
import os
import re
import statistics
import urllib.request
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional, Tuple

try:                                    # jalan sebagai modul paket
    from idx import providers as P
except Exception:                       # jalan sebagai script langsung
    import providers as P               # type: ignore

VERSI_EKSTERNAL = "1.0.0"
TIDAK_ADA = "data tidak tersedia"

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PATH_EXTERNAL = os.path.join(_ROOT, "data", "external.json")
PATH_HISTORY = os.path.join(_ROOT, "data", "history.json")

WIB = timezone(timedelta(hours=7))

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0 Safari/537.36")


# ── util ────────────────────────────────────────────────────────────────────
def sekarang_wib() -> datetime:
    return datetime.now(WIB)


def _tidak_ada(alasan: str) -> Dict[str, Any]:
    """Bentuk baku untuk sumber yang gagal — jangan pernah diisi 0/tebakan."""
    return {"nilai": TIDAK_ADA, "alasan": alasan, "sumber": TIDAK_ADA,
            "tanggal_sumber": None}


def _angka(v: Any) -> Optional[float]:
    """Konversi ke float tanpa melempar; None kalau tidak masuk akal."""
    if v is None or isinstance(v, bool):
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    if f != f or f in (float("inf"), float("-inf")):
        return None
    return f


def _tulis_atomic(path: str, payload: Any) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = f"{path}.tmp.{os.getpid()}"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=1)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def _get_html(url: str, timeout: int = 30) -> Tuple[Optional[str], Optional[str]]:
    """Ambil HTML. Kembalikan (html, error). Tidak pernah melempar."""
    req = urllib.request.Request(url, headers={
        "User-Agent": UA,
        "Accept": "text/html,application/xhtml+xml",
        "Accept-Language": "id-ID,id;q=0.9,en;q=0.8",
    })
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read()
        for enc in ("utf-8", "cp1252", "latin-1"):
            try:
                return raw.decode(enc), None
            except UnicodeDecodeError:
                continue
        return raw.decode("utf-8", "ignore"), None
    except Exception as e:
        return None, f"{type(e).__name__}: {e}"


def _teks_sel(html: str) -> str:
    s = re.sub(r"<script.*?</script>", " ", html, flags=re.S | re.I)
    s = re.sub(r"<style.*?</style>", " ", s, flags=re.S | re.I)
    s = re.sub(r"<[^>]+>", " ", s)
    s = s.replace("&#160;", " ").replace("&nbsp;", " ").replace("\u200b", " ")
    return re.sub(r"\s+", " ", s).strip()


def _tabel(html: str) -> List[List[List[str]]]:
    """Semua <table> → list baris → list sel teks."""
    out: List[List[List[str]]] = []
    for t in re.findall(r"<table.*?</table>", html, re.S | re.I):
        rows: List[List[str]] = []
        for rr in re.findall(r"<tr.*?</tr>", t, re.S | re.I):
            cells = [re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", c)).strip()
                     for c in re.findall(r"<t[dh].*?</t[dh]>", rr, re.S | re.I)]
            cells = [c.replace("&#160;", " ").replace("\u200b", "").strip()
                     for c in cells]
            if any(cells):
                rows.append(cells)
        if rows:
            out.append(rows)
    return out


def _parse_persen_id(txt: str) -> Optional[float]:
    """'5,75 %' / '5.75%' -> 5.75"""
    m = re.search(r"(-?\d+[.,]\d+|-?\d+)\s*%", txt or "")
    if not m:
        return None
    return _angka(m.group(1).replace(".", "").replace(",", ".")) \
        if "," in m.group(1) else _angka(m.group(1))


# ── 1. MAKRO (yfinance) ─────────────────────────────────────────────────────
# Simbol yang benar-benar terbukti ada datanya (dicek 21 Okt 2026):
#   ^JKSE, USDIDR=X, ^GSPC, CL=F (minyak), GC=F (emas), CPO=F (CPO Malaysia),
#   TIO=F (iron ore 62% Fe CFR China).
# Batubara & nikel TIDAK punya kontrak yang bisa diambil dari yfinance
# (COAL=F, MTF=F, NI=F, CO=F semua kosong / delisted per 21 Okt 2026)
# -> ditulis "data tidak tersedia", TIDAK diganti proxy yang mirip.
MAKRO_YF: List[Dict[str, str]] = [
    {"kunci": "ihsg",     "simbol": "^JKSE",    "label": "IHSG",             "satuan": "poin"},
    {"kunci": "usdidr",   "simbol": "USDIDR=X", "label": "USD/IDR",          "satuan": "IDR"},
    {"kunci": "sp500",    "simbol": "^GSPC",    "label": "S&P 500",          "satuan": "poin"},
    {"kunci": "minyak",   "simbol": "CL=F",     "label": "Minyak WTI",       "satuan": "USD/barel"},
    {"kunci": "emas",     "simbol": "GC=F",     "label": "Emas",             "satuan": "USD/ons"},
    {"kunci": "cpo",      "simbol": "CPO=F",    "label": "CPO (Malaysia)",   "satuan": "MYR/ton"},
    {"kunci": "iron_ore", "simbol": "TIO=F",    "label": "Biji Besi 62% Fe", "satuan": "USD/ton"},
]

KOMODITAS_TANPA_SUMBER: List[Dict[str, str]] = [
    {"kunci": "batubara", "label": "Batubara (Newcastle/API2)",
     "alasan": "tidak ada kontrak batubara yang tersedia di yfinance "
               "(COAL=F/MTF=F kosong); sumber lain (IDX/globalcoal) "
               "diblokir atau berbayar"},
    {"kunci": "nikel", "label": "Nikel (LME)",
     "alasan": "tidak ada kontrak nikel yang tersedia di yfinance "
               "(NI=F/CO=F kosong); LME butuh langganan"},
]


def _riwayat(simbol: str, periode: str = "3mo") -> List[Dict[str, Any]]:
    """Ambil riwayat close via providers (sudah menangani suffix .JK & cache)."""
    res = P.yfinance_history(simbol, period=periode)
    if not res.get("ok"):
        return []
    rows = res.get("data") or []
    return [r for r in rows if _angka(r.get("close")) is not None]


def _ringkas_seri(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Harga terakhir + perubahan 1/5/20 sesi dari seri close."""
    if not rows:
        return {}
    close = [_angka(r.get("close")) for r in rows]
    close = [c for c in close if c is not None]
    if not close:
        return {}

    def ret(n: int) -> Optional[float]:
        if len(close) <= n:
            return None
        dasar = close[-1 - n]
        if not dasar:
            return None
        return round((close[-1] / dasar - 1.0) * 100.0, 3)

    return {
        "terakhir": round(close[-1], 4),
        "tanggal": rows[-1].get("date"),
        "chg_1h_pct": ret(1),
        "chg_5h_pct": ret(5),
        "chg_20h_pct": ret(20),
        "jumlah_bar": len(close),
    }


def ambil_makro() -> Dict[str, Any]:
    """Makro + komoditas. Field yang gagal = 'data tidak tersedia'."""
    out: Dict[str, Any] = {"diambil_pada": P.stamp_wib()}
    for item in MAKRO_YF:
        k = item["kunci"]
        rows = _riwayat(item["simbol"])
        if not rows:
            out[k] = _tidak_ada(f"yfinance tidak punya data untuk {item['simbol']}")
            out[k]["label"] = item["label"]
            out[k]["simbol"] = item["simbol"]
            continue
        s = _ringkas_seri(rows)
        if not s:
            out[k] = _tidak_ada(f"seri close kosong untuk {item['simbol']}")
            out[k]["label"] = item["label"]
            out[k]["simbol"] = item["simbol"]
            continue
        out[k] = {
            "label": item["label"],
            "simbol": item["simbol"],
            "satuan": item["satuan"],
            "nilai": s["terakhir"],
            "tanggal_sumber": s["tanggal"],
            "chg_1h_pct": s["chg_1h_pct"],
            "chg_5h_pct": s["chg_5h_pct"],
            "chg_20h_pct": s["chg_20h_pct"],
            "jumlah_bar": s["jumlah_bar"],
            "sumber": "yfinance",
        }
    for item in KOMODITAS_TANPA_SUMBER:
        out[item["kunci"]] = {
            "label": item["label"],
            "nilai": TIDAK_ADA,
            "alasan": item["alasan"],
            "sumber": TIDAK_ADA,
            "tanggal_sumber": None,
        }
    return out


# ── 2. BI RATE (situs resmi bi.go.id) ───────────────────────────────────────
URL_BI_RATE = "https://www.bi.go.id/id/statistik/indikator/bi-rate.aspx"
URL_BI_INFLASI = "https://www.bi.go.id/id/statistik/indikator/data-inflasi.aspx"


def ambil_bi_rate() -> Dict[str, Any]:
    """BI-Rate terbaru dari tabel resmi bi.go.id.

    Tabel yang dipakai berheader: No | Tanggal | BI-7 Day | BI-Rate | Pranala.
    Kolom 'BI-7 Day' berisi nilai rate (mis. '5,75 %').
    """
    html, err = _get_html(URL_BI_RATE)
    if not html:
        return _tidak_ada(f"bi.go.id tidak bisa diakses ({err})")

    for tab in _tabel(html):
        hdr = " ".join(tab[0]).lower()
        if "bi-rate" not in hdr and "bi-7" not in hdr:
            continue
        for row in tab[1:]:
            if len(row) < 3:
                continue
            tgl_txt = row[1] if not row[0].strip().isdigit() else row[1]
            nilai = None
            for sel in row[1:]:
                nilai = _parse_persen_id(sel)
                if nilai is not None:
                    break
            if nilai is None:
                continue
            tgl_iso = _parse_tanggal_id(tgl_txt)
            if tgl_iso is None:
                continue
            return {
                "nilai_pct": nilai,
                "tanggal": tgl_iso,
                "tanggal_teks": tgl_txt,
                "sumber": "bi.go.id — Statistik Indikator BI-Rate",
                "url": URL_BI_RATE,
                "satuan": "persen",
            }
    return _tidak_ada("tabel BI-Rate tidak ditemukan di halaman bi.go.id")


def ambil_inflasi() -> Dict[str, Any]:
    """Inflasi terbaru (YoY) dari tabel resmi bi.go.id."""
    html, err = _get_html(URL_BI_INFLASI)
    if not html:
        return _tidak_ada(f"bi.go.id tidak bisa diakses ({err})")

    for tab in _tabel(html):
        hdr = " ".join(tab[0]).lower()
        if "inflation" not in hdr and "inflasi" not in hdr:
            continue
        for row in tab[1:]:
            if len(row) < 2:
                continue
            nilai = _parse_persen_id(row[1])
            if nilai is None:
                continue
            return {
                "nilai_pct": nilai,
                "periode": row[0].strip(),
                "sumber": "bi.go.id — Statistik Indikator Data Inflasi",
                "url": URL_BI_INFLASI,
                "satuan": "persen (YoY)",
            }
    return _tidak_ada("tabel inflasi tidak ditemukan di halaman bi.go.id")


BULAN_ID = {
    "januari": 1, "februari": 2, "maret": 3, "april": 4, "mei": 5, "juni": 6,
    "juli": 7, "agustus": 8, "september": 9, "oktober": 10, "november": 11,
    "desember": 12,
}


def _parse_tanggal_id(txt: str) -> Optional[str]:
    """'21 Oktober 2026' -> '2026-10-21'. None kalau tidak dikenali."""
    m = re.search(r"(\d{1,2})\s+([A-Za-z]+)\s+(\d{4})", txt or "")
    if not m:
        return None
    bln = BULAN_ID.get(m.group(2).lower())
    if not bln:
        return None
    try:
        return datetime(int(m.group(3)), bln, int(m.group(1))).strftime("%Y-%m-%d")
    except ValueError:
        return None


# ── 3. KALENDER: RAPAT BI (jadwal RDG resmi) ────────────────────────────────
URL_BI_KALENDER = "https://www.bi.go.id/id/publikasi/kalender/default.aspx"


def ambil_kalender_bi(tahun: Optional[int] = None) -> Dict[str, Any]:
    """Jadwal Rapat Dewan Gubernur (RDG) dari kalender resmi BI.

    Tabel jadwal berheader 'No | Bulan | Waktu'. Sel Waktu berisi rentang
    seperti 'Selasa-Rabu, 20-21 Oktober 2026' → diambil tanggal TERAKHIR
    (hari keputusan rate diumumkan).
    """
    tahun = tahun or sekarang_wib().year
    html, err = _get_html(URL_BI_KALENDER)
    if not html:
        return {"nilai": TIDAK_ADA, "alasan": f"kalender BI gagal ({err})",
                "sumber": TIDAK_ADA, "events": []}

    events: List[Dict[str, Any]] = []
    for tab in _tabel(html):
        hdr = " ".join(tab[0]).lower()
        if "bulan" not in hdr or "waktu" not in hdr:
            continue
        for row in tab[1:]:
            if len(row) < 3:
                continue
            bulan, waktu = row[1].strip("* "), row[2]
            # Format: "Selasa-Rabu,  20-21 Januari 2026" atau "Rabu, 8 Januari 2026".
            # Ambil HANYA kelompok hari di depan nama bulan — jangan
            # memindai seluruh sel, karena "2026" ikut terpecah jadi "20"/"26"
            # dan menghasilkan tanggal palsu (bug 21 Okt 2026: Januari 20-21
            # terbaca 26 Januari).
            m = re.search(r"(\d{1,2})(?:\s*[-\u2013]\s*(\d{1,2}))?\s+"
                          r"([A-Za-z]+)\s+(\d{4})", waktu)
            if not m:
                continue
            hari = int(m.group(2) or m.group(1))   # tanggal TERAKHIR rentang
            bln = BULAN_ID.get(m.group(3).lower())
            thn = int(m.group(4))
            if not bln:
                continue
            try:
                d = datetime(thn, bln, hari)
            except ValueError:
                continue
            events.append({
                "tanggal": d.strftime("%Y-%m-%d"),
                "judul": f"Rapat Dewan Gubernur BI ({bulan or m.group(3)})",
                "jenis": "rapat_bi",
                "sumber": "bi.go.id — Kalender Publikasi",
                "url": URL_BI_KALENDER,
            })
    events.sort(key=lambda e: e["tanggal"])
    if not events:
        return {"nilai": TIDAK_ADA, "alasan": "tabel jadwal RDG tidak ditemukan",
                "sumber": TIDAK_ADA, "events": []}
    hari_ini = sekarang_wib().strftime("%Y-%m-%d")
    return {
        "nilai": "tersedia",
        "sumber": "bi.go.id — Kalender Publikasi",
        "url": URL_BI_KALENDER,
        "jumlah_event": len(events),
        "events": events,
        "berikutnya": next((e for e in events if e["tanggal"] >= hari_ini), None),
    }


# ── 4. KEKUATAN RELATIF SEKTOR (dari data emiten) ───────────────────────────
def _ret_seri(close: List[float], n: int) -> Optional[float]:
    if len(close) <= n:
        return None
    dasar = close[-1 - n]
    if not dasar:
        return None
    return (close[-1] / dasar - 1.0) * 100.0


def hitung_kekuatan_sektor(hist: Dict[str, Any],
                          sektor_map: Dict[str, str],
                          ihsg_rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Return sektor 5 & 20 sesi dibanding IHSG.

    Return sektor = MEDIAN return emiten di sektor itu (median, bukan mean,
    supaya satu emiten liar tidak menggeser seluruh sektor).
    Hanya sektor dengan >= 3 emiten berdata yang diberi angka.
    """
    ihsg_close = [_angka(r.get("close")) for r in ihsg_rows]
    ihsg_close = [c for c in ihsg_close if c is not None]
    r_ihsg = {n: _ret_seri(ihsg_close, n) for n in (5, 20)}

    emiten = hist.get("emiten") or {}
    per_sektor: Dict[str, Dict[str, List[float]]] = {}
    for kode, seri in emiten.items():
        sek = (sektor_map.get(kode) or "").upper().strip()
        if not sek:
            continue
        close = [_angka(c) for c in (seri.get("c") or [])]
        close = [c for c in close if c is not None]
        if len(close) < 6:
            continue
        d = per_sektor.setdefault(sek, {"r5": [], "r20": []})
        r5 = _ret_seri(close, 5)
        r20 = _ret_seri(close, 20)
        if r5 is not None:
            d["r5"].append(r5)
        if r20 is not None:
            d["r20"].append(r20)

    out: List[Dict[str, Any]] = []
    for sek, d in per_sektor.items():
        n = max(len(d["r5"]), len(d["r20"]))
        if n < 3:
            out.append({"sektor": sek, "jumlah_emiten": n,
                        "nilai": TIDAK_ADA,
                        "alasan": "kurang dari 3 emiten berdata"})
            continue
        r5 = round(statistics.median(d["r5"]), 3) if d["r5"] else None
        r20 = round(statistics.median(d["r20"]), 3) if d["r20"] else None
        out.append({
            "sektor": sek,
            "jumlah_emiten": n,
            "ret_5h_pct": r5,
            "ret_20h_pct": r20,
            "rel_5h_pct": (round(r5 - r_ihsg[5], 3)
                           if r5 is not None and r_ihsg[5] is not None else None),
            "rel_20h_pct": (round(r20 - r_ihsg[20], 3)
                            if r20 is not None and r_ihsg[20] is not None else None),
        })
    out.sort(key=lambda x: (x.get("rel_5h_pct") is None,
                            -(x.get("rel_5h_pct") or 0)))
    kuat = next((x for x in out if x.get("rel_5h_pct") is not None), None)
    lemah = next((x for x in reversed(out) if x.get("rel_5h_pct") is not None), None)
    return {
        "ihsg_ret_5h_pct": None if r_ihsg[5] is None else round(r_ihsg[5], 3),
        "ihsg_ret_20h_pct": None if r_ihsg[20] is None else round(r_ihsg[20], 3),
        "per_sektor": out,
        "jumlah_sektor": len(out),
        "sektor_terkuat": kuat,
        "sektor_terlemah": lemah,
        "catatan": ("Return sektor = median return emiten (min. 3 emiten "
                    "berdata). rel_* = selisih terhadap IHSG pada jendela sama."),
        "sumber": "data/history.json + ^JKSE (yfinance)",
    }


# ── 5. ALIRAN DANA & KALENDER EMITEN (Sectors/IDX) ──────────────────────────
def ambil_aliran_dana() -> Dict[str, Any]:
    """Net asing & broker summary. Sectors tanpa API key -> tidak tersedia."""
    if not P.sectors_available():
        return {
            "nilai": TIDAK_ADA,
            "alasan": ("SECTORS_API_KEY tidak tersedia; net asing & broker "
                       "summary tidak bisa diambil tanpa key"),
            "sumber": TIDAK_ADA,
            "net_asing": TIDAK_ADA,
            "broker_summary": TIDAK_ADA,
        }
    res = P.sectors_foreign_flow()
    if not res.get("ok"):
        return {"nilai": TIDAK_ADA, "alasan": res.get("error") or "gagal",
                "sumber": "sectors", "net_asing": TIDAK_ADA,
                "broker_summary": TIDAK_ADA}
    return {"nilai": "tersedia", "sumber": "sectors",
            "net_asing": res.get("data"),
            "broker_summary": TIDAK_ADA}


def ambil_kalender_emiten() -> Dict[str, Any]:
    """Tanggal rilis lapkeu, RUPS, cum-date dividen.

    www.idx.co.id memblokir Cloudflare (403) untuk semua endpoint yang
    dicoba pada 21 Okt 2026 -> ditulis "data tidak tersedia", bukan ditebak
    dari pola musiman.
    """
    if not P.sectors_available():
        alasan = ("tidak ada sumber sah yang bisa diakses: idx.co.id menolak "
                  "dengan Cloudflare 403, dan SECTORS_API_KEY tidak tersedia")
        return {"nilai": TIDAK_ADA, "alasan": alasan, "events": [],
                "sumber": TIDAK_ADA,
                "cek": {
                    "idx_rups": "HTTP 403 (Cloudflare)",
                    "idx_dividen": "HTTP 403 (Cloudflare)",
                    "idx_lapkeu": "HTTP 403 (Cloudflare)",
                    "sectors": "tidak ada API key",
                }}
    return {"nilai": TIDAK_ADA,
            "alasan": "endpoint kalender emiten belum diimplementasikan untuk Sectors",
            "events": [], "sumber": "sectors"}


# ── orkestrasi ──────────────────────────────────────────────────────────────
def _muat_json(path: str) -> Optional[Any]:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def bangun(hist: Optional[Dict[str, Any]] = None,
           latest: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Bangun payload external.json lengkap. Tidak menulis file."""
    hist = hist if hist is not None else (_muat_json(PATH_HISTORY) or {})
    latest = latest if latest is not None else (_muat_json(
        os.path.join(_ROOT, "data", "latest.json")) or {})

    sektor_map = {e.get("kode"): (e.get("sektor") or "")
                  for e in (latest.get("emiten") or []) if e.get("kode")}

    makro = ambil_makro()
    ihsg_rows = _riwayat("^JKSE")
    kekuatan = hitung_kekuatan_sektor(hist, sektor_map, ihsg_rows)

    return {
        "versi_skema": VERSI_EKSTERNAL,
        "diperbarui_pada": P.stamp_wib(),
        "diperbarui_pada_teks": P.human_wib(),
        "tanggal_data": latest.get("tanggal_data"),
        "makro": makro,
        "bi_rate": ambil_bi_rate(),
        "inflasi": ambil_inflasi(),
        "kalender_bi": ambil_kalender_bi(),
        "kekuatan_sektor": kekuatan,
        "aliran_dana": ambil_aliran_dana(),
        "kalender_emiten": ambil_kalender_emiten(),
        "disclaimer": "Hasil screening otomatis, bukan rekomendasi beli.",
        "catatan": ("Nilai 'data tidak tersedia' berarti sumber benar-benar "
                    "tidak bisa diakses atau tidak punya data — bukan nol, "
                    "bukan tebakan."),
    }


def jalankan(tulis: bool = True) -> Dict[str, Any]:
    payload = bangun()
    if tulis:
        _tulis_atomic(PATH_EXTERNAL, payload)
    return payload


if __name__ == "__main__":
    import sys
    p = jalankan(tulis="--dry" not in sys.argv)
    print(json.dumps(p, ensure_ascii=False, indent=1)[:4000])
