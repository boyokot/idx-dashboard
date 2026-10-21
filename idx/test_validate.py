#!/usr/bin/env python3
"""
Tes Kelompok E — validasi prepublish, perlindungan snapshot, metadata
sumber, notifikasi kegagalan.

Dijalankan: python3 idx/test_validate.py
Exit 0 hanya bila GAGAL == 0 (bukan ambang jumlah tes).
"""
from __future__ import annotations

import copy
import json
import os
import sys
import tempfile

sys.path.insert(0, "/root/idx-dashboard")

from idx import validate as V            # noqa: E402
from idx import notify as N              # noqa: E402

LULUS = 0
GAGAL = 0
BAGIAN = [""]


def bagian(n: str) -> None:
    BAGIAN[0] = n
    print(f"\n== {n} ==")


def cek(nama: str, kondisi: bool, detail: str = "") -> None:
    global LULUS, GAGAL
    if kondisi:
        LULUS += 1
        print(f"  OK   {nama}")
    else:
        GAGAL += 1
        print(f"  GAGAL {nama}" + (f" :: {detail}" if detail else ""))


def baris(kode: str, **kw) -> dict:
    d = {"kode": kode, "nama": f"Nama {kode}", "harga": 1000.0,
         "chg_pct": 1.0, "rsi": 50.0, "skor": 60.0, "per": 10.0,
         "pbv": 1.0, "roe": 0.1, "der": 0.5, "volume": 1000.0,
         "sumber_data": "yfinance", "sumber_harga": "yfinance",
         "sumber_fundamental": "yfinance"}
    d.update(kw)
    return d


def payload(n: int = 30, **kw) -> dict:
    p = {"tanggal_data": "2026-10-21", "versi_skema": "1.4.0",
         "emiten": [baris(f"AAA{i:03d}") for i in range(n)],
         "skor_parsial": {"penuh": 0, "sebagian": n, "tanpa_skor": 0},
         "alerts": {"total": 5}}
    p.update(kw)
    return p


# ── 1. payload sehat lolos ────────────────────────────────────────────────
bagian("1. payload sehat")
h = V.validasi(payload(), total_universe=30)
cek("payload sehat lolos", h["ok"], str(h["errors"]))
cek("tidak ada error", h["errors"] == [], str(h["errors"]))
cek("stats emiten benar", h["stats"]["emiten"] == 30, str(h["stats"]))
cek("rasio harga 1.0", h["stats"]["rasio_harga"] == 1.0)
cek("ringkas menyebut OK", "OK" in V.ringkas(h), V.ringkas(h))

# ── 2. emiten kosong / bukan list -> DITOLAK ──────────────────────────────
bagian("2. payload kosong")
for isi, label in [([], "list kosong"), (None, "None"), ({}, "dict")]:
    h = V.validasi({"emiten": isi, "tanggal_data": "x"})
    cek(f"emiten {label} ditolak", not h["ok"], str(h))
cek("alasan menyebut kosong",
    "kosong" in " ".join(V.validasi({"emiten": []})["errors"]).lower())

# ── 3. jumlah emiten di bawah minimum -> DITOLAK ──────────────────────────
bagian("3. emiten terlalu sedikit")
h = V.validasi(payload(n=5), total_universe=975)
cek("5 emiten ditolak", not h["ok"], str(h["errors"]))
cek("alasan menyebut jumlah",
    any("hanya" in e for e in h["errors"]), str(h["errors"]))
h = V.validasi(payload(n=V.MIN_EMITEN), total_universe=975)
cek(f"{V.MIN_EMITEN} emiten lolos", h["ok"], str(h["errors"]))

# ── 4. kode duplikat -> DITOLAK ───────────────────────────────────────────
bagian("4. kode duplikat")
p = payload()
p["emiten"][1]["kode"] = p["emiten"][0]["kode"]
h = V.validasi(p)
cek("duplikat ditolak", not h["ok"], str(h["errors"]))
cek("alasan menyebut duplikat",
    any("duplikat" in e for e in h["errors"]), str(h["errors"]))

# kode sama tapi beda kapitalisasi tetap duplikat
p = payload()
p["emiten"][1]["kode"] = p["emiten"][0]["kode"].lower()
h = V.validasi(p)
cek("duplikat beda kapitalisasi terdeteksi", not h["ok"], str(h["errors"]))

