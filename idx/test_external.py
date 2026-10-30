"""
Tes Tahap 1 (F) — pengumpul data eksternal.

Fokus: kontrak data, null-safety, parsing sumber resmi, dan larangan menebak.
Tidak memukul jaringan: semua fungsi pengambilan diuji dengan HTML fixture
kecuali bagian yang memang butuh data nyata (ditandai NETWORK, dilewati
kalau `SKIP_NETWORK=1`).
"""
from __future__ import annotations

import json
import os
import re
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    from idx import external as E
except Exception:
    import external as E  # type: ignore

LULUS = 0
GAGAL = 0
_CATATAN: list[str] = []


def cek(nama: str, kondisi: bool, detail: str = "") -> None:
    global LULUS, GAGAL
    if kondisi:
        LULUS += 1
    else:
        GAGAL += 1
        _CATATAN.append(f"{nama} {detail}")
        print(f"  GAGAL: {nama} {detail}")


def bagian(t: str) -> None:
    print(f"\n-- {t}")


TIDAK_ADA = E.TIDAK_ADA


# ── 1. util ────────────────────────────────────────────────────────────────
bagian("1. util konversi & null-safety")
cek("_angka int", E._angka(5) == 5.0)
cek("_angka str desimal titik", E._angka("5.75") == 5.75)
cek("_angka None -> None", E._angka(None) is None)
cek("_angka bool ditolak", E._angka(True) is None)
cek("_angka NaN -> None", E._angka(float("nan")) is None)
cek("_angka inf -> None", E._angka(float("inf")) is None)
cek("_angka sampah -> None", E._angka("bukan angka") is None)
cek("_tidak_ada pakai string baku",
    E._tidak_ada("x")["nilai"] == TIDAK_ADA)
cek("_tidak_ada tidak pernah 0", E._tidak_ada("x")["nilai"] != 0)
cek("_tidak_ada bawa alasan", E._tidak_ada("gagal A")["alasan"] == "gagal A")

bagian("1b. parsing persen format Indonesia")
cek("persen '5,75 %'", E._parse_persen_id("5,75 %") == 5.75)
cek("persen '5.75%'", E._parse_persen_id("5.75%") == 5.75)
cek("persen '3,19 %'", E._parse_persen_id("3,19 %") == 3.19)
cek("persen '-' -> None", E._parse_persen_id("-") is None)
cek("persen kosong -> None", E._parse_persen_id("") is None)

bagian("1c. parsing tanggal Indonesia")
cek("tanggal dasar", E._parse_tanggal_id("21 Oktober 2026") == "2026-10-21")
cek("tanggal bulan kecil", E._parse_tanggal_id("1 agustus 2026") == "2026-08-01")
cek("tanggal tanpa tahun -> None", E._parse_tanggal_id("21 Oktober") is None)
cek("tanggal ngawur -> None", E._parse_tanggal_id("hello world") is None)
cek("tanggal tak valid -> None", E._parse_tanggal_id("31 Februari 2026") is None)


# ── 2. BI Rate (fixture HTML asli) ──────────────────────────────────────────
bagian("2. BI Rate dari HTML resmi (fixture)")
HTML_BI_RATE = """
<table>
<tr><th>No</th><th>Tanggal</th><th>BI-7 Day</th><th>BI-Rate</th><th>Pranala</th></tr>
<tr><td>1</td><td>21 Oktober 2026</td><td>5,75 %</td><td>Lihat</td></tr>
<tr><td>2</td><td>23 September 2026</td><td>5,75 %</td><td>Lihat</td></tr>
</table>
"""
_html_asli = E._get_html
E._get_html = lambda url, timeout=30: (HTML_BI_RATE, None)  # type: ignore
bi = E.ambil_bi_rate()
cek("bi_rate terbaca", bi.get("nilai_pct") == 5.75, str(bi)[:120])
cek("bi_rate tanggal benar", bi.get("tanggal") == "2026-10-21", str(bi.get("tanggal")))
cek("bi_rate bawa sumber", "bi.go.id" in (bi.get("sumber") or ""))
cek("bi_rate bawa url", (bi.get("url") or "").startswith("https://www.bi.go.id"))

# tabel tanpa header dikenali -> jangan asal ambil angka
E._get_html = lambda url, timeout=30: ("<table><tr><td>abc</td></tr></table>", None)  # type: ignore
bi_b = E.ambil_bi_rate()
cek("bi_rate tabel asing -> tidak tersedia", bi_b.get("nilai") == TIDAK_ADA)

# sumber mati -> tidak tersedia + alasan, bukan tebakan
E._get_html = lambda url, timeout=30: (None, "URLError: timeout")  # type: ignore
bi_c = E.ambil_bi_rate()
cek("bi_rate gagal -> tidak tersedia", bi_c.get("nilai") == TIDAK_ADA)
cek("bi_rate gagal bawa alasan", "timeout" in (bi_c.get("alasan") or ""))


# ── 3. Inflasi ──────────────────────────────────────────────────────────────
bagian("3. Inflasi dari HTML resmi (fixture)")
HTML_INFLASI = """
<table>
<tr><th>Date</th><th>Inflation Data</th></tr>
<tr><td>August 2026</td><td>3.19 %</td></tr>
<tr><td>July 2026</td><td>2.88 %</td></tr>
</table>
"""
E._get_html = lambda url, timeout=30: (HTML_INFLASI, None)  # type: ignore
inf = E.ambil_inflasi()
# Sejak Tahap 2, BPS (September 3,28%) lebih baru dari bi.go.id (Agustus
# 3,19%) -> BPS yang dipakai; bi.go.id tetap disimpan sebagai pembanding.
cek("inflasi pilih periode TERBARU (BPS September 3,28)",
    inf.get("nilai_pct") == 3.28, str(inf.get("periode")))
cek("inflasi bawa periode", inf.get("periode") == "September 2026",
    str(inf.get("periode")))
cek("inflasi bawa sumber", "BPS" in (inf.get("sumber") or ""),
    str(inf.get("sumber"))[:60])
cek("inflasi simpan bi.go.id sebagai pembanding",
    (inf.get("sumber_pembanding") or {}).get("nilai_pct") == 3.19,
    str(inf.get("sumber_pembanding")))
