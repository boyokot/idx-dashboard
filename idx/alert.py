#!/usr/bin/env python3
"""
alert.py — Kelompok D: mesin alert.

Menghasilkan daftar alert dari baris emiten yang SUDAH dihitung pipeline.
Tidak ada request jaringan di sini: semuanya turunan dari indikator yang
sudah tersimpan, supaya pipeline tidak makin lambat.

Aturan penting:
  - Alert HANYA untuk kondisi yang datanya benar-benar ada. Volume `None`
    bukan bukti ilikuid; RSI `None` bukan bukti netral.
  - Urutan prioritas stabil (severity desc, lalu kode asc) supaya diff
    antar hari bisa dibandingkan.
  - Tidak ada alert yang mengklaim "beli". Semua label netral/peringatan.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

# severity: 3 = kritis, 2 = perhatian, 1 = informasi
SEVERITY = {"kritis": 3, "perhatian": 2, "info": 1}

# Ambang batas — satu tempat, jangan disebar ke UI.
AMBANG = {
    "rsi_jenuh": 75.0,
    "rsi_oversold": 25.0,
    "chg_lonjakan": 15.0,
    "volume_tipis_rasio": 0.15,
}


def _f(v: Any) -> Optional[float]:
    """Konversi aman ke float; bool & non-angka -> None."""
    if isinstance(v, bool) or v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _alert(kode: str, nama: Optional[str], jenis: str, severity: str,
           judul: str, detail: str, nilai: Any = None) -> Dict[str, Any]:
    return {
        "kode": kode,
        "nama": nama,
        "jenis": jenis,
        "severity": severity,
        "prioritas": SEVERITY.get(severity, 1),
        "judul": judul,
        "detail": detail,
        "nilai": nilai,
    }


def dari_baris(r: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Alert untuk SATU emiten. Mengembalikan list (bisa kosong)."""
    out: List[Dict[str, Any]] = []
    kode = r.get("kode") or "?"
    nama = r.get("nama")
    sinyal = r.get("sinyal")
    rsi = _f(r.get("rsi"))
    chg = _f(r.get("chg_pct"))
    vol = _f(r.get("volume"))
    harga = _f(r.get("harga"))
    support = _f(r.get("support"))
    resistance = _f(r.get("resistance"))
    skor = _f(r.get("skor"))

    # ── breakout dengan konfirmasi volume (informasi positif) ──────────
    if sinyal == "Breakout":
        out.append(_alert(
            kode, nama, "breakout", "info",
            "Breakout + volume",
            f"Tembus resistance Rp{resistance:,.0f} dengan volume "
            f"{vol:,.0f} lembar".replace(",", ".") if resistance else
            "Tembus resistance 20-hari dengan volume di atas rata-rata",
            {"harga": harga, "resistance": resistance, "volume": vol},
        ))
    elif sinyal == "Breakout (volume tipis)":
        out.append(_alert(
            kode, nama, "breakout_tanpa_volume", "perhatian",
            "Breakout tanpa volume",
            "Tembus resistance 20-hari tetapi volume tidak mendukung — "
            "rawan gagal bertahan",
            {"harga": harga, "resistance": resistance},
        ))

    # ── tembus support (kritis) ────────────────────────────────────────
    if sinyal == "Tembus Support":
        out.append(_alert(
            kode, nama, "support_break", "kritis",
            "Tembus support",
            f"Harga Rp{harga:,.0f} di bawah support 20-hari "
            f"Rp{support:,.0f}".replace(",", ".") if (harga and support)
            else "Harga di bawah support 20-hari",
            {"harga": harga, "support": support},
        ))

    # ── RSI ekstrem ────────────────────────────────────────────────────
    if rsi is not None:
        if rsi > AMBANG["rsi_jenuh"]:
            out.append(_alert(
                kode, nama, "rsi_jenuh", "perhatian",
                "RSI jenuh beli",
                f"RSI(14) {rsi:.1f} > {AMBANG['rsi_jenuh']:.0f} — harga "
                "sudah naik cepat, risiko koreksi pendek",
                {"rsi": rsi},
            ))
        elif rsi < AMBANG["rsi_oversold"]:
            out.append(_alert(
                kode, nama, "rsi_oversold", "info",
                "RSI jenuh jual",
                f"RSI(14) {rsi:.1f} < {AMBANG['rsi_oversold']:.0f} — "
                "tekanan jual tinggi, belum tentu pembalikan",
                {"rsi": rsi},
            ))

    # ── lonjakan harga tak wajar ───────────────────────────────────────
    if chg is not None and abs(chg) >= AMBANG["chg_lonjakan"]:
        arah = "naik" if chg > 0 else "turun"
        out.append(_alert(
            kode, nama, "lonjakan_harga", "perhatian",
            f"Lonjakan {arah} {abs(chg):.1f}%",
            f"Perubahan harga {chg:+.2f}% dalam satu sesi — di luar "
            "kebiasaan, periksa penyebabnya",
            {"chg_pct": chg},
        ))

    # ── volume anjlok (hanya kalau volume dilaporkan) ──────────────────
    if vol is not None and vol > 0 and harga is not None:
        # heuristik: nilai transaksi sangat kecil
        nilai_trx = vol * harga
        if nilai_trx < 1_000_000_000:  # < Rp1 miliar
            out.append(_alert(
                kode, nama, "likuiditas_tipis", "info",
                "Likuiditas tipis",
                f"Nilai transaksi ±Rp{nilai_trx / 1e9:.2f} miliar — "
                "sulit keluar masuk tanpa menggerakkan harga",
                {"nilai_transaksi": nilai_trx, "volume": vol},
            ))

    # ── skor tinggi tapi waspada = konflik sinyal ──────────────────────
    if skor is not None and skor >= 65 and r.get("waspada"):
        flags = r.get("flag_waspada") or []
        out.append(_alert(
            kode, nama, "konflik_skor", "perhatian",
            "Skor tinggi tetapi waspada",
            "Skor " + f"{skor:.1f}" + " tetapi ada flag risiko: "
            + (", ".join(flags) if isinstance(flags, list) else str(flags)),
            {"skor": skor, "flags": flags},
        ))

    return out


