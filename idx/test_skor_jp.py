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

    print("10. gabung ke latest & versi skema")
    latest2 = {"versi_skema": "1.4.0", "emiten": [{"kode": "AAA0"}]}
    S.gabung_ke_latest(latest2, res)
    cek("versi 1.6.0", latest2["versi_skema"] == "1.6.0")
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

    print(f"\nHASIL: {lulus} lulus, {gagal} gagal")
    return 1 if gagal else 0


if __name__ == "__main__":
    raise SystemExit(main())
