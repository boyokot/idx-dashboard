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

  TINGGI  -> diskualifikasi dari Top 10. Sejak 30 Okt 2026 aturannya
             dipersempit (disetujui pengguna): ROE negatif / PER negatif,
             ATAU penny stock + volume sangat kecil, ATAU tembus support
             + volume sangat kecil.
  RINGAN  -> catatan/kewaspadaan, TIDAK mendiskualifikasi. Termasuk ciri
             tunggal penny stock, volume sangat kecil, tembus support,
             lonjakan, volume tidak tersedia, volume anjlok, RSI jenuh,
             dan breakout tanpa volume.

Ciri tunggal tetap disimpan dan ditampilkan; beberapa di antaranya bisa
menjadi bagian TINGGI bila muncul dalam kombinasi di atas. Tidak ada flag
yang dibuang. Fungsi murni (tanpa jaringan) supaya bisa diuji.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Set, Tuple

TINGGI = "tinggi"
RINGAN = "ringan"

# Urutan tampil di UI/laporan. Sejak 30 Okt 2026 tidak semua label ini
# cukup membuat emiten TINGGI; lihat KUNCI_TINGGI_SENDIRI/KOMBINASI.
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

# Aturan TINGGI yang disetujui 30 Okt 2026.
KUNCI_TINGGI_SENDIRI = ("roe negatif", "per negatif")
KUNCI_TINGGI_KOMBINASI = (
    ("penny stock", "volume sangat kecil"),
    ("tembus support", "volume sangat kecil"),
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


def memenuhi_tinggi(kriteria_emiten: Set[str]) -> bool:
    """Aturan TINGGI yang berlaku sejak 30 Okt 2026.

    Ciri tunggal penny/volume/support/lonjakan TIDAK cukup. Yang cukup:
    fundamental berat, atau kombinasi likuiditas+teknikal yang disebut di
    KUNCI_TINGGI_KOMBINASI.
    """
    ks = set(kriteria_emiten)
    if ks & set(KUNCI_TINGGI_SENDIRI):
        return True
    return any(set(pasangan) <= ks for pasangan in KUNCI_TINGGI_KOMBINASI)


def tingkat(flags: Optional[List[str]]) -> Optional[str]:
    """Tingkat waspada sebuah emiten, atau None kalau tidak ada flag.

    (Review 30 Okt, fix 9) Aturan baru: TINGGI hanya untuk fundamental
    berat atau kombinasi penny/support + volume sangat kecil. Ciri tinggi
    tunggal lainnya turun ke RINGAN.
    """
    if not flags:
        return None
    return TINGGI if memenuhi_tinggi({kriteria(f) for f in flags}) else RINGAN


def tingkat_lama(flags: Optional[List[str]]) -> Optional[str]:
    """Aturan lama (sebelum 30 Okt 2026), hanya untuk baseline laporan."""
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

    (Review 30 Okt, fix 9) Rincian khusus waspada TINGGI: kriteria utama,
    kombinasi, jumlah kriteria per emiten, dan status aturan penyempitan
    yang sudah diterapkan 30 Okt 2026. Baseline aturan lama tetap dihitung
    agar perubahan bisa diaudit.
    """
    per_kriteria: Dict[str, Dict[str, Any]] = {}
    per_tingkat: Dict[str, int] = {TINGGI: 0, RINGAN: 0}
    jumlah_skor: Dict[str, int] = {TINGGI: 0, RINGAN: 0}

    total = 0
    punya_skor = 0
    tanpa_flag = 0
    tinggi_rows: List[Set[str]] = []
    lama_tinggi = 0

    for r in rows:
        flags = r.get("flag_waspada") or []
        punya_skor += 1 if r.get("skor") is not None else 0
        if not flags:
            tanpa_flag += 1
            continue
        total += 1
        t = tingkat(flags)
        if tingkat_lama(flags) == TINGGI:
            lama_tinggi += 1
        if t:
            per_tingkat[t] = per_tingkat.get(t, 0) + 1
            if r.get("skor") is not None:
                jumlah_skor[t] = jumlah_skor.get(t, 0) + 1
        for f in flags:
            k = kriteria(f)
            slot = per_kriteria.setdefault(
                k, {"tingkat": tingkat([f]), "jumlah": 0, "contoh": []})
            slot["bisa_tinggi_dalam_kombinasi"] = (
                k in _KUNCI_TINGGI and k not in KUNCI_TINGGI_SENDIRI)
            slot["jumlah"] += 1
            if len(slot["contoh"]) < 5:
                slot["contoh"].append(r.get("kode"))
        if t == TINGGI:
            tinggi_rows.append({kriteria(f) for f in flags})

    urut = sorted(per_kriteria.items(), key=lambda kv: -kv[1]["jumlah"])

    # ── rincian waspada tinggi ────────────────────────────────────────────
    kunci_tinggi = {k.lower() for k in LABEL_TINGGI}
    kriteria_tinggi: Dict[str, int] = {}
    kombinasi: Dict[Tuple[str, ...], int] = {}
    distribusi: Dict[int, int] = {}
    for ks in tinggi_rows:
        utama = ks & kunci_tinggi
        for k in utama:
            kriteria_tinggi[k] = kriteria_tinggi.get(k, 0) + 1
        kombinasi[tuple(sorted(utama))] = kombinasi.get(
            tuple(sorted(utama)), 0) + 1
        distribusi[len(utama)] = distribusi.get(len(utama), 0) + 1
    kombinasi_urut = sorted(kombinasi.items(), key=lambda kv: -kv[1])
    n_universe = len(rows)
    n_skor = punya_skor or 1
    n_tinggi = len(tinggi_rows)

    # Aturan penyempitan sudah diterapkan 30 Okt 2026. Baseline lama tetap
    # dihitung supaya laporan bisa membandingkan sebelum/sesudah.
    calon = n_tinggi
    usulan = {
        "status": "DITERAPKAN 30 Okt 2026",
        "diterapkan_pada": "2026-10-30",
        "versi_aturan": "2.0.0",
        "target_persen_universe": [15, 20],
        "aturan": (
            "Waspada TINGGI hanya jika ROE negatif / PER negatif, ATAU "
            "penny stock + volume sangat kecil, ATAU tembus support + "
            "volume sangat kecil. Ciri tinggi tunggal lainnya (volume sangat "
            "kecil, penny stock, tembus support, lonjakan) turun ke RINGAN."
        ),
        "perkiraan_jumlah": calon,
        "perkiraan_persen_universe": round(calon / n_universe * 100, 1)
        if n_universe else None,
        "perkiraan_persen_punya_skor": round(calon / n_skor * 100, 1),
        "jumlah_sebelum": lama_tinggi,
        "jumlah_sesudah": n_tinggi,
        "dampak": (
            f"{lama_tinggi - n_tinggi} emiten turun dari TINGGI ke RINGAN. "
            "Mereka tetap tampil di tabel; status RINGAN tidak lagi "
            "mendiskualifikasi dari Top 10. Tidak ada flag yang dibuang."
        ),
        "alasan": (
            f"Baseline aturan lama {lama_tinggi}/{n_universe} "
            f"({round(lama_tinggi / n_universe * 100, 1) if n_universe else 0}%) "
            "terlalu luas; kriteria tunggal seperti volume sangat kecil atau "
            "tembus support lebih tepat jadi catatan. Aturan baru menjaga "
            "sinyal fundamental tetap tegas dan sudah disetujui pengguna."
        ),
    }

    return {
        "total_waspada": total,
        "total_emiten": n_universe,
        "emiten_punya_skor": punya_skor,
        "tanpa_flag": tanpa_flag,
        "per_tingkat": per_tingkat,
        "per_tingkat_punya_skor": jumlah_skor,
        "per_kriteria": {k: v for k, v in urut},
        "urutan_kriteria": [k for k, _ in urut],
        "tinggi_ringkasan": {
            "jumlah": n_tinggi,
            "persen_universe": round(n_tinggi / n_universe * 100, 1)
            if n_universe else None,
            "persen_punya_skor": round(n_tinggi / n_skor * 100, 1),
            "sebelum_penyempitan": {
                "jumlah": lama_tinggi,
                "persen_universe": round(lama_tinggi / n_universe * 100, 1)
                if n_universe else None,
                "persen_punya_skor": round(lama_tinggi / n_skor * 100, 1),
            },
            "per_kriteria_utama": dict(sorted(kriteria_tinggi.items(),
                                              key=lambda kv: -kv[1])),
            "kombinasi": [
                {"kriteria": list(k), "jumlah": v}
                for k, v in kombinasi_urut[:10]
            ],
            "jumlah_kriteria_per_emiten": {
                str(k): v for k, v in sorted(distribusi.items())
            },
            "hanya_satu_kriteria": distribusi.get(1, 0),
            "catatan": (
                "Satu emiten bisa punya beberapa kriteria, jadi jumlah per "
                "kriteria tidak sama dengan jumlah emiten."
            ),
        },
        "usulan_penyempitan": usulan,
        "catatan": (
            "Sejak 30 Okt 2026 waspada TINGGI hanya jika ROE/PER negatif, "
            "penny stock + volume sangat kecil, atau tembus support + volume "
            "sangat kecil. Waspada RINGAN hanya catatan. Kedua tingkat tetap "
            "ditampilkan — tidak ada flag yang dibuang."
        ),
    }


def ringkas_teks(ringkasan: Dict[str, Any]) -> str:
    """Satu baris ringkas untuk pesan Telegram/log."""
    pt = ringkasan.get("per_tingkat") or {}
    return (f"waspada {ringkasan.get('total_waspada', 0)}"
            f" (tinggi {pt.get(TINGGI, 0)}, ringan {pt.get(RINGAN, 0)})")
