#!/usr/bin/env python3
"""Tes offline J2 — Skor Investasi Jangka Panjang.

Tidak memakai jaringan. Jalankan dari /root/idx-dashboard:
    python3 idx/test_skor_jp.py
"""
from __future__ import annotations

import json
import os
import sys
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from idx import skor_jp as S  # noqa: E402

CFG = json.load(open(os.path.join(S._ROOT, "config", "bobot_jp.json")))

lulus = 0
gagal = 0


def cek(nama: str, kondisi: bool, detail: str = "") -> None:
    global lulus, gagal
    if kondisi:
        lulus += 1
        print(f"  OK  {nama}")
    else:
        gagal += 1
        print(f"  GAGAL {nama} {detail}")


def _fund(kode: str, i: int, sektor: str = "SEKTOR A") -> dict:
    th = [2025, 2024, 2023, 2022]
    return {
        "kode": kode, "sektor": sektor, "tahun_keuangan": th,
        "periode_laporan": {str(y): f"{y}-12-31" for y in th},
        "data_mentah": {
            "laba_bersih": {str(y): 100e9 + i * 1e9 for y in th},
            "pendapatan": {str(y): 1000e9 + i * 10e9 for y in th},
            "ekuitas": {str(y): 500e9 for y in th},
            "total_utang": {str(y): 200e9 for y in th},
            "arus_kas_operasi": {str(y): 110e9 for y in th},
            "beban_bunga": {str(y): 20e9 for y in th},
            "eps": {str(y): 100.0 for y in th},
            "jumlah_saham": {str(y): 1e9 for y in th},
            "dividen_ttm": 40.0,
        },
        "metrik": {
            "profitabilitas": {
                "roe_rata2_3th": 0.12 + i * 0.01,
                "margin_laba_rata2_3th": 0.10 + i * 0.005,
            },
            "pertumbuhan": {
                "cagr_laba_3th": 0.05 + i * 0.01,
                "cagr_pendapatan_3th": 0.04 + i * 0.01,
            },
            "kesehatan_keuangan": {
                "der_rata2_3th": 0.4, "interest_coverage_rata2_3th": 5.0,
                "ocf_laba_rata2_3th": 1.1,
            },
            "dividen": {
                "dividend_yield_ttm": 0.02 + i * 0.002,
                "payout_ratio_rata2_3th": 0.4,
                "konsistensi_dividen_3th": 3,
            },
            "konsistensi": {
                "laba_positif_3th": 3, "laba_positif_3th_n": 3,
                "laba_positif_4th": 4, "laba_positif_4th_n": 4,
            },
        },
        "cakupan": {"jumlah_tahun_ada_data": 4},
    }


def _hist(kode: str, n: int = 60) -> dict:
    d0 = date(2026, 1, 1)
    d = [(d0 + timedelta(days=i)).isoformat() for i in range(n)]
    return {"d": d, "c": [1000.0] * n, "v": [1_000_000] * n}


def _harga(kode: str) -> dict:
    # Bulanan 5 tahun + harian 260 sesi.
    mb = []
    y, m = 2021, 11
    for _ in range(60):
        mb.append(f"{y:04d}-{m:02d}-28")
        m += 1
        if m > 12:
            m = 1
            y += 1
    hd = [(date(2025, 10, 1) + timedelta(days=i)).isoformat() for i in range(260)]
    return {
        "bulanan": {"d": mb, "c": [1000.0 + i for i in range(60)]},
        "harian": {"d": hd, "c": [1000.0 + (i % 30) for i in range(260)]},
    }


def _info(kode: str, sektor: str) -> dict:
    return {
        "financialCurrency": "IDR", "marketCap": 1e12,
        "sharesOutstanding": 1e9, "floatShares": 3e8,
        "trailingEps": 100.0, "bookValue": 500.0,
        "totalDebt": 200e9, "debtToEquity": 40.0,
        "sector": sektor,
    }


