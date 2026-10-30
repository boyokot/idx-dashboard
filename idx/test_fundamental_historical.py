#!/usr/bin/env python3
"""
Tes offline J1 — idx/fundamental_historical.py

Tidak memanggil jaringan. Fokus:
  * exact-match baris laporan (Net Income vs Net Income From Continuing...)
  * periode laporan memakai tanggal fiskal asli
  * CAGR 3 tahun wajib tepat 3 tahun, bukan jarak yang kebetulan tersedia
  * data hilang -> null + "data tidak tersedia", bukan 0/tebakan
  * dividen: TTM yield, payout, konsistensi
  * kolom tanggal masa depan tidak dipakai

Jalankan: python3 idx/test_fundamental_historical.py
"""
from __future__ import annotations

import sys
from datetime import date

import pandas as pd

sys.path.insert(0, "/root/idx-dashboard")

from idx import fundamental_historical as F  # noqa: E402

LULUS = 0
GAGAL = 0


def cek(nama: str, kondisi: bool, detail: str = "") -> None:
    global LULUS, GAGAL
    if kondisi:
        LULUS += 1
        print(f"  OK   {nama}")
    else:
        GAGAL += 1
        print(f"  GAGAL {nama}" + (f" :: {detail}" if detail else ""))


def df(rows: dict, years: tuple[int, ...] = (2025, 2024, 2023, 2022)) -> pd.DataFrame:
    cols = [pd.Timestamp(f"{y}-12-31") for y in years]
    rows = {k: tuple(v)[:len(cols)] for k, v in rows.items()}
    return pd.DataFrame(rows, index=cols).T


def lengkap(
    *,
    ni=(133.1, 121.0, 110.0, 100.0),
    rev=(200.0, 180.0, 160.0, 140.0),
    eq=(1000.0, 900.0, 800.0, 700.0),
    debt=(300.0, 270.0, 240.0, 210.0),
    ocf=(150.0, 130.0, 120.0, 100.0),
    bunga=(10.0, 10.0, 10.0, 10.0),
    eps=(13.31, 12.1, 11.0, 10.0),
    dividen=True,
    years=(2025, 2024, 2023, 2022),
) -> dict:
    inc = df({
        "Net Income From Continuing Operation Net Minority Interest": (999, 999, 999, 999),
        "Net Income": ni,
        "Total Revenue": rev,
        "Interest Expense": bunga,
        "Pretax Income": (160, 145, 132, 120),
        "EBIT": (170, 155, 142, 130),
        "Diluted EPS": eps,
    }, years)
    bal = df({
        "Stockholders Equity": eq,
        "Total Debt": debt,
        "Ordinary Shares Number": (10, 10, 10, 10),
    }, years)
    cf = df({"Operating Cash Flow": ocf}, years)
    if dividen:
        div = pd.Series(
            [6.0, 5.0, 4.0, 3.0, 2.0],
            index=pd.to_datetime([
                "2026-06-01", "2025-06-01", "2024-06-01",
                "2023-06-01", "2022-06-01",
            ]),
        )
    else:
        div = pd.Series(dtype=float)
    return {
        "ok": True,
        "income_stmt": inc,
        "balance_sheet": bal,
        "cashflow": cf,
        "dividends": div,
        "diambil_pada": "2026-10-30T00:00:00+07:00",
    }


HARI = date(2026, 10, 30)


print("== 1. exact-match & periode fiskal ==")
p = F._hitung_dari_raw("TEST", "Test Tbk", "TEST", lengkap(), 100.0, "2026-10-29", HARI)
cek("Net Income memakai baris exact 133.1, bukan decoy 999",
    p["data_mentah"]["laba_bersih"]["2025"] == 133.1,
    str(p["data_mentah"]["laba_bersih"]))
cek("periode 2025 = 2025-12-31", p["periode_laporan"]["2025"] == "2025-12-31")
cek("tahun keuangan urut terbaru dulu", p["tahun_keuangan"] == [2025, 2024, 2023, 2022])
cek("status lengkap", p["status_data"] == "lengkap", p["status_data"])

print("== 2. CAGR 3 tahun & ROE ==")
cek("CAGR laba ~10%", abs(p["metrik"]["pertumbuhan"]["cagr_laba_3th"] - 0.1) < 1e-9,
    str(p["metrik"]["pertumbuhan"]["cagr_laba_3th"]))
cagr_rev = (200.0 / 140.0) ** (1 / 3) - 1
cek("CAGR pendapatan benar",
    abs(p["metrik"]["pertumbuhan"]["cagr_pendapatan_3th"] - cagr_rev) < 1e-9)
roe_2025 = 133.1 / ((1000.0 + 900.0) / 2)
cek("ROE 2025 memakai rata-rata ekuitas",
    abs(p["metrik"]["profitabilitas"]["roe_tahunan"][2025] - roe_2025) < 1e-9)

print("== 3. DER, interest coverage, OCF/laba ==")
cek("DER 2025 = 0.3", abs(p["metrik"]["kesehatan_keuangan"]["der_tahunan"][2025] - 0.3) < 1e-9)
cek("interest coverage 2025 = 17",
    abs(p["metrik"]["kesehatan_keuangan"]["interest_coverage_tahunan"][2025] - 17.0) < 1e-9)
