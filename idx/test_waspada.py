#!/usr/bin/env python3
"""Tes ringkasan waspada + aturan penyempitan yang diterapkan 30 Okt 2026."""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from idx import waspada as W

LULUS = 0
GAGAL = 0


def cek(nama: str, kondisi: bool, detail: str = "") -> None:
    global LULUS, GAGAL
    if kondisi:
        LULUS += 1
        print(f"  OK  {nama}")
    else:
        GAGAL += 1
        print(f"  GAGAL {nama} :: {detail}")


rows = [
    {"kode": "A", "skor": 70, "flag_waspada": ["ROE negatif"]},
    {"kode": "B", "skor": 60, "flag_waspada": ["penny stock", "volume sangat kecil (<100rb)"]},
    {"kode": "C", "skor": 50, "flag_waspada": ["tembus support 20-hari", "volume sangat kecil (<100rb)"]},
    {"kode": "D", "skor": 40, "flag_waspada": ["volume sangat kecil (<100rb)"]},
    {"kode": "E", "skor": None, "flag_waspada": ["ROE negatif"]},
    {"kode": "F", "skor": 80, "flag_waspada": ["volume tidak tersedia"]},
    {"kode": "G", "skor": 90, "flag_waspada": []},
]
r = W.ringkas(rows)

print("== 1. aturan tinggi ==")
cek("ROE negatif sendiri = tinggi", W.tingkat(["ROE negatif"]) == W.TINGGI)
cek("penny sendiri = ringan", W.tingkat(["penny stock"]) == W.RINGAN)
cek("volume kecil sendiri = ringan",
    W.tingkat(["volume sangat kecil (<100rb)"]) == W.RINGAN)
cek("tembus support sendiri = ringan", W.tingkat(["tembus support"]) == W.RINGAN)
cek("lonjakan sendiri = ringan", W.tingkat(["lonjakan +17% tanpa berita"]) == W.RINGAN)
cek("penny + volume = tinggi",
    W.tingkat(["penny stock", "volume sangat kecil (<100rb)"]) == W.TINGGI)
cek("tembus + volume = tinggi",
    W.tingkat(["tembus support", "volume sangat kecil (<100rb)"]) == W.TINGGI)

print("== 2. waspada tinggi ==")
hi = r.get("tinggi_ringkasan") or {}
cek("jumlah tinggi = 4", hi.get("jumlah") == 4, str(hi.get("jumlah")))
cek("persen universe = 57.1%", hi.get("persen_universe") == 57.1, str(hi.get("persen_universe")))
utama = hi.get("per_kriteria_utama") or {}
cek("kriteria utama roe negatif", utama.get("roe negatif") == 2, str(utama))
cek("kriteria utama volume sangat kecil", utama.get("volume sangat kecil") == 2, str(utama))
cek("kombinasi tersedia", isinstance(hi.get("kombinasi"), list) and hi["kombinasi"])
cek("distribusi kriteria", hi.get("jumlah_kriteria_per_emiten") == {"1": 2, "2": 2},
    str(hi.get("jumlah_kriteria_per_emiten")))
cek("baseline sebelum penyempitan = 5",
    (hi.get("sebelum_penyempitan") or {}).get("jumlah") == 5,
    str(hi.get("sebelum_penyempitan")))

print("== 3. status penyempitan ==")
u = r.get("usulan_penyempitan") or {}
cek("status diterapkan", "DITERAPKAN" in (u.get("status") or ""), str(u.get("status")))
cek("target 15-20%", u.get("target_persen_universe") == [15, 20], str(u.get("target_persen_universe")))
# A, B, C, E memenuhi aturan; D (volume saja) turun ke ringan.
cek("perkiraan kandidat = 4", u.get("perkiraan_jumlah") == 4, str(u.get("perkiraan_jumlah")))
cek("persen kandidat 57.1", u.get("perkiraan_persen_universe") == 57.1, str(u.get("perkiraan_persen_universe")))
cek("jumlah sebelum = 5", u.get("jumlah_sebelum") == 5, str(u.get("jumlah_sebelum")))
cek("jumlah sesudah = 4", u.get("jumlah_sesudah") == 4, str(u.get("jumlah_sesudah")))
cek("dampak menyebut 1 turun", "1 emiten" in (u.get("dampak") or ""), str(u.get("dampak")))

print("== 4. field lama tetap ==")
cek("per_tingkat baru", r.get("per_tingkat") == {"tinggi": 4, "ringan": 2},
    str(r.get("per_tingkat")))
cek("per_kriteria tetap", "volume tidak tersedia" in (r.get("per_kriteria") or {}))
pk = (r.get("per_kriteria") or {}).get("volume sangat kecil") or {}
cek("volume kecil tampil ringan + catatan kombinasi",
    pk.get("tingkat") == "ringan" and pk.get("bisa_tinggi_dalam_kombinasi") is True,
    str(pk))
cek("total waspada = 6", r.get("total_waspada") == 6, str(r.get("total_waspada")))

print(f"\nHASIL: {LULUS} lulus, {GAGAL} gagal")
if GAGAL:
    sys.exit(1)
