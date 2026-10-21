"""
Tes integrasi Tahap 2 — rezim pasar -> skor -> payload -> dashboard.

Yang diuji di sini (bukan unit modul, tapi IKATAN antar bagian):
  1. idx/rezim.py: rezim stres/normal/positif, transparansi pemicu.
  2. idx/scoring.py: flag "pasar stres" — skor TIDAK boleh diubah diam-diam.
  3. idx/pipeline.py: payload latest.json memuat konteks_pasar + rezim_pasar.
  4. index.html: banner rezim ada di DOM & fungsi render-nya dipanggil.
  5. idx/backtest.py: statistik dengan/tanpa periode anomali.

Tidak memukul jaringan.
"""
from __future__ import annotations

import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from idx import backtest as BT      # noqa: E402
from idx import pipeline as PIPE    # noqa: E402
from idx import rezim as RZ         # noqa: E402
from idx import scoring as S        # noqa: E402

LULUS = 0
GAGAL = 0
_CATATAN: list[str] = []


def cek(nama: str, kondisi: bool, detail: str = "") -> None:
    global LULUS, GAGAL
    if kondisi:
        LULUS += 1
    else:
        GAGAL += 1
        _CATATAN.append(f"{nama} {detail}")
        print(f"  GAGAL: {nama} {detail}")


def bagian(t: str) -> None:
    print(f"\n-- {t}")


NA = "data tidak tersedia"


def _bars(nilai: list[float]) -> list[dict]:
    return [{"date": f"2026-10-{i+1:02d}", "close": c}
            for i, c in enumerate(nilai)]


# ── 1. modul rezim ────────────────────────────────────────────────────────
bagian("1. idx/rezim.py — deteksi rezim")

_rz_turun = RZ.deteksi_rezim(_bars([100.0, 98.0, 96.0, 95.0, 94.0, 88.0]))
cek("rezim: turun tajam -> stres", _rz_turun.get("rezim") == "stres",
    str(_rz_turun.get("rezim")))
cek("rezim: pasar_stres True", _rz_turun.get("pasar_stres") is True)
cek("rezim: pemicu tidak kosong", bool(_rz_turun.get("pemicu")))
cek("rezim: pemicu punya angka & ambang",
    all(("nilai_pct" in p and "ambang_pct" in p) for p in _rz_turun["pemicu"]))
cek("rezim: ambang dipublikasikan", isinstance(_rz_turun.get("ambang"), dict)
    and _rz_turun["ambang"].get("ihsg_turun_5h_pct") == -5.0)

_rz_datar = RZ.deteksi_rezim(_bars([100.0] * 8))
cek("rezim: datar -> normal", _rz_datar.get("rezim") == "normal",
    str(_rz_datar.get("rezim")))
cek("rezim: datar -> tidak stres", _rz_datar.get("pasar_stres") is False)
cek("rezim: datar -> pemicu kosong", _rz_datar.get("pemicu") == [])

_rz_naik = RZ.deteksi_rezim(_bars([100.0 + i for i in range(8)]))
cek("rezim: naik -> positif", _rz_kiko if False else _rz_naik.get("rezim") == "positif",
    str(_rz_naik.get("rezim")))

_rz_kecil = RZ.deteksi_rezim(_bars([100.0, 99.0]))
cek("rezim: <6 bar -> data tidak tersedia", _rz_kecil.get("rezim") == NA,
    str(_rz_kecil.get("rezim")))
cek("rezim: <6 bar -> pasar_stres None (bukan False)",
    _rz_kecil.get("pasar_stres") is None)
cek("rezim: <6 bar -> alasan diisi", bool(_rz_kecil.get("alasan")))

_rz_halt = RZ.deteksi_rezim(_bars([100.0] * 8),
                            event_halt=[{"sumber": "x", "url": "https://a/b"}])
cek("rezim: halt dengan bukti -> terdeteksi",
    _rz_halt["trading_halt"]["terdeteksi"] is True)
cek("rezim: halt memicu stres", _rz_halt.get("pasar_stres") is True)
cek("rezim: halt tanpa bukti -> TIDAK diklaim",
    RZ.deteksi_rezim(_bars([100.0] * 8))["trading_halt"]["terdeteksi"] is False)
cek("rezim: halt tanpa bukti -> alasannya jujur",
    "tidak diklaim" in RZ.deteksi_rezim(_bars([100.0] * 8))["trading_halt"]["alasan"])