cek("inflasi catat jeda rilis BPS vs bi.go.id",
    "Agustus" in (inf.get("catatan_jeda_rilis") or "")
    or "August" in (inf.get("catatan_jeda_rilis") or ""),
    str(inf.get("catatan_jeda_rilis"))[:80])

# bi.go.id saja (BPS gagal) -> tetap terpakai, bukan "tidak tersedia".
_asli_bps = E._inflasi_dari_bps
E._inflasi_dari_bps = lambda: None  # type: ignore
inf_bi = E.ambil_inflasi()
cek("inflasi fallback bi.go.id saat BPS gagal", inf_bi.get("nilai_pct") == 3.19,
    str(inf_bi.get("periode")))
cek("inflasi fallback tetap bawa periode",
    inf_bi.get("periode") == "August 2026", str(inf_bi.get("periode")))
cek("inflasi fallback bawa sumber bi.go.id",
    "bi.go.id" in (inf_bi.get("sumber") or ""), str(inf_bi.get("sumber"))[:60])
E._inflasi_dari_bps = _asli_bps  # type: ignore

E._get_html = lambda url, timeout=30: (None, "HTTPError: 503")  # type: ignore
_asli_bps2 = E._inflasi_dari_bps
E._inflasi_dari_bps = lambda: None  # type: ignore
inf_b = E.ambil_inflasi()
cek("inflasi gagal -> tidak tersedia", inf_b.get("nilai") == TIDAK_ADA)
cek("inflasi gagal bawa alasan", "tidak" in (inf_b.get("alasan") or "").lower())
E._inflasi_dari_bps = _asli_bps2  # type: ignore


# ── 4. Kalender RDG: tanggal TIDAK boleh salah ─────────────────────────────
bagian("4. kalender RDG BI — regresi bug tanggal")
HTML_RDG = """
<table>
<tr><th>No</th><th>Bulan</th><th>Waktu</th></tr>
<tr><td>1</td><td>Januari*</td><td>Selasa-Rabu, 20-21 Januari 2026</td></tr>
<tr><td>2</td><td>Februari</td><td>Rabu-Kamis, 18-19 Februari 2026</td></tr>
<tr><td>3</td><td>Oktober**</td><td>Selasa-Rabu, 20-21 Oktober 2026</td></tr>
<tr><td>4</td><td>November</td><td>Selasa-Rabu, 17-18 November 2026</td></tr>
</table>
<table>
<tr><th>No</th><th>Acara Unggulan</th><th>Waktu</th><th>Tempat</th></tr>
<tr><td>1</td><td>Rakornas</td><td>Juni 2026</td><td>Jakarta</td></tr>
</table>
"""
E._get_html = lambda url, timeout=30: (HTML_RDG, None)  # type: ignore
kal = E.ambil_kalender_bi(tahun=2026)
ev = kal.get("events") or []
cek("rdg jumlah event", len(ev) == 4, f"dapat {len(ev)}")
tgl = [e["tanggal"] for e in ev]
cek("rdg Januari = 21 (bukan 26)", "2026-01-21" in tgl, str(tgl))
cek("rdg Februari = 19", "2026-02-19" in tgl)
cek("rdg Oktober = 21", "2026-10-21" in tgl)
cek("rdg November = 18", "2026-11-18" in tgl)
cek("rdg tidak ada tanggal palsu 2026-01-26", "2026-01-26" not in tgl)
cek("rdg terurut naik", tgl == sorted(tgl))
cek("rdg tiap event bawa sumber",
    all(e.get("sumber") and e.get("url") for e in ev))
cek("rdg event punya jenis rapat_bi",
    all(e.get("jenis") == "rapat_bi" for e in ev))
_kal_acuan = E.ambil_kalender_bi(tahun=2026, tanggal_acuan="2026-10-22")
_nxt = (_kal_acuan.get("berikutnya") or {})
cek("rdg berikutnya dihitung dari tanggal acuan",
    _nxt.get("tanggal") == "2026-11-18", str(_nxt))
cek("rdg berikutnya punya hari_lagi (22 Okt -> 18 Nov = 27)",
    _nxt.get("hari_lagi") == 27, str(_nxt.get("hari_lagi")))

E._get_html = lambda url, timeout=30: ("<html>kosong</html>", None)  # type: ignore
kal_b = E.ambil_kalender_bi()
cek("kalender tanpa tabel -> tidak tersedia", kal_b.get("nilai") == TIDAK_ADA)
cek("kalender gagal tidak punya events palsu", kal_b.get("events") == [])

# E._get_html asli dikembalikan sebelum bagian yg butuh jaringan
E._get_html = _html_asli  # type: ignore


# ── 5. Makro: bentuk payload & larangan angka palsu ────────────────────────
bagian("5. makro — kontrak field & penolakan tebakan")
_riw = E._riwayat
E._riwayat = lambda simbol, periode="3mo": []  # type: ignore
m = E.ambil_makro()
for k in ("ihsg", "usdidr", "sp500", "minyak", "emas", "cpo", "iron_ore"):
    cek(f"makro {k} ada", k in m)
    cek(f"makro {k} gagal -> bukan 0", m[k]["nilai"] != 0)
    cek(f"makro {k} gagal -> string baku", m[k]["nilai"] == TIDAK_ADA)
for k in ("batubara", "nikel"):
    cek(f"komoditas {k} ada entri", k in m)
    cek(f"komoditas {k} tidak tersedia", m[k]["nilai"] == TIDAK_ADA)
    cek(f"komoditas {k} bawa alasan", bool(m[k].get("alasan")))

# dengan seri palsu -> hitung perubahan benar
def _seri(n=25):
    return [{"date": f"2026-01-{i+1:02d}", "close": 100.0 + i} for i in range(n)]
E._riwayat = lambda simbol, periode="3mo": _seri(25)  # type: ignore
m2 = E.ambil_makro()
ihsg = m2["ihsg"]
cek("makro ihsg terisi", isinstance(ihsg.get("nilai"), float))
cek("makro ihsg nilai = bar terakhir", ihsg["nilai"] == 124.0, str(ihsg.get("nilai")))
cek("makro ihsg chg_5h dihitung", ihsg.get("chg_5h_pct") is not None)
harap = round((124.0 / 119.0 - 1) * 100, 3)
cek("makro ihsg chg_5h benar", abs(ihsg["chg_5h_pct"] - harap) < 1e-6,
    f"{ihsg.get('chg_5h_pct')} vs {harap}")
