#!/usr/bin/env python3
"""
Tes J4 — snapshot bulanan Skor JP dan akurasi 3/6/12 bulan.

Dijalankan: python3 idx/test_akurasi_jp.py
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from idx import akurasi_jp as AK  # noqa: E402

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


def payload(tanggal: str, harga: dict[str, float], ihsg: float) -> dict:
    emiten = []
    for i, (kode, h) in enumerate(sorted(harga.items())):
        emiten.append({
            "kode": kode,
            "nama": "Emiten " + kode,
            "sektor": "BANK" if i < 3 else "COAL MINING",
            "skor_jp": 70.0 - i,
            "peringkat_jp": i + 1,
            "diperingkat": True,
            "lolos_gerbang": True,
            "cakupan_bobot": 100.0,
            "harga": h,
        })
    return {
        "tanggal_data": tanggal,
        "ringkasan_pasar": {"ihsg": {"tersedia": True, "nilai": ihsg}},
        "emiten": emiten,
    }


def harga_naik(mult: float) -> dict[str, float]:
    return {k: round(v * mult, 2) for k, v in
            {"AAAA": 100.0, "BBBB": 200.0, "CCCC": 50.0,
             "DDDD": 80.0, "EEEE": 120.0, "FFFF": 60.0}.items()}


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="akurasi_jp_") as td:
        d = Path(td)
        AK.SNAP_DIR = d / "snapshots_jp"
        AK.INDEX_FILE = AK.SNAP_DIR / "index.json"
        AK.AKURASI_FILE = d / "akurasi_jp.json"

        print("1. snapshot bulanan idempoten")
        p1 = payload("2026-01-30", harga_naik(1.0), 1000.0)
        s1 = AK.simpan_snapshot_bulanan(p1, tulis=True)
        cek("snapshot pertama ditulis", s1["status"] == "ditulis", str(s1))
        cek("file bulanan dibuat", (AK.SNAP_DIR / "2026-01.json").exists())
        teks1 = (AK.SNAP_DIR / "2026-01.json").read_text()
        s2 = AK.simpan_snapshot_bulanan(p1, tulis=True)
        teks2 = (AK.SNAP_DIR / "2026-01.json").read_text()
        cek("run kedua tidak menimpa", s2["status"] == "sudah_ada", str(s2))
        cek("harga awal tidak bergeser", teks1 == teks2)
        idx = json.loads(AK.INDEX_FILE.read_text())
        cek("index berisi satu file", idx["jumlah"] == 1 and idx["files"] == ["2026-01.json"])

        print("\n2. hitung return 3/6/12 bulan")
        AK.simpan_snapshot_bulanan(payload("2026-04-30", harga_naik(1.10), 1050.0), tulis=True)
        AK.simpan_snapshot_bulanan(payload("2026-07-30", harga_naik(1.20), 1100.0), tulis=True)
        AK.simpan_snapshot_bulanan(payload("2027-01-29", harga_naik(1.30), 1200.0), tulis=True)
        a = AK.hitung_akurasi(tulis=True)
        cek("status akurasi ok", a["status"] == "ok", a["status"])
        cek("file akurasi ditulis", AK.AKURASI_FILE.exists())
        cek("snapshot terbaca 4 bulan", a["snapshot"]["jumlah"] == 4, str(a["snapshot"]))

        h3 = [x for x in a["hasil"] if x["mulai"] == "2026-01" and x["horizon_bulan"] == 3]
        cek("hasil 3 bulan ada", len(h3) == 1, str(len(h3)))
        if h3:
            x = h3[0]
            cek("Top 20 return 10%", abs(x["top20_return_pct"] - 10.0) < 0.01, str(x))
            cek("IHSG return 5%", abs(x["ihsg_return_pct"] - 5.0) < 0.01, str(x))
            cek("sektor rata-rata 10%", abs(x["sektor_return_pct"] - 10.0) < 0.01, str(x))
            cek("excess IHSG 5%", abs(x["excess_ihsg_pct"] - 5.0) < 0.01, str(x))
            cek("excess sektor 0%", abs(x["excess_sektor_pct"]) < 0.01, str(x))
            cek("n Top 20 = 6", x["n_top20_berharga"] == 6, str(x["n_top20_berharga"]))

        h12 = [x for x in a["hasil"] if x["mulai"] == "2026-01" and x["horizon_bulan"] == 12]
        cek("hasil 12 bulan Jan 2026 tersedia", len(h12) == 1)
        if h12:
            cek("Top 20 return 12 bulan 30%", abs(h12[0]["top20_return_pct"] - 30.0) < 0.01,
                str(h12[0]["top20_return_pct"]))
            cek("IHSG return 12 bulan 20%", abs(h12[0]["ihsg_return_pct"] - 20.0) < 0.01,
                str(h12[0]["ihsg_return_pct"]))
        p12 = [x for x in a["pending"] if x["mulai"] == "2026-04" and x["horizon_bulan"] == 12]
        cek("pending 12 bulan Apr 2026 tercatat",
            len(p12) == 1 and p12[0]["target"] == "2027-04", str(p12))

        print("\n3. tahan data hilang (bukan 0)")
        # Snapshot dengan harga null harus tetap tersimpan null, bukan 0.
        pn = payload("2026-10-29", {"AAAA": None, "BBBB": 200.0}, 1000.0)  # type: ignore[arg-type]
        sn = AK.simpan_snapshot_bulanan(pn, tulis=True)
        cek("snapshot dengan harga null tetap dibuat", sn["status"] == "ditulis", str(sn))
        js = json.loads((AK.SNAP_DIR / "2026-10.json").read_text())
        aaaa = next(x for x in js["emiten"] if x["kode"] == "AAAA")
        cek("harga null tidak diubah menjadi 0", aaaa["harga"] is None, str(aaaa))

    print(f"\n== TOTAL: {LULUS} lulus, {GAGAL} gagal ==")
    return 1 if GAGAL else 0


if __name__ == "__main__":
    raise SystemExit(main())
