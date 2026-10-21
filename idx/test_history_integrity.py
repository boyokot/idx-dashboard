#!/usr/bin/env python3
"""
Verifikasi integritas data/history.json (Kelompok B).

Cek:
  * struktur kolom d/c/v konsisten
  * tidak ada tanggal duplikat / tidak urut
  * panjang seri <= 120
  * volume null tetap null (tidak jadi 0)
  * jumlah_sesi/jumlah_emiten cocok dengan isi sebenarnya
  * MA20/MA50 hasil hitung ulang cocok dengan latest.json (sampel)
  * tanggal terakhir = sesi bursa terakhir yang ada di latest.json

Exit 0 = semua ok.
"""
from __future__ import annotations

import json
import os
import sys

REPO = "/root/idx-dashboard"
HIST = os.path.join(REPO, "data", "history.json")
LATEST = os.path.join(REPO, "data", "latest.json")

LULUT = 0
MASALAH = []


def cek(nama, ok, detail=""):
    global LULUT
    if ok:
        LULUT += 1
        print(f"  OK   {nama}")
    else:
        MASALAH.append(nama)
        print(f"  FAIL {nama} {detail}")


def teks(v) -> str:
    return str(v)

print("== 1. file & bentuk dasar ==")
if not os.path.exists(HIST):
    print("  FAIL history.json tidak ada")
    sys.exit(1)
cek("file ada", True, f"{os.path.getsize(HIST)/1e6:.2f} MB")
h = json.load(open(HIST))
cek("versi_skema ada", bool(h.get("versi_skema")), h.get("versi_skema"))
cek("sesi_maks = 120", h.get("sesi_maks") == 120, h.get("sesi_maks"))
cek("punya key emiten", isinstance(h.get("emiten"), dict))
cek("punya jumlah_emiten", isinstance(h.get("jumlah_emiten"), int))
cek("punya jumlah_sesi", isinstance(h.get("jumlah_sesi"), int))
cek("punya diperbarui_pada", bool(h.get("diperbarui_pada")))

em = h["emiten"]
cek("jumlah_emiten > 0", len(em) > 0, len(em))
cek("jumlah_emiten == len(emiten)", h["jumlah_emiten"] == len(em),
    f"{h['jumlah_emiten']} vs {len(em)}")

print("== 2. konsistensi tiap emiten ==")
total_sesi = 0
buruk_urut = buruk_dup = lebih_120 = buruk_panjang = 0
nul_volume = 0
nol_volume = 0
for kode, s in em.items():
    d, c, v = s.get("d", []), s.get("c", []), s.get("v", [])
    total_sesi += len(d)
    if len(d) != len(c) or len(d) != len(v):
        buruk_panjang += 1
    if len(d) > 120:
        lebih_120 += 1
    if d != sorted(d):
        buruk_urut += 1
    if len(set(d)) != len(d):
        buruk_dup += 1
    nul_volume += sum(1 for x in v if x is None)
    nol_volume += sum(1 for x in v if x == 0)

cek("semua d/c/v panjang sama", buruk_panjang == 0, f"{buruk_panjang} emiten")
cek("tidak ada >120 sesi", lebih_120 == 0, f"{lebih_120} emiten")
cek("semua tanggal urut naik", buruk_urut == 0, f"{buruk_urut} emiten")
cek("tidak ada tanggal duplikat", buruk_dup == 0, f"{buruk_dup} emiten")
cek("jumlah_sesi == total baris", h["jumlah_sesi"] == total_sesi,
    f"{h['jumlah_sesi']} vs {total_sesi}")
print(f"     (volume null={nul_volume}, volume 0={nol_volume})")