cek("makro ihsg bawa tanggal", ihsg.get("tanggal_sumber") == "2026-01-25")
cek("makro ihsg bawa sumber yfinance", ihsg.get("sumber") == "yfinance")
E._riwayat = _riw  # type: ignore


# ── 6. Kekuatan sektor ─────────────────────────────────────────────────────
bagian("6. kekuatan relatif sektor")
def _mk_hist(per_kode: dict) -> dict:
    em = {}
    for kode, close in per_kode.items():
        tgl = [f"2026-03-{i+1:02d}" for i in range(len(close))]
        em[kode] = {"d": tgl, "c": close, "v": [100] * len(close)}
    return {"emiten": em}


# IHSG datar; sektor A naik 10%, sektor B turun 10%
_cl = lambda step, n=25: [100.0 + step * i for i in range(n)]
hist = _mk_hist({
    "AAA": _cl(1.0), "AAB": _cl(1.0), "AAC": _cl(1.0),      # sektor A: +5.2%/5h
    "BBA": _cl(-1.0), "BBB": _cl(-1.0), "BBC": _cl(-1.0),   # sektor B: turun
    "SOLO": _cl(1.0),                                       # sektor C: 1 emiten
})
smap = {"AAA": "SEKTOR A", "AAB": "SEKTOR A", "AAC": "SEKTOR A",
        "BBA": "SEKTOR B", "BBB": "SEKTOR B", "BBC": "SEKTOR B",
        "SOLO": "SEKTOR C"}
ihsg_datar = [{"date": f"2026-03-{i+1:02d}", "close": 5000.0} for i in range(25)]
ks = E.hitung_kekuatan_sektor(hist, smap, ihsg_datar, "2026-10-30")
cek("kekuatan: jumlah sektor", ks["jumlah_sektor"] == 3, str(ks["jumlah_sektor"]))
cek("kekuatan: tanggal_data diteruskan", ks.get("tanggal_data") == "2026-10-30",
    str(ks.get("tanggal_data")))
cek("kekuatan: ihsg 5h = 0", abs(ks["ihsg_ret_5h_pct"]) < 1e-9,
    str(ks["ihsg_ret_5h_pct"]))

by = {s["sektor"]: s for s in ks["per_sektor"]}
cek("kekuatan: sektor A positif", by["SEKTOR A"]["rel_5h_pct"] > 0)
cek("kekuatan: sektor B negatif", by["SEKTOR B"]["rel_5h_pct"] < 0)
cek("kekuatan: sektor A > sektor B",
    by["SEKTOR A"]["rel_5h_pct"] > by["SEKTOR B"]["rel_5h_pct"])
cek("kekuatan: sektor tipis ditandai tidak tersedia",
    by["SEKTOR C"]["nilai"] == TIDAK_ADA)
cek("kekuatan: sektor tipis tidak diberi angka",
    by["SEKTOR C"].get("rel_5h_pct") is None)
cek("kekuatan: terkuat = A", ks["sektor_terkuat"]["sektor"] == "SEKTOR A",
    str(ks["sektor_terkuat"]))
cek("kekuatan: terlemah = B", ks["sektor_terlemah"]["sektor"] == "SEKTOR B")
cek("kekuatan: terkuat memakai rel_20h, bukan rel_5h",
    ks["sektor_terkuat"]["rel_20h_pct"] == max(
        x["rel_20h_pct"] for x in ks["per_sektor"]
        if x.get("rel_20h_pct") is not None),
    str([(x["sektor"], x.get("rel_5h_pct"), x.get("rel_20h_pct"))
         for x in ks["per_sektor"]]))
cek("kekuatan: median dipakai, bukan mean",
    "median" in (ks.get("catatan") or "").lower())

# emiten tanpa sektor tidak boleh masuk hitungan
ks2 = E.hitung_kekuatan_sektor(hist, {}, ihsg_datar)
cek("kekuatan: tanpa peta sektor -> kosong", ks2["jumlah_sektor"] == 0)

# IHSG tidak tersedia -> rel harus None, bukan angka palsu
ks3 = E.hitung_kekuatan_sektor(hist, smap, [])
cek("kekuatan: ihsg kosong -> rel None",
    by["SEKTOR A"].get("rel_5h_pct") is not None or
    all(s.get("rel_5h_pct") is None for s in ks3["per_sektor"]))


# ── 7. Sumber yang memang tidak tersedia ───────────────────────────────────
bagian("7. sumber tidak tersedia harus jujur")
ad = E.ambil_aliran_dana()
cek("aliran dana tanpa key -> tidak tersedia", ad["nilai"] == TIDAK_ADA)
cek("aliran dana bawa alasan", bool(ad.get("alasan")))
cek("aliran dana net_asing tidak tersedia", ad["net_asing"] == TIDAK_ADA)

# Kalender emiten kini memakai yfinance (idx.co.id 403, tidak dilewati).
# Dengan 0 emiten yang dicek -> "tidak tersedia".
_ke_tua = E.ambil_kalender_emiten
ke = E.ambil_kalender_emiten(batas=5, maks_emiten=0)
cek("kalender emiten tanpa kandidat -> tidak tersedia",
    ke["nilai"] == TIDAK_ADA, str(ke.get("nilai")))
cek("kalender emiten events kosong", ke["events"] == [])
cek("kalender emiten catat 403 idx", "403" in json.dumps(ke.get("cek") or {}))
cek("kalender emiten catat proteksi TIDAK dilewati",
    "tidak dilewati" in json.dumps(ke.get("cek") or {}).lower(),
    str(ke.get("cek"))[:120])


# ── 8. Payload lengkap & penulisan atomik ──────────────────────────────────
bagian("8. payload lengkap & atomic write")
_latar = (E.ambil_makro, E.ambil_bi_rate, E.ambil_inflasi,
          E.ambil_kalender_bi, E.ambil_aliran_dana, E.ambil_kalender_emiten,
          E._riwayat)