# ── 5. harga hilang massal -> DITOLAK ─────────────────────────────────────
bagian("5. harga hilang")
p = payload(n=40)
for r in p["emiten"][:30]:
    r["harga"] = None
h = V.validasi(p)
cek("harga <50% ditolak", not h["ok"], str(h["errors"]))
cek("alasan menyebut sumber harga",
    any("sumber harga" in e for e in h["errors"]), str(h["errors"]))

p = payload(n=40)
for r in p["emiten"][:10]:
    r["harga"] = None           # 30/40 = 75% -> lolos tapi warning
h = V.validasi(p)
cek("harga 75% lolos", h["ok"], str(h["errors"]))
cek("harga 75% memicu warning",
    any("cakupan harga" in w for w in h["warnings"]), str(h["warnings"]))

# ── 6. pelanggaran satuan -> DITOLAK ──────────────────────────────────────
bagian("6. satuan di luar batas")
kasus = [
    ("skor", {"skor": 150.0}, "skor di luar"),
    ("skor negatif", {"skor": -5.0}, "skor di luar"),
    ("rsi", {"rsi": 130.0}, "rsi di luar"),
    ("chg_pct", {"chg_pct": -150.0}, "chg_pct"),
    ("volume", {"volume": -100.0}, "volume negatif"),
    ("der", {"der": 500.0}, "der >"),
    ("roe", {"roe": 50.0}, "roe"),
]
for label, patch_, cari in kasus:
    p = payload()
    p["emiten"][0].update(patch_)
    h = V.validasi(p)
    ketemu = any(cari in e for e in h["errors"])
    cek(f"{label} ditolak", not h["ok"] and ketemu,
        f"ok={h['ok']} errors={h['errors']}")

# DER negatif = ekuitas negatif: data SAH (LAPD/CNKO/MYTX), WAJIB lolos
# dengan warning. Kalau ini ditolak, pipeline asli tidak akan pernah publish.
p = payload()
p["emiten"][0]["der"] = -4.08
h = V.validasi(p)
cek("DER negatif LOLOS (ekuitas negatif, bukan data rusak)",
    h["ok"], str(h["errors"]))
cek("DER negatif memicu warning",
    any("DER negatif" in w for w in h["warnings"]), str(h["warnings"]))
cek("DER negatif tidak masuk error der",
    not any("der >" in e for e in h["errors"]), str(h["errors"]))

# ── 7. nilai aneh tidak boleh jadi 0 diam-diam ────────────────────────────
bagian("7. null bukan 0")
# Uji konversi langsung: kalau _angka salah, semua validasi di atas ikut salah.
cek("_angka(None) -> None", V._angka(None) is None)
cek("_angka('') -> None", V._angka("") is None)
cek("_angka('  ') -> None", V._angka("  ") is None)
cek("_angka(True) -> None (bool bukan angka)", V._angka(True) is None)
cek("_angka(False) -> None", V._angka(False) is None)
cek("_angka('abc') -> None", V._angka("abc") is None)
cek("_angka('nan') -> None", V._angka("nan") is None)
cek("_angka('inf') -> None", V._angka("inf") is None)
cek("_angka(0) -> 0.0 (nol itu angka sah)", V._angka(0) == 0.0)
cek("_angka('12.5') -> 12.5", V._angka("12.5") == 12.5)
# "97,5" format id-ID TIDAK boleh jadi 97.5: float("97,5") error -> None.
# Kalau ini salah, skor 97,5 bisa lolos sebagai angka yang tidak pernah ada.
cek("_angka('97,5') -> None (koma id-ID bukan desimal)", V._angka("97,5") is None,
    repr(V._angka("97,5")))
# "97.5%" -> persen dibuang, jadi 97.5
cek("_angka('97.5%') -> 97.5", V._angka("97.5%") == 97.5,
    repr(V._angka("97.5%")))

p = payload()
p["emiten"][0].update({"skor": None, "rsi": None, "per": None,
                       "volume": None, "roe": None, "der": None})
h = V.validasi(p)
cek("null tidak dianggap pelanggaran", h["ok"], str(h["errors"]))

# Fundamental kosong total HARUS jadi warning; harga tetap penuh sehingga
# rasio harga tidak berubah. Ini memastikan null benar-benar dilewati.
p = payload()
for r in p["emiten"]:
    r["per"] = None
    r["roe"] = None