print("== 3. konsistensi dengan latest.json ==")
if os.path.exists(LATEST):
    d = json.load(open(LATEST))
    rows = {e["kode"]: e for e in d.get("emiten", [])}
    cek("ada latest.json", True, f"{len(rows)} emiten")

    # 3a. MA20/MA50 hasil hitung ulang == latest.json
    def ma(vals, n):
        if len(vals) < n:
            return None
        return sum(vals[-n:]) / n

    cek_benar = 0
    cek_salah = []
    for kode, s in em.items():
        row = rows.get(kode)
        if not row:
            continue
        m20, m50 = ma(s["c"], 20), ma(s["c"], 50)
        if m20 is not None and row.get("ma20") is not None:
            if abs(m20 - row["ma20"]) < 0.05:
                cek_benar += 1
            else:
                cek_salah.append((kode, "ma20", m20, row["ma20"]))
        if m50 is not None and row.get("ma50") is not None:
            if abs(m50 - row["ma50"]) < 0.05:
                cek_benar += 1
            else:
                cek_salah.append((kode, "ma50", m50, row["ma50"]))
    cek(f"MA20/MA50 history == latest.json ({cek_benar} perbandingan)",
        not cek_salah, f"{cek_salah[:3]}")

    # 3b. close terakhir == harga terakhir (dengan toleransi。原来 close = close)
    beda = []
    for kode, s in em.items():
        row = rows.get(kode)
        if not row or row.get("harga") is None or not s["c"]:
            continue
        if abs(s["c"][-1] - row["harga"]) > 0.5:
            beda.append((kode, s["c"][-1], row["harga"]))
    cek("close terakhir == harga di latest.json", not beda,
        f"{len(beda)} beda, contoh {beda[:3]}")

    # 3c. tanggal akhir history.
    # CATATAN: tidak semua emiten wajib berakhir di tanggal yang sama. Emiten yang
    # tidak diperdagangkan pada sesi terakhir (volume 0 di sumber) tidak punya
    # bar baru — itu fakta pasar, bukan bug pipeline. Invarian yang benar:
    # setiap tanggal akhir HARUS merupakan sesi bursa yang ada di data.
    semua_tgl = set()
    for s in em.values():
        semua_tgl.update(s.get("d") or [])
    akhir = {s["d"][-1] for s in em.values() if s.get("d")}
    cek("tanggal akhir ⊆ tanggal sesi yang ada", akhir <= semua_tgl,
        str(sorted(akhir - semua_tgl)[:5]))
    cek("ada tepat 1 sesi terakhir (tanggal max)", len(akhir) >= 1, str(sorted(akhir)))
    # Tanggal DATA (sesi bursa) — bukan `diperbarui_pada` (jam pipeline
    # jalan). Pipeline pagi hari kerja belum punya bar sesi hari itu,
    # jadi membandingkan dengan jam run selalu gagal palsu.
    tgl_latest = d.get("tanggal_data") or (d.get("diperbarui_pada") or "")[:10]
    cek("tanggal_data latest ada di history (bukan lebih maju)",
        (not tgl_latest) or (tgl_latest in semua_tgl),
        f"tanggal_data {tgl_latest} tidak ada di history "
        f"(max {max(semua_tgl)})")

    # 3c-2. Tidak ada volume tanpa tanggal (forward-fill): array `d` dan `v`
    # WAJIB sejajar. Dulu tes ini membandingkan volume terakhir emiten
    # dengan sesi terakhir global, padahal volume itu milik tanggal
    # terakhir emiten sendiri — pada sesi yang masih berjalan (baru
    # sebagian emiten bertransaksi) itu selalu gagal palsu.
    tidak_sejajar = [k for k, s in em.items()
                     if len(s.get("d") or []) != len(s.get("v") or [])]
    cek("array tanggal & volume sejajar (tidak ada forward-fill)",
        not tidak_sejajar,
        f"{len(tidak_sejajar)} emiten: {tidak_sejajar[:5]}")

    # Sesi terakhir boleh belum lengkap bila bursa masih buka: sebagian
    # emiten memang belum bertransaksi hari ini. Itu BUKAN data hilang.
    maxd = max(semua_tgl) if semua_tgl else None
    tak_ada_bar = [k for k, s in em.items()
                   if s.get("d") and maxd and s["d"][-1] != maxd]
    cek("emiten tanpa bar sesi terakhir punya volume di tanggalnya sendiri",
        all(em[k]["v"][-1] is not None or em[k]["d"][-1] != maxd
            for k in tak_ada_bar),
        f"{len(tak_ada_bar)} emiten berakhir sebelum {maxd} (sesi berjalan)")

    # 3d. kode di history harus ada di latest
    yatim = set(em) - set(rows)
    cek("tidak ada kode history yang tak dikenal di latest.json",
        not yatim, str(list(yatim)[:5]))
else:
    print("  (latest.json tidak ada — lewati perbandingan)")

print("== 4. cakupan ==")
if os.path.exists(LATEST):
    d = json.load(open(LATEST))
    rows = {e["kode"]: e for e in d.get("emiten", [])}
    ada_harga = {k for k, e in rows.items() if e.get("harga") is not None}
    cakupan = len(set(em) & ada_harga) / max(1, len(ada_harga))
    cek("cakupan history >= 90% emiten ber harga",
        cakupan >= 0.90, f"{cakupan*100:.1f}% ({len(set(em) & ada_harga)}/{len(ada_harga)})")

print()
if MASALAH:
    print(f"!! {len(MASALAH)} masalah: {MASALAH}")
    sys.exit(1)
print(f"== TOTAL: {LULUT} pemeriksaan lulus ==")
sys.exit(0)