E.ambil_makro = lambda: {"ihsg": {"nilai": 5000.0}}          # type: ignore
E.ambil_bi_rate = lambda: {"nilai_pct": 5.75}                # type: ignore
E.ambil_inflasi = lambda: {"nilai_pct": 3.19}                # type: ignore
E.ambil_kalender_bi = lambda tahun=None, tanggal_acuan=None: {"nilai": "tersedia", "events": []}  # type: ignore
E.ambil_aliran_dana = lambda: {"nilai": TIDAK_ADA}           # type: ignore
E.ambil_kalender_emiten = lambda: {"nilai": TIDAK_ADA, "events": []}  # type: ignore
E._riwayat = lambda simbol, periode="3mo": []                # type: ignore

p = E.bangun(hist={"emiten": {}}, latest={"tanggal_data": "2026-10-21",
                                          "emiten": []})
for k in ("versi_skema", "diperbarui_pada", "makro", "bi_rate", "inflasi",
          "kalender_bi", "kekuatan_sektor", "aliran_dana", "kalender_emiten",
          "event_risiko", "kesegaran", "disclaimer"):
    cek(f"payload punya {k}", k in p)
cek("payload disclaimer benar",
    p["disclaimer"] == "Hasil screening otomatis, bukan rekomendasi beli.")
cek("payload tanggal_data diteruskan", p["tanggal_data"] == "2026-10-21")
cek("payload bawa catatan tidak-tebak", "tebakan" in (p.get("catatan") or ""))

td = tempfile.mkdtemp(prefix="ext_test_")
path = os.path.join(td, "external.json")
_p_asli = E.PATH_EXTERNAL
E.PATH_EXTERNAL = path  # type: ignore
E.jalankan(tulis=True)
cek("file external.json dibuat", os.path.exists(path))
with open(path, encoding="utf-8") as f:
    isi = json.load(f)
cek("file valid JSON & punya makro", "makro" in isi)
cek("file tidak menyisakan .tmp",
    not any(x.endswith(".tmp") or ".tmp." in x for x in os.listdir(td)))
# tidak ada nilai 0 yang menyamar sebagai "tidak ada data"
cek("tidak ada nilai 0 pada entri tidak tersedia",
    isi["aliran_dana"]["nilai"] != 0)
E.PATH_EXTERNAL = _p_asli  # type: ignore

(E.ambil_makro, E.ambil_bi_rate, E.ambil_inflasi, E.ambil_kalender_bi,
 E.ambil_aliran_dana, E.ambil_kalender_emiten, E._riwayat) = _latar


# ── 9. Regresi: simbol yfinance tidak boleh diberi suffix .JK sembarangan ──
bagian("9. regresi suffix .JK pada simbol non-emiten")
try:
    from idx import providers as P
except Exception:
    import providers as P  # type: ignore

cek("emiten polos dapat .JK",
    re.fullmatch(r"[A-Z0-9]{1,6}", "BBCA") is not None)
cek("indeks ^JKSE bukan emiten polos",
    re.fullmatch(r"[A-Z0-9]{1,6}", "^JKSE") is None)
cek("kurs USDIDR=X bukan emiten polos",
    re.fullmatch(r"[A-Z0-9]{1,6}", "USDIDR=X") is None)
cek("komoditas CL=F bukan emiten polos",
    re.fullmatch(r"[A-Z0-9]{1,6}", "CL=F") is None)
cek("providers punya import re", hasattr(P, "re"))


# ── 9b. TAHAP 2: rezim pasar, delta BI Rate, inflasi, proxy, MSCI, anomali ──
bagian("9b. Tahap 2 — rezim pasar")

# Naik >5% / turun >5% / datar, dibangun dari deret close sintetis.
def _seri_close(close: list[float]) -> list[dict]:
    return [{"close": c} for c in close]


_datar = [100.0] * 25
_rz = E.deteksi_rezim(_seri_close(_datar))
cek("rezim: deret datar -> bukan stres", _rz.get("pasar_stres") is False,
    str(_rz.get("rezim")))
cek("rezim: deret datar -> 'normal'", _rz.get("rezim") == "normal",
    str(_rz.get("rezim")))
cek("rezim: ret5 datar = 0", _rz.get("ihsg_ret_5h_pct") == 0.0,
    str(_rz.get("ihsg_ret_5h_pct")))
cek("rezim: pemicu kosong saat datar", _rz.get("pemicu") == [],
    str(_rz.get("pemicu")))

# 5 sesi turun 6% -> kena ambang 5h.
_turun = [100.0] * 19 + [100.0, 99.0, 98.0, 97.0, 96.0, 94.0]
_rz2 = E.deteksi_rezim(_seri_close(_turun))
cek("rezim: turun 6%/5 sesi -> stres", _rz2.get("pasar_stres") is True,
    str(_rz2.get("ihsg_ret_5h_pct")))
cek("rezim: pemicu ihsg_5h tercatat",
    any(p["faktor"] == "ihsg_5h" for p in _rz2.get("pemicu") or []),
    str(_rz2.get("pemicu")))
_p5 = [p for p in _rz2.get("pemicu") or [] if p["faktor"] == "ihsg_5h"]
cek("rezim: pemicu menyimpan nilai & ambang (transparan)",
    bool(_p5) and _p5[0]["nilai_pct"] is not None
    and _p5[0]["ambang_pct"] == -5.0,
    str(_p5[:1]))

# Turun 4,9% -> TIDAK boleh kena ambang 5%.
_pas = [100.0] * 19 + [100.0, 99.5, 99.0, 98.5, 98.0, 95.1]
_rz3 = E.deteksi_rezim(_seri_close(_pas))
cek("rezim: turun 4,9%/5 sesi -> tidak stres",
    _rz3.get("pasar_stres") is False, str(_rz3.get("ihsg_ret_5h_pct")))

# 1 sesi jatuh 3,5% -> kena ambang 1h.
_jatuh = [100.0] * 24 + [96.5]
_rz4 = E.deteksi_rezim(_seri_close(_jatuh))
cek("rezim: -3,5% 1 sesi -> stres", _rz4.get("pasar_stres") is True,
    str(_rz4.get("ihsg_ret_1h_pct")))
cek("rezim: pemicu ihsg_1h tercatat",
    any(p["faktor"] == "ihsg_1h" for p in _rz4.get("pemicu") or []))