cek("rezim: fungsi murni (bar sama -> hasil sama)",
    RZ.deteksi_rezim(_bars([100.0, 98.0, 96.0, 95.0, 94.0, 88.0])) == _rz_turun)

# ── 2. ringkas konteks + flag skor ───────────────────────────────────────
bagian("2. flag skor 'pasar stres'")

_konteks_stres = RZ.ringkas_konteks(_rz_turun)
cek("konteks: label = 'pasar stres'",
    _konteks_stres.get("label") == "pasar stres", str(_konteks_stres.get("label")))
cek("konteks: pasar_stres True", _konteks_stres.get("pasar_stres") is True)
cek("konteks: pemicu ikut terbawa", bool(_konteks_stres.get("pemicu")))

_konteks_normal = RZ.ringkas_konteks(_rz_datar)
cek("konteks normal: label rezim normal",
    _konteks_normal.get("label") == "rezim normal", str(_konteks_normal.get("label")))
cek("konteks normal: tanpa flag stres",
    _konteks_normal.get("pasar_stres") is False)

_f_stres = S.flag_konteks_pasar(_konteks_stres)
cek("flag: stres -> flags berisi 'pasar stres'",
    _f_stres.get("flags") == ["pasar stres"], str(_f_stres.get("flags")))
_f_normal = S.flag_konteks_pasar(_konteks_normal)
cek("flag: normal -> flags kosong", _f_normal.get("flags") == [])
_f_na = S.flag_konteks_pasar(None)
cek("flag: konteks None -> label 'data tidak tersedia'",
    _f_na.get("label") == NA, str(_f_na.get("label")))
cek("flag: konteks None -> pasar_stres None",
    _f_na.get("pasar_stres") is None)
cek("flag: konteks None -> bukan 'normal'",
    _f_na.get("label") != "rezim normal")

# skor TIDAK boleh ikut berubah karena rezim
_tech = {"tersedia": True, "skor": 70.0}
_fund = {"tersedia": True, "skor": 50.0}
_cat = {"tersedia": True, "skor": 60.0}
_a = S.combine(_tech, _fund, _cat)
_b = S.combine(_tech, _fund, _cat)
cek("skor: combine deterministik (rezim tidak masuk combine)", _a["skor"] == _b["skor"])
cek("skor: combine tidak punya kunci rezim (skor netral)",
    "rezim" not in _a and "pasar_stres" not in _a)

# ── 3. pipeline: payload memuat konteks ──────────────────────────────────
bagian("3. pipeline — konteks_pasar di payload")

_riz = PIPE._market_summary([])
cek("pipeline: ringkasan_pasar selalu punya kunci rezim_pasar",
    "rezim_pasar" in (PIPE._market_summary([]) or {}))
cek("pipeline: ringkasan_pasar selalu punya kunci konteks_pasar",
    "konteks_pasar" in (PIPE._market_summary([]) or {}))
_ks = PIPE._market_summary([]).get("konteks_pasar") or {}
cek("pipeline: konteks_pasar punya rezim", bool(_ks.get("rezim")))
cek("pipeline: konteks_pasar punya label", bool(_ks.get("label")))

_src = open(os.path.join(ROOT, "idx", "pipeline.py"), encoding="utf-8").read()
cek("pipeline: payloadlatest.json punya konteks_pasar",
    '"konteks_pasar": konteks_pasar' in _src)
cek("pipeline: skor_detail tiap baris punya konteks_pasar",
    '"konteks_pasar": flag_konteks' in _src)
cek("pipeline: ringkasan_pasar tidak dihitung dua kali",
    _src.count('"ringkasan_pasar": _market_summary(rows)') == 0)

# ── 4. dashboard: banner rezim ────────────────────────────────────────────
bagian("4. dashboard — banner rezim pasar")

_html = open(os.path.join(ROOT, "index.html"), encoding="utf-8").read()
cek("UI: elemen #rezimBanner ada", 'id="rezimBanner"' in _html)
cek("UI: judul banner ada", 'id="rezimTitle"' in _html)
cek("UI: daftar pemicu ada", 'id="rezimPemicu"' in _html)
cek("UI: fungsi renderRezim ada", "function renderRezim()" in _html)
cek("UI: renderRezim dipanggil", "renderRezim();" in _html)
cek("UI: banner menyebut 'pasar stres'",
    "PASAR STRES" in _html or "pasar stres" in _html)
cek("UI: banner menampilkan pemicu (transparan)",
    "rezimPemicu" in _html and "pemicu" in _html)

