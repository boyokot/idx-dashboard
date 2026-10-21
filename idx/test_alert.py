#!/usr/bin/env python3
"""
Tes Kelompok D — backend: alert, skor parsial, median valuasi sektor.

Jalankan: python3 idx/test_alert.py
Exit 0 = semua lulus.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from idx import alert as AL          # noqa: E402
from idx import pipeline as PL       # noqa: E402
from idx import scoring as S         # noqa: E402

LULUT = 0
GAGAL = 0


def cek(nama, kondisi, detail=""):
    global LULUT, GAGAL
    if kondisi:
        LULUT += 1
        print(f"  OK   {nama}")
    else:
        GAGAL += 1
        if not isinstance(detail, str):
            detail = repr(detail)
        print(f"  FAIL {nama} {detail}")


def baris(**kw):
    r = {"kode": "XXXX", "nama": "Contoh", "sinyal": "Normal", "rsi": None,
         "chg_pct": None, "volume": None, "harga": None, "skor": None,
         "waspada": False, "flag_waspada": []}
    r.update(kw)
    return r


print("== 1. alert: aturan dasar ==")
a = AL.kumpulkan([baris(kode="AAAA", sinyal="Breakout", volume=5_000_000,
                        harga=1000, resistance=980, rsi=60, skor=70)])
cek("breakout -> 1 alert", a["total"] == 1, a)
cek("jenis breakout", "breakout" in a["per_jenis"], a["per_jenis"])

a = AL.kumpulkan([baris(kode="BBBB", sinyal="Tembus Support", harga=800,
                        support=900, rsi=40, skor=50, waspada=True)])
cek("tembus support -> kritis", a["per_severity"]["kritis"] == 1, a["per_severity"])
cek("tembus support = 1 alert", a["total"] == 1, a)

print("== 2. alert: nilai kosong TIDAK jadi tuduhan ==")
a = AL.kumpulkan([baris(kode="CCCC")])
cek("emiten tanpa data -> 0 alert", a["total"] == 0, a)
cek("tidak ada 'volume kecil' saat volume None",
    "volume_kecil" not in a["per_jenis"] and "likuiditas_tipis" not in a["per_jenis"],
    a["per_jenis"])
a = AL.kumpulkan([baris(kode="DDDD", volume=1000, harga=10)])
cek("volume 1000 + harga 10 -> likuiditas tipis (data ADA)",
    "likuiditas_tipis" in a["per_jenis"], a["per_jenis"])
a = AL.kumpulkan([baris(kode="EEEE", rsi=90, skor=50)])
cek("RSI 90 -> rsi_jenuh", "rsi_jenuh" in a["per_jenis"], a["per_jenis"])
a = AL.kumpulkan([baris(kode="FFFF", rsi=10, skor=50)])
cek("RSI 10 -> rsi_oversold", "rsi_oversold" in a["per_jenis"], a["per_jenis"])
a = AL.kumpulkan([baris(kode="GGGG", chg_pct=20.0, skor=50)])
cek("lonjakan 20% -> alert", "lonjakan_harga" in a["per_jenis"], a["per_jenis"])
a = AL.kumpulkan([baris(kode="HHHH", chg_pct=-20.0, skor=50)])
cek("lonjakan -20% -> alert", "lonjakan_harga" in a["per_jenis"], a["per_jenis"])
a = AL.kumpulkan([baris(kode="IIII", chg_pct=14.9, skor=50)])
cek("lonjakan 14.9% -> TIDAK alert", "lonjakan_harga" not in a["per_jenis"],
    a["per_jenis"])

print("== 3. alert: urutan & konsistensi ==")
rows = [baris(kode="ZZZ", sinyal="Tembus Support", harga=1, support=2, skor=10),
        baris(kode="AAA", sinyal="Breakout", volume=9_000_000, harga=1000,
              resistance=990, skor=70),
        baris(kode="MMM", sinyal="Normal", rsi=90, skor=50)]
a = AL.kumpulkan(rows)
kode_urut = [x["kode"] for x in a["daftar"]]
cek("kritis didahulukan", kode_urut[0] == "ZZZ", kode_urut)
cek("urutan stabil (deterministik)",
    kode_urut == [x["kode"] for x in AL.kumpulkan(rows)["daftar"]], kode_urut)
a2 = AL.kumpulkan(list(reversed(rows)))
cek("urutan tidak bergantung urutan input",
    [x["kode"] for x in a2["daftar"]] == kode_urut,
    [x["kode"] for x in a2["daftar"]])

print("== 4. alert: failures & truncasi ==")
# Baris harus BENAR-BENAR memicu alert, kalau tidak tidak ada yang dipotong.
banyak = [baris(kode=f"X{i:03d}", sinyal="Tembus Support", harga=100,
                support=200, skor=10) for i in range(300)]
a = AL.kumpulkan(banyak, maks=50)
cek("dipotong flagged", a["dipotong"] is True, a["dipotong"])
cek("daftar dipotong ke maks", len(a["daftar"]) == 50, len(a["daftar"]))
cek("total tetap hitung SEMUA", a["total"] >= 300, a["total"])

bad = [{"kode": "BOOM", "sinyal": "Breakout", "volume": "bukan-angka",
        "harga": None, "rsi": None, "chg_pct": None}]
try:
    a = AL.kumpulkan(bad)
    cek("baris rusak tidak melempar", True)
except Exception as e:
    cek("baris rusak tidak melempar", False, f"{type(e).__name__}: {e}")

print("== 5. skor parsial ==")
sp = AL.skor_parsial([
    baris(kode="P1", skor=80, skor_detail={"bobot_terisi": 1.0}),
    baris(kode="P2", skor=70, skor_detail={"bobot_terisi": 0.6}),
    baris(kode="P3", skor=60, skor_detail={"bobot_terisi": 0.8,
                                           "komponen": {"katalis": {"tersedia": False}}}),
    baris(kode="P4", skor=None),
])
cek("penuh = 1", sp["penuh"] == 1, sp)
cek("sebagian = 2", sp["sebagian"] == 2, sp)
cek("tanpa_skor = 1", sp["tanpa_skor"] == 1, sp)
cek("total = 4", sp["total"] == 4, sp)
cek("contoh bagianiah memuat kode", sp["contoh_sebagian"][0]["kode"] == "P2",
    sp["contoh_sebagian"])
cek("komponen hilang terdeteksi",
    sp["contoh_sebagian"][1]["komponen_hilang"] == ["katalis"],
    sp["contoh_sebagian"])
cek("ada catatan", bool(sp["catatan"]), sp)
sp0 = AL.skor_parsial([baris(kode="Z", skor=None)])
cek("0 Persen penuh = None, bukan 0", sp0["persen_penuh"] is None, sp0)

print("== 6. median valuasi sektor ==")
rows = [{"sektor": "BANK", "per": 10, "pbv": 1.0},
        {"sektor": "BANK", "per": 12, "pbv": 1.5},
        {"sektor": "BANK", "per": 14, "pbv": 2.0},
        {"sektor": "RITEL", "per": 20, "pbv": 3.0},
        {"sektor": "RITEL", "per": 22, "pbv": 3.5}]
mv = PL._median_valuasi_sektor(rows, *S.sector_medians(rows))
by = {x["sektor"]: x for x in mv["per_sektor"]}
cek("median PER BANK = 12 (bentuk flat!)", by["BANK"]["median_per"] == 12.0, by["BANK"])
cek("median PBV BANK = 1.5", by["BANK"]["median_pbv"] == 1.5, by["BANK"])
cek("sektor < 3 emiten TANPA median", by["RITEL"]["median_per"] is None, by["RITEL"])
cek("sektor tipis ditandai", by["RITEL"]["cukup_data"] is False, by["RITEL"])
cek("sektor tipis punya catatan", bool(by["RITEL"]["catatan"]), by["RITEL"])
cek("sektor_with_median dihitung", mv["sektor_dengan_median"] == 1, mv)

print("== 7. median: bentuk baris BERSARANG tetap jalan ==")
nested = [{"sektor": "BANK", "fundamental": {"per": 10, "pbv": 1.0}},
          {"sektor": "BANK", "fundamental": {"per": 12, "pbv": 1.5}},
          {"sektor": "BANK", "fundamental": {"per": 14, "pbv": 2.0}}]
pe, pb = S.sector_medians(nested)
cek("median dari bentuk bersarang", pe.get("BANK") == 12.0 and pb.get("BANK") == 1.5,
    (pe, pb))
pe0, pb0 = S.sector_medians([{"sektor": "X", "per": 1, "pbv": 1}])
cek("1 emiten -> TIDAK ada median (min 3)", pe0 == {} and pb0 == {}, (pe0, pb0))
pe2, pb2 = S.sector_medians([
    {"sektor": "BANK", "per": 10, "pbv": 1.0},
    {"sektor": "BANK", "per": 12, "pbv": 1.5},
    {"sektor": "BANK", "per": None, "pbv": 1.8}])
cek("nilai None diabaikan, 2 cukup? (min3 -> kosong)", pe2 == {}, pe2)
pe3, pb3 = S.sector_medians([
    {"sektor": "BANK", "per": 10, "pbv": 1.0},
    {"sektor": "BANK", "per": 12, "pbv": 1.5},
    {"sektor": "BANK", "per": 0, "pbv": 0}])
cek("PER 0 / PBV 0 diabaikan (bukan median valid)", pe3 == {} and pb3 == {}, (pe3, pb3))
pe4, _ = S.sector_medians([
    {"sektor": "BANK", "per": True, "pbv": 1.0},
    {"sektor": "BANK", "per": 12, "pbv": 1.5},
    {"sektor": "BANK", "per": 14, "pbv": 2.0}])
cek("bool True bukan PER", pe4 == {}, pe4)

print("== 8. pipeline: alert gagal TIDAK menggagalkan publish ==")
payload = PL._alerts_payload([baris(kode="OK", sinyal="Breakout",
                                     volume=5_000_000, harga=1000, skor=70)])
cek("alert tersedia", payload.get("tersedia") is True, payload)
cek("total > 0", payload["total"] >= 1, payload)
cek("punya per_severity lengkap",
    set(payload["per_severity"]) == {"kritis", "perhatian", "info"},
    payload["per_severity"])

print("== 9. latest.json punya field D ==")
import json
lj = "/root/idx-dashboard/data/latest.json"
if os.path.exists(lj):
    d = json.load(open(lj))
    if "alerts" in d:
        cek("field alerts ada", True)
        cek("field skor_parsial ada", "skor_parsial" in d, list(d.keys()))
        cek("field median_valuasi_sektor ada", "median_valuasi_sektor" in d,
            list(d.keys()))
        cek("alerts punya total", isinstance(d["alerts"].get("total"), int),
            d["alerts"].get("total"))
    else:
        print("  (lewati: latest.json belum di-regenerasi dengan field D)")
else:
    print("  (lewati: latest.json belum ada)")

print(f"\n== TOTAL: {LULUT} tes lulus, {GAGAL} gagal ==")
sys.exit(0 if GAGAL == 0 else 1)