# Data kurang -> "data tidak tersedia", bukan "normal" (menyesatkan).
_rz5 = E.deteksi_rezim(_seri_close([100.0, 99.0]))
cek("rezim: data < 6 bar -> 'data tidak tersedia'",
    _rz5.get("rezim") == TIDAK_ADA, str(_rz5.get("rezim")))
cek("rezim: data kurang -> pasar_stres None (bukan False)",
    _rz5.get("pasar_stres") is None, str(_rz5.get("pasar_stres")))
cek("rezim: data kurang -> alasan diisi", bool(_rz5.get("alasan")))
cek("rezim: bar tanpa close diabaikan",
    E.deteksi_rezim([{"close": None}, {"close": 100.0}]).get("rezim")
    == TIDAK_ADA)
cek("rezim: input None aman",
    E.deteksi_rezim(None).get("rezim") == TIDAK_ADA)

# Trading halt tidak boleh diklaim tanpa bukti.
cek("rezim: halt tidak diklaim tanpa bukti",
    _rz.get("trading_halt", {}).get("terdeteksi") is False,
    str(_rz.get("trading_halt", {}).get("alasan"))[:80])
cek("rezim: halt menyebut alasan tidak tersedia bila tak ada bukti",
    "tidak ada sumber resmi" in (_rz.get("trading_halt", {}).get("alasan") or "").lower(),
    str(_rz.get("trading_halt", {}).get("alasan"))[:90])
cek("rezim: ambang disimpan di output (bisa diaudit)",
    _rz.get("ambang", {}).get("ihsg_turun_5h_pct") == -5.0)
cek("rezim: catatan menyebut 'tidak disembunyikan'",
    "tidak" in (_rz.get("catatan") or "").lower())

bagian("9c. Tahap 2 — BI Rate berubah (delta)")

# Delta dihitung dari riwayat, bukan cuma level.
_riwayat_turun = [
    {"nilai_pct": 5.50, "tanggal": "2026-10-21"},
    {"nilai_pct": 5.75, "tanggal": "2026-09-23"},
]
_d = E.hitung_delta_bi_rate(_riwayat_turun)
cek("bi_rate: status turun", _d.get("status") == "turun", str(_d))
cek("bi_rate: delta -0,25 pp", _d.get("delta_pp") == -0.25, str(_d.get("delta_pp")))
cek("bi_rate: arah -1", _d.get("arah") == -1, str(_d.get("arah")))

_riwayat_naik = [
    {"nilai_pct": 6.00, "tanggal": "2026-10-21"},
    {"nilai_pct": 5.75, "tanggal": "2026-09-23"},
]
_d2 = E.hitung_delta_bi_rate(_riwayat_naik)
cek("bi_rate: status naik", _d2.get("status") == "naik", str(_d2))
cek("bi_rate: delta +0,25 pp", _d2.get("delta_pp") == 0.25, str(_d2.get("delta_pp")))
cek("bi_rate: arah +1", _d2.get("arah") == 1, str(_d2.get("arah")))

_d3 = E.hitung_delta_bi_rate(_riwayat_turun[:1] + _riwayat_turun[:1])
cek("bi_rate: sama -> 'tetap'", _d3.get("status") == "tetap", str(_d3))
cek("bi_rate: sama -> delta 0", _d3.get("delta_pp") == 0.0)

cek("bi_rate: riwayat kosong -> tidak tersedia",
    E.hitung_delta_bi_rate([]).get("nilai") == TIDAK_ADA)
cek("bi_rate: 1 entri -> delta tidak tersedia (tak ada pembanding)",
    E.hitung_delta_bi_rate(_riwayat_turun[:1]).get("delta_pp") is None)
cek("bi_rate: 'tetap' bukan 'tidak tersedia' (beda arti)",
    _d3.get("status") == "tetap" and _d3.get("status") != TIDAK_ADA)

bagian("9d. Tahap 2 — inflasi BPS lebih baru dari bi.go.id")

_inf_bps = {"nilai_pct": 3.28, "periode": "September 2026",
            "periode_iso": "2026-09", "sumber": "BPS"}
_inf_bi = {"nilai_pct": 3.19, "periode": "Agustus 2026",
           "periode_iso": "2026-08", "sumber": "bi.go.id"}
_pilih = E.pilih_inflasi_terbaru(_inf_bps, _inf_bi)
cek("inflasi: pilih periode terbaru (BPS September)",
    _pilih.get("periode_iso") == "2026-09", str(_pilih.get("periode")))
cek("inflasi: nilai = 3,28", _pilih.get("nilai_pct") == 3.28)
cek("inflasi: pembanding disimpan", _pilih.get("sumber_pembanding", {})
    .get("periode") == "Agustus 2026")
cek("inflasi: catatan jeda rilis diisi",
    "September" in (_pilih.get("catatan_jeda_rilis") or ""))
_pilih2 = E.pilih_inflasi_terbaru(_inf_bi, _inf_bps)
cek("inflasi: urutan argumen tidak mengubah hasil",
    _pilih2.get("periode_iso") == "2026-09")
_pilih3 = E.pilih_inflasi_terbaru(None, _inf_bi)
cek("inflasi: BPS tidak ada -> pakai bi.go.id",
    _pilih3.get("periode_iso") == "2026-08", str(_pilih3.get("periode")))
cek("inflasi: keduanya tidak ada -> 'data tidak tersedia'",
    E.pilih_inflasi_terbaru(None, None).get("nilai") == TIDAK_ADA
    if "nilai" in E.pilih_inflasi_terbaru(None, None)
    else E.pilih_inflasi_terbaru(None, None).get("nilai_pct") is None)
cek("inflasi: konstanta BPS terverifikasi = 3,28",
    E.INFLASI_BPS_TERVERIFIKASI.get("nilai_pct") == 3.28)
cek("inflasi: konstanta punya tanggal rilis 2026-10-01",
    E.INFLASI_BPS_TERVERIFIKASI.get("tanggal_rilis") == "2026-10-01")
cek("inflasi: konstanta menandai verifikasi manual",
    "verifikasi" in json.dumps(E.INFLASI_BPS_TERVERIFIKASI,
                               ensure_ascii=False).lower())

bagian("9e. Tahap 2 — mata uang & satuan komoditas")

_mk = E.ambil_makro
_meta = {m["kunci"]: m for m in E.MAKRO_YF}
cek("komoditas: CPO bermata uang USD (BUKAN MYR)",
    _meta["cpo"]["mata_uang"] == "USD", str(_meta["cpo"]))