# ── 5. backtest: statistik dengan/tanpa anomali ───────────────────────────
bagian("5. backtest — statistik dengan/tanpa anomali")

_ht = {"emiten": {}}
_hasil = BT.hitung(_ht, [])
cek("backtest: hitung tidak crash tanpa data",
    isinstance(_hasil, dict) and "kelompok_skor" in _hasil)
cek("backtest: statistik anomali ada di hasil",
    ("tanpa_anomali" in _hasil) or ("dengan_anomali" in _hasil)
    or ("anomali" in str(_hasil.keys())), str(list(_hasil.keys()))[:200])

# ── 6. fail-closed: konteks tidak boleh dikarang ─────────────────────────
bagian("6. integritas konteks")
cek("konteks: tidak pernah label 'rezim normal' saat data kosong",
    RZ.ringkas_konteks({"rezim": NA}).get("label") != "rezim normal")
_kosong = RZ.ringkas_konteks({})
cek("konteks: dict kosong -> honestly 'data tidak tersedia'",
    _kosong.get("label") == NA, str(_kosong.get("label")))
cek("konteks: dict kosong -> pasar_stres None",
    _kosong.get("pasar_stres") is None)

# ── 7. validasi fail-closed (idx/validate.py) ─────────────────────────────
bagian("7. validasi fail-closed konteks/rezim")

from idx import validate as V   # noqa: E402

_base = {
    "emiten": [{"kode": f"A{i:03d}", "harga": 100.0, "skor": 50.0,
                "skor_detail": {"total": 50.0}} for i in range(25)],
    "tanggal_data": "2026-10-21",
    "diperbarui_pada": "2026-10-21T10:00:00+07:00",
}


def _v(payload):
    return V.validasi(payload)


_r = _v(dict(_base))
cek("validasi: payload dasar tanpa konteks tetap OK", _r["ok"], str(_r["errors"])[:200])

_r = _v(dict(_base, konteks_pasar={"rezim": "stres", "pemicu": [],
                                   "pasar_stres": True}))
cek("validasi: rezim stres tanpa pemicu -> DITOLAK", not _r["ok"])
cek("validasi: pesan error menyebut pemicu",
    any("pemicu" in e for e in _r["errors"]), str(_r["errors"])[:200])

_r = _v(dict(_base, konteks_pasar={"rezim": "stres",
                                   "pemicu": [{"faktor": "x"}],
                                   "pasar_stres": True}))
cek("validasi: rezim stres dengan pemicu -> OK", _r["ok"], str(_r["errors"])[:200])

_r = _v(dict(_base, konteks_pasar={"rezim": "ngawur", "pemicu": []}))
cek("validasi: rezim tak dikenal -> DITOLAK", not _r["ok"])

_r = _v(dict(_base, konteks_pasar={"rezim": "normal", "pemicu": [],
                                   "pasar_stres": True}))
cek("validasi: pasar_stres True tapi rezim normal -> DITOLAK", not _r["ok"])

_r = _v(dict(_base, konteks_pasar={"rezim": "stres", "pemicu": [{"faktor": "x"}],
                                   "pasar_stres": False}))
cek("validasi: rezim stres tapi pasar_stres False -> DITOLAK", not _r["ok"])

_r = _v(dict(_base, konteks_pasar={"rezim": NA, "pemicu": [],
                                   "pasar_stres": None}))
cek("validasi: rezim 'data tidak tersedia' -> DITOLAK? tidak, harus OK",
    _r["ok"], str(_r["errors"])[:200])

_r = _v(dict(_base, konteks_pasar={"rezim": "stres", "pemicu": [{"faktor": "x"}],
                                   "pasar_stres": True},
             ringkasan_pasar={"rezim_pasar": {"rezim": "normal"}}))
cek("validasi: rezim tidak konsisten antar 2 sumber -> DITOLAK", not _r["ok"])

_r = _v(dict(_base, konteks_pasar="bukan objek"))
cek("validasi: konteks_pasar bukan objek -> DITOLAK", not _r["ok"])


# ── 8. definisi anomali: IRISAN JENDELA evaluasi (bukan hanya tanggal sinyal) ──
bagian("8. anomali — definisi jendela evaluasi")

from idx import anomali as ANOM  # noqa: E402

MULAI, SELESAI = ANOM.PERIODE_ANOMALI[0]["mulai"], ANOM.PERIODE_ANOMALI[0]["selesai"]
cek("anomali: periode 15-21 Okt 2026 terdaftar",
    MULAI == "2026-10-15" and SELESAI == "2026-10-21", f"{MULAI}..{SELESAI}")