def main() -> int:
    print("1. persentil")
    peers = [10, 20, 30, 40, 50]
    cek("nilai terendah -> 10", S._persentil(10, peers, "tinggi") == 10.0)
    cek("nilai tertinggi -> 90", S._persentil(50, peers, "tinggi") == 90.0)
    cek("arah rendah membalik", S._persentil(10, peers, "rendah") == 90.0)
    cek("satu pembanding -> 50", S._persentil(10, [10], "tinggi") == 50.0)

    print("2. payout & laba sekali-jadi")
    cek("payout 0.5 sehat", S._payout_sehat(0.5, CFG["valuasi"]) == 1.0)
    cek("payout 2.5 tidak wajar", S._payout_sehat(2.5, CFG["valuasi"]) == 0.0)
    cek("payout None", S._payout_sehat(None, CFG["valuasi"]) is None)
    fund = _fund("AAAA", 0)
    fund["data_mentah"]["laba_bersih"] = {"2025": 300.0, "2024": 100.0,
                                          "2023": 100.0, "2022": 100.0}
    once = S._laba_once(fund, CFG["valuasi"])
    cek("laba 3x median -> flag", once["flag"] is True and once["rasio"] == 3.0)

    print("2b. valuasi relatif (diskon/overprice)")
    def row_vr(harga=800.0, **raw_update):
        raw = {
            "per": 8.0, "pbv": 1.6, "eps_idr": 100.0, "ekuitas": 500e9,
            "laba_once": {"flag": False},
            "valuasi_historis": {
                "status": "ok",
                "tahun": [
                    {"tahun": 2025, "per": 9.0, "pbv": 1.5},
                    {"tahun": 2024, "per": 9.0, "pbv": 1.5},
                ],
            },
        }
        raw.update(raw_update)
        return {
            "fund_ada": True, "kelompok": "nonkeuangan",
            "sektor": "SEKTOR A", "asli": {"harga": harga}, "_raw": raw,
        }
    vr = S._valuasi_relatif(row_vr(), {"SEKTOR A": 10.0},
                            {"SEKTOR A": 1.8}, {"SEKTOR A": 10}, CFG)
    cek("status penuh", vr["status"] == "ok", str(vr["status"]))
    cek("PER wajar 9.6", vr["komponen"]["per"]["wajar"] == 9.6,
        str(vr["komponen"]["per"]))
    cek("PBV wajar 1.68", vr["komponen"]["pbv"]["wajar"] == 1.68,
        str(vr["komponen"]["pbv"]))
    cek("harga relatif 906", vr["harga_relatif"] == 906.0, str(vr["harga_relatif"]))
    cek("diskon +13.25%", vr["diskon_pct"] == 13.25, str(vr["diskon_pct"]))
    cek("label diskon", vr["label"] == "Diskon", vr["label"])
    cek("tidak dipakai skor", CFG["valuasi_relatif"]["dipakai_skor"] is False)
    cek("output menandai tidak dipakai skor", vr["dipakai_skor"] is False)

    vr_tipis = S._valuasi_relatif(
        row_vr(), {"SEKTOR A": 10.0}, {"SEKTOR A": 1.8},
        {"SEKTOR A": 3}, CFG)
    cek("sektor <5 emiten tidak memakai median sektor",
        vr_tipis["komponen"]["per"]["sektor"] is None
        and vr_tipis["komponen"]["pbv"]["sektor"] is None,
        str(vr_tipis["komponen"]))
    cek("sektor tipis jatuh ke median historis",
        vr_tipis["komponen"]["per"]["wajar"] == 9.0
        and vr_tipis["komponen"]["pbv"]["wajar"] == 1.5,
        str(vr_tipis["komponen"]))
    cek("sektor tipis diberi catatan",
        any("sektor hanya 3" in x for x in vr_tipis["catatan"]),
        str(vr_tipis["catatan"]))
    rows_strict = [{"sektor": "A", "per": 10.0 + i} for i in range(5)]
    cek("median sektor ketat butuh 5 observasi",
        S._median_sektor_strict(rows_strict, "per") == {"A": 12.0},
        str(S._median_sektor_strict(rows_strict, "per")))
    cek("median sektor ketat tidak memakai median gabungan",
        S._median_sektor_strict(rows_strict[:4], "per") == {},
        str(S._median_sektor_strict(rows_strict[:4], "per")))

    vr_over = S._valuasi_relatif(row_vr(harga=1200.0, per=12.0, pbv=2.4),
                                 {"SEKTOR A": 10.0}, {"SEKTOR A": 1.8},
                                 {"SEKTOR A": 10}, CFG)
    cek("overprice terdeteksi", vr_over["diskon_pct"] < -20,
        str(vr_over["diskon_pct"]))
    cek("label overprice berat", vr_over["label"] == "Overprice berat",
        vr_over["label"])

    vr_pbv = S._valuasi_relatif(row_vr(per=None, eps_idr=-1.0),
                                {"SEKTOR A": 10.0}, {"SEKTOR A": 1.8},
                                {"SEKTOR A": 10}, CFG)
    cek("EPS negatif -> hanya PBV", vr_pbv["status"] == "sebagian",
        str(vr_pbv["status"]))
    cek("bobot PBV menjadi 100%", vr_pbv["bobot_efektif"] == {"per": 0.0, "pbv": 1.0},
        str(vr_pbv["bobot_efektif"]))
    cek("PER tidak diisi 0", vr_pbv["komponen"]["per"]["tersedia"] is False)

    vr_campur = S._valuasi_relatif(row_vr(), {"SEKTOR A": 10.4},
                                   {"SEKTOR A": 1.28}, {"SEKTOR A": 10}, CFG)
    cek("sinyal campuran", vr_campur["campuran"] is True and
        vr_campur["label"] == "Sinyal campuran", str(vr_campur))
    cek("label komposit tetap dicatat", vr_campur["label_komposit"] == "Wajar",
        str(vr_campur["label_komposit"]))

    hv5 = {"tahun": [{"tahun": 2021, "per": 10.0},
                     {"tahun": 2025, "per": 50.0},
                     {"tahun": 2024, "per": 40.0},
                     {"tahun": 2023, "per": 30.0},
                     {"tahun": 2022, "per": 20.0}]}
    median4, n4 = S._median_historis(hv5, "per", 2, 1.0, 100.0, 4)
    cek("median historis memakai maksimal 4 tahun terbaru",
        median4 == 35.0 and n4 == 4, f"{median4} / {n4}")
    median_kurang, n_kurang = S._median_historis(
        {"tahun": [{"per": 10.0}, {"per": None}]}, "per", 2, 1.0, 100.0, 4)
    cek("median historis butuh minimal 2 tahun valid",
        median_kurang is None and n_kurang == 1,
        f"{median_kurang} / {n_kurang}")

    print("2c. penyesuaian split pada median historis")
    fund_split = {
        "tahun_keuangan": [2025, 2024],
        "periode_laporan": {"2025": "2025-12-31", "2024": "2024-12-31"},
        "data_mentah": {
            "laba_bersih": {"2025": 30e9, "2024": 30e9},
            "ekuitas": {"2025": 500e9, "2024": 500e9},
            "eps": {"2025": 30.0, "2024": 30.0},
            "jumlah_saham": {"2025": 1e9, "2024": 1e9},
        },
    }
    hv_split = {"tahun": [
        {"tahun": 2025, "target_harga": "2026-04-30", "harga": 120.0,
         "eps_idr": 30.0, "per": 4.0, "pbv": 0.24, "kurs_usd_idr": None},
        {"tahun": 2024, "target_harga": "2025-04-30", "harga": 1000.0,
         "eps_idr": 30.0, "per": 33.333, "pbv": 2.0, "kurs_usd_idr": None},
    ]}
    harga_split = {"split": [{"tanggal": "2025-09-09", "rasio": 10.0}]}
    hp_split, np_split = S._median_historis_relatif(
        fund_split, harga_split, hv_split, "per", 2, 1.0, 100.0, 4)
    hb_split, nb_split = S._median_historis_relatif(
        fund_split, harga_split, hv_split, "pbv", 2, 0.1, 20.0, 4)
    cek("PER historis disesuaikan split 10:1",
        hp_split is not None and abs(hp_split - 3.6667) < 0.001 and np_split == 2,
        f"{hp_split} / {np_split}")
    cek("PBV historis disesuaikan split 10:1",
        hb_split is not None and abs(hb_split - 0.22) < 0.001 and nb_split == 2,
        f"{hb_split} / {nb_split}")
    cek("data mentah historis tidak diubah",
        hv_split["tahun"][1]["per"] == 33.333)

    harga_reverse = {"split": [{"tanggal": "2025-09-09", "rasio": 0.5}]}
    hp_rev, np_rev = S._median_historis_relatif(
        fund_split, harga_reverse, hv_split, "per", 2, 1.0, 100.0, 4)
    cek("reverse split 0.5 menaikkan harga historis",
        hp_rev is not None and abs(hp_rev - 35.3333) < 0.001 and np_rev == 2,
        f"{hp_rev} / {np_rev}")

    row_split = {
        "fund_ada": True, "kelompok": "nonkeuangan", "sektor": "SEKTOR A",
        "asli": {"harga": 100.0}, "fund": fund_split,
        "_raw": {
            "per": 10.0, "pbv": 1.0, "eps_idr": 30.0, "ekuitas": 500e9,
            "laba_once": {"flag": False}, "valuasi_historis": hv_split,
            "harga_emiten": harga_split,
        },
    }
    vr_split = S._valuasi_relatif(
        row_split, {"SEKTOR A": 10.0}, {"SEKTOR A": 1.8},
        {"SEKTOR A": 10}, CFG)
    cek("indikator memakai median split-adjusted",
        abs(vr_split["komponen"]["per"]["historis"] - 3.6667) < 0.001
        and abs(vr_split["komponen"]["pbv"]["historis"] - 0.22) < 0.001,
        str(vr_split["komponen"]))
    cek("catatan split ada di indikator",
        any("disesuaikan split" in x for x in vr_split["catatan"]),
        str(vr_split["catatan"]))

    print("2d. laba berbalik dari rugi")
    fund_turn = {
        "tahun_keuangan": [2025, 2024, 2023],
        "data_mentah": {
            "laba_bersih": {"2025": 10e9, "2024": -5e9, "2023": -3e9},
            "eps": {"2025": 10.0, "2024": -5.0, "2023": -3.0},
            "ekuitas": {"2025": 500e9, "2024": 490e9, "2023": 480e9},
            "jumlah_saham": {"2025": 1e9, "2024": 1e9, "2023": 1e9},
        },
    }
    cek("turnaround terdeteksi",
        S._laba_transisi(fund_turn)["flag"] is True,
        str(S._laba_transisi(fund_turn)))
    cek("laba stabil tidak dianggap turnaround",
        S._laba_transisi(_fund("AAAA", 0))["flag"] is False)
    row_turn = {
        "fund_ada": True, "kelompok": "nonkeuangan", "sektor": "SEKTOR A",
        "asli": {"harga": 800.0}, "fund": fund_turn,
        "_raw": {
            "per": 8.0, "pbv": 1.6, "eps_idr": 10.0, "ekuitas": 500e9,
            "laba_once": {"flag": False},
            "valuasi_historis": {"tahun": [
                {"tahun": 2025, "per": 9.0, "pbv": 1.5},
                {"tahun": 2024, "per": 9.0, "pbv": 1.5},
            ]},
        },
    }
    vr_turn = S._valuasi_relatif(
        row_turn, {"SEKTOR A": 10.0}, {"SEKTOR A": 1.8},
        {"SEKTOR A": 10}, CFG)
    cek("PER turnaround tidak dipakai",
        vr_turn["komponen"]["per"]["tersedia"] is False
        and vr_turn["komponen"]["pbv"]["tersedia"] is True,
        str(vr_turn["komponen"]))
    cek("status turnaround sebagian",
        vr_turn["status"] == "sebagian", str(vr_turn["status"]))
    cek("catatan turnaround ada",
        any("tahun rugi" in x or "belum tentu berulang" in x
            for x in vr_turn["catatan"]),
        str(vr_turn["catatan"]))

    vr_bank = S._valuasi_relatif(
        {**row_vr(), "kelompok": "keuangan"}, {"SEKTOR A": 10.0},
        {"SEKTOR A": 1.8}, {"SEKTOR A": 10}, CFG)
    cek("bobot bank PBV dominan", vr_bank["bobot_efektif"] == {"per": 0.3, "pbv": 0.7},
        str(vr_bank["bobot_efektif"]))
    cek("diskon bank 9.5%", vr_bank["diskon_pct"] == 9.5, str(vr_bank["diskon_pct"]))

    vr_kosong = S._valuasi_relatif(
        {"fund_ada": True, "kelompok": "nonkeuangan", "sektor": "SEKTOR A",
         "asli": {"harga": 800.0}, "_raw": {"eps_idr": 100.0,
                                            "valuasi_historis": {"tahun": []}}},
        {}, {}, {"SEKTOR A": 2}, CFG)
    cek("tanpa data -> None", vr_kosong["diskon_pct"] is None and
        vr_kosong["status"] == "tidak tersedia", str(vr_kosong))

    vr_ekstrem = S._valuasi_relatif(
        row_vr(per=0.4, pbv=0.05), {"SEKTOR A": 10.0},
        {"SEKTOR A": 1.8}, {"SEKTOR A": 10}, CFG)
    cek("PER/PBV ekstrem tidak dipakai", vr_ekstrem["status"] == "tidak tersedia",
        str(vr_ekstrem))
    vr_besar = S._valuasi_relatif(row_vr(per=1.2, pbv=0.12),
                                  {"SEKTOR A": 10.0}, {"SEKTOR A": 1.8},
                                  {"SEKTOR A": 10}, CFG)
    cek("selisih >100% diberi flag ekstrem",
        vr_besar["ekstrem"] is True and
        any("cek split" in x for x in vr_besar["catatan"]), str(vr_besar))

    print("3. cakupan pilar (metrik hilang harus menurunkan cakupan)")
    row = {"kelompok": "nonkeuangan", "_raw": {"ic_relevan": True},
           "_metrik": {"roe_rata2_3th": {"nilai": 0.2, "persentil": 80.0,
                                          "basis": "sektor", "n_pembanding": 10,
                                          "persentil_gabungan": False}}}
    p = S._hitung_pilar(row, "kualitas_profitabilitas", CFG)
    cek("cakupan 40% (1 dari 4 metrik)", p["cakupan"] == 40.0, str(p["cakupan"]))
    cek("skor tetap 80", p["skor"] == 80.0)

    print("4. bank/lembaga keuangan")
    row_bank = {
        "kelompok": "keuangan", "_raw": {"ic_relevan": True},
        "_metrik": {
            "roe_keuangan": {"nilai": 0.2, "persentil": 90.0, "basis": "sektor",
                              "n_pembanding": 20, "persentil_gabungan": False},
            "pbv_keuangan": {"nilai": 1.0, "persentil": 70.0, "basis": "sektor",
                              "n_pembanding": 20, "persentil_gabungan": False},
        },
    }
    hb = S._hitung_pilar(row_bank, "kesehatan_keuangan", CFG)
    cek("cakupan bank 100%", hb["cakupan"] == 100.0, str(hb["cakupan"]))
    cek("skor bank 0.6*90+0.4*70=82", hb["skor"] == 82.0, str(hb["skor"]))
    cek("tidak ada DER/IC di detail bank",
        not any(d["kunci"] in ("der_rata2_3th", "interest_coverage_rata2_3th")
                for d in hb["metrik"]))
    cek("NPL/CAR ditandai tidak tersedia",
        any(d["kunci"] == "npl" and not d["tersedia"] for d in hb["metrik"]))

    print("5. tanpa utang: IC tidak relevan, cakupan tetap 100%")
    row_no_debt = {
        "kelompok": "nonkeuangan", "_raw": {"ic_relevan": False},
        "_metrik": {"der_rata2_3th": {"nilai": 0.0, "persentil": 100.0,
                                       "basis": "sektor", "n_pembanding": 10,
                                       "persentil_gabungan": False}},
    }
    hd = S._hitung_pilar(row_no_debt, "kesehatan_keuangan", CFG)
    cek("cakupan 100%", hd["cakupan"] == 100.0, str(hd["cakupan"]))
    cek("skor = DER", hd["skor"] == 100.0)

    print("6. gerbang")
    def row_gate(**kw):
        raw = {
            "likuiditas": {"nilai": 2e9, "n_sesi": 60, "sumber": "tes"},
            "market_cap": {"nilai": 1e12, "sumber": "tes"},
            "laba": {"positif_3th": 3, "n_3th": 3, "positif_4th": 4, "n_4th": 4},
            "ekuitas": 1e12, "tahun_ekuitas": 2025,
            "waspada_tingkat": None,
        }
        raw.update(kw.pop("raw", {}))
        return {"asli": {"waspada_diskualifikasi": kw.pop("diskualifikasi", False)},
                "_raw": raw}
    g = S._gerbang(row_gate(), 100.0, CFG)
    cek("semua lolos", g["lolos_gerbang"] is True)
    g = S._gerbang(row_gate(raw={"likuiditas": {"nilai": 0, "n_sesi": 60}}), 100.0, CFG)
    cek("likuiditas gagal", g["lolos_gerbang"] is False and
        any("likuiditas" in x for x in g["alasan_gagal_gerbang"]))
    g = S._gerbang(row_gate(raw={"laba": {"positif_3th": 2, "n_3th": 3,
                                          "positif_4th": 2, "n_4th": 4}}), 100.0, CFG)
    cek("laba 2/4 gagal", g["lolos_gerbang"] is False)
    g = S._gerbang(row_gate(raw={"ekuitas": -1}), 100.0, CFG)
    cek("ekuitas negatif gagal", g["lolos_gerbang"] is False)
    g = S._gerbang(row_gate(raw={"waspada_tingkat": "tinggi"}), 100.0, CFG)
    cek("waspada tinggi gagal", g["lolos_gerbang"] is False)
    g = S._gerbang(row_gate(), 69.9, CFG)
    cek("cakupan 69.9 gagal", g["lolos_gerbang"] is False and g["data_kurang"])

    print("7. emiten tanpa laporan -> metrik kosong, skor None")
    row_kosong = S._bangun_baris({"kode": "ZZZZ", "sektor": "SEKTOR A",
                                  "harga": 1000.0}, {}, {},
                                 None, None, None, None, {}, {}, CFG, {})
    cek("fund_ada false", row_kosong["fund_ada"] is False)
    cek("metrik kosong", row_kosong["_metrik"] == {})

    print("8. skor total: tanpa pilar data -> skor None")
    pilars = {p: {"skor": None, "cakupan": 0.0, "bobot": CFG["bobot_pilar"][p]}
              for p in S.PILAR_DATA}
    pilars["risiko_tata_kelola"] = {"skor": 90.0, "cakupan": 100.0,
                                     "bobot": 0.05}
    skor, _ = S._skor_total(pilars, CFG, 0.0)
    cek("skor None", skor is None)

    print("9. integrasi 8 emiten")
    latest = {"tanggal_data": "2026-10-29", "emiten": []}
    fundamental = {"emiten": {}}
    history = {"emiten": {}}
    harga = {"emiten": {}, "kurs_usd_idr": {"d": ["2022-01-31"], "c": [15000.0]}}
    info = {"emiten": {}}
    for i in range(8):
        k = f"AAA{i}"
        sektor = "SEKTOR A" if i < 5 else "SEKTOR B"
        latest["emiten"].append({
            "kode": k, "nama": f"Emiten {i}", "sektor": sektor,
            "harga": 1000.0 + i, "per": 10.0 + i, "pbv": 1.0 + i * 0.1,
            "roe": 0.1 + i * 0.01, "waspada_tingkat": None,
        })
        fundamental["emiten"][k] = _fund(k, i, sektor)
        history["emiten"][k] = _hist(k)
        harga["emiten"][k] = _harga(k)
        info["emiten"][k] = _info(k, sektor)
    sens = {"versi": "1.0.0", "status": "uji", "dipakai_skor": False,
            "sektor": {s: {"usd_idr_naik": 0, "alasan": "uji"}
                       for s in ("SEKTOR A", "SEKTOR B")}}
    res = S.hitung(latest, fundamental, history, harga, info, CFG, sens)
    cek("8 output", len(res["outputs"]) == 8)
    cek("semua lolos gerbang", res["meta"]["jumlah"]["lolos_gerbang"] == 8,
        str(res["meta"]["jumlah"]))
    cek("top20 <= 8", len(res["top20"]) == 8)
    cek("peringkat 1 skor tertinggi",
        res["top20"][0]["skor_jp"] == max(t["skor_jp"] for t in res["top20"]))
    cek("zona timing terisi", res["top20"][0]["zona_timing"] != S.TIDAK_ADA)
    cek("cakupan 100", res["top20"][0]["cakupan_bobot"] == 100.0)
    cek("sensitivitas tidak dipakai skor",
        res["top20"][0]["penanda_risiko"]["dipakai_skor"] is False)
    cek("tidak ada NaN/Infinity",
        "NaN" not in json.dumps(res["outputs"]) and
        "Infinity" not in json.dumps(res["outputs"]))
    cek("valuasi relatif di output",
        res["outputs"][0].get("valuasi_relatif") is not None)
    cek("valuasi relatif di top20",
        res["top20"][0].get("valuasi_relatif") is not None)

    print("10. gabung ke latest & versi skema")
    latest2 = {"versi_skema": "1.4.0", "emiten": [{"kode": "AAA0"}]}
    S.gabung_ke_latest(latest2, res)
    cek("versi 1.7.0", latest2["versi_skema"] == "1.7.0")
    cek("field skor_jp ada", "skor_jp" in latest2["emiten"][0])
    cek("top20_jp ada", len(latest2.get("top20_jp") or []) == 8)
    cek("meta ada", latest2.get("skor_jp_meta", {}).get("versi") == S.VERSI)
    _meta = latest2.get("skor_jp_meta", {})
    cek("meta: status ambang final", _meta.get("status_ambang") == "final",
        str(_meta.get("status_ambang")))
    cek("meta: tanggal konfirmasi ambang", _meta.get("dikonfirmasi_pada") == "2026-10-30",
        str(_meta.get("dikonfirmasi_pada")))
    cek("meta: dasar ambang tercatat", bool(_meta.get("dasar_ambang")),
        str(_meta.get("dasar_ambang"))[:80])
    cek("meta: indikator diskon tidak dipakai skor",
        _meta.get("valuasi_relatif", {}).get("dipakai_skor") is False,
        str(_meta.get("valuasi_relatif")))

    print(f"\nHASIL: {lulus} lulus, {gagal} gagal")
    return 1 if gagal else 0


if __name__ == "__main__":
    raise SystemExit(main())
