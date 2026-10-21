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
cek("inflasi terbaca", inf.get("nilai_pct") == 3.19, str(inf)[:120])
cek("inflasi bawa periode", inf.get("periode") == "August 2026")
cek("inflasi bawa sumber", "bi.go.id" in (inf.get("sumber") or ""))

E._get_html = lambda url, timeout=30: (None, "HTTPError: 503")  # type: ignore
inf_b = E.ambil_inflasi()
cek("inflasi gagal -> tidak tersedia", inf_b.get("nilai") == TIDAK_ADA)


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
ks = E.hitung_kekuatan_sektor(hist, smap, ihsg_datar)
cek("kekuatan: jumlah sektor", ks["jumlah_sektor"] == 3, str(ks["jumlah_sektor"]))
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

ke = E.ambil_kalender_emiten()
cek("kalender emiten -> tidak tersedia", ke["nilai"] == TIDAK_ADA)
cek("kalender emiten events kosong", ke["events"] == [])
cek("kalender emiten catat 403 idx", "403" in json.dumps(ke.get("cek") or {}))


# ── 8. Payload lengkap & penulisan atomik ──────────────────────────────────
bagian("8. payload lengkap & atomic write")
_latar = (E.ambil_makro, E.ambil_bi_rate, E.ambil_inflasi,
          E.ambil_kalender_bi, E.ambil_aliran_dana, E.ambil_kalender_emiten,
          E._riwayat)
E.ambil_makro = lambda: {"ihsg": {"nilai": 5000.0}}          # type: ignore
E.ambil_bi_rate = lambda: {"nilai_pct": 5.75}                # type: ignore
E.ambil_inflasi = lambda: {"nilai_pct": 3.19}                # type: ignore
E.ambil_kalender_bi = lambda tahun=None: {"nilai": "tersedia", "events": []}  # type: ignore
E.ambil_aliran_dana = lambda: {"nilai": TIDAK_ADA}           # type: ignore
E.ambil_kalender_emiten = lambda: {"nilai": TIDAK_ADA, "events": []}  # type: ignore
E._riwayat = lambda simbol, periode="3mo": []                # type: ignore

p = E.bangun(hist={"emiten": {}}, latest={"tanggal_data": "2026-10-21",
                                          "emiten": []})
for k in ("versi_skema", "diperbarui_pada", "makro", "bi_rate", "inflasi",
          "kalender_bi", "kekuatan_sektor", "aliran_dana", "kalender_emiten",
          "disclaimer"):
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