h = V.validasi(p)
cek("semua fundamental null -> ada warning fundamental",
    any("fundamental" in w for w in h["warnings"]), str(h["warnings"]))
cek("fundamental null -> ada_fundamental 0",
    h["stats"]["ada_fundamental"] == 0, str(h["stats"]))
cek("fundamental null -> ada_harga tetap 30",
    h["stats"]["ada_harga"] == 30, str(h["stats"]))

# bool bukan angka: skor True tidak boleh dibaca sebagai 1.0
p = payload()
p["emiten"][0]["skor"] = True
cek("skor True tidak dianggap angka",
    V.validasi(p)["ok"], str(V.validasi(p)["errors"]))

# skor string angka valid tetap diterima
p = payload()
p["emiten"][0]["skor"] = "65.5"
cek("skor string '65.5' diterima", V.validasi(p)["ok"])

# ── 8. tanggal_data wajib ─────────────────────────────────────────────────
bagian("8. tanggal_data")
p = payload()
del p["tanggal_data"]
h = V.validasi(p)
cek("tanpa tanggal_data ditolak", not h["ok"], str(h["errors"]))
cek("alasan menyebut tanggal_data",
    any("tanggal_data" in e for e in h["errors"]), str(h["errors"]))
p["tanggal_data"] = "   "
cek("tanggal_data spasi ditolak", not V.validasi(p)["ok"])

# ── 9. warning tidak memblokir ────────────────────────────────────────────
bagian("9. warning vs error")
p = payload(n=100, total_universe=975)
p["skor_parsial"] = {"penuh": 0, "sebagian": 100}
p["alerts"] = {"total": 0}
h = V.validasi(p, total_universe=975, run_penuh=True)
cek("warning tidak memblokir publish", h["ok"], str(h["errors"]))
cek("warning cakupan emiten ada",
    any("emiten" in w for w in h["warnings"]), str(h["warnings"]))
cek("warning skor penuh 0 ada",
    any("skor penuh" in w for w in h["warnings"]), str(h["warnings"]))
cek("warning alert kosong ada",
    any("alert kosong" in w for w in h["warnings"]), str(h["warnings"]))
cek("ringkas menyebut warning", "warning" in V.ringkas(h), V.ringkas(h))

# run parsial tidak memicu warning cakupan emiten
h = V.validasi(p, total_universe=975, run_penuh=False)
cek("run parsial tidak warning cakupan emiten",
    not any("cakupan" in w or "emiten 100" in w for w in h["warnings"]),
    str(h["warnings"]))

# ── 10. validasi TIDAK mengubah payload ───────────────────────────────────
bagian("10. tidak mengubah data")
p = payload()
asli = copy.deepcopy(p)
V.validasi(p)
cek("payload tidak dimutasi", p == asli, "payload berubah setelah validasi")

# ── 11. notifikasi kegagalan ──────────────────────────────────────────────
bagian("11. notifikasi gagal")
m = N.bangun_pesan_gagal("Publish ditolak", ["harga hanya 10/100 emiten"])
cek("judul ada", "Publish ditolak" in m, m)
cek("alasan ada", "harga hanya 10/100" in m, m)
cek("ada keterangan data lama dipertahankan",
    "Data lama dipertahankan" in m, m)
cek("tidak ada HTML rusak", m.count("<b>") == m.count("</b>"), m)
cek("tidak ada tag i menggantung", m.count("<i>") == m.count("</i>"), m)

m2 = N.bangun_pesan_gagal("X", [f"alasan {i}" for i in range(10)])
cek("alasan dibatasi 6", m2.count("•") == 7, m2)   # 6 + 1 baris "… dan N"
cek("menyebut sisa alasan", "dan 4 alasan lain" in m2, m2)

m3 = N.bangun_pesan_gagal("X", "satu alasan",
                          {"emiten": 10, "ada_harga": 3,
                           "ada_fundamental": 2})
cek("stats ditampilkan", "emiten 10" in m3 and "harga 3" in m3, m3)

# kirim_gagal tanpa konfigurasi tidak melempar
r = N.kirim_gagal("X", "y")
cek("kirim_gagal tidak melempar", isinstance(r, dict), str(r))
cek("kirim_gagal melaporkan status",
    "terkirim" in r and "alasan" in r, str(r))

