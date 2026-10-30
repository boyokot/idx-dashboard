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
    from idx import anomali as ANOM
    from idx import providers as P
    from idx import rezim as RZ
except Exception:                       # jalan sebagai script langsung
    import anomali as ANOM              # type: ignore
    import providers as P               # type: ignore
    import rezim as RZ                  # type: ignore

VERSI_EKSTERNAL = "1.1.0"
TIDAK_ADA = "data tidak tersedia"

_ROOT = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
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
    {"kunci": "ihsg",     "simbol": "^JKSE",    "label": "IHSG",
     "mata_uang": "IDR",  "satuan": "poin indeks"},
    {"kunci": "usdidr",   "simbol": "USDIDR=X", "label": "USD/IDR",
     "mata_uang": "IDR",  "satuan": "rupiah per 1 USD"},
    {"kunci": "sp500",    "simbol": "^GSPC",    "label": "S&P 500",
     "mata_uang": "USD",  "satuan": "poin indeks"},
    {"kunci": "minyak",   "simbol": "CL=F",     "label": "Minyak WTI",
     "mata_uang": "USD",  "satuan": "USD per barel"},
    {"kunci": "emas",     "simbol": "GC=F",     "label": "Emas",
     "mata_uang": "USD",  "satuan": "USD per troy ounce"},
    # Diverifikasi 21 Okt 2026 lewat yfinance .info: currency=USD, exchange=CME,
    # nama kontrak "USD Malaysian Crude Palm Oil Ca..." -> kuotasi dalam USD.
    # (Bukan MYR seperti dugaan awal; kontrak Bursa Malaysia dalam MYR adalah
    # FCPO, tidak tersedia di yfinance.)
    {"kunci": "cpo",      "simbol": "CPO=F",    "label": "CPO (Malaysia, CME)",
     "mata_uang": "USD",  "satuan": "USD per metrik ton"},
    {"kunci": "iron_ore", "simbol": "TIO=F",    "label": "Biji Besi 62% Fe",
     "mata_uang": "USD",  "satuan": "USD per metrik ton"},
]

# Komoditas yang tidak punya seri harga langsung. Sesuai instruksi, dipakai
# PROXY kekuatan relatif sektor dan WAJIB berlabel "proxy" agar tidak
# disalahartikan sebagai harga komoditas.
PROXY_KOMODITAS: List[Dict[str, Any]] = [
    {"kunci": "batubara", "label": "Batubara (Newcastle/API2)",
     "sektor_proxy": ["COAL MINING"],
     "catatan": ("tidak ada kontrak batubara di yfinance (COAL=F/MTF=F/"
                 "QCE=F kosong), globalCOAL berbayar -> dipakai return sektor "
                 "COAL MINING sebagai proxy arah, BUKAN harga batubara")},
    {"kunci": "nikel", "label": "Nikel (LME)",
     "sektor_proxy": ["METAL AND MINERAL MINING", "METAL AND ALLIED PRODUCTS"],
     "catatan": ("tidak ada kontrak nikel di yfinance (NI=F/CO=F kosong), "
                 "LME butuh langganan -> dipakai return sektor pertambangan "
                 "logam & mineral sebagai proxy arah, BUKAN harga nikel")},
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
            "mata_uang": item["mata_uang"],
            "satuan": item["satuan"],
            "nilai": s["terakhir"],
            "tanggal_sumber": s["tanggal"],
            "chg_1h_pct": s["chg_1h_pct"],
            "chg_5h_pct": s["chg_5h_pct"],
            "chg_20h_pct": s["chg_20h_pct"],
            "jumlah_bar": s["jumlah_bar"],
            "sumber": "yfinance",
        }

    # Komoditas tanpa simbol yfinance: entri tetap ADA tapi nilainya
    # eksplisit "data tidak tersedia" — bukan dihilangkan, bukan 0.
    # Proxy sektor diisi kemudian oleh lengkapi_proxy_komoditas().
    for item in PROXY_KOMODITAS:
        k = item["kunci"]
        if k in out:
            continue
        out[k] = {
            "label": item["label"],
            "simbol": None,
            "mata_uang": None,
            "satuan": None,
            "nilai": TIDAK_ADA,
            "jenis": "tidak tersedia",
            "alasan": item["catatan"],
            "sumber": TIDAK_ADA,
        }
    return out


