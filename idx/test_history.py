#!/usr/bin/env python3
"""
Tes untuk idx/history_store.py (Kelompok B).

Cek yang ditekankan:
  * build() memotong ke max_sesi dan menyisakan seri TERAKHIR
  * volume None tetap None (bukan 0)
  * merge_lama mempertahankan emiten yang gagal diambil
  * publish() menulis atomik & file bisa dibaca ulang
  * MA20/MA50 frontend = MA20/MA50 pipeline (sumber angka sama)
"""
from __future__ import annotations

import json
import os
import sys
import tempfile

sys.path.insert(0, "/root/scripts")

from idx import history_store as H  # noqa: E402

LULUT = 0
GAGAL = 0


def cek(nama, kondisi, detail=""):
    global LULUT, GAGAL
    if kondisi:
        LULUT += 1
        print(f"  OK   {nama}")
    else:
        GAGAL += 1
        print(f"  FAIL {nama} {detail}")


def bars(n, mulai=1.0, vol_ada=True):
    """n baris dengan tanggal BERTANGGUR (bukan '2026-01-200')."""
    import datetime as _dt
    d0 = _dt.date(2026, 1, 1)
    out = []
    for i in range(n):
        dt = d0 + _dt.timedelta(days=i)
        b = {"date": dt.isoformat(), "open": mulai, "high": mulai,
             "low": mulai, "close": mulai + i, "volume": 1000 + i}
        if not vol_ada and i % 3 == 0:
            b["volume"] = None
        out.append(b)
    return out


print("== 1. build(): potong ke max_sesi, sisakan yang terbaru ==")
p = H.build({"AAAA": bars(200)}, max_sesi=120)
e = p["emiten"]["AAAA"]
cek("jumlah seri = 120", len(e["d"]) == 120, f"dapat {len(e['d'])}")
cek("tidak ada tanggal duplikat", len(set(e["d"])) == 120)
cek("tanggal urut naik", e["d"] == sorted(e["d"]))
cek("close terakhir = 200 (i=199, paling baru)",
    abs(e["c"][-1] - 200.0) < 1e-6, f"dapat {e['c'][-1]}")
cek("close awal window = 81 (i=80)",
    abs(e["c"][0] - 81.0) < 1e-6, f"dapat {e['c'][0]}")
cek("hanya simpan d/c/v", set(e.keys()) == {"d", "c", "v"}, f"dapat {set(e.keys())}")
cek("panjang d == c == v",
    len(e["d"]) == len(e["c"]) == len(e["v"]))

print("== 2. volume None dipertahankan (bukan 0) ==")
p2 = H.build({"BBBB": bars(10, vol_ada=False)})
v = p2["emiten"]["BBBB"]["v"]
cek("ada nilai None di volume", any(x is None for x in v), f"dapat {v}")
cek("tidak ada 0 palsu", 0 not in [x for x in v if x is not None])

print("== 3. emitten tanpa baris -> dilewati ==")
p3 = H.build({"CCCC": [], "DDDD": bars(3)})
cek("CCCC tidak masuk", "CCCC" not in p3["emiten"])
cek("DDDD masuk", "DDDD" in p3["emiten"])

print("== 4. merge_lama(): emiten gagal diambil tetap dipertahankan ==")
lama = H.build({"AAAA": bars(50), "ZZZZ": bars(30)})
baru = H.build({"AAAA": bars(55)})
merged, dipertahankan = H.merge_lama(baru, lama)
cek("emiten dipertahankan = 1", dipertahankan == 1, f"dapat {dipertahankan}")
cek("ZZZZ masih ada", "ZZZZ" in merged["emiten"])
cek("AAAA di-overwrite yang baru", len(merged["emiten"]["AAAA"]["d"]) == 55)
cek("jumlah_emiten konsisten",
    merged["jumlah_emiten"] == len(merged["emiten"]))