# ── 12. ringkas() semua keadaan ───────────────────────────────────────────
bagian("12. ringkas")
cek("ringkas OK", V.ringkas({"ok": True, "errors": [], "warnings": [],
                             "stats": {"emiten": 5}}) .startswith("[validasi] OK"))
cek("ringkas DITOLAK", "DITOLAK" in V.ringkas(
    {"ok": False, "errors": ["x"], "warnings": [],
     "stats": {"emiten": 5}}))
cek("ringkas aman tanpa stats", isinstance(V.ringkas({}), str))

# ── 13. data ASLI harus lolos ─────────────────────────────────────────────
# Ini uji paling penting: validator yang menolak data produksi yang sah akan
# membekukan dashboard selamanya. Kalau bagian ini gagal, batas di atas
# salah — bukan datanya.
bagian("13. data produksi asli")
try:
    with open("/root/idx-dashboard/data/latest.json", encoding="utf-8") as f:
        asli = json.load(f)
    ha = V.validasi(asli, total_universe=975,
                    run_penuh=asli.get("jumlah_emiten", 0) >= 975)
    cek("latest.json asli LOLOS validasi", ha["ok"], str(ha["errors"]))
    cek("data asli: emiten >= 900", ha["stats"]["emiten"] >= 900,
        str(ha["stats"]))
    cek("data asli: rasio harga > 0.9", ha["stats"]["rasio_harga"] > 0.9,
        str(ha["stats"]))
    cek("data asli: tanggal_data terisi",
        bool(str(asli.get("tanggal_data") or "").strip()),
        repr(asli.get("tanggal_data")))
    if ha["warnings"]:
        print(f"  (info) {len(ha['warnings'])} warning pada data asli:")
        for w in ha["warnings"]:
            print(f"         - {w}")
except FileNotFoundError:
    cek("latest.json ada", False, "file tidak ditemukan")

# ── 14. perlindungan snapshot ─────────────────────────────────────────────
bagian("14. perlindungan snapshot")
from idx import pipeline as PIPE          # noqa: E402

tmpdir = tempfile.mkdtemp()
snap_p = os.path.join(tmpdir, "2026-10-21.json")


def rows_dgn_harga(n_harga: int, n_total: int = 30) -> list:
    out = [baris(f"BBB{i:03d}") for i in range(n_total)]
    for r in out[n_harga:]:
        r["harga"] = None
    return out


# belum ada snapshot -> selalu boleh tulis
boleh, info = PIPE.snapshot_layak_timpa(snap_p, rows_dgn_harga(10))
cek("snapshot belum ada -> boleh tulis", boleh, str(info))
cek("info lama 0", info["lama"] == 0, str(info))
cek("info baru dihitung", info["baru"] == 10, str(info))

# buat snapshot lama dengan 25 berharga
with open(snap_p, "w", encoding="utf-8") as f:
    json.dump({"emiten": rows_dgn_harga(25)}, f)

boleh, info = PIPE.snapshot_layak_timpa(snap_p, rows_dgn_harga(10))
cek("cakupan LEBIH RENDAH -> tidak boleh timpa", not boleh, str(info))
cek("info melaporkan lama 25", info["lama"] == 25, str(info))
cek("info melaporkan baru 10", info["baru"] == 10, str(info))

boleh, _ = PIPE.snapshot_layak_timpa(snap_p, rows_dgn_harga(25))
cek("cakupan SAMA -> boleh timpa", boleh)

boleh, _ = PIPE.snapshot_layak_timpa(snap_p, rows_dgn_harga(30))
cek("cakupan LEBIH TINGGI -> boleh timpa", boleh)

# 0 berharga sama sekali tidak boleh menimpa snapshot sehat
boleh, _ = PIPE.snapshot_layak_timpa(snap_p, rows_dgn_harga(0))
cek("nol harga tidak menimpa snapshot sehat", not boleh)

# snapshot lama rusak/JSON invalid -> anggap tidak ada, boleh tulis
with open(snap_p, "w", encoding="utf-8") as f:
    f.write("{rusak")
boleh, info = PIPE.snapshot_layak_timpa(snap_p, rows_dgn_harga(5))
cek("snapshot lama rusak -> boleh tulis", boleh, str(info))
cek("snapshot rusak -> lama 0", info["lama"] == 0, str(info))