def lengkapi_proxy_komoditas(makro: Dict[str, Any],
                             kekuatan: Dict[str, Any]) -> None:
    """Isi batubara/nikel dengan PROXY kekuatan relatif sektor.

    Nilai proxy BUKAN harga komoditas — selalu diberi "jenis": "proxy" dan
    "catatan" supaya tidak salah dibaca sebagai harga.
    """
    sektor_ret = {s.get("sektor"): s for s in (kekuatan.get("per_sektor") or [])}
    for item in PROXY_KOMODITAS:
        nama = [n for n in item["sektor_proxy"] if n in sektor_ret]
        if not nama:
            makro[item["kunci"]] = {
                "label": item["label"],
                "nilai": TIDAK_ADA,
                "jenis": "tidak tersedia",
                "alasan": item["catatan"] + " — sektor proxy juga tidak "
                          "punya data yang cukup",
                "sumber": TIDAK_ADA,
                "tanggal_sumber": None,
            }
            continue
        acuan = sektor_ret[nama[0]]
        makro[item["kunci"]] = {
            "label": item["label"],
            "nilai": TIDAK_ADA,
            "jenis": "proxy",
            "proxy": {
                "sektor": nama[0],
                "ret_5h_pct": acuan.get("ret_5h_pct"),
                "ret_20h_pct": acuan.get("ret_20h_pct"),
                "rel_5h_pct": acuan.get("rel_5h_pct"),
                "rel_20h_pct": acuan.get("rel_20h_pct"),
            },
            "satuan": "persen (return sektor, bukan harga komoditas)",
            "catatan": item["catatan"],
            "sumber": "data/history.json (return median sektor)",
            "tanggal_sumber": kekuatan.get("tanggal_data"),
        }


# ── 2. BI RATE (situs resmi bi.go.id) ───────────────────────────────────────
URL_BI_RATE = "https://www.bi.go.id/id/statistik/indikator/bi-rate.aspx"
URL_BI_INFLASI = "https://www.bi.go.id/id/statistik/indikator/data-inflasi.aspx"


def ambil_bi_rate() -> Dict[str, Any]:
    """BI-Rate terbaru + PERUBAHAN vs RDG sebelumnya dari tabel resmi bi.go.id.

    Tabel berheader: No | Tanggal | BI-7 Day | BI-Rate | Pranala.
    Kolom 'BI-7 Day' berisi nilai rate (mis. '5,75 %').

    Selain level, disimpan delta dan status (naik/turun/tetap) karena level
    saja tidak memberi tahu arah kebijakan — 5,75% setelah 5,50% berbeda
    artinya dengan 5,75% setelah 5,75%.
    """
    html, err = _get_html(URL_BI_RATE)
    if not html:
        return _tidak_ada(f"bi.go.id tidak bisa diakses ({err})")

    for tab in _tabel(html):
        hdr = " ".join(tab[0]).lower()
        if "bi-rate" not in hdr and "bi-7" not in hdr:
            continue
        # Kumpulkan SEMUA baris ber-tanggal, bukan berhenti di baris pertama.
        riwayat: List[Dict[str, Any]] = []
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
            riwayat.append({"nilai_pct": nilai, "tanggal": tgl_iso,
                            "tanggal_teks": tgl_txt})
        if not riwayat:
            continue
        # Tabel bi.go.id menurun (terbaru di atas); urutkan turun agar pasti.
        riwayat.sort(key=lambda r: r["tanggal"], reverse=True)
        return hitung_delta_bi_rate(riwayat)
    return _tidak_ada("tabel BI-Rate tidak ditemukan di halaman bi.go.id")