cek("OCF/laba 2025 benar",
    abs(p["metrik"]["kesehatan_keuangan"]["ocf_laba_tahunan"][2025] - (150 / 133.1)) < 1e-9)

print("== 4. dividen: yield TTM, payout, konsistensi ==")
cek("dividen TTM = 6.0 (hanya ex-date dalam 12 bulan)",
    abs(p["metrik"]["dividen"]["dividen_ttm"] - 6.0) < 1e-9,
    str(p["metrik"]["dividen"]["dividen_ttm"]))
cek("dividend yield TTM = 6%",
    abs(p["metrik"]["dividen"]["dividend_yield_ttm"] - 0.06) < 1e-9)
cek("payout 2025 = 5 / 13.31",
    abs(p["metrik"]["dividen"]["payout_ratio_tahunan"][2025] - (5 / 13.31)) < 1e-9)
cek("konsistensi dividen 3th = 3",
    p["metrik"]["dividen"]["konsistensi_dividen_3th"] == 3)

print("== 5. data hilang: interest expense tidak tersedia ==")
raw = lengkap()
raw["income_stmt"] = raw["income_stmt"].drop(index="Interest Expense")
p2 = F._hitung_dari_raw("TEST", "Test Tbk", "TEST", raw, 100.0, "2026-10-29", HARI)
cek("interest coverage = None, bukan 0",
    p2["metrik"]["kesehatan_keuangan"]["interest_coverage_rata2_3th"] is None)
cek("tercatat di tidak_tersedia",
    "kesehatan_keuangan.interest_coverage_rata2_3th" in p2["metrik"]["tidak_tersedia"])
cek("status bukan lengkap", p2["status_data"] != "lengkap", p2["status_data"])

print("== 6. CAGR tidak boleh dihitung dari 2 tahun ==")
raw = lengkap(years=(2025, 2024, 2023))
p3 = F._hitung_dari_raw("TEST", "Test Tbk", "TEST", raw, 100.0, "2026-10-29", HARI)
cek("CAGR laba None karena 2022 tidak ada",
    p3["metrik"]["pertumbuhan"]["cagr_laba_3th"] is None)

print("== 7. dividen kosong vs tidak tersedia ==")
p4 = F._hitung_dari_raw("TEST", "Test Tbk", "TEST", lengkap(dividen=False),
                        100.0, "2026-10-29", HARI)
cek("tidak ada catatan dividen -> yield 0", p4["metrik"]["dividen"]["dividend_yield_ttm"] == 0.0)
cek("payout non-payer = 0", p4["metrik"]["dividen"]["payout_ratio_tahunan"][2025] == 0.0)
raw = lengkap()
raw["dividends"] = None
p5 = F._hitung_dari_raw("TEST", "Test Tbk", "TEST", raw, 100.0, "2026-10-29", HARI)
cek("dividen gagal diambil -> yield None", p5["metrik"]["dividen"]["dividend_yield_ttm"] is None)

print("== 8. kolom masa depan diabaikan ==")
raw = lengkap()
raw["income_stmt"][pd.Timestamp("2027-12-31")] = raw["income_stmt"][pd.Timestamp("2025-12-31")]
p6 = F._hitung_dari_raw("TEST", "Test Tbk", "TEST", raw, 100.0, "2026-10-29", HARI)
cek("2027 tidak masuk tahun keuangan", 2027 not in p6["tahun_keuangan"])
cek("2027 tidak masuk periode", "2027" not in p6["periode_laporan"])

print("== 9. tanpa data sama sekali ==")
raw = {"income_stmt": pd.DataFrame(), "balance_sheet": pd.DataFrame(),
       "cashflow": pd.DataFrame(), "dividends": None, "diambil_pada": "x"}
p7 = F._hitung_dari_raw("TEST", "Test Tbk", "TEST", raw, None, None, HARI)
cek("status kosong", p7["status_data"] == "kosong", p7["status_data"])
cek("tidak ada angka 0 palsu",
    p7["metrik"]["profitabilitas"]["roe_rata2_3th"] is None
    and p7["metrik"]["pertumbuhan"]["cagr_laba_3th"] is None)
cek("semua metrik kunci tercatat tidak tersedia",
    len(p7["metrik"]["tidak_tersedia"]) == len(F._METRIK_KUNCI))

print("== 10. mapping jalur cepat camelCase -> label ==")
gab = pd.DataFrame({
    "2025-12-31": [100, 200, 500, 50, 80, 10, 5],
    "2024-12-31": [90, 180, 450, 45, 70, 9, 4],
}, index=["NetIncome", "TotalRevenue", "StockholdersEquity", "TotalDebt",
          "OperatingCashFlow", "InterestExpense", "DilutedEPS"])
inc = F._potong_statement(gab, F._KUNCI_INCOME)
bal = F._potong_statement(gab, F._KUNCI_BALANCE)
cf = F._potong_statement(gab, F._KUNCI_CASHFLOW)
cek("NetIncome -> Net Income", "Net Income" in inc.index)
cek("StockholdersEquity -> Stockholders Equity", "Stockholders Equity" in bal.index)
cek("OperatingCashFlow -> Operating Cash Flow", "Operating Cash Flow" in cf.index)

print(f"\nHASIL: {LULUS} lulus, {GAGAL} gagal")
raise SystemExit(0 if GAGAL == 0 else 1)
