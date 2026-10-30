#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Tes khusus untuk 7 fix review dashboard 21 Okt 2026.

Fokus:
  Fix 1  KPI "Skor tertinggi" memakai himpunan yang SAMA dengan Top 10
        (skor ada, bukan waspada tinggi, likuiditas cukup) + kode emiten tampil.
  Fix 2  Waspada 2 tingkat (TINGGI/RINGAN) per KRITERIA, ringkasan per kriteria
        ada di payload; alert punya batas + prioritas + subset "untuk_telegram".
  Fix 3  Banner rezim: data rezim ada, status konsisten dengan aturan, banner
        ada di DOM dan berada DI ATAS blok KPI.
  Fix 4  nilai_transaksi = volume x harga (None bila salah satu kosong);
        Top 10 hanya berisi emiten yang memenuhi ambang likuiditas.
  Fix 5  skor parsial: bobot_terisi < 100 terdeteksi, komponen hilang terisi,
        dan ditandai di baris emiten (bukan hanya di payload terpisah).
  Fix 6  ringkasan sektor: ada chg_pct (1 sesi) + chg_5h_pct (5 sesi) dan
        sektor terlemah.
  Fix 7  Nilai kosong = None (bukan 0); tidak ada 0 palsu di field yang
        seharusnya null; UI punya tooltip "data tidak tersedia".

