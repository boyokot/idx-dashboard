"""
Klasifikasi bertingkat untuk penanda `waspada`.

Latar belakang (review dashboard 21 Okt 2026):
    "Waspada 507/916 terlalu banyak."

Angka itu benar, tetapi bukan karena ambangnya salah — melainkan karena
SEMUA ciri risiko diperlakukan setara. Akibatnya satu flag ringan
(mis. "volume tidak tersedia", yang hanya berarti data kurang, BUKAN bukti
saham tidak likuid) membuat emiten terlempar dari Top 10 sama seperti
emiten dengan ROE negatif.

Modul ini memisahkan dua tingkat:

  TINGGI  -> diskualifikasi dari Top 10. Bukti kuat saham tidak sehat:
             ROE negatif, PER negatif, penny stock, volume sangat kecil,
             tembus support 20-hari, lonjakan harga tanpa berita.
  RINGAN  -> catatan/kewaspadaan, TIDAK mendiskualifikasi. Ciri yang bisa
             berarti data kurang atau kondisi sesaat: volume tidak
             tersedia, volume anjlok vs rata-rata, RSI jenuh beli,
             breakout tanpa volume.

Prinsip: tidak ada flag yang dibuang. Kedua tingkat tetap disimpan dan
ditampilkan; yang berubah hanya hak masuk Top 10. Fungsi murni (tanpa
jaringan) supaya bisa diuji.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

TINGGI = "tinggi"
RINGAN = "ringan"

# Urutan tampil di UI/laporan; juga urutan prioritas saat memilih tingkat
# dominan sebuah emiten.
LABEL_TINGGI = [
    "ROE negatif",
    "PER negatif (rugi)",
    "penny stock",
    "volume sangat kecil",
    "tembus support",
    "lonjakan",
]
LABEL_RINGAN = [
    "volume tidak tersedia",
    "volume anjlok",
    "RSI jenuh beli",
    "breakout tanpa volume",
]

_KUNCI_TINGGI = (
    "roe negatif",
    "per negatif",
    "penny stock",
    "volume sangat kecil",
    "tembus support",
    "lonjakan",
)
_KUNCI_RINGAN = (
    "volume tidak tersedia",
    "volume anjlok",
    "rsi jenuh",
    "breakout tanpa volume",
)


def _norm(flag: str) -> str:
    """Normalisasi teks flag: huruf kecil, tanpa angka dalam kurung.

    Flag asli mengandung nilai yang berubah tiap hari (mis.
    "RSI jenuh beli (82)" atau "lonjakan +17% tanpa berita"). Untuk
    pengelompokan, nilai itu dibuang supaya bisa dihitung per kriteria.
    """
    s = str(flag).lower()
    # buang isi kurung
    out: List[str] = []
    depth = 0
    for ch in s:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth = max(0, depth - 1)
        elif depth == 0:
            out.append(ch)
    s = "".join(out)
    # buang angka + simbol persen
    s = "".join(ch for ch in s if not (ch.isdigit() or ch in "%+-<>"))
    return " ".join(s.split())


def kriteria(flag: str) -> str:
    """Nama kriteria yang stabil untuk pengelompokan hitungan."""
    n = _norm(flag)
    for k in _KUNCI_TINGGI:
        if k in n:
            return k
    for k in _KUNCI_RINGAN:
        if k in n:
            return k
    return n


def tingkat(flags: Optional[List[str]]) -> Optional[str]:
    """Tingkat waspada sebuah emiten, atau None kalau tidak ada flag.

    Emiten dengan campuran flag -> TINGGI (yang terberat menang).
    """
    if not flags:
        return None
    n = [_norm(f) for f in flags]
    if any(any(k in x for k in _KUNCI_TINGGI) for x in n):
        return TINGGI
    return RINGAN


def klasifikasi(flags: Optional[List[str]]) -> Dict[str, Any]:
    """Rincian tingkat + kriteria per emiten."""
    flags = flags or []
    t = tingkat(flags)
    return {
        "tingkat": t,
        "kriteria": [kriteria(f) for f in flags],
        "diskualifikasi": t == TINGGI,
        "jumlah_flag": len(flags),
    }


def ringkas(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Rincian waspada seluruh pasar: per kriteria dan per tingkat.

    Hitungan per kriteria memakai seluruh baris (bukan potongan), jadi
    angkanya bisa diaudit ulang dari data mentah.
    """
    per_kriteria: Dict[str, Dict[str, Any]] = {}
    per_tingkat: Dict[str, int] = {TINGGI: 0, RINGAN: 0}
    jumlah_skor: Dict[str, int] = {TINGGI: 0, RINGAN: 0}

    total = 0
    punya_skor = 0
    tanpa_flag = 0

    for r in rows:
        flags = r.get("flag_waspada") or []
        punya_skor += 1 if r.get("skor") is not None else 0
        if not flags:
            tanpa_flag += 1
            continue
        total += 1
        t = tingkat(flags)
        if t:
            per_tingkat[t] = per_tingkat.get(t, 0) + 1
            if r.get("skor") is not None:
                jumlah_skor[t] = jumlah_skor.get(t, 0) + 1
        for f in flags:
            k = kriteria(f)
            slot = per_kriteria.setdefault(
                k, {"tingkat": tingkat([f]), "jumlah": 0, "contoh": []})
            slot["jumlah"] += 1
            if len(slot["contoh"]) < 5:
                slot["contoh"].append(r.get("kode"))

    urut = sorted(per_kriteria.items(), key=lambda kv: -kv[1]["jumlah"])
    return {
        "total_waspada": total,
        "total_emiten": len(rows),
        "emiten_punya_skor": punya_skor,
        "tanpa_flag": tanpa_flag,
        "per_tingkat": per_tingkat,
        "per_tingkat_punya_skor": jumlah_skor,
        "per_kriteria": {k: v for k, v in urut},
        "urutan_kriteria": [k for k, _ in urut],
        "catatan": (
            "Waspada TINGGI mendiskualifikasi dari Top 10; waspada RINGAN "
            "hanya jadi catatan. Kedua tingkat tetap ditampilkan — tidak ada "
            "flag yang dibuang."
        ),
    }


def ringkas_teks(ringkasan: Dict[str, Any]) -> str:
    """Satu baris ringkas untuk pesan Telegram/log."""
    pt = ringkasan.get("per_tingkat") or {}
    return (f"waspada {ringkasan.get('total_waspada', 0)}"
            f" (tinggi {pt.get(TINGGI, 0)}, ringan {pt.get(RINGAN, 0)})")