cek("jumlah_sesi konsisten",
    merged["jumlah_sesi"] == sum(len(x["d"]) for x in merged["emiten"].values()))

print("== 5. merge_lama() tanpa file lama -> tidak error ==")
m2, d2 = H.merge_lama(H.build({"AAAA": bars(5)}), None)
cek("tanpa lama tetap aman", d2 == 0 and "AAAA" in m2["emiten"])

print("== 6. publish() atomik & bisa dibaca ulang ==")
with tempfile.TemporaryDirectory() as td:
    fp = os.path.join(td, "history.json")
    payload = H.build({"AAAA": bars(10)})
    H.publish(payload, fp)
    cek("file ada", os.path.exists(fp))
    cek("tidak ada file .tmp tersisa",
        not any(f.startswith("history.json.tmp") for f in os.listdir(td)),
        f"dapat {os.listdir(td)}")
    back = H.load(fp)
    cek("baca ulang jalan", back is not None and "AAAA" in back["emiten"])
    cek("isi sama", back["emiten"]["AAAA"]["d"] == payload["emiten"]["AAAA"]["d"])
    # tulis ulang -> harus menimpa, bukan menumpuk
    H.publish(H.build({"BBBB": bars(4)}), fp)
    back2 = H.load(fp)
    cek("tulis ulang menimpa", "BBBB" in back2["emiten"] and "AAAA" not in back2["emiten"])

print("== 7. load() file rusak -> None, bukan crash ==")
with tempfile.TemporaryDirectory() as td:
    fp = os.path.join(td, "history.json")
    with open(fp, "w", encoding="utf-8") as f:
        f.write("{bukan json")
    cek("file rusak -> None", H.load(fp) is None)

print("== 8. MA pipeline == MA frontend (sumber angka sama) ==")
# Pipeline memakai scoring.sma(closes, n) = mean window terakhir n.
# Frontend menghitung dari seri 'c' yang IDENTIK -> harus sama persis.
try:
    from idx import scoring as S
except ImportError:                       # jalan dari /root/scripts/idx
    import scoring as S

seri = [100.0 + i * 1.5 for i in range(120)]
import datetime as _dt
_d0 = _dt.date(2026, 1, 1)
bars_p = [{"date": (_d0 + _dt.timedelta(days=i)).isoformat(), "close": seri[i],
           "volume": 100, "open": seri[i], "high": seri[i], "low": seri[i]}
          for i in range(120)]
p4 = H.build({"EEEE": bars_p}, max_sesi=120)
c4 = p4["emiten"]["EEEE"]["c"]
cek("seri c == seri close asli", c4 == seri)

# replika rumus frontend: MA(n) dihitung untuk setiap titik, null sebelum n
def ma_frontend(vals, n):
    out = []
    for i in range(len(vals)):
        out.append(sum(vals[i - n + 1:i + 1]) / n if i + 1 >= n else None)
    return out

for n in (20, 50):
    ma_py = S.sma(seri, n)                    # nilai terakhir dari pipeline
    ma_js = ma_frontend(c4, n)[-1]            # nilai terakhir dari "frontend"
    cek(f"MA{n} pipeline == frontend", abs(ma_py - ma_js) < 1e-9,
        f"py={ma_py} js={ma_js}")
    cek(f"MA{n} null sebelum {n} titik",
        ma_frontend(c4, n)[n - 2] is None)

print("== 9. kurva MA benar (titik null tidak dihitung sebagai 0) ==")
naik = [10.0] * 19 + [100.0]        # hanya 1 nilai besar di akhir
ma = ma_frontend(naik, 20)
cek("MA20 titik terakhir = 14,5",
    abs(ma[-1] - (19 * 10 + 100) / 20) < 1e-9, f"dapat {ma[-1]}")

print()
print(f"== TOTAL: {LULUT} tes lulus, {GAGAL} gagal ==")
sys.exit(0 if GAGAL == 0 else 1)
