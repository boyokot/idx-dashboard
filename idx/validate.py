#!/usr/bin/env python3
"""
Validasi prepublish — Kelompok E.

Tujuan: `data/latest.json` yang dipakai dashboard TIDAK PERNAH ditimpa oleh
data yang jelas rusak. Sebelum modul ini ada, pipeline menulis latest.json
tanpa satu pun pemeriksaan (`_tulis_atomic` dipanggil langsung setelah
payload disusun), sehingga run yang menghasilkan harga kosong atau satuan
kacau akan langsung merusak tampilan.

Prinsip desain:

1. **Dua tingkat.** ERROR = data tidak layak terbit, publish ditolak dan
   latest.json lama dipertahankan. WARNING = mencurigakan tapi data masih
   masuk akal; tetap terbit, hanya dicatat.

2. **Tidak menebak.** Semua cek berbasis invarian yang PASTI benar
   (volume tidak boleh negatif, RSI 0-100, tidak ada kode duplikat), bukan
   ambang selera yang bisa saja salah. Ambang yang memang selera
   (cakupan minimum) dibuat longgar dan hanya jadi WARNING.

3. **Tidak mengubah data.** Modul ini hanya MEMBACA payload. Tidak ada
   perbaikan otomatis di sini — memperbaiki diam-diam justru menyembunyikan
   bug di hulu.

Modul ini tidak melakukan request jaringan dan tidak menyentuh file.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

# String baku untuk "tidak ada data" — sama dengan yang dipakai modul eksternal,
# supaya validasi tidak bergantung pada wording teks.
TIDAK_ADA = "data tidak tersedia"

# ── ambang ────────────────────────────────────────────────────────────────
# ERROR ambang absolut: di bawah ini data dianggap tidak berguna sama sekali.
MIN_EMITEN = 20          # universe IDX ~975; <20 jelas bukan run yang benar
MIN_ADA_HARGA = 0.50     # <50% emiten punya harga -> sumber harga mati

# WARNING: mencurigakan, tapi tetap boleh terbit.
WARN_EMITEN_RASIO = 0.60     # <60% universe -> kemungkinan run parsial
WARN_FUNDAMENTAL = 0.40      # <40% punya fundamental
WARN_ADA_HARGA = 0.80        # <80% punya harga

# Batas kewarasan satuan (bukan preferensi).
ROE_ABS_MAKS = 10.0      # fraksi; ROE 1000% masih mungkin, 10000% tidak
DER_MAKS = 100.0         # rasio; DER 100x sudah ekstrem
CHG_ABS_MAKS = 100.0     # persen; harga tidak bisa turun >100%
SKOR_MIN, SKOR_MAKS = 0.0, 100.0
RSI_MIN, RSI_MAKS = 0.0, 100.0

# DER negatif BUKAN data rusak: artinya total ekuitas negatif, yang memang
# terjadi pada emiten distress (LAPD, CNKO, MYTX, ...). ROE-nya biasanya
# None karena laba/ekuitas negatif tidak menghasilkan rasio bermakna.
# Menolak publish karena ini akan memblokir data yang benar — jadi hanya
# dijadikan warning.


def _angka(v: Any) -> Optional[float]:
    """Konversi ke float; None/''/bool/NaN -> None (BUKAN 0)."""
    if v is None or isinstance(v, bool):
        return None
    if isinstance(v, str):
        v = v.strip().replace("%", "")
        if not v:
            return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    if f != f or f in (float("inf"), float("-inf")):
        return None
    return f


def validasi(payload: Dict[str, Any],
             total_universe: int = 0,
             run_penuh: bool = True) -> Dict[str, Any]:
    """Validasi payload latest.json sebelum ditulis.

    Kembalikan:
      {"ok": bool,                 # False -> JANGAN terbitkan
       "errors": [str, ...],       # alasan penolakan
       "warnings": [str, ...],     # mencurigakan, tetap terbit
       "stats": {...}}             # angka yang diperiksa (untuk log)
    """
    errors: List[str] = []
    warnings: List[str] = []

    rows = payload.get("emiten")
    if not isinstance(rows, list) or not rows:
        return {"ok": False, "errors": ["payload.emiten kosong atau bukan list"],
                "warnings": [], "stats": {"emiten": 0}}

    n = len(rows)
    kode_valid: List[str] = []
    duplikat: set[str] = set()
    sudah: set[str] = set()

    ada_harga = ada_fund = 0
    # penghitung pelanggaran satuan — dikumpulkan lalu dilaporkan sekali,
    # supaya pesan error tidak dibanjiri 900 baris.
    bad: Dict[str, List[str]] = {
        "skor": [], "rsi": [], "chg_pct": [], "volume": [], "der": [], "roe": [],
    }
    der_negatif: List[str] = []

    for r in rows:
        if not isinstance(r, dict):
            errors.append("ada baris yang bukan dict")
            continue
        kode = r.get("kode")
        if not isinstance(kode, str) or not kode.strip():
            errors.append("ada baris tanpa kode")
            continue
        kode = kode.strip().upper()
        if kode in sudah:
            duplikat.add(kode)
        sudah.add(kode)
        kode_valid.append(kode)

        if _angka(r.get("harga")) is not None:
            ada_harga += 1
        if _angka(r.get("per")) is not None or _angka(r.get("roe")) is not None:
            ada_fund += 1

        v = _angka(r.get("skor"))
        if v is not None and not (SKOR_MIN <= v <= SKOR_MAKS):
            bad["skor"].append(f"{kode}={v}")

        v = _angka(r.get("rsi"))
        if v is not None and not (RSI_MIN <= v <= RSI_MAKS):
            bad["rsi"].append(f"{kode}={v}")

        v = _angka(r.get("chg_pct"))
        if v is not None and abs(v) > CHG_ABS_MAKS:
            bad["chg_pct"].append(f"{kode}={v}")

        v = _angka(r.get("volume"))
        if v is not None and v < 0:
            bad["volume"].append(f"{kode}={v}")

        v = _angka(r.get("der"))
        if v is not None and v > DER_MAKS:
            bad["der"].append(f"{kode}={v}")
        elif v is not None and v < 0:
            der_negatif.append(kode)

        v = _angka(r.get("roe"))
        if v is not None and abs(v) > ROE_ABS_MAKS:
            bad["roe"].append(f"{kode}={v}")

    # ── ERROR: invarian yang pasti salah ──────────────────────────────
    if duplikat:
        errors.append(f"{len(duplikat)} kode duplikat: "
                      f"{sorted(duplikat)[:5]}")

    if n < MIN_EMITEN:
        errors.append(f"hanya {n} emiten (< {MIN_EMITEN})")

    rasio_harga = ada_harga / n if n else 0.0
    if rasio_harga < MIN_ADA_HARGA:
        errors.append(f"harga hanya {ada_harga}/{n} emiten "
                      f"({rasio_harga:.0%} < {MIN_ADA_HARGA:.0%}) — sumber harga mati?")

    label = {
        "skor": "skor di luar 0-100",
        "rsi": "rsi di luar 0-100",
        "chg_pct": f"chg_pct di luar ±{CHG_ABS_MAKS:.0f}%",
        "volume": "volume negatif",
        "der": f"der > {DER_MAKS:.0f}",
        "roe": f"|roe| > {ROE_ABS_MAKS:.0f} (fraksi)",
    }
    for k, items in bad.items():
        if items:
            errors.append(f"{len(items)} emiten {label[k]}: {items[:3]}")

    # DER negatif = ekuitas negatif (emiten distress). Data SAH, jadi hanya
    # warning — tapi wajib dilaporkan karena skor fundamental mereka tidak
    # bisa dipercaya dan UI tidak boleh menampilkannya sebagai sehat.
    if der_negatif:
        warnings.append(
            f"{len(der_negatif)} emiten DER negatif (ekuitas negatif, "
            f"skor fundamentalnya tidak bermakna): {sorted(der_negatif)[:5]}")

    # tanggal_data wajib ada: tanpa itu UI & tes tidak bisa tahu data ini
    # berasal dari sesi bursa kapan.
    if not (payload.get("tanggal_data") or "").strip():
        errors.append("tanggal_data kosong")

    # ── WARNING: mencurigakan, tetap terbit ───────────────────────────
    if total_universe and run_penuh:
        rasio = n / total_universe
        if rasio < WARN_EMITEN_RASIO:
            warnings.append(f"emiten {n}/{total_universe} ({rasio:.0%}) "
                            f"< {WARN_EMITEN_RASIO:.0%}")

    if rasio_harga < WARN_ADA_HARGA:
        warnings.append(f"cakupan harga {rasio_harga:.0%} < {WARN_ADA_HARGA:.0%}")

    rasio_fund = ada_fund / n if n else 0.0
    if rasio_fund < WARN_FUNDAMENTAL:
        warnings.append(f"cakupan fundamental {rasio_fund:.0%} "
                        f"< {WARN_FUNDAMENTAL:.0%}")

    sp = payload.get("skor_parsial") or {}
    if isinstance(sp, dict) and sp.get("penuh") == 0 and n:
        warnings.append("tidak ada emiten dengan skor penuh "
                        "(komponen katalis kosong?)")

    al = payload.get("alerts") or {}
    if isinstance(al, dict) and al.get("total") == 0:
        warnings.append("alert kosong — indikator tidak terisi?")


    # ── Tahap 2: konteks pasar & konsistensi rezim (fail-closed) ─────
    # Aturan: kalau pipeline mengklaim rezim, angka pendukungnya WAJIB ada.
    # Klaim "normal" tanpa data lebih berbahaya daripada "tidak tersedia",
    # jadi itu dijadikan ERROR, bukan warning.
    kp = payload.get("konteks_pasar")
    if kp is not None:
        if not isinstance(kp, dict):
            errors.append("konteks_pasar bukan objek")
        else:
            rez = kp.get("rezim")
            if rez is not None and rez != TIDAK_ADA:
                if rez not in ("normal", "stres", "positif"):
                    errors.append(f"konteks_pasar.rezim tidak dikenal: {rez!r}")
                # rezim sah wajib punya pemicu berbentuk list (boleh kosong)
                if not isinstance(kp.get("pemicu"), list):
                    errors.append("konteks_pasar.pemicu harus list")
                # stres wajib punya minimal satu pemicu — kalau tidak,
                # flag "pasar stres" tidak bisa dipertanggungjawabkan.
                if rez == "stres" and not kp.get("pemicu"):
                    errors.append("rezim 'stres' tanpa pemicu — tidak dapat dipertanggungjawabkan")
            if kp.get("pasar_stres") is True and rez not in ("stres",):
                errors.append("pasar_stres True tetapi rezim bukan 'stres'")
            if kp.get("pasar_stres") is False and rez == "stres":
                errors.append("rezim 'stres' tetapi pasar_stres False")
    rp = (payload.get("ringkasan_pasar") or {}).get("rezim_pasar")
    if rp is not None and isinstance(rp, dict):
        rr = rp.get("rezim")
        if rr is not None and rr != TIDAK_ADA and rr not in ("normal", "stres", "positif"):
            errors.append(f"ringkasan_pasar.rezim_pasar.rezim tidak dikenal: {rr!r}")
        # konsistensi antara dua tempat publikasi rezim
        if kp and isinstance(kp, dict) and rr and kp.get("rezim") and rr != kp.get("rezim"):
            errors.append(
                f"rezim tidak konsisten: konteks_pasar={kp.get('rezim')!r} "
                f"vs ringkasan_pasar={rr!r}")

    return {
        "ok": not errors,
        "errors": errors,
        "warnings": warnings,
        "stats": {"emiten": n, "ada_harga": ada_harga,
                  "ada_fundamental": ada_fund,
                  "rasio_harga": round(rasio_harga, 4),
                  "rasio_fundamental": round(rasio_fund, 4),
                  "total_universe": total_universe,
                  "run_penuh": bool(run_penuh)},
    }


def ringkas(hasil: Dict[str, Any]) -> str:
    """Satu baris untuk log pipeline."""
    s = hasil.get("stats") or {}
    st = "OK" if hasil.get("ok") else "DITOLAK"
    teks = (f"[validasi] {st}: {s.get('emiten', 0)} emiten, "
            f"harga {s.get('ada_harga', 0)}, "
            f"fundamental {s.get('ada_fundamental', 0)}")
    if hasil.get("warnings"):
        teks += f" | {len(hasil['warnings'])} warning"
    if hasil.get("errors"):
        teks += f" | {len(hasil['errors'])} error"
    return teks