def kumpulkan(rows: List[Dict[str, Any]], maks: int = 200) -> Dict[str, Any]:
    """Kumpulkan alert seluruh pasar + hitungan per jenis.

    `maks` membatasi jumlah alert yang dikirim ke payload (default 200).
    Hitungan per jenis tetap dihitung dari SELURUH baris, bukan dari
    potongan, supaya angka ringkasan tidak menipu.
    """
    semua: List[Dict[str, Any]] = []
    for r in rows:
        try:
            semua.extend(dari_baris(r))
        except Exception:
            # satu baris rusak tidak boleh menggagalkan seluruh alert
            continue

    semua.sort(key=lambda a: (-a["prioritas"], a["kode"], a["jenis"]))

    per_jenis: Dict[str, int] = {}
    per_severity: Dict[str, int] = {"kritis": 0, "perhatian": 0, "info": 0}
    for a in semua:
        per_jenis[a["jenis"]] = per_jenis.get(a["jenis"], 0) + 1
        per_severity[a["severity"]] = per_severity.get(a["severity"], 0) + 1

    return {
        "total": len(semua),
        "per_jenis": per_jenis,
        "per_severity": per_severity,
        "dipotong": len(semua) > maks,
        "daftar": semua[:maks],
    }


def skor_parsial(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Ringkas skor yang tidak dihitung dari data lengkap.

    Skor dengan `bobot_terisi` < 1 bukan skor penuh. Ini penting
    dikatakan terang-terangan: membandingkan skor 80 (data 60%) dengan
    skor 75 (data 100%) tanpa catatan itu menyesatkan.
    """
    penuh = 0
    sebagian = 0
    tanpa_skor = 0
    contoh: List[Dict[str, Any]] = []

    for r in rows:
        skor = _f(r.get("skor"))
        if skor is None:
            tanpa_skor += 1
            continue
        det = r.get("skor_detail") or {}
        bt = _f(det.get("bobot_terisi"))
        if bt is None:
            bt = 1.0
        if bt >= 0.99:
            penuh += 1
        else:
            sebagian += 1
            if len(contoh) < 10:
                komp = det.get("komponen") or {}
                hilang = [n for n, c in komp.items()
                          if isinstance(c, dict) and not c.get("tersedia")]
                contoh.append({
                    "kode": r.get("kode"),
                    "skor": skor,
                    "bobot_terisi": bt,
                    "komponen_hilang": hilang,
                })

    return {
        "penuh": penuh,
        "sebagian": sebagian,
        "tanpa_skor": tanpa_skor,
        "total": penuh + sebagian + tanpa_skor,
        "persen_penuh": round(100.0 * penuh / (penuh + sebagian), 1)
        if (penuh + sebagian) else None,
        "contoh_sebagian": contoh,
        "catatan": (
            "Skor dengan data sebagian TIDAK sebanding langsung dengan skor "
            "berdata penuh; bobot dinormalisasi ke komponen yang tersedia."
            if sebagian else None
        ),
    }