# Sinyal DI DALAM periode -> jendela beririsan (tapi belum evaluable).
cek("anomali: sinyal di dalam periode -> beririsan",
    ANOM.jendela_beririsan("2026-10-16", "2026-10-23"))
# Sinyal SEBELUM periode, jendela berakhir DI DALAM periode -> beririsan.
cek("anomali: sinyal sebelum periode tapi jendela masuk -> beririsan",
    ANOM.jendela_beririsan("2026-10-01", "2026-10-16"))
# Sinyal jauh sebelum, jendela berakhir sebelum periode -> TIDAK beririsan.
cek("anomali: jendela berakhir sebelum periode -> tidak beririsan",
    not ANOM.jendela_beririsan("2026-09-01", "2026-10-10"))
# Sinyal setelah periode, jendela mulai setelah -> tidak beririsan.
cek("anomali: sinyal setelah periode -> tidak beririsan",
    not ANOM.jendela_beririsan("2026-10-22", "2026-11-10"))
# Jendela menutupi seluruh periode (sinyal sebelum, ujung setelah).
cek("anomali: jendela menutupi seluruh periode -> beririsan",
    ANOM.jendela_beririsan("2026-10-01", "2026-11-10"))
# Kasus batas: ujung tepat di hari pertama periode.
cek("anomali: ujung tepat di mulai periode -> beririsan",
    ANOM.jendela_beririsan("2026-09-30", "2026-10-15"))
# Kasus batas: ujung tepat sehari sebelum periode.
cek("anomali: ujung sehari sebelum mulai -> tidak beririsan",
    not ANOM.jendela_beririsan("2026-09-30", "2026-10-14"))
# Data rusak tidak boleh jadi error.
cek("anomali: tanggal None -> tidak beririsan, tidak crash",
    ANOM.jendela_beririsan(None, "2026-10-16") is False)
cek("anomali: ujung None -> fallback ke tanggal sinyal",
    ANOM.jendela_beririsan("2026-10-16", None) is True)
cek("anomali: jendela terbalik (akhir < awal) -> tidak beririsan",
    ANOM.jendela_beririsan("2026-10-16", "2026-10-01") is False)

# ── 9. backtest memakai definisi jendela, dan melapor jumlah per horizon ──
bagian("9. backtest — anomali per horizon")

_cek = BT.hitung({"emiten": {}}, [])
cek("backtest: laporkan titik terpengaruh per horizon",
    "titik_terpengaruh_per_horizon" in _cek.get("tanpa_anomali", {}),
    str(list(_cek.get("tanpa_anomali", {}).keys()))[:200])
cek("backtest: ada catatan evaluasi anomali",
    bool(_cek.get("catatan_evaluasi_anomali")))
cek("backtest: catatan menyebut sinyal belum bisa dievaluasi",
    "BELUM bisa dievaluasi" in (_cek.get("catatan_evaluasi_anomali") or ""))
cek("backtest: cakupan punya sinyal_anomali_belum_evaluable",
    "sinyal_anomali_belum_evaluable" in _cek.get("cakupan", {}))
cek("backtest: definisi jendela ditulis eksplisit di output",
    "JENDELA" in (_cek.get("tanpa_anomali", {}).get("definisi") or ""))

# Sumber: iter_titik harus menyertakan tanggal ujung jendela per horizon
_src_bt = open(os.path.join(ROOT, "idx", "backtest.py"), encoding="utf-8").read()
cek("backtest: sumber memakai jendela_beririsan",
    "ANOM.jendela_beririsan" in _src_bt)
cek("backtest: sumber menyimpan jendela_akhir per horizon",
    '"jendela_akhir"' in _src_bt)

# ── 10. dashboard menampilkan jumlah titik terpengaruh per horizon ────────
bagian("10. dashboard — titik terpengaruh per horizon")
cek("UI: info anomali menampilkan titik terpengaruh per horizon",
    "titik_terpengaruh_per_horizon" in _html)
cek("UI: menampilkan catatan evaluasi anomali",
    "catatan_evaluasi_anomali" in _html)


# ── 11. backtest: return mentah vs excess + sinyal unik + catatan tren ──
bagian("11. backtest — return mentah vs excess, sinyal unik, catatan tren pasar")
import json as _json
_bt = _json.load(open("/root/idx-dashboard/data/backtest.json", encoding="utf-8"))