def hitung_delta_bi_rate(riwayat: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Perubahan BI Rate vs RDG sebelumnya dari daftar riwayat (murni).

    Level saja tidak cukup: 5,75% setelah 5,50% (naik) berbeda arti dengan
    5,75% setelah 5,75% (tetap). Status "tetap" adalah INFORMASI, bukan
    "data tidak tersedia" — dua hal itu tidak boleh dicampur.
    """
    bersih = [r for r in (riwayat or [])
              if isinstance(r, dict) and isinstance(r.get("nilai_pct"), (int, float))
              and r.get("tanggal")]
    if not bersih:
        return _tidak_ada("riwayat BI Rate kosong")
    bersih.sort(key=lambda r: str(r["tanggal"]), reverse=True)

    kini = bersih[0]
    sebelum = bersih[1] if len(bersih) > 1 else None
    delta = None
    status = TIDAK_ADA
    if sebelum is not None:
        delta = round(kini["nilai_pct"] - sebelum["nilai_pct"], 4)
        if delta > 0:
            status = "naik"
        elif delta < 0:
            status = "turun"
        else:
            status = "tetap"

    return {
        "nilai_pct": kini["nilai_pct"],
        "tanggal": kini["tanggal"],
        "tanggal_teks": kini.get("tanggal_teks"),
        "sebelumnya": sebelum,
        "delta_pp": delta,
        "status": status,
        "arah": {"naik": 1, "turun": -1, "tetap": 0}.get(status, 0),
        "riwayat": bersih[:8],
        "sumber": "bi.go.id — Statistik Indikator BI-Rate",
        "url": URL_BI_RATE,
        "satuan": "persen",
        "catatan_satuan": "delta_pp dalam poin persentase (percentage point)",
    }


URL_BPS_INFLASI = "https://www.bps.go.id/id/pressrelease"

# Nilai BPS yang SUDAH diverifikasi manual dari halaman resmi (bukan berita,
# bukan tebakan). Halaman BPS dirender JavaScript sehingga isinya tidak ada di
# HTML awal -> parser otomatis tidak bisa membacanya. Konstanta ini dipakai
# sebagai sumber terverifikasi dan WAJIB diperbarui saat rilis baru keluar.
# Diverifikasi 21 Okt 2026 dari https://www.bps.go.id/id/pressrelease :
#   "Inflasi year-on-year (y-on-y) pada September 2026 sebesar 3,28 persen",
#   rilis 1 Oktober 2026, IHK 112,31, m-to-m 0,30%, y-to-d 2,17%,
#   inflasi inti y-on-y 2,84%.
INFLASI_BPS_TERVERIFIKASI: Dict[str, Any] = {
    "nilai_pct": 3.28,
    "periode": "September 2026",
    "periode_iso": "2026-09",
    "sumber": "BPS — Berita Resmi Statistik (Inflasi), diverifikasi manual",
    "url": URL_BPS_INFLASI,
    "satuan": "persen (YoY)",
    "tanggal_rilis": "2026-10-01",
    "ihk": 112.31,
    "mtm_pct": 0.30,
    "ytd_pct": 2.17,
    "inti_yoy_pct": 2.84,
    "diverifikasi_pada": "2026-10-21",
    "catatan_verifikasi": (
        "Dibaca langsung dari halaman resmi BPS dengan browser (halaman "
        "dirender JavaScript, jadi tidak terbaca oleh HTTP GET biasa). "
        "Perbarui konstanta ini saat BPS merilis periode berikutnya."),
}


def _inflasi_dari_bps() -> Optional[Dict[str, Any]]:
    """Inflasi YoY terbaru dari halaman Berita Resmi Statistik BPS.

    Dua jalur:
      1. regex pada HTML (kalau suatu saat BPS merender server-side)
      2. konstanta INFLASI_BPS_TERVERIFIKASI (hasil verifikasi manual)
    Jalur 2 diberi tanda "metode": "terverifikasi_manual" supaya jelas bukan
    hasil scraping otomatis.
    """
    html, _err = _get_html(URL_BPS_INFLASI)
    if html:
        teks = _teks_sel(html)
        pola = (r"inflasi\s+year-on-year\s*\(y-on-y\)\s+pada\s+"
                r"([A-Za-z]+)\s+(\d{4})\s+sebesar\s+([\d.,]+)\s*persen")
        m = re.search(pola, teks, re.I)
        if m:
            bulan_txt, tahun_txt, angka_txt = m.group(1), m.group(2), m.group(3)
            nilai = _parse_persen_id(angka_txt + " %")
            bln = BULAN_SEMUA.get(bulan_txt.lower())
            if nilai is not None and bln:
                return {
                    "nilai_pct": nilai,
                    "periode": f"{bulan_txt.capitalize()} {tahun_txt}",
                    "periode_iso": f"{tahun_txt}-{bln:02d}",
                    "sumber": "BPS — Berita Resmi Statistik (Inflasi)",
                    "url": URL_BPS_INFLASI,
                    "satuan": "persen (YoY)",
                    "metode": "scraping",
                }
    const = dict(INFLASI_BPS_TERVERIFIKASI)
    const["metode"] = "terverifikasi_manual"
    return const


def pilih_inflasi_terbaru(bps: Optional[Dict[str, Any]],
                          bi: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Pilih periode inflasi PALING BARU dari dua sumber (fungsi murni).

    Sumber pertama yang berhasil belum tentu paling baru: per 21 Okt 2026
    BPS sudah September (3,28%) sementara bi.go.id masih Agustus (3,19%).
    Urutan argumen tidak boleh mengubah hasil.
    """
    kandidat = [r for r in (bps, bi)
                if isinstance(r, dict) and r.get("periode_iso")]
    if not kandidat:
        return _tidak_ada(
            "BPS dan bi.go.id tidak memberi periode inflasi yang bisa diparse")
    kandidat.sort(key=lambda r: str(r["periode_iso"]), reverse=True)
    utama = dict(kandidat[0])
    lain = kandidat[1] if len(kandidat) > 1 else None
    if lain:
        utama["sumber_pembanding"] = {
            "sumber": lain.get("sumber"),
            "nilai_pct": lain.get("nilai_pct"),
            "periode": lain.get("periode"),
        }
        if lain["periode_iso"] != utama["periode_iso"]:
            utama["catatan_jeda_rilis"] = (
                f"{lain.get('sumber')} masih periode {lain.get('periode')}; "
                f"dipakai {utama.get('sumber')} periode "
                f"{utama.get('periode')} karena lebih baru")
    return utama


def ambil_inflasi() -> Dict[str, Any]:
    """Inflasi YoY TERBARU: BPS dulu, lalu bi.go.id sebagai pembanding."""
    return pilih_inflasi_terbaru(_inflasi_dari_bps(), _inflasi_dari_bi())


def _inflasi_dari_bi() -> Optional[Dict[str, Any]]:
    """Inflasi bulanan terbaru dari tabel resmi bi.go.id (fallback)."""
    html, _err = _get_html(URL_BI_INFLASI)
    if not html:
        return None

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
            m = re.match(r"([A-Za-z]+)\s+(\d{4})", row[0].strip())
            bln = BULAN_SEMUA.get(m.group(1).lower()) if m else None
            return {
                "nilai_pct": nilai,
                "periode": row[0].strip(),
                "periode_iso": (f"{m.group(2)}-{bln:02d}"
                                if m and bln else None),
                "sumber": "bi.go.id — Statistik Indikator Data Inflasi",
                "url": URL_BI_INFLASI,
                "satuan": "persen (YoY)",
            }
    return None


BULAN_ID = {
    "januari": 1, "februari": 2, "maret": 3, "april": 4, "mei": 5, "juni": 6,
    "juli": 7, "agustus": 8, "september": 9, "oktober": 10, "november": 11,
    "desember": 12,
}

# bi.go.id memakai nama bulan INGGRIS ("August 2026") sementara BPS memakai
# bahasa Indonesia ("Agustus 2026"). Keduanya harus dikenali, kalau tidak
# periode_iso jadi None dan perbandingan "periode terbaru" gagal diam-diam.
BULAN_EN = {
    "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6,
    "july": 7, "august": 8, "september": 9, "october": 10, "november": 11,
    "december": 12,
}
BULAN_SEMUA = {**BULAN_ID, **BULAN_EN}


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


def ambil_kalender_bi(tahun: Optional[int] = None,
                      tanggal_acuan: Optional[str] = None) -> Dict[str, Any]:
    """Jadwal Rapat Dewan Gubernur (RDG) dari kalender resmi BI.

    `tanggal_acuan` = tanggal data pasar (bukan jam run) supaya
    `berikutnya` tidak menunjuk rapat yang sudah lewat ketika pipeline
    dijalankan beberapa hari setelah sesi terakhir.

    Tabel jadwal berheader 'No | Bulan | Waktu'. Sel Waktu berisi rentang
    seperti 'Selasa-Rabu, 20-21 Oktober 2026' → diambil tanggal TERAKHIR
    (hari keputusan rate diumumkan).
    """
    tahun = tahun or sekarang_wib().year
    html, err = None, None
    for _ in range(2):                 # sumber BI kadang mereset koneksi
        html, err = _get_html(URL_BI_KALENDER)
        if html:
            break
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
    hari_ini, nxt = _acuan_dan_berikutnya(events, tanggal_acuan)
    return {
        "nilai": "tersedia",
        "sumber": "bi.go.id — Kalender Publikasi",
        "url": URL_BI_KALENDER,
        "jumlah_event": len(events),
        "events": events,
        "dihitung_dari": hari_ini,
        "berikutnya": nxt,
        "catatan_berikutnya": (
            None if nxt is not None else
            "Tidak ada jadwal RDG berikutnya di kalender yang terbaca; "
            "jangan mengisi tanggal tebakan."),
    }


def _acuan_dan_berikutnya(events: List[Dict[str, Any]],
                          tanggal_acuan: Optional[str]) -> Tuple[str, Optional[Dict[str, Any]]]:
    """Tanggal acuan + event berikutnya. Dipakai juga oleh fallback cache."""
    hari_ini = str(tanggal_acuan or sekarang_wib().strftime("%Y-%m-%d"))[:10]
    try:
        acuan = datetime.strptime(hari_ini, "%Y-%m-%d").date()
    except ValueError:
        acuan = sekarang_wib().date()
        hari_ini = acuan.isoformat()
    nxt = next((e for e in events if e.get("tanggal", "") >= hari_ini), None)
    if nxt is not None:
        nxt = dict(nxt)
        try:
            nxt["hari_lagi"] = (datetime.strptime(
                nxt["tanggal"], "%Y-%m-%d").date() - acuan).days
        except ValueError:
            nxt["hari_lagi"] = None
    return hari_ini, nxt


def _kalender_bi_aman(tanggal_acuan: Optional[str]) -> Dict[str, Any]:
    """Ambil kalender BI dengan retry + fallback cache sukses terakhir.

    (Review 30 Okt, fix 5) Sumber BI kadang mereset koneksi. Jangan langsung
    menimpa kalender yang sudah terbaca dengan `data tidak tersedia` hanya
    karena satu percobaan gagal; coba ulang, lalu pakai salinan sukses
    terakhir — dengan label cadangan yang jujur.
    """
    terakhir: Dict[str, Any] = {}
    for _ in range(2):
        terakhir = ambil_kalender_bi(tanggal_acuan=tanggal_acuan)
        if (terakhir.get("events") or []):
            return terakhir
    prev = _muat_json(PATH_EXTERNAL) or {}
    lama = prev.get("kalender_bi") if isinstance(prev, dict) else None
    if isinstance(lama, dict) and (lama.get("events") or []):
        salin = dict(lama)
        salin["events"] = [dict(e) for e in lama["events"]]
        hari_ini, nxt = _acuan_dan_berikutnya(salin["events"], tanggal_acuan)
        salin.update({
            "nilai": "tersedia (cadangan)",
            "dihitung_dari": hari_ini,
            "berikutnya": nxt,
            "alasan_sumber_terkini": terakhir.get("alasan") or "sumber BI gagal",
            "catatan_cadangan": (
                "Sumber BI gagal saat run ini; dipakai salinan kalender "
                "terakhir yang pernah berhasil. Tanggal berikutnya dihitung "
                "ulang dari tanggal data, bukan dari jam run."),
        })
        return salin
    return terakhir or {
        "nilai": TIDAK_ADA,
        "alasan": "kalender BI gagal dan tidak ada cadangan",
        "sumber": TIDAK_ADA,
        "events": [],
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
                          ihsg_rows: List[Dict[str, Any]],
                          tanggal_data: Optional[str] = None) -> Dict[str, Any]:
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
    # (Review 30 Okt, fix 3) Terkuat/terlemah memakai rel 20 sesi vs IHSG,
    # sama dengan tabel Kekuatan Relatif Sektor dan ringkasan pasar. Jangan
    # memakai rel 5 sesi untuk label "terkuat" karena bisa berbeda arah.
    out.sort(key=lambda x: (x.get("rel_20h_pct") is None,
                            -(x.get("rel_20h_pct") or 0)))
    valid20 = [x for x in out if x.get("rel_20h_pct") is not None]
    kuat = valid20[0] if valid20 else None
    lemah = valid20[-1] if valid20 else None
    return {
        "ihsg_ret_5h_pct": None if r_ihsg[5] is None else round(r_ihsg[5], 3),
        "ihsg_ret_20h_pct": None if r_ihsg[20] is None else round(r_ihsg[20], 3),
        "per_sektor": out,
        "jumlah_sektor": len(out),
        "min_emiten": 3,
        "sektor_terkuat": kuat,
        "sektor_terlemah": lemah,
        "tanggal_data": tanggal_data,
        "periode_terkuat": "20 sesi vs IHSG",
        "catatan": ("Return sektor = median return emiten (min. 3 emiten "
                    "berdata). rel_* = selisih terhadap IHSG pada jendela sama; "
                    "terkuat/terlemah memakai rel_20h_pct."),
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


def ambil_kalender_emiten(batas: int = 12, maks_emiten: int = 60) -> Dict[str, Any]:
    """Kalender emiten dari sumber SAH tanpa melewati proteksi idx.co.id.

    idx.co.id membalas HTTP 403 (Cloudflare) untuk semua endpoint kalender
    pada 21 Okt 2026. Proteksi itu TIDAK dilewati. Sebagai gantinya dipakai
    `yfinance.Ticker.calendar` yang memang menyediakan:
      - Earnings Date (jadwal rilis laporan keuangan)
      - Ex-Dividend Date (tanggal ex-dividen)
    RUPS dan cum-date resmi IDX tetap "data tidak tersedia" karena tidak
    ada sumber sah yang bisa diakses.

    Hanya emiten yang tanggalnya >= hari ini yang dimasukkan sebagai event
    mendatang; sisanya dicatat sebagai "terakhir" agar tidak menyesatkan.
    """
    try:                                # modul paket
        from idx import providers as _P  # lokal: hindari siklus impor
    except ImportError:                 # python3 idx/external.py
        import providers as _P          # type: ignore

    latest = _muat_json(os.path.join(_ROOT, "data", "latest.json")) or {}
    kode_semua = [e.get("kode") for e in (latest.get("emiten") or [])
                  if e.get("kode")]
    if not kode_semua:
        return {"nilai": TIDAK_ADA,
                "alasan": "latest.json tidak memuat daftar emiten",
                "events": [], "sumber": TIDAK_ADA}

    # Batasi jumlah panggilan jaringan: pakai emiten paling likuid lebih dulu.
    kandidat = kode_semua[:maks_emiten]
    hari_ini = sekarang_wib().date().isoformat()

    events: List[Dict[str, Any]] = []
    terakhir: List[Dict[str, Any]] = []
    gagal = 0
    dicek = 0
    for kode in kandidat:
        try:
            res = _P.yfinance_calendar(kode)
        except Exception:
            gagal += 1
            continue
        dicek += 1
        # yfinance_calendar mengembalikan wrapper {ok, data, ...} seperti
        # helper providers lain. Bug 21 Okt 2026: sempat dibaca sebagai dict
        # datar -> .get() selalu None -> 0 event padahal data tersedia.
        cal = res.get("data") if isinstance(res, dict) else None
        if not res.get("ok") or not isinstance(cal, dict):
            continue
        for jenis, kunci_out in (("laporan_keuangan", "earnings"),
                                 ("ex_dividen", "ex_dividend")):
            tgl = cal.get(kunci_out)
            if not tgl:
                continue
            rec = {"kode": kode, "jenis": jenis, "tanggal": tgl,
                   "sumber": "yfinance (calendar)",
                   "dapat_diverifikasi": True}
            (events if tgl >= hari_ini else terakhir).append(rec)

    events.sort(key=lambda e: (e["tanggal"], e["kode"]))
    terakhir.sort(key=lambda e: e["tanggal"], reverse=True)
    events = events[:batas]

    if not dicek:
        return {"nilai": TIDAK_ADA,
                "alasan": "yfinance tidak memberi data kalender untuk emiten "
                          "yang dicek",
                "events": [], "sumber": TIDAK_ADA,
                "cek": {"idx_rups": "HTTP 403 (Cloudflare) — tidak dilewati",
                        "idx_dividen": "HTTP 403 (Cloudflare) — tidak dilewati",
                        "idx_lapkeu": "HTTP 403 (Cloudflare) — tidak dilewati"}}
    return {
        "nilai": "sebagian",
        "sumber": "yfinance (calendar) — earnings & ex-dividend",
        "url": "https://finance.yahoo.com",
        "catatan": ("Hanya laporan keuangan & ex-dividen. RUPS/cum-date resmi "
                    "IDX TIDAK tersedia karena idx.co.id memblokir (403) dan "
                    "proteksinya tidak dilewati."),
        "events": events,
        "terakhir": terakhir[:8],
        "jumlah_event": len(events),
        "dicek": dicek,
        "gagal": gagal,
        "cek": {"idx_rups": "HTTP 403 (Cloudflare) — tidak dilewati",
                "idx_dividen": "HTTP 403 (Cloudflare) — tidak dilewati",
                "idx_lapkeu": "HTTP 403 (Cloudflare) — tidak dilewati",
                "yfinance_calendar": f"{dicek} emiten dicek, {gagal} gagal"},
    }


# ── 6. REZIM PASAR ──────────────────────────────────────────────────────────
# Ambang ditetapkan eksplisit agar bisa diaudit, bukan angka tersembunyi.
AMBANG_REZIM = {
    "ihsg_turun_5h_pct": -5.0,   # IHSG turun > 5% dalam 5 sesi
    "ihsg_turun_1h_pct": -3.0,   # atau turun > 3% dalam 1 sesi
    "ihsg_turun_20h_pct": -10.0,  # atau > 10% dalam 20 sesi
    "vix_tinggi": 30.0,          # S&P VIX > 30 (kalau tersedia)
}

# Bukti trading halt dicari dari berita resmi; tanpa sumber -> tidak tersedia.
# Alias ke RZ.EVENT_HALT (sumber tunggal) — daftar kosong = tidak ada bukti
# yang bisa dibaca otomatis, jadi halt TIDAK pernah diklaim tanpa sumber.
_EVENT_HALT: List[Dict[str, Any]] = RZ.EVENT_HALT


def deteksi_rezim(ihsg_rows: List[Dict[str, Any]],
                  makro: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Klasifikasi rezim pasar dari data harga IHSG.

    Thin wrapper di atas idx/rezim.py — modul itu sumber tunggal supaya
    external.json, payload latest.json, dan dashboard memakai angka yang
    sama persis. Argumen `makro` dipertahankan demi kompatibilitas tapi
    tidak dipakai (rezim cukup dari harga IHSG).
    """
    return RZ.deteksi_rezim(ihsg_rows)


# ── 7. EVENT RISIKO (MSCI & sejenisnya) ─────────────────────────────────────
# Setiap event WAJIB punya URL sumber yang bisa dibuka & tanggal pengumuman.
EVENT_RISIKO: List[Dict[str, Any]] = [
    {
        "id": "msci_reklasifikasi_2026",
        "judul": ("MSCI usul reklasifikasi Indonesia: Emerging Market -> "
                  "Standalone Market"),
        "tanggal_diumumkan": "2026-10-15",
        "status": "usulan, BELUM final",
        "arah": "negatif (risiko arus keluar)",
        "sumber_nama": "MSCI Index Review — pengumuman konsultasi",
        "sumber_url": "https://www.msci.com/index-review",
        "sumber_url_alternatif": [
            "https://www.cnbcindonesia.com/market",
            "https://www.antaranews.com/berita/ekonomi",
        ],
        "catatan_verifikasi": (
            "msci.com menolak akses otomatis (challenge/403) sehingga isi "
            "pengumuman tidak bisa dibaca langsung dari sumber primer; klaim "
            "INI BELUM DIVERIFIKASI ke dokumen MSCI. Status 'usulan, belum "
            "final' diambil dari pemberitaan, bukan dari dokumen resmi."),
        "dampak": ("risiko arus keluar dana asing pada emiten dengan "
                   "kepemilikan asing tinggi dan konstituen indeks MSCI"),
        "emiten_terdampak": TIDAK_ADA,
        "alasan_emiten": ("daftar konstituen MSCI dan data kepemilikan asing "
                          "per emiten tidak tersedia tanpa langganan; TIDAK "
                          "ditebak"),
        "sumber_berita": [
            {"nama": "CNBC Indonesia — Market",
             "url": "https://www.cnbcindonesia.com/market"},
            {"nama": "Reuters — Asia Markets",
             "url": "https://www.reuters.com/markets/asia/"},
            {"nama": "Antara — Ekonomi",
             "url": "https://www.antaranews.com/berita/ekonomi"},
        ],
    },
    {
        "id": "trading_halt_16okt2026",
        "judul": "Trading halt Bursa Efek Indonesia, 16 Oktober 2026",
        "tanggal_diumumkan": "2026-10-16",
        "status": "terjadi; tanggal dari review, sumber resmi perlu dibuka",
        "arah": "negatif jangka pendek / likuiditas",
        "sumber_nama": "IDX — Pengumuman Bursa; CNBC Indonesia; Reuters",
        "sumber_url": "https://www.idx.co.id/berita/pengumuman/",
        "sumber_url_alternatif": [
            "https://www.cnbcindonesia.com/market",
            "https://www.reuters.com/markets/asia/",
        ],
        "sumber_berita": [
            {"nama": "IDX — Pengumuman Bursa",
             "url": "https://www.idx.co.id/berita/pengumuman/"},
            {"nama": "CNBC Indonesia — Market",
             "url": "https://www.cnbcindonesia.com/market"},
            {"nama": "Reuters — Asia Markets",
             "url": "https://www.reuters.com/markets/asia/"},
        ],
        "catatan_verifikasi": (
            "Peristiwa trading halt 16 Okt 2026 dicatat dari review. URL yang "
            "tersedia adalah halaman pengumuman/berita, bukan artikel spesifik; "
            "durasi dan cakupan halt tidak diklaim tanpa membuka sumber resmi."),
        "dampak": ("gangguan likuiditas dan kepanikan jangka pendek; bukan "
                   "pengurang skor otomatis"),
        "emiten_terdampak": TIDAK_ADA,
        "alasan_emiten": ("tidak ada daftar emiten yang dapat diverifikasi "
                          "dari sumber otomatis; TIDAK ditebak"),
    },
]


def ambil_event_risiko() -> Dict[str, Any]:
    """Event risiko makro/indeks. Tanpa daftar emiten kalau tak terlacak."""
    events = []
    for ev in EVENT_RISIKO:
        e = dict(ev)
        e["umur_hari"] = _umur_hari(ev.get("tanggal_diumumkan"))
        events.append(e)
    return {
        "nilai": "tersedia" if events else TIDAK_ADA,
        "jumlah": len(events),
        "events": events,
        "catatan": ("Faktor risiko dipakai sebagai penanda, bukan pengurang "
                    "skor otomatis sampai daftar emiten terlacak."),
    }


def _umur_hari(tanggal_iso: Optional[str]) -> Optional[int]:
    if not tanggal_iso:
        return None
    try:
        d = datetime.strptime(tanggal_iso, "%Y-%m-%d").date()
    except ValueError:
        return None
    return (sekarang_wib().date() - d).days


def _tandai_kesegaran(makro: Dict[str, Any],
                      tanggal_data: Optional[str]) -> Dict[str, Any]:
    """Tandai indikator makro yang lebih lama dari data pasar utama.

    (Review 30 Okt, fix 1 & 7) Tanggal sumber per indikator tidak boleh
    disamarkan oleh tanggal run. CPO/iron ore yang tertinggal beberapa hari
    diberi `basi: true`, sehingga UI bisa memberi peringatan eksplisit.
    """
    acuan = str(tanggal_data)[:10] if tanggal_data else None
    item_basi: List[Dict[str, Any]] = []
    for key in [x["kunci"] for x in MAKRO_YF] + [x["kunci"] for x in PROXY_KOMODITAS]:
        ind = makro.get(key)
        if not isinstance(ind, dict):
            continue
        tgl = str(ind.get("tanggal_sumber") or "")[:10] or None
        basi = bool(acuan and tgl and tgl < acuan)
        ind["basi"] = basi
        ind["tanggal_data_utama"] = acuan
        ind["umur_hari_terhadap_data"] = None
        if acuan and tgl:
            try:
                ind["umur_hari_terhadap_data"] = (
                    datetime.strptime(acuan, "%Y-%m-%d").date()
                    - datetime.strptime(tgl, "%Y-%m-%d").date()).days
            except ValueError:
                pass
        if basi:
            item_basi.append({
                "kunci": key,
                "label": ind.get("label"),
                "tanggal_sumber": tgl,
                "selisih_hari": ind["umur_hari_terhadap_data"],
            })
    return {
        "tanggal_data_utama": acuan,
        "status": "basi" if item_basi else "segar",
        "jumlah_basi": len(item_basi),
        "item_basi": item_basi,
        "catatan": (
            "Indikator dengan `basi: true` lebih lama dari tanggal data pasar "
            "utama. Jangan ditampilkan sebagai kondisi hari ini."
            if item_basi else
            "Semua indikator makro yang punya tanggal sumber tidak lebih lama "
            "dari data pasar utama."
        ),
    }


# ── 8. ANOMALI PERIODE (untuk backtest) ─────────────────────────────────────
# Sumber tunggal: idx/anomali.py (dipakai juga oleh backtest & UI).
# Definisi periode TIDAK boleh diubah diam-diam.
PERIODE_ANOMALI = ANOM.PERIODE_ANOMALI


def periode_anomali(hari_ini: Optional[str] = None) -> Dict[str, Any]:
    """Daftar periode anomali + helper untuk memfilter statistik."""
    return ANOM.ringkas(hari_ini)


def tanggal_dalam_anomali(tanggal_iso: Optional[str]) -> bool:
    """True kalau tanggal masuk salah satu periode anomali."""
    return ANOM.tanggal_dalam_anomali(tanggal_iso)


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
    kekuatan = hitung_kekuatan_sektor(hist, sektor_map, ihsg_rows,
                                      latest.get("tanggal_data"))
    lengkapi_proxy_komoditas(makro, kekuatan)
    kesegaran = _tandai_kesegaran(makro, latest.get("tanggal_data"))
    rezim = deteksi_rezim(ihsg_rows, makro)

    return {
        "versi_skema": VERSI_EKSTERNAL,
        "diperbarui_pada": P.stamp_wib(),
        "diperbarui_pada_teks": P.human_wib(),
        "tanggal_data": latest.get("tanggal_data"),
        "kesegaran": kesegaran,
        "makro": makro,
        "bi_rate": ambil_bi_rate(),
        "inflasi": ambil_inflasi(),
        "kalender_bi": _kalender_bi_aman(
            latest.get("tanggal_data")),
        "kekuatan_sektor": kekuatan,
        "aliran_dana": ambil_aliran_dana(),
        "kalender_emiten": ambil_kalender_emiten(),
        "rezim_pasar": rezim,
        "event_risiko": ambil_event_risiko(),
        "periode_anomali": periode_anomali(),
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
