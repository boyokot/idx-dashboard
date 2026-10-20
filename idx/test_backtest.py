#!/usr/bin/env python3
"""
Tes Kelompok C: backtest vs IHSG.

Fokus pada hal yang mudah salah dan berbahaya kalau salah:
  * horizon = SESI BURSA (bukan hari kalender) -> harus melompati akhir pekan
  * tidak ada look-ahead -> label di titik i hanya dari bar <= i
  * IHSG dibandingkan pada TANGGAL yang sama, bukan offset bar
  * titik tanpa IHSG dibuang, TIDAK diisi nol
  * excess = return emiten - return IHSG (bukan sebaliknya)
  * sinyal hari ini dari pipeline == label_sinyal dari scoring
  * data kurang / kosong tidak crash

Jalankan: python3 idx/test_backtest.py
Exit 0 = semua lulus.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import backtest as BT       # noqa: E402
import scoring as S         # noqa: E402

GAGAL = []
LULUS = 0


def cek(nama, ok, detail=""):
    global LULUS
    if ok:
        LULUS += 1
        print(f"  OK   {nama}" + (f"  [{detail}]" if detail else ""))
    else:
        GAGAL.append(nama)
        print(f"  FAIL {nama}  [{detail}]")


def hari_bursa(mulai: dt.date, n: int):
    """n tanggal bursa berturut (Senin-Jumat). Untuk tes saja."""
    out, d = [], mulai
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d.isoformat())
        d += dt.timedelta(days=1)
    return out


# ══════════════════════════════════════════════════════════════════
print("== 1. helper statistik ==")
cek("_median ganjil", BT._median([1, 2, 3]) == 2)
cek("_median genap", BT._median([1, 2, 3, 4]) == 2.5)
cek("_median kosong = None", BT._median([]) is None)
cek("_pct q0 = min", BT._pct([5, 1, 3], 0.0) == 1)
cek("_pct q1 = max", BT._pct([5, 1, 3], 1.0) == 5)
cek("_pct kosong = None", BT._pct([], 0.5) is None)

print("== 2. pengelompokan skor ==")
cek("73.5 -> tinggi", BT._kelompok(73.5) == "tinggi")
cek("70.0 -> tinggi (batas bawah inklusif)", BT._kelompok(70.0) == "tinggi")
cek("69.9 -> sedang", BT._kelompok(69.9) == "sedang")
cek("50.0 -> sedang", BT._kelompok(50.0) == "sedang")
cek("49.9 -> rendah", BT._kelompok(49.9) == "rendah")
cek("0 -> rendah", BT._kelompok(0.0) == "rendah")
cek("None -> None", BT._kelompok(None) is None)

# ══════════════════════════════════════════════════════════════════
print("== 3. horizon = SESI BURSA, bukan hari kalender ==")
# 40 sesi bursa (8 minggu). Close naik +1% tiap sesi.
tgl = hari_bursa(dt.date(2026, 1, 5), 40)   # mulai Senin
closes = [100 * (1.01 ** i) for i in range(40)]
hist1 = {"emiten": {"TST": {"d": tgl, "c": closes, "v": [1000] * 40}}}
ihsg_closes = [5000 * (1.001 ** i) for i in range(40)]
ihsg1 = [{"date": t, "close": ihsg_closes[i]} for i, t in enumerate(tgl)]
h1 = BT.hitung(hist1, ihsg1, horizon=(5,))

# titik pertama yang dihitung = indeks MIN_SESI-1 .. len-hz_maks-1
i0 = BT.MIN_SESI - 1
r5 = (closes[i0 + 5] / closes[i0] - 1) * 100
ri5 = (ihsg_closes[i0 + 5] / ihsg_closes[i0] - 1) * 100
sisi = S.label_sinyal(S.score_technical(
    [{"date": tgl[i], "open": closes[i], "high": closes[i], "low": closes[i],
      "close": closes[i], "volume": 1000} for i in range(i0 + 1)])["indikator"])
st = h1["per_sinyal"].get(sisi, {}).get("5")
cek("ada hasil untuk label sinyal pertama", st is not None, sisi)
if st:
    cek("rata2 return = 5 SESI bursa (bukan 7 hari kalender)",
        abs(st["rata2"] - r5) < 0.01, f"{st['rata2']:.4f} vs {r5:.4f}")
    cek("IHSG pada tanggal yang sama",
        abs(st["ihsg_rata2"] - ri5) < 0.01, f"{st['ihsg_rata2']:.4f} vs {ri5:.4f}")
    cek("excess = emiten - IHSG", abs(st["excess"] - (r5 - ri5)) < 0.01,
        f"{st['excess']:.4f}")
    cek("excess positif (1%/sesi vs 0.1%/sesi)", st["excess"] > 0)

# Bukti bahwa 5 sesi != 7 hari kalender untuk titik tertentu:
j0, j5 = i0, i0 + 5
d0, d5 = dt.date.fromisoformat(tgl[j0]), dt.date.fromisoformat(tgl[j5])
selisih_kalender = (d5 - d0).days
cek("5 sesi bursa melewati >= 7 hari kalender (ada akhir pekan)",
    selisih_kalender >= 7, f"{tgl[j0]} -> {tgl[j5]} = {selisih_kalender} hari")

# ══════════════════════════════════════════════════════════════════
print("== 4. IHSG hilang -> titik DIBUANG, bukan diisi nol ==")
# hapus tanggal acuan (titik pertama) dari IHSG
tot_penuh = BT.hitung(hist1, ihsg1, horizon=(5,))["cakupan"]["titik_dihitung"]
tanpa = [b for b in ihsg1 if b["date"] != tgl[i0]]
h2 = BT.hitung(hist1, tanpa, horizon=(5,))
tot2 = h2["cakupan"]["titik_dihitung"]
cek("titik tanpa IHSG dihitung terpisah", h2["cakupan"]["titik_tanpa_ihsg"] >= 1,
    h2["cakupan"]["titik_tanpa_ihsg"])
cek("titik tanpa IHSG dibuang dari agregat (berkurang 1)",
    tot2 == tot_penuh - 1, f"{tot_penuh} -> {tot2}")
# yang penting: tidak ada return 0 palsu — agregat harus tetap dari titik yang ada
st2 = h2["per_sinyal"]
ada_nol = [st for per in st2.values() for st in per.values()
           if st.get("rata2") == 0 and st.get("n", 0) > 0 and st.get("excess") == 0]
cek("tidak ada baris return 0 palsu", not ada_nol, len(ada_nol))

# ══════════════════════════════════════════════════════════════════
print("== 5. tidak ada look-ahead ==")
# Label di titik i hanya boleh bergantung pada bar <= i. Uji langsung lewat
# iter_titik: dua seri yang SAMA sampai bar i tapi BEDA setelahnya harus
# menghasilkan label & skor yang identik di titik i.
dasar = [100.0] * 40
tgl_a = hari_bursa(dt.date(2026, 2, 2), 40)
naik = dasar[:30] + [500.0] * 10
turun = dasar[:30] + [50.0] * 10
ihsg_datar = {t: 5000.0 for t in tgl_a}
titik_naik = {t["i"]: t for t in BT.iter_titik(
    {"d": tgl_a, "c": naik, "v": [1000] * 40}, ihsg_datar, (5,))
    if t.get("alasan") is None}
titik_turun = {t["i"]: t for t in BT.iter_titik(
    {"d": tgl_a, "c": turun, "v": [1000] * 40}, ihsg_datar, (5,))
    if t.get("alasan") is None}
bersama = sorted(set(titik_naik) & set(titik_turun))
cek("ada titik evaluasi bersama", len(bersama) > 0, len(bersama))
# Dua seri identik s/d indeks 29, baru berbeda mulai indeks 30. Jadi titik
# dengan i <= 29 WAJIB punya label & skor identik (prefix-nya sama persis).
SEBELUM = 29
awal = [i for i in bersama if i <= SEBELUM]
beda_label = [i for i in awal
              if titik_naik[i]["sinyal"] != titik_turun[i]["sinyal"]]
beda_skor = [i for i in awal if titik_naik[i]["skor"] != titik_turun[i]["skor"]]
cek("ada titik sebelum divergensi", len(awal) >= 3, len(awal))
cek("label di titik SEBELUM divergensi TIDAK terpengaruh masa depan",
    not beda_label, f"{len(beda_label)} beda")
cek("skor di titik SEBELUM divergensi TIDAK terpengaruh masa depan",
    not beda_skor, f"{len(beda_skor)} beda")
# Return HARUS berbeda di titik yang jendela ke depannya melewati divergensi
# (i=25 -> j=30). Ini bukti masa depan dipakai untuk RETURN, bukan untuk label.
i_cek = [i for i in awal if i + 5 > SEBELUM]
cek("ada titik yang jendela return-nya melewati divergensi", bool(i_cek), i_cek)
if i_cek:
    i0r = i_cek[0]
    beda_return = titik_naik[i0r]["return"][5] != titik_turun[i0r]["return"][5]
    cek("return BERBEDA di titik itu (masa depan dipakai utk return)",
        beda_return,
        f"{titik_naik[i0r]['return'][5]} vs {titik_turun[i0r]['return'][5]}")
    cek("label tetap SAMA di titik itu (bukan look-ahead)",
        titik_naik[i0r]["sinyal"] == titik_turun[i0r]["sinyal"],
        titik_naik[i0r]["sinyal"])

# ══════════════════════════════════════════════════════════════════
print("== 6. label sinyal SATU definisi (pipeline == scoring) ==")
kasus = [
    ({"support_break": True, "breakout": True, "vol_surge": True, "rsi": 80},
     "Tembus Support"),
    ({"support_break": False, "breakout": True, "vol_surge": True, "rsi": 60},
     "Breakout"),
    ({"support_break": False, "breakout": True, "vol_surge": False, "rsi": 60},
     "Breakout (volume tipis)"),
    ({"support_break": False, "breakout": False, "vol_surge": False, "rsi": 80},
     "Waspada (RSI jenuh)"),
    ({"support_break": False, "breakout": False, "vol_surge": False, "rsi": 55},
     "Normal"),
    ({}, "Data tidak tersedia"),   # dict kosong = tidak ada indikator
]
for ind, harap in kasus:
    cek(f"label {harap}", S.label_sinyal(ind) == harap, S.label_sinyal(ind))
cek("label tanpa indikator = 'Data tidak tersedia'",
    S.label_sinyal(None) == "Data tidak tersedia", S.label_sinyal(None))
cek("indikator kosong {} -> 'Data tidak tersedia' (falsy, sama seperti no data)",
    S.label_sinyal({}) == "Data tidak tersedia", S.label_sinyal({}))

# ══════════════════════════════════════════════════════════════════
print("== 7. kasus batas (tidak boleh crash / tidak boleh menipu) ==")
cek("history kosong -> 0 titik",
    BT.hitung({"emiten": {}}, ihsg1)["cakupan"]["titik_dihitung"] == 0)
cek("seri terlalu pendek dilewati",
    BT.hitung({"emiten": {"X": {"d": tgl[:10], "c": closes[:10],
                                "v": [1] * 10}}}, ihsg1)
       ["cakupan"]["emiten_dipakai"] == 0)
cek("IHSG kosong -> 0 titik (bukan crash)",
    BT.hitung(hist1, [])["cakupan"]["titik_dihitung"] == 0)
cek("panjang d/c beda -> emiten dilewati",
    BT.hitung({"emiten": {"Y": {"d": tgl, "c": closes[:20], "v": [1] * 40}}}, ihsg1)
       ["cakupan"]["emiten_dipakai"] == 0)
# volume None harus tetap None di bars yang dibangun
h3 = BT.hitung({"emiten": {"Z": {"d": tgl, "c": closes, "v": [None] * 40}}},
               ihsg1, horizon=(5,))
cek("volume None tidak menggagalkan hitung",
    h3["cakupan"]["emiten_dipakai"] == 1, h3["cakupan"]["emiten_dipakai"])

# ══════════════════════════════════════════════════════════════════
print("== 8. file hasil nyata (kalau sudah ada) ==")
OUT = BT.OUT
if os.path.exists(OUT):
    b = json.load(open(OUT, encoding="utf-8"))
    cek("backtest.json ok=true", b.get("ok") is True)
    cek("ada catatan risiko", len(b.get("catatan_risiko") or []) >= 3,
        len(b.get("catatan_risiko") or []))
    cek("metode menyebut SESI BURSA", "SESI BURSA" in (b.get("metode") or "").upper())
    cek("horizon = [5,10,20]", b.get("horizon_sesi") == [5, 10, 20])
    cek("pembanding IHSG", "IHSG" in (b.get("pembanding") or ""))
    for k in ("per_sinyal", "per_skor"):
        ada_n = any(st.get("n") for per in (b.get(k) or {}).values()
                    for st in per.values())
        cek(f"{k} punya data", ada_n)
    # konsistensi: excess == rata2 - ihsg_rata2
    beda = []
    for per in list((b.get("per_sinyal") or {}).values()) + \
               list((b.get("per_skor") or {}).values()):
        for st in per.values():
            if st.get("excess") is not None and st.get("rata2") is not None:
                if abs(st["excess"] - (st["rata2"] - st["ihsg_rata2"])) > 0.02:
                    beda.append(st)
    cek("excess == rata2 - ihsg_rata2 di semua baris", not beda, len(beda))
    # menang harus 0..100
    luar = [st for per in (b.get("per_sinyal") or {}).values()
            for st in per.values()
            if st.get("menang") is not None and not (0 <= st["menang"] <= 100)]
    cek("persen menang dalam 0..100", not luar, len(luar))
    cek("ukuran file wajar (<1 MB)", os.path.getsize(OUT) < 1_000_000,
        f"{os.path.getsize(OUT)} byte")
else:
    print("  (backtest.json belum ada — lewati)")

print()
if GAGAL:
    print(f"!! {len(GAGAL)} GAGAL: {GAGAL}")
    sys.exit(1)
print(f"== TOTAL: {LULUS} tes lulus ==")
sys.exit(0)