_st = _bt["per_skor"]["tinggi"]["20"]
cek("per_skor tandai jenis_rata2 = return mentah",
    _st.get("jenis_rata2") == "return mentah (bukan excess)",
    str(_st.get("jenis_rata2")))

_ok = True
_detail = []
for _k, _per in _bt["per_skor"].items():
    for _h, _s in _per.items():
        if not _s.get("n"):
            continue
        _exp = round(_s["rata2"] - _s["ihsg_rata2"], 4)
        if abs(_s["excess"] - _exp) > 0.01:
            _ok = False
            _detail.append(f"{_k}/{_h}: {_s['excess']} vs {_exp}")
cek("excess = rata2 mentah - ihsg_rata2 (semua kelompok/horizon)", _ok,
    "; ".join(_detail[:3]))

_ok = True
_detail = []
for _k, _per in _bt["per_skor"].items():
    for _h, _s in _per.items():
        if not _s.get("n"):
            continue
        for _k2 in ("excess_rata2", "excess_median", "excess_menang", "excess_n"):
            if _s.get(_k2) is None:
                _ok = False
                _detail.append(f"{_k}/{_h} tidak punya {_k2}")
cek("statistik excess point-wise lengkap (rata2/median/menang/n)", _ok,
    "; ".join(_detail[:3]))

cek("excess_n == n titik (excess point-wise satu per titik)",
    _bt["per_skor"]["tinggi"]["20"]["excess_n"] == _bt["per_skor"]["tinggi"]["20"]["n"],
    f'{_bt["per_skor"]["tinggi"]["20"]["excess_n"]} vs {_bt["per_skor"]["tinggi"]["20"]["n"]}')

_su = _bt.get("sinyal_unik")
cek("sinyal_unik ada di backtest.json", isinstance(_su, dict))
cek("sinyal_unik punya definisi", bool((_su or {}).get("definisi")))
_tot = (_su or {}).get("total_per_horizon") or {}
cek("sinyal_unik total per horizon ada untuk 5/10/20",
    all(str(h) in _tot for h in (5, 10, 20)), str(_tot))
_ok = True
_detail = []
for _k, _per in _bt["per_skor"].items():
    for _h, _s in _per.items():
        _u = ((_su or {}).get("per_skor") or {}).get(_k, {}).get(str(_h))
        if _u is None:
            _ok = False
            _detail.append(f"{_k}/{_h} tanpa sinyal unik")
        elif _u > _s["n"]:
            _ok = False
            _detail.append(f"{_k}/{_h} unik {_u} > titik {_s['n']}")
cek("sinyal unik per kelompok/horizon ada dan <= jumlah titik", _ok,
    "; ".join(_detail[:3]))

_ok = True
_detail = []
for _h in ("5", "10", "20"):
    _a = _tot.get(_h)
    _b = ((_su or {}).get("tanpa_anomali", {}).get("total_per_horizon") or {}).get(_h)
    if _a is None or _b is None or _b > _a:
        _ok = False
        _detail.append(f"T+{_h}: dengan {_a} tanpa {_b}")
cek("sinyal unik tanpa_anomali <= dengan_anomali (per horizon)", _ok,
    "; ".join(_detail[:3]))

_tr = _bt.get("catatan_tren_pasar")
cek("catatan_tren_pasar ada", bool(_tr))
cek("catatan_tren_pasar menyebut MENTAH dan excess",
    bool(_tr) and "MENTAH" in _tr and "excess" in _tr.lower(),
    (_tr or "")[:80])

_htm = open("/root/idx-dashboard/index.html", encoding="utf-8").read()
cek("UI: header kolom 'Sinyal unik' ada", "Sinyal unik" in _htm)
cek("UI: header kolom 'Rata-rata (mentah)' ada", "Rata-rata (mentah)" in _htm)
cek("UI: kolom 'Excess' ada", ">Excess (selisih)<" in _htm)
cek("UI: '% Naik excess' ada", "% Naik excess" in _htm)
cek("UI: merender bt.sinyal_unik", "bt.sinyal_unik" in _htm)
cek("UI: merender excess_menang", "excess_menang" in _htm)
cek("UI: merender catatan_tren_pasar", "catatan_tren_pasar" in _htm)


print(f"\n== TOTAL: {LULUS} lulus, {GAGAL} gagal ==")
if GAGAL:
    print("Gagal:", "; ".join(_CATATAN[:12]))
sys.exit(0 if GAGAL == 0 else 1)