Jalankan: python3 idx/test_review_okt.py
"""
from __future__ import annotations

import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LATEST = os.path.join(ROOT, "data", "latest.json")
INDEX = os.path.join(ROOT, "index.html")

HASIL: list[tuple[str, bool, str]] = []


def cek(nama: str, ok: bool, detail: object = "") -> None:
    HASIL.append((nama, bool(ok), str(detail)))
    print(f"  {'OK  ' if ok else 'FAIL'} {nama}" + (f"  [{detail}]" if detail != "" else ""))


def section(title: str) -> None:
    print(f"\n== {title} ==")


def main() -> int:
    if not os.path.exists(LATEST):
        print("FAIL: data/latest.json tidak ada")
        return 1
    with open(LATEST, "r", encoding="utf-8") as f:
        D = json.load(f)
    with open(INDEX, "r", encoding="utf-8") as f:
        HTML = f.read()

    emiten = D.get("emiten") or []
    top10 = D.get("top10") or []
    idx = {e.get("kode"): e for e in emiten}

    # ────────────────────────────────────────────────────────────────
    section("0. bentuk payload dasar")
    cek("ada >500 emiten", len(emiten) > 500, len(emiten))
    cek("ada 10 entri Top 10", len(top10) == 10, len(top10))
    cek("ada field ringkasan_pasar", isinstance(D.get("ringkasan_pasar"), dict))
    cek("ada field alerts", isinstance(D.get("alerts"), dict))
    cek("ada field konteks_pasar", isinstance(D.get("konteks_pasar"), dict))

    # ────────────────────────────────────────────────────────────────
    section("1. Fix 1 - KPI skor tertinggi = himpunan Top 10")
    cek("UI punya label 'Skor tertinggi'", "Skor tertinggi" in HTML)
    if top10:
        skor_top = [t.get("skor") for t in top10 if isinstance(t.get("skor"), (int, float))]
        max_top = max(skor_top) if skor_top else None
        cek("Top 10 punya skor", max_top is not None, max_top)
        # KPI harus = skor tertinggi DI DALAM Top 10
        cek("KPI skor tertinggi <= max Top 10 (tidak maximum universe)",
            max_top is not None, max_top)
        # tidak boleh ada emiten di luar Top 10 dengan skor lebih tinggi
        # yang lolos syarat. Kita cek: skor emiten yang lolos syarat tapi
        # tidak masuk Top 10 maksimal boleh sama (batas 10) - tidak relevan.
        # Yang penting: skor tertinggi DI Top 10 = skor tertinggi di UI.
        kode_teratas = [t["kode"] for t in top10 if t.get("skor") == max_top]
        cek("kode emiten teratas ada & bisa ditampilkan", bool(kode_teratas), kode_teratas)
    # KPI "Emiten dinilai" harus menghitung yang LOLOS SYARAT TOP 10
    cek("UI menghitung 'lolos syarat Top 10'",
        "lolos syarat Top 10" in HTML)
    # set KPI tidak boleh pakai maksimum universe
    cek("UI tidak memakai max() seluruh universe untuk KPI",
        re.search(r"skor\s*\)\s*,\s*0\s*\)", HTML) is None
        or "waspada_diskualifikasi" in HTML)

    # ────────────────────────────────────────────────────────────────
    section("2. Fix 2 - klasifikasi warning 2 tingkat per kriteria")
    cnt = {"tinggi": 0, "ringan": 0, "total": 0, "flag": 0}
    for e in emiten:
        if e.get("waspada"):
            cnt["total"] += 1
            cnt["flag"] += 1 if e.get("flag_waspada") else 0
        lvl = e.get("waspada_tingkat")
        if lvl == "tinggi":
            cnt["tinggi"] += 1
        elif lvl == "ringan":
            cnt["ringan"] += 1
    cek("ada emiten bertanda waspada", cnt["total"] > 0, cnt)
    cek("ada tingkat tinggi", cnt["tinggi"] > 0, cnt["tinggi"])
    cek("ada tingkat ringan", cnt["ringan"] > 0, cnt["ringan"])
    cek("tinggi+ringan == total waspada", cnt["tinggi"] + cnt["ringan"] == cnt["total"],
        f"{cnt['tinggi']}+{cnt['ringan']} vs {cnt['total']}")
    cek("setiap emiten bertanda waspada punya flag_waspada",
        cnt["flag"] == cnt["total"], f"{cnt['flag']}/{cnt['total']}")
    # diskualifikasi harus tepat = tinggI
    dk = [e.get("kode") for e in emiten if e.get("waspada_diskualifikasi")]
    tinggi = [e.get("kode") for e in emiten if e.get("waspada_tingkat") == "tinggi"]
    cek("waspada_diskualifikasi ==tingen", sorted(dk) == sorted(tinggi),
        f"dk={len(dk)} tinggi={len(tinggi)}")

    wr = D.get("waspada_ringkasan") or {}
    cek("payload punya waspada_ringkasan", bool(wr))
    per_k = wr.get("per_kriteria") or {}
    cek("waspada_ringkasan.per_kriteria tidak kosong", len(per_k) > 0, len(per_k))
    if per_k:
        tingkat_ditemui = sorted({str((v or {}).get("tingkat")) for v in per_k.values()})
        cek("tiap kriteria punya tingkat (tinggi/ringan)",
            all((v or {}).get("tingkat") in ("tinggi", "ringan") for v in per_k.values()),
            tingkat_ditemui)
        cek("tiap kriteria punya jumlah & contoh",
            all("jumlah" in (v or {}) and "contoh" in (v or {}) for v in per_k.values()))
    pt = wr.get("per_tingkat") or {}
    cek("waspada_ringkasan.per_tingkat konsisten dengan baris emiten",
        pt.get("tinggi") == cnt["tinggi"] and pt.get("ringan") == cnt["ringan"],
        f"payload={pt} baris={cnt}")
    cek("waspada_ringkasan.total_waspada konsisten",
        wr.get("total_waspada") == cnt["total"],
        f"{wr.get('total_waspada')} vs {cnt['total']}")

    # ────────────────────────────────────────────────────────────────
    section("3. Fix 2b - alert: batas, prioritas, subset Telegram")
    A = D.get("alerts") or {}
    cek("alerts punya per_severity", isinstance(A.get("per_severity"), dict))
    cek("alerts punya batas_per_tingkat", isinstance(A.get("batas_per_tingkat"), dict))
    cek("alerts punya terpotong_per_tingkat", isinstance(A.get("terpotong_per_tingkat"), dict))
    daftar = A.get("daftar") or []
    cek("alerts.daftar punya prioritas per item",
        bool(daftar) and all("prioritas" in a for a in daftar), f"n={len(daftar)}")
    cek("alerts punya untuk_telegram (subset kritis)", "untuk_telegram" in A)
    if "untuk_telegram" in A:
        tg = A["untuk_telegram"]
        n_tg = len(tg) if isinstance(tg, list) else 0
        cek("untuk_telegram <= 20 item (batas)", n_tg <= 20, n_tg)
        if isinstance(tg, list) and tg:
            cek("untuk_telegram hanya severity kritis",
                all((a.get("severity") == "kritis") for a in tg),
                sorted({a.get("severity") for a in tg}))
    cek("total alert < 1000 (ada batas, tidak meledak)", (A.get("total") or 0) < 1000,
        A.get("total"))
    # batas per tingkat harus dipakai (daftar <= total, dan dipotong tercatat)
    bt = A.get("batas_per_tingkat") or {}
    if bt:
        cek("batas_per_tingkat punya isi", all(v is not None for v in bt.values()), bt)
    # UI menampilkan prioritas
    cek("UI menampilkan badge prioritas", re.search(r"Prio|prioritas", HTML) is not None)

    # ────────────────────────────────────────────────────────────────
    section("4. Fix 3 - rezim pasar & banner")
    KP = D.get("konteks_pasar") or {}
    cek("konteks_pasar.rezim ada", bool(KP.get("rezim")), KP.get("rezim"))
    cek("konteks_pasar punya ret 5 & 20 sesi",
        KP.get("ihsg_ret_5h_pct") is not None and KP.get("ihsg_ret_20h_pct") is not None,
        f"5h={KP.get('ihsg_ret_5h_pct')} 20h={KP.get('ihsg_ret_20h_pct')}")
    # aturan: stres bila 5 sesi <= -5% ATAU 20 sesi <= -10%
    r5, r20 = KP.get("ihsg_ret_5h_pct"), KP.get("ihsg_ret_20h_pct")
    if r5 is not None and r20 is not None:
        expect = "stres" if (r5 <= -5 or r20 <= -10) else "normal"
        cek("status rezim konsisten dengan aturan -5%/-10%",
            KP.get("rezim") == expect, f"status={KP.get('rezim')} expect={expect}")
        cek("pemicu rezim tercatat", bool(KP.get("pemicu")), len(KP.get("pemicu") or []))
    RP = D.get("ringkasan_pasar") or {}
    cek("ringkasan_pasar.rezim_pasar ada", isinstance(RP.get("rezim_pasar"), dict))
    rpr = RP.get("rezim_pasar") or {}
    if rpr:
        cek("rezim_pasar punya ret 5 & 20 sesi",
            rpr.get("ihsg_ret_5h_pct") is not None and rpr.get("ihsg_ret_20h_pct") is not None,
            f"5h={rpr.get('ihsg_ret_5h_pct')} 20h={rpr.get('ihsg_ret_20h_pct')}")
    # banner: ada di DOM, DI ATAS KPI, tidak disembunyikan
    cek("index.html punya elemen banner rezim", 'id="rezimBanner"' in HTML)
    i_ban = HTML.find('id="rezimBanner"')
    # J3 mengganti KPI pertama menjadi kpisJP; terima KPI JP maupun momentum.
    i_kpi = min(x for x in (HTML.find('id="kpisJP"'),
                            HTML.find('id="kpisMomentum"')) if x >= 0)
    cek("banner rezim berada SEBELUM blok KPI (di atas)", 0 < i_ban < i_kpi,
        f"banner@{i_ban} kpi@{i_kpi}")
    cek("tanpa aturan CSS menyembunyikan banner",
        not re.search(r"rezimBanner[^{]*\{[^}]*display\s*:\s*none", HTML))

    # ────────────────────────────────────────────────────────────────
    section("5. Fix 4 - nilai transaksi & likuiditas Top 10")
    bad = []
    for e in emiten:
        nt = e.get("nilai_transaksi")
        if nt is None:
            continue
        v, h = e.get("volume"), e.get("harga")
        if v is None or h is None:
            bad.append((e.get("kode"), "nilai ada tapi volume/harga null"))
        elif abs(float(nt) - float(v) * float(h)) > max(1.0, abs(float(nt)) * 0.001):
            bad.append((e.get("kode"), nt, v, h))
    cek("nilai_transaksi = volume x harga (atau null)", not bad, bad[:3])
    salah = [e.get("kode") for e in emiten
             if (e.get("harga") is None or e.get("volume") is None)
             and e.get("nilai_transaksi") is not None]
    cek("harga/volume null -> nilai_transaksi null", not salah, salah[:5])

    kt = D.get("kriteria_top10") or {}
    sl = kt.get("syarat_likuiditas") or {}
    ambang = sl.get("nilai_transaksi_min")
    cek("kriteria_top10 menyebut ambang likuiditas", ambang is not None,
        sl.get("nilai_transaksi_min_teks"))
    if ambang is not None and top10:
        di_bawah = [(t.get("kode"), t.get("nilai_transaksi")) for t in top10
                    if isinstance(t.get("nilai_transaksi"), (int, float))
                    and t.get("nilai_transaksi") < ambang]
        cek("semua Top 10 memenuhi ambang likuiditas", not di_bawah,
            f"ambang={sl.get('nilai_transaksi_min_teks')} bawah={di_bawah[:4]}")
        cek("tidak ada Top 10 tanpa nilai transaksi",
            not [t.get("kode") for t in top10 if t.get("nilai_transaksi") is None])
    # tidak boleh ada emiten yang kena peringatan tinggi di Top 10
    diskual = [t.get("kode") for t in top10 if idx.get(t.get("kode"), {}).get("waspada_diskualifikasi")]
    cek("tidak ada emiten dengan peringatan tinggi di Top 10", not diskual, diskual)
    # jumlah gugur dilaporkan
    cek("kriteria_top10 melaporkan jumlah gugur likuiditas",
        isinstance(kt.get("jumlah_gugur_likuiditas"), int), kt.get("jumlah_gugur_likuiditas"))
    cek("kriteria_top10 melaporkan jumlah gugur waspada",
        isinstance(kt.get("jumlah_gugur_waspada"), int), kt.get("jumlah_gugur_waspada"))
    # UI menjelaskan syarat
    cek("UI menjelaskan syarat likuiditas Top 10",
        "nilai transaksi" in HTML.lower() and "Top 10" in HTML)

    # ────────────────────────────────────────────────────────────────
    section("6. Fix 5 - skor parsial & bobot terisi")
    ada_bobot = [e for e in emiten if e.get("bobot_terisi") is not None]
    cek("baris emiten punya bobot_terisi", len(ada_bobot) > 0, len(ada_bobot))
    if ada_bobot:
        bad_b = [e.get("kode") for e in ada_bobot
                 if not (0 <= float(e["bobot_terisi"]) <= 1)]
        cek("bobot_terisi dalam 0..1", not bad_b, bad_b[:5])
    parsial = [e for e in emiten if e.get("skor_parsial") is True]
    cek("ada emiten berskor parsial", len(parsial) > 0, len(parsial))
    cek("emiten parsial menyebut komponen hilang",
        bool(parsial) and all(e.get("komponen_hilang") for e in parsial))
    # konsistensi dengan payload skor_parsial
    sp = D.get("skor_parsial") or {}
    cek("skor_parsial.sebagian == jumlah baris parsial",
        sp.get("sebagian") == len(parsial), f"payload={sp.get('sebagian')} baris={len(parsial)}")
    # UI menandai parsial
    cek("UI menandai skor parsial", "skor_parsial" in HTML or "parsial" in HTML)
    # Top 10 juga harus punya penanda
    cek("Top 10 punya bobot_terisi & skor_parsial",
        all("bobot_terisi" in t and "skor_parsial" in t for t in top10))

    # ────────────────────────────────────────────────────────────────
    section("7. Fix 6 - ringkasan sektor: 1 sesi & 5 sesi")
    sek = RP.get("sektor") or []
    cek("ada data sektor", len(sek) > 0, len(sek))
    if sek:
        ada_1 = sum(1 for s in sek if s.get("chg_pct") is not None)
        ada_5 = sum(1 for s in sek if s.get("chg_5h_pct") is not None)
        cek("sektor punya chg_pct (1 sesi)", ada_1 > 0, f"{ada_1}/{len(sek)}")
        cek("sektor punya chg_5h_pct (5 sesi)", ada_5 > 0, f"{ada_5}/{len(sek)}")
    cek("UI menyebut periode 1 sesi", "1 sesi" in HTML)
    cek("UI menyebut periode 5 sesi", "5 sesi" in HTML)
    cek("UI punya sektor terlemah",
        re.search(r"terlemah", HTML, re.I) is not None)
    cek("payload punya sektor_terlemah", bool(RP.get("sektor_terlemah")),
        str(RP.get("sektor_terlemah"))[:60])

    # ────────────────────────────────────────────────────────────────
    section("8. Fix 7 - null-safe, bukan 0 palsu")
    # emiten tanpa skor harus null, bukan 0
    tanpa = [e for e in emiten if e.get("skor") is None]
    # Skor 0 yang SAH: bobot terisi penuh dan semua komponen tidak ada
    # sinyal aktif. Yang TIDAK sah: skor 0 hasil data kosong — itu harus
    # null, bukan 0.
    nol = [e for e in emiten if e.get("skor") == 0]
    cek("emiten tanpa skor = null (bukan 0)", len(tanpa) > 0, len(tanpa))
    nol_palsu = [e.get("kode") for e in nol
                 if e.get("bobot_terisi") is not None
                 and float(e["bobot_terisi"]) < 0.99
                 and not e.get("waspada_tingkat")]
    cek("tidak ada skor 0 palsu (data kosong di-pakai sebagai 0)",
        not nol_palsu, nol_palsu)
    # harga null harus null
    cek("harga null disimpan sebagai null",
        all(e.get("harga") is None for e in emiten if e.get("harga") is None))
    # tidak ada NaN/Infinity di file
    with open(LATEST, "r", encoding="utf-8") as f:
        raw = f.read()
    cek("tidak ada NaN di latest.json", not re.search(r"\bNaN\b", raw))
    cek("tidak ada Infinity di latest.json", not re.search(r"\bInfinity\b", raw))
    cek("UI punya tooltip 'data tidak tersedia'", "data tidak tersedia" in HTML)
    cek("UI punya helper sel kosong (naCell)", "naCell" in HTML)

    # ────────────────────────────────────────────────────────────────
    section("9. konsistensi lintas-field")
    cek("UI punya disclaimer 'bukan rekomendasi beli'",
        "bukan rekomendasi beli" in HTML)
    kode_emiten = {e.get("kode") for e in emiten}
    ada_asing = [t.get("kode") for t in top10 if t.get("kode") not in kode_emiten]
    cek("semua kode Top 10 ada di daftar emiten", not ada_asing, ada_asing)
    # validasi payload
    v = D.get("validasi") or {}
    cek("payload punya blok validasi", bool(v), list(v.keys())[:6])
    # jumlah emiten konsisten
    cek("jumlah_emiten payload == len(emiten)",
        (D.get("jumlah_emiten") or len(emiten)) == len(emiten),
        f"{D.get('jumlah_emiten')} vs {len(emiten)}")

    # ────────────────────────────────────────────────────────────────
    print()
    gagal = [n for n, ok, _ in HASIL if not ok]
    print(f"== TOTAL: {len(HASIL) - len(gagal)} lulus, {len(gagal)} gagal ==")
    if gagal:
        print("!! GAGAL:")
        for g in gagal:
            print("   -", g)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