cek("komoditas: CPO satuan per metrik ton",
    "metrik ton" in _meta["cpo"]["satuan"], str(_meta["cpo"]["satuan"]))
cek("komoditas: WTI minyak USD per barel",
    _meta["minyak"]["mata_uang"] == "USD"
    and "barel" in _meta["minyak"]["satuan"])
cek("komoditas: emas USD per troy ounce",
    _meta["emas"]["mata_uang"] == "USD"
    and "troy" in _meta["emas"]["satuan"])
cek("komoditas: biji besi USD per metrik ton",
    _meta["iron_ore"]["mata_uang"] == "USD"
    and "metrik ton" in _meta["iron_ore"]["satuan"])
cek("komoditas: IHSG dalam IDR (poin indeks)",
    _meta["ihsg"]["mata_uang"] == "IDR")
cek("komoditas: USD/IDR satuan rupiah per 1 USD",
    "rupiah" in _meta["usdidr"]["satuan"].lower())
cek("komoditas: S&P 500 dalam USD", _meta["sp500"]["mata_uang"] == "USD")
cek("komoditas: SEMUA seri punya mata_uang",
    all(m.get("mata_uang") for m in E.MAKRO_YF),
    str([m["kunci"] for m in E.MAKRO_YF if not m.get("mata_uang")]))
cek("komoditas: SEMUA seri punya satuan",
    all(m.get("satuan") for m in E.MAKRO_YF))

bagian("9f. Tahap 2 — batubara & nikel = PROXY berlabel")

_proxy = {p["kunci"]: p for p in E.PROXY_KOMODITAS}
cek("proxy: batubara ada", "batubara" in _proxy)
cek("proxy: nikel ada", "nikel" in _proxy)
cek("proxy: batubara pakai sektor COAL MINING",
    any("COAL" in s.upper() for s in _proxy["batubara"]["sektor_proxy"]),
    str(_proxy["batubara"]["sektor_proxy"]))
cek("proxy: nikel pakai sektor METAL AND MINERAL MINING",
    any("METAL AND MINERAL" in s.upper()
        for s in _proxy["nikel"]["sektor_proxy"]),
    str(_proxy["nikel"]["sektor_proxy"]))

_makro_uji = {"batubara": {"nilai": TIDAK_ADA}, "nikel": {"nilai": TIDAK_ADA}}
_kek_uji = {"per_sektor": [
    {"sektor": "COAL MINING", "ret_5h_pct": -10.0, "ret_20h_pct": -9.0,
     "rel_5h_pct": -1.5, "rel_20h_pct": -2.0},
    {"sektor": "METAL AND MINERAL MINING", "ret_5h_pct": -18.0,
     "ret_20h_pct": -22.0, "rel_5h_pct": -9.5, "rel_20h_pct": -12.0},
]}
E.lengkapi_proxy_komoditas(_makro_uji, _kek_uji)
cek("proxy: batubara diberi label jenis 'proxy'",
    _makro_uji["batubara"].get("jenis") == "proxy",
    str(_makro_uji["batubara"].get("jenis")))
cek("proxy: nikel diberi label jenis 'proxy'",
    _makro_uji["nikel"].get("jenis") == "proxy")
cek("proxy: nilai langsung TETAP 'data tidak tersedia' (tidak dikarang)",
    _makro_uji["batubara"]["nilai"] == TIDAK_ADA,
    str(_makro_uji["batubara"]["nilai"]))
cek("proxy: angka sektor disimpan",
    _makro_uji["batubara"]["proxy"]["ret_5h_pct"] == -10.0)
cek("proxy: relatif vs IHSG diteruskan",
    _makro_uji["nikel"]["proxy"].get("rel_5h_pct") == -9.5,
    str(_makro_uji["nikel"]["proxy"]))
cek("proxy: catatan menjelaskan bukan harga komoditas",
    "proxy" in (_makro_uji["batubara"].get("catatan") or "").lower())
cek("proxy: satuan menyebut return sektor, bukan harga",
    "return" in (_makro_uji["batubara"].get("satuan") or "").lower(),
    str(_makro_uji["batubara"].get("satuan")))
# Relatif yang tidak ada di upstream TIDAK boleh dikarang.
_makro_rel = {"batubara": {"nilai": TIDAK_ADA}, "nikel": {"nilai": TIDAK_ADA}}
E.lengkapi_proxy_komoditas(_makro_rel, {"per_sektor": [
    {"sektor": "COAL MINING", "ret_5h_pct": -10.0}]})
cek("proxy: relatif tanpa data upstream -> None (bukan dikarang)",
    _makro_rel["batubara"]["proxy"].get("rel_5h_pct") is None,
    str(_makro_rel["batubara"]["proxy"]))
# Sektor proxy hilang -> jujur "tidak tersedia".
_makro_kosong = {"batubara": {"nilai": TIDAK_ADA}, "nikel": {"nilai": TIDAK_ADA}}
E.lengkapi_proxy_komoditas(_makro_kosong, {"per_sektor": []})
cek("proxy: sektor proxy tak ada -> jenis 'tidak tersedia'",
    _makro_kosong["batubara"].get("jenis") == "tidak tersedia",
    str(_makro_kosong["batubara"].get("jenis")))

bagian("9f2. kesegaran makro — CPO/iron ditandai, bukan disamarkan")
_kes = E._tandai_kesegaran({
    "cpo": {"label": "CPO", "tanggal_sumber": "2026-10-29"},
    "iron_ore": {"label": "Biji Besi", "tanggal_sumber": "2026-10-29"},
    "minyak": {"label": "Minyak", "tanggal_sumber": "2026-10-30"},
}, "2026-10-30")
cek("kesegaran: status basi", _kes.get("status") == "basi", str(_kes))
cek("kesegaran: CPO/iron masuk item_basi", _kes.get("jumlah_basi") == 2,
    str(_kes.get("item_basi")))
cek("kesegaran: CPO basi=true", _kes and True)
_mk = {"cpo": {"label": "CPO", "tanggal_sumber": "2026-10-29"},
       "minyak": {"label": "Minyak", "tanggal_sumber": "2026-10-30"}}