# snapshot lama ada tapi emiten kosong -> boleh tulis
with open(snap_p, "w", encoding="utf-8") as f:
    json.dump({"emiten": []}, f)
boleh, _ = PIPE.snapshot_layak_timpa(snap_p, rows_dgn_harga(5))
cek("snapshot lama kosong -> boleh tulis", boleh)

# ── 15. notifikasi: anti-spam & tidak pernah melempar ────────────────────
bagian("15. anti-spam notifikasi")
from idx import notify as N          # noqa: E402

# Jangan pernah menyentuh state produksi saat tes.
st_tmp = os.path.join(tempfile.mkdtemp(), "state.json")
N._STATE_NYAMPAI = st_tmp

# telegram tidak dikonfigurasi -> jangan melempar, laporkan alasannya
_env_simpan = {k: os.environ.pop(k, None)
               for k in ("TELEGRAM_BOT_TOKEN", "IDX_TELEGRAM_TOKEN",
                         "TELEGRAM_CHAT_ID", "IDX_TELEGRAM_CHAT",
                         "TELEGRAM_HOME_CHANNEL")}
N._ENV_DIMUAT = True                  # jangan muat ulang .env saat tes ini
ada_tok = N.tersedia()
cek("tanpa kredensial: tersedia() False", not ada_tok, str(ada_tok))
hasil = N.kirim_gagal("Uji", ["alasan"])
cek("tanpa kredensial: kirim_gagal tidak melempar",
    isinstance(hasil, dict) and hasil.get("terkirim") is False, str(hasil))
cek("tanpa kredensial: alasan jelas",
    "tidak dikonfigurasi" in str(hasil.get("alasan", "")), str(hasil))

# pulihkan kredensial lalu uji dedupe memakai _kirim palsu
for k, v in _env_simpan.items():
    if v:
        os.environ[k] = v
N._ENV_DIMUAT = True

kiriman = []


def _kirim_palsu(teks):
    kiriman.append(teks)
    return True


_asli = N._kirim
N._kirim = _kirim_palsu

if N.tersedia():
    r1 = N.kirim_gagal("Publish ditolak", ["hanya 5 emiten (< 20)"], {"emiten": 5})
    cek("pengiriman pertama terkirim", r1.get("terkirim") is True, str(r1))
    cek("isi terkirim tercatat", len(kiriman) == 1, str(len(kiriman)))

    r2 = N.kirim_gagal("Publish ditolak", ["hanya 5 emiten (< 20)"], {"emiten": 5})
    cek("kegagalan IDENTIK kedua kali ditahan", r2.get("terkirim") is False,
        str(r2))
    cek("anti-spam menyebut alasannya",
        "anti-spam" in str(r2.get("alasan", "")), str(r2))
    cek("tidak ada pesan kedua", len(kiriman) == 1, str(len(kiriman)))

    r3 = N.kirim_gagal("Publish ditolak", ["cakupan harga 3%"], {"emiten": 5})
    cek("kegagalan BERBEDA tetap dikirim", r3.get("terkirim") is True, str(r3))
    cek("pesan berbeda tercatat", len(kiriman) == 2, str(len(kiriman)))

    # state rusak tidak boleh memblokir pengiriman
    with open(st_tmp, "w", encoding="utf-8") as f:
        f.write("{rusak")
    r4 = N.kirim_gagal("Publish ditolak", ["hanya 5 emiten (< 20)"], {"emiten": 5})
    cek("state rusak -> tetap kirim (lebih baik dobel)",
        r4.get("terkirim") is True, str(r4))
else:
    for nm in ("pengiriman pertama", "identik ditahan", "berbeda dikirim"):
        cek(f"(dilewati: kredensial tidak ada) {nm}", True)

N._kirim = _asli

# kirim_gagal tidak boleh melempar walau _kirim melempar
N._kirim = lambda teks: (_ for _ in ()).throw(RuntimeError("jaringan mati"))
r5 = N.kirim_gagal("Uji", ["x"])
cek("_kirim melempar -> kirim_gagal tidak melempar",
    isinstance(r5, dict) and r5.get("terkirim") is False, str(r5))
cek("alasan exception tercatat", "RuntimeError" in str(r5.get("alasan", "")),
    str(r5))
N._kirim = _asli

print(f"\n== TOTAL: {LULUS} lulus, {GAGAL} gagal ==")
sys.exit(1 if GAGAL else 0)