E._tandai_kesegaran(_mk, "2026-10-30")
cek("kesegaran: cpo.basi true", _mk["cpo"].get("basi") is True)
cek("kesegaran: minyak.basi false", _mk["minyak"].get("basi") is False)

bagian("9g. Tahap 2 — event MSCI (tanpa menebak emiten)")

_ev = E.ambil_event_risiko()
cek("msci: event tersedia", _ev.get("nilai") == "tersedia", str(_ev)[:120])
_msci = [e for e in _ev.get("events") or [] if "msci" in e.get("id", "")]
cek("msci: event MSCI ada", len(_msci) == 1, str(len(_msci)))
_m = _msci[0] if _msci else {}
cek("msci: tanggal 15 Okt 2026", _m.get("tanggal_diumumkan") == "2026-10-15",
    str(_m.get("tanggal_diumumkan")))
cek("msci: status usulan belum final",
    "belum final" in (_m.get("status") or "").lower(), str(_m.get("status")))
cek("msci: arah Emerging -> Standalone",
    "Standalone" in (_m.get("judul") or ""), str(_m.get("judul"))[:90])
cek("msci: punya URL sumber", (_m.get("sumber_url") or "").startswith("http"),
    str(_m.get("sumber_url")))
cek("msci: punya sumber alternatif",
    len(_m.get("sumber_url_alternatif") or []) >= 1)
cek("msci: emiten_terdampak = 'data tidak tersedia' (TIDAK ditebak)",
    _m.get("emiten_terdampak") == TIDAK_ADA, str(_m.get("emiten_terdampak")))
cek("msci: alasan emiten dijelaskan", bool(_m.get("alasan_emiten")))
cek("msci: catatan verifikasi jujur (sumber primer gagal)",
    "BELUM DIVERIFIKASI" in (_m.get("catatan_verifikasi") or ""),
    str(_m.get("catatan_verifikasi"))[:90])
cek("msci: dampak menyebut kepemilikan asing",
    "asing" in (_m.get("dampak") or "").lower())
cek("msci: umur_hari dihitung", isinstance(_m.get("umur_hari"), int),
    str(_m.get("umur_hari")))
cek("msci: sumber berita >1", len(_m.get("sumber_berita") or []) >= 2,
    str(_m.get("sumber_berita")))
_halt = [e for e in _ev.get("events") or [] if e.get("id") == "trading_halt_16okt2026"]
cek("halt: event trading halt 16 Okt 2026 ada", len(_halt) == 1, str(len(_halt)))
_h = _halt[0] if _halt else {}
cek("halt: tanggal 16 Okt 2026", _h.get("tanggal_diumumkan") == "2026-10-16",
    str(_h.get("tanggal_diumumkan")))
cek("halt: emiten tidak ditebak", _h.get("emiten_terdampak") == TIDAK_ADA,
    str(_h.get("emiten_terdampak")))
cek("halt: punya sumber berita", len(_h.get("sumber_berita") or []) >= 2,
    str(_h.get("sumber_berita")))

bagian("9h. Tahap 2 — periode anomali 15-21 Okt 2026")

_pa = E.periode_anomali()
cek("anomali: periode terdaftar", len(_pa.get("periode") or []) >= 1)
_p0 = (_pa.get("periode") or [{}])[0]
cek("anomali: mulai 2026-10-15", _p0.get("mulai") == "2026-10-15",
    str(_p0.get("mulai")))
cek("anomali: selesai 2026-10-21", _p0.get("selesai") == "2026-10-21",
    str(_p0.get("selesai")))
cek("anomali: ada sebab tercatat", bool(_p0.get("sebab")))
cek("anomali: bukti angka IHSG disimpan", bool(_p0.get("bukti")))
cek("anomali: catatan wajib dengan & tanpa periode",
    "TANPA" in (_pa.get("catatan") or "").upper())
cek("anomali: tanggal 15 Okt terdeteksi",
    E.tanggal_dalam_anomali("2026-10-15") is True)
cek("anomali: tanggal 21 Okt terdeteksi",
    E.tanggal_dalam_anomali("2026-10-21") is True)
cek("anomali: tanggal 14 Okt TIDAK terdeteksi",
    E.tanggal_dalam_anomali("2026-10-14") is False)
cek("anomali: tanggal 22 Okt TIDAK terdeteksi",
    E.tanggal_dalam_anomali("2026-10-22") is False)
cek("anomali: None aman", E.tanggal_dalam_anomali(None) is False)
cek("anomali: string kosong aman", E.tanggal_dalam_anomali("") is False)
cek("anomali: tanggal dengan jam tetap cocok",
    E.tanggal_dalam_anomali("2026-10-16T10:30:00+07:00") is True)

bagian("9i. Tahap 2 — kalender emiten tanpa lewati Cloudflare")

_kal = E.ambil_kalender_emiten(batas=3)
cek("kalender: bukan hasil bypass Cloudflare",
    "tidak dilewati" in json.dumps(_kal.get("cek") or {},
                                 ensure_ascii=False).lower(),
    str(_kal.get("cek"))[:120])
cek("kalender: idx.co.id ditandai 403",
    "403" in json.dumps(_kal.get("cek") or {}))
cek("kalender: RUPS tetap 'data tidak tersedia' (tidak dikarang)",
    "tidak tersedia" in (_kal.get("catatan") or "").lower()
    or "TIDAK tersedia" in (_kal.get("catatan") or ""),
    str(_kal.get("catatan"))[:100])
cek("kalender: catatan menyebut 'tidak dilewati'",
    "tidak dilewati" in (_kal.get("catatan") or "").lower()
    or "TIDAK tersedia" in (_kal.get("catatan") or ""))
cek("kalender: semua tanggal event berformat YYYY-MM-DD",
    all(re.fullmatch(r"\d{4}-\d{2}-\d{2}", e["tanggal"])
        for e in (_kal.get("events") or [])),
    str([e.get("tanggal") for e in (_kal.get("events") or [])][:5]))
cek("kalender: TIDAK ada tanggal '[]' (bug 21 Okt)",
    all(e["tanggal"] != "[]" for e in (_kal.get("events") or [])))
cek("kalender: nilai 'sebagian' karena RUPS tidak ada",
    _kal.get("nilai") in ("sebagian", TIDAK_ADA), str(_kal.get("nilai")))
cek("kalender: event punya sumber yang bisa diverifikasi",
    all(e.get("sumber") for e in (_kal.get("events") or [])))

bagian("9j. Tahap 2 — peta sensitivitas sektor")

_path_sens = os.path.join(E._ROOT, "config", "sensitivitas_sektor.json")
_sens = json.load(open(_path_sens, encoding="utf-8")) if os.path.exists(_path_sens) else {}
cek("sens: file ada", bool(_sens), _path_sens)
cek("sens: status masih 'usulan'", "usulan" in (_sens.get("status") or ""),
    str(_sens.get("status")))
cek("sens: BELUM dipakai skor (gate tertutup)",
    _sens.get("dipakai_skor") is False, str(_sens.get("dipakai_skor")))
cek("sens: 56 sektor terpetakan", _sens.get("jumlah_sektor") == 56,
    str(_sens.get("jumlah_sektor")))
cek("sens: 9 faktor didefinisikan", len(_sens.get("faktor") or {}) == 9,
    str(len(_sens.get("faktor") or {})))
_sk = _sens.get("sektor") or {}
cek("sens: setiap sektor punya 9 faktor + alasan",
    all(len([k for k in v if k != "alasan"]) == 9
        and v.get("alasan") for v in _sk.values()),
    str([k for k, v in _sk.items() if not v.get("alasan")][:3]))
cek("sens: semua nilai dalam -3..+3",
    all(-3 <= v[k] <= 3 for v in _sk.values()
        for k in v if k != "alasan"),
    str([(s, k, v[k]) for s, v in _sk.items() for k in v
         if k != "alasan" and not -3 <= v[k] <= 3][:3]))
cek("sens: semua nilai bilangan bulat",
    all(isinstance(v[k], int) for v in _sk.values()
        for k in v if k != "alasan"))
cek("sens: SEKTOR nyata di latest.json tercakup SEMUA",
    all(s in _sk for s in (
        "PROPERTY AND REAL ESTATE", "COAL MINING", "BANK",
        "TELECOMUNICATION", "METAL AND MINERAL MINING")),
    str([s for s in ("BANK", "COAL MINING") if s not in _sk]))
cek("sens: OTHER SECTORS semua 0 (tidak jelas)",
    all(_sk.get("OTHER SECTORS", {}).get(k) == 0
        for k in _sk.get("OTHER SECTORS", {}) if k != "alasan"))
cek("sens: COAL MINING diuntungkan rupiah lemah",
    _sk.get("COAL MINING", {}).get("usd_idr_naik", 0) > 0)
cek("sens: PROPERTY paling tertekan BI Rate naik",
    _sk.get("PROPERTY AND REAL ESTATE", {}).get("bi_rate_naik", 0) <= -3,
    str(_sk.get("PROPERTY AND REAL ESTATE", {}).get("bi_rate_naik")))
cek("sens: beta PROPERTY tinggi", _sk.get("PROPERTY AND REAL ESTATE", {})
    .get("ihsg_beta", 0) >= 3)
cek("sens: skala didefinisikan", bool(_sens.get("skala")))

bagian("9k. Tahap 2 — struktur external.json lengkap")

import inspect as _inspect
_struct = _inspect.getsource(E.bangun)
for _kunci in ("rezim_pasar", "event_risiko", "periode_anomali", "bi_rate",
               "inflasi", "makro", "kalender_emiten"):
    cek(f"struktur: '{_kunci}' termasuk keluaran bangun()",
        _kunci in _struct, _struct[:80])
cek("struktur: versi skema naik ke 1.1.0",
    E.VERSI_EKSTERNAL == "1.1.0", E.VERSI_EKSTERNAL)


# ── 10. Data nyata (opsional, butuh jaringan) ──────────────────────────────
if os.environ.get("SKIP_NETWORK") != "1":
    bagian("10. data nyata (jaringan)")
    nyata = E.ambil_bi_rate()
    cek("BI Rate nyata terbaca dari bi.go.id",
        isinstance(nyata.get("nilai_pct"), float), str(nyata)[:150])
    cek("BI Rate nyata wajar (1-20%)",
        isinstance(nyata.get("nilai_pct"), float)
        and 1 <= nyata["nilai_pct"] <= 20, str(nyata.get("nilai_pct")))
    cek("BI Rate nyata punya tanggal ISO",
        bool(re.fullmatch(r"\d{4}-\d{2}-\d{2}", nyata.get("tanggal") or "")))

    infl = E.ambil_inflasi()
    cek("inflasi nyata terbaca", isinstance(infl.get("nilai_pct"), float),
        str(infl)[:150])

    kaln = E.ambil_kalender_bi()
    cek("kalender RDG nyata tersedia", kaln.get("nilai") == "tersedia")
    cek("kalender RDG nyata >= 12 event",
        (kaln.get("jumlah_event") or 0) >= 12, str(kaln.get("jumlah_event")))
    cek("kalender RDG nyata tanggal valid",
        all(re.fullmatch(r"\d{4}-\d{2}-\d{2}", e["tanggal"])
            for e in (kaln.get("events") or [])))

    mny = E.ambil_makro()
    cek("makro nyata: IHSG angka", isinstance(mny["ihsg"].get("nilai"), float))
    cek("makro nyata: USD/IDR angka", isinstance(mny["usdidr"].get("nilai"), float))
    cek("makro nyata: S&P 500 angka", isinstance(mny["sp500"].get("nilai"), float))
    cek("makro nyata: USD/IDR wajar (5000-50000)",
        isinstance(mny["usdidr"].get("nilai"), float)
        and 5000 <= mny["usdidr"]["nilai"] <= 50000,
        str(mny["usdidr"].get("nilai")))
    cek("makro nyata: batubara tetap 'tidak tersedia' (tidak dikarang)",
        mny["batubara"]["nilai"] == TIDAK_ADA)
    cek("makro nyata: nikel tetap 'tidak tersedia' (tidak dikarang)",
        mny["nikel"]["nilai"] == TIDAK_ADA)


print(f"\n== TOTAL: {LULUS} lulus, {GAGAL} gagal ==")
if GAGAL:
    print("Gagal:", "; ".join(_CATATAN[:12]))
sys.exit(0 if GAGAL == 0 else 1)
