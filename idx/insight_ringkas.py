"""
Pembangun narasi Tab Insight (TAHAP I2) — SEMUA poin dihasilkan dari data.

Aturan keras:
  * Setiap poin WAJIB membawa `angka` dan `sumber` yang bisa ditelusuri.
  * Tidak ada klaim tanpa data. Kalau data tidak ada -> poin tidak dibuat,
    atau dibuat dengan nilai null + label "data tidak tersedia".
  * Tidak mengarang berita. Tidak memakai bahasa pasti ("akan naik").
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

REPO = "/root/idx-dashboard"
DATA_DIR = os.path.join(REPO, "data")
WIB = timezone(timedelta(hours=7))

BULAN_ID = {
    1: "Januari", 2: "Februari", 3: "Maret", 4: "April", 5: "Mei", 6: "Juni",
    7: "Juli", 8: "Agustus", 9: "September", 10: "Oktober", 11: "November",
    12: "Desember",
}


def _tgl_teks(iso: Optional[str]) -> str:
    if not iso:
        return "data tidak tersedia"
    try:
        d = datetime.strptime(str(iso)[:10], "%Y-%m-%d")
    except (ValueError, TypeError):
        return str(iso)
    return f"{d.day} {BULAN_ID[d.month]} {d.year}"


def _f(v: Any, nd: int = 2, suffix: str = "") -> str:
    if v is None:
        return "data tidak tersedia"
    try:
        return f"{float(v):.{nd}f}{suffix}"
    except (TypeError, ValueError):
        return str(v)


def _tanda(v: Optional[float], nd: int = 2) -> str:
    if v is None:
        return "data tidak tersedia"
    return f"{'+' if v >= 0 else ''}{float(v):.{nd}f}%"


def _rp(v: Any) -> str:
    if v is None:
        return "data tidak tersedia"
    try:
        n = float(v)
    except (TypeError, ValueError):
        return str(v)
    if abs(n) >= 1e12:
        return f"Rp{n/1e12:.2f} T"
    if abs(n) >= 1e9:
        return f"Rp{n/1e9:.2f} M"
    if abs(n) >= 1e6:
        return f"Rp{n/1e6:.1f} jt"
    return f"Rp{n:,.0f}".replace(",", ".")


# ---------------------------------------------------------------------------
# Poin 1: Breadth / kesehatan pasar
# ---------------------------------------------------------------------------
def _poin_breadth(insight: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    b = (insight or {}).get("breadth") or {}
    if not b.get("ok") or b.get("total_emiten") in (None, 0):
        return None

    naik, turun, tetap = b.get("naik"), b.get("turun"), b.get("tetap")
    total = b.get("total_emiten")
    pct_ma50 = b.get("pct_above_ma50")
    pct_ma200 = b.get("pct_above_ma200")

    narasi = (
        f"Dari {total} emiten, {naik} naik, {turun} turun, {tetap} tidak berubah. "
        f"Yang masih bertahan di atas MA50 hanya {pct_ma50}% dan di atas MA200 "
        f"{pct_ma200}% — artinya mayoritas harga sudah di bawah rata-rata "
        f"tren menengahnya."
    )

    tone = "netral"
    if pct_ma50 is not None:
        if pct_ma50 >= 60:
            tone = "positif"
        elif pct_ma50 <= 25:
            tone = "negatif"

    return {
        "id": "breadth",
        "judul": "Kesehatan pasar",
        "narasi": narasi,
        "angka": {
            "naik": naik, "turun": turun, "tetap": tetap,
            "pct_naik": b.get("pct_naik"),
            "pct_above_ma50": pct_ma50, "pct_above_ma200": pct_ma200,
            "median_volume_ratio": b.get("median_volume_ratio"),
        },
        "sumber": f"insight.json → breadth ({_tgl_teks(b.get('tanggal'))}), "
                  f"dihitung dari history 5 tahun yfinance",
        "tanggal": b.get("tanggal"),
        "tone": tone,
    }


# ---------------------------------------------------------------------------
# Poin 2: Rezim pasar
# ---------------------------------------------------------------------------
def _poin_rezim(insight: Dict[str, Any], external: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    r = (external or {}).get("rezim_pasar") or {}
    rezim = r.get("rezim")
    if not rezim:
        return None

    ih = (insight or {}).get("ihsg") or {}
    ret5 = r.get("ihsg_ret_5h_pct", ih.get("ret5"))
    ret20 = r.get("ihsg_ret_20h_pct", ih.get("ret20"))
    pemicu = r.get("pemicu") or []

    pemicu_teks = ""
    if pemicu:
        bagian = [f"{p.get('keterangan', p.get('faktor'))} ({_tanda(p.get('nilai_pct'))})"
                  for p in pemicu[:2]]
        pemicu_teks = " Pemicu: " + "; ".join(bagian) + "."

    label_rezim = {"stres": "STRES", "waspada": "WASPADA", "normal": "NORMAL"}.get(
        str(rezim).lower(), str(rezim).upper()
    )

    narasi = (
        f"Rezim pasar terdeteksi **{label_rezim}**. IHSG {_tanda(ret5)} dalam 5 sesi "
        f"dan {_tanda(ret20)} dalam 20 sesi.{pemicu_teks}"
    )

    return {
        "id": "rezim",
        "judul": "Rezim pasar",
        "narasi": narasi,
        "angka": {
            "rezim": rezim,
            "pasar_stres": r.get("pasar_stres"),
            "ihsg_ret_5h_pct": ret5,
            "ihsg_ret_20h_pct": ret20,
            "ihsg_terakhir": r.get("ihsg_terakhir"),
            "jumlah_pemicu": len(pemicu),
        },
        "sumber": "external.json → rezim_pasar (ambang: IHSG −5%/5 sesi, −10%/20 sesi)",
        "tanggal": (external or {}).get("tanggal_data"),
        "tone": "negatif" if r.get("pasar_stres") else "netral",
    }


# ---------------------------------------------------------------------------
# Poin 3: Rotasi sektor (dua sisi)
# ---------------------------------------------------------------------------
def _poin_rotasi(insight: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    rot = (insight or {}).get("rotasi_sektor") or []
    valid = [x for x in rot if x.get("excess20") is not None]
    if len(valid) < 2:
        return None

    kuat = valid[0]
    lemah = valid[-1]
    neg20 = sum(1 for x in valid if x.get("ret20") is not None and x["ret20"] < 0)
    total20 = sum(1 for x in valid if x.get("ret20") is not None)

    narasi = (
        f"Secara relatif terhadap IHSG dalam 20 sesi, sektor paling kuat adalah "
        f"**{kuat['sektor']}** ({_tanda(kuat['excess20'])} di atas IHSG, "
        f"{kuat['jumlah_emiten']} emiten), sedangkan paling lemah **{lemah['sektor']}** "
        f"({_tanda(lemah['excess20'])} di bawah IHSG, {lemah['jumlah_emiten']} emiten). "
    )
    if total20:
        narasi += (
            f"{neg20} dari {total20} sektor masih negatif dalam nominal 20 sesi — "
            f"yang unggul di sini unggul secara relatif, bukan absolut."
        )

    return {
        "id": "rotasi_sektor",
        "judul": "Rotasi sektor",
        "narasi": narasi,
        "angka": {
            "terkuat": {k: kuat.get(k) for k in ("sektor", "ret5", "ret20", "excess5", "excess20", "jumlah_emiten")},
            "terlemah": {k: lemah.get(k) for k in ("sektor", "ret5", "ret20", "excess5", "excess20", "jumlah_emiten")},
            "jumlah_sektor": len(valid),
        },
        "sumber": "insight.json → rotasi_sektor (median return sektor vs IHSG, history 5 tahun yfinance)",
        "tanggal": (insight or {}).get("breadth", {}).get("tanggal"),
        "tone": "netral",
    }


# ---------------------------------------------------------------------------
# Poin 4: Volume / partisipasi
# ---------------------------------------------------------------------------
def _poin_volume(insight: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    b = (insight or {}).get("breadth") or {}
    vr = b.get("median_volume_ratio")
    if vr is None:
        return None

    if vr >= 1.5:
        ket = "jauh di atas rata-rata (aktivitas tinggi)"
        tone = "positif"
    elif vr >= 1.05:
        ket = "sedikit di atas rata-rata"
        tone = "positif"
    elif vr >= 0.95:
        ket = "seperti biasa"
        tone = "netral"
    elif vr >= 0.6:
        ket = "di bawah rata-rata (partisipasi menurun)"
        tone = "negatif"
    else:
        ket = "jauh di bawah rata-rata (partisipasi tipis)"
        tone = "negatif"

    narasi = (
        f"Rasio volume harian terhadap rata-rata 20 sesi berada di {_f(vr, 2, '×')} "
        f"pada median — {ket}."
    )

    return {
        "id": "volume",
        "judul": "Partisipasi volume",
        "narasi": narasi,
        "angka": {"median_volume_ratio": vr},
        "sumber": "insight.json → breadth.median_volume_ratio (volume ÷ MA20 volume per emiten)",
        "tanggal": b.get("tanggal"),
        "tone": tone,
    }


# ---------------------------------------------------------------------------
# Poin 5: Makro (BI Rate + USD/IDR)
# ---------------------------------------------------------------------------
def _poin_makro(external: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    e = external or {}
    bi = e.get("bi_rate") or {}
    makro = e.get("makro") or {}
    usd = makro.get("usdidr") or {}
    inf = e.get("inflasi") or {}

    if bi.get("nilai_pct") is None:
        return None

    arah = bi.get("arah", 0)
    arah_teks = "tetap" if arah == 0 else ("naik" if arah > 0 else "turun")
    delta = bi.get("delta_pp")
    delta_teks = f" ({'+' if (delta or 0) > 0 else ''}{_f(delta, 2)} pp)" if delta else ""

    bagian = [f"BI Rate {_f(bi.get('nilai_pct'), 2, '%')} — {arah_teks}{delta_teks} "
              f"sejak {_tgl_teks((bi.get('sebelumnya') or {}).get('tanggal'))}"]

    if usd.get("nilai") is not None:
        bagian.append(
            f"USD/IDR {usd['nilai']:,.0f}".replace(",", ".")
            + f" ({_tanda(usd.get('chg_5h_pct'))} 5 sesi, {_tanda(usd.get('chg_20h_pct'))} 20 sesi)"
        )
    if inf.get("nilai_pct") is not None:
        bagian.append(f"inflasi {_f(inf.get('nilai_pct'), 2, '%')} YoY ({inf.get('periode')})")

    narasi = "; ".join(bagian) + "."

    tone = "netral"
    if usd.get("chg_20h_pct") is not None and usd["chg_20h_pct"] > 2:
        tone = "negatif"

    return {
        "id": "makro",
        "judul": "Kondisi makro",
        "narasi": narasi,
        "angka": {
            "bi_rate_pct": bi.get("nilai_pct"),
            "bi_rate_arah": arah_teks,
            "bi_rate_delta_pp": delta,
            "usdidr": usd.get("nilai"),
            "usdidr_chg_5h_pct": usd.get("chg_5h_pct"),
            "usdidr_chg_20h_pct": usd.get("chg_20h_pct"),
            "inflasi_pct": inf.get("nilai_pct"),
            "inflasi_periode": inf.get("periode"),
        },
        "sumber": "external.json → bi_rate (bi.go.id), makro.usdidr (yfinance), inflasi (BPS)",
        "tanggal": e.get("tanggal_data"),
        "tone": tone,
    }


# ---------------------------------------------------------------------------
# Poin 6: Skor screen + Top 10
# ---------------------------------------------------------------------------
def _poin_skor(latest: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    rows = (latest or {}).get("emiten") or []
    berskor = [r for r in rows if r.get("skor") is not None]
    if not berskor:
        return None

    top10 = (latest or {}).get("top10") or []
    wr = (latest or {}).get("waspada_ringkasan") or {}
    per_tingkat = wr.get("per_tingkat") or {}

    puncak = max(berskor, key=lambda r: r["skor"])
    narasi = (
        f"Skor tertinggi hari ini {puncak['kode']} ({_f(puncak.get('skor'), 1)}/100). "
        f"{len(berskor)} dari {len(rows)} emiten punya skor lengkap"
    )
    if top10:
        narasi += f", dan {len(top10)} emiten masuk Top 10"
    narasi += ". "

    if per_tingkat:
        tinggi = per_tingkat.get("tinggi", 0)
        ringan = per_tingkat.get("ringan", 0)
        narasi += (
            f"Waspada: {tinggi} emiten kena minimal satu kriteria tingkat tinggi "
            f"dan {ringan} emiten hanya tingkat ringan. Waspada tingkat tinggi "
            f"mendiskualifikasi emiten dari Top 10."
        )

    return {
        "id": "skor",
        "judul": "Hasil screening",
        "narasi": narasi,
        "angka": {
            "skor_tertinggi": puncak.get("skor"),
            "kode_tertinggi": puncak.get("kode"),
            "jumlah_berskor": len(berskor),
            "jumlah_total": len(rows),
            "jumlah_top10": len(top10),
            "waspada_tinggi": per_tingkat.get("tinggi"),
            "waspada_ringan": per_tingkat.get("ringan"),
        },
        "sumber": "latest.json → emiten[].skor, top10, waspada_ringkasan",
        "tanggal": (latest or {}).get("tanggal_data"),
        "tone": "netral",
    }


# ---------------------------------------------------------------------------
# Poin 7: Event risiko
# ---------------------------------------------------------------------------
def _poin_event(external: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    ev = (external or {}).get("event_risiko") or {}
    items = ev.get("events") or []
    if not items:
        return None

    teratas = items[0]
    umur = teratas.get("umur_hari")
    narasi = (
        f"Faktor risiko aktif: {teratas.get('judul')} "
        f"(status: {teratas.get('status')}, diumumkan {_tgl_teks(teratas.get('tanggal_diumumkan'))}"
        + (f", {umur} hari lalu" if umur is not None else "") + "). "
        f"Catatan verifikasi: {teratas.get('catatan_verifikasi', 'tidak ada')}"
    )

    return {
        "id": "event_risiko",
        "judul": "Faktor risiko",
        "narasi": narasi,
        "angka": {
            "jumlah_event": len(items),
            "judul": teratas.get("judul"),
            "status": teratas.get("status"),
            "tanggal_diumumkan": teratas.get("tanggal_diumumkan"),
            "umur_hari": umur,
            "arah": teratas.get("arah"),
        },
        "sumber": f"{teratas.get('sumber_nama', 'tidak tersedia')} → {teratas.get('sumber_url', '')}",
        "tautan": teratas.get("sumber_url"),
        "tautan_alternatif": teratas.get("sumber_url_alternatif") or [],
        "tanggal": (external or {}).get("tanggal_data"),
        "tone": "negatif",
    }


# ---------------------------------------------------------------------------
# Kalender event mendatang
# ---------------------------------------------------------------------------
def build_kalender(external: Dict[str, Any], hari_ke_depan: int = 120) -> Dict[str, Any]:
    """
    Gabungkan event mendatang dari kalender_bi, event_risiko, kalender_emiten.
    Hanya yang tanggalnya >= hari ini.
    """
    e = external or {}
    hari_ini = datetime.now(WIB).strftime("%Y-%m-%d")
    batas = (datetime.now(WIB) + timedelta(days=hari_ke_depan)).strftime("%Y-%m-%d")

    out: List[Dict[str, Any]] = []

    # RDG BI
    for ev in (e.get("kalender_bi") or {}).get("events") or []:
        tgl = ev.get("tanggal")
        if tgl and hari_ini <= tgl <= batas:
            out.append({
                "tanggal": tgl,
                "judul": ev.get("judul"),
                "jenis": "RDG BI",
                "sumber": ev.get("sumber"),
                "url": ev.get("url"),
            })

    # Event risiko (MSCI dll) — pakai tanggal diumumkan sebagai penanda, bukan tanggal event
    for ev in (e.get("event_risiko") or {}).get("events") or []:
        tgl = ev.get("tanggal_diumumkan")
        if tgl and hari_ini <= tgl <= batas:
            out.append({
                "tanggal": tgl,
                "judul": ev.get("judul"),
                "jenis": "Event risiko",
                "sumber": ev.get("sumber_nama"),
                "url": ev.get("sumber_url"),
                "catatan": ev.get("catatan_verifikasi"),
            })

    # Laporan emiten
    for ev in (e.get("kalender_emiten") or {}).get("events") or []:
        tgl = ev.get("tanggal")
        if tgl and hari_ini <= tgl <= batas:
            out.append({
                "tanggal": tgl,
                "judul": f"{ev.get('kode')} — {str(ev.get('jenis', '')).replace('_', ' ')}",
                "jenis": "Laporan emiten",
                "sumber": ev.get("sumber"),
                "url": (e.get("kalender_emiten") or {}).get("url"),
                "dapat_diverifikasi": ev.get("dapat_diverifikasi"),
            })

    out.sort(key=lambda x: x["tanggal"])
    return {
        "hari_ini": hari_ini,
        "batas_hari": hari_ke_depan,
        "jumlah": len(out),
        "events": out,
        "catatan": (
            "Kalender hanya memuat event yang tanggalnya tersedia di sumber otomatis. "
            "RUPS dan cum-date resmi IDX tidak tersedia karena idx.co.id memblokir akses otomatis."
        ),
    }


# ---------------------------------------------------------------------------
# Edukasi
# ---------------------------------------------------------------------------
EDUKASI = [
    {
        "istilah": "RSI (Relative Strength Index)",
        "singkat": "Ukur kecepatan & besaran perubahan harga, skala 0–100.",
        "detail": (
            "RSI di atas 70 biasanya disebut jenuh beli (harga naik terlalu cepat), "
            "di bawah 30 jenuh jual. Dashboard memakai RSI(14): 14 sesi terakhir. "
            "RSI bukan sinyal beli/jual tunggal — dia hanya menggambarkan kecepatan gerak."
        ),
        "kapan_relevan": "saat banyak emiten RSI >70 atau <30",
    },
    {
        "istilah": "Breakout",
        "singkat": "Harga menembus level resistance (batas atas) yang sudah bertahan lama.",
        "detail": (
            "Breakout dianggap lebih meyakinkan kalau disertai volume di atas rata-rata. "
            "Breakout tanpa volume (di dashboard ditandai waspada ringan) lebih sering gagal "
            "— harga kembali masuk ke rentang."
        ),
        "kapan_relevan": "saat jumlah breakout meningkat",
    },
    {
        "istilah": "Trading halt",
        "singkat": "Penghentian sementara perdagangan satu saham oleh bursa.",
        "detail": (
            "Bisa karena lonjakan/turun harga ekstrem, permintaan regulator, atau "
            "pengumuman penting. Saat halt, harga tidak bergerak dan order tidak bisa dieksekusi. "
            "Dashboard hanya menandai halt kalau ada bukti; tidak menebak."
        ),
        "kapan_relevan": "saat ada emiten dengan pergerakan ekstrem",
    },
    {
        "istilah": "Breadth pasar",
        "singkat": "Seberapa banyak saham ikut bergerak, bukan hanya indeksnya.",
        "detail": (
            "Indeks bisa naik walau mayoritas saham turun — karena saham berkapitalisasi besar "
            "mendorong indeks. Breadth (% saham di atas MA50/MA200, jumlah naik vs turun) "
            "menunjukkan kondisi sebenarnya di lapisan bawah."
        ),
        "kapan_relevan": "selalu, terutama saat indeks dan breadth berbeda arah",
    },
    {
        "istilah": "MA50 / MA200",
        "singkat": "Rata-rata harga penutupan 50 dan 200 sesi terakhir.",
        "detail": (
            "MA50 menggambarkan tren menengah, MA200 tren jangka panjang. Harga di atas MA200 "
            "umumnya dianggap tren naik sehat. Persentase emiten di atas MA200 adalah salah satu "
            "ukuran kekuatan pasar paling sering dipakai."
        ),
        "kapan_relevan": "saat % di atas MA200 berubah tajam",
    },
    {
        "istilah": "MSCI reklasifikasi",
        "singkat": "Perubahan klasifikasi pasar oleh MSCI (mis. Emerging → Standalone).",
        "detail": (
            "Kalau Indonesia turun kelas, dana asing yang mengikuti indeks MSCI berpotensi keluar. "
            "Status resmi harus dicek ke msci.com. Dashboard menandai ini sebagai faktor risiko, "
            "bukan memprediksi dampaknya ke harga."
        ),
        "kapan_relevan": "saat ada pengumuman/konsultasi MSCI",
    },
]


def pilih_edukasi(insight: Dict[str, Any], external: Dict[str, Any],
                  latest: Dict[str, Any], maks: int = 3) -> List[Dict[str, Any]]:
    """
    Pilih kartu edukasi yang relevan dengan kondisi data hari ini (deterministik).
    """
    dipilih: List[str] = []
    b = (insight or {}).get("breadth") or {}
    wr = (latest or {}).get("waspada_ringkasan") or {}
    per_kriteria = wr.get("per_kriteria") or {}
    rot = (insight or {}).get("rotasi_sektor") or []

    # breadth selalu relevan kalau data ada
    if b.get("ok"):
        dipilih.append("Breadth pasar")

    # MA200 relevan kalau persentasenya rendah/tinggi ekstrem
    pct200 = b.get("pct_above_ma200")
    if pct200 is not None and (pct200 <= 30 or pct200 >= 70):
        dipilih.append("MA50 / MA200")

    # RSI relevan kalau ada kriteria rsi jenuh
    if (per_kriteria.get("rsi jenuh") or {}).get("jumlah"):
        dipilih.append("RSI (Relative Strength Index)")

    # Breakout relevan kalau ada kriteria breakout
    if ((per_kriteria.get("breakout tanpa volume") or {}).get("jumlah") or
            (per_kriteria.get("lonjakan") or {}).get("jumlah")):
        dipilih.append("Breakout")

    # MSCI relevan kalau ada event risiko
    if ((external or {}).get("event_risiko") or {}).get("events"):
        dipilih.append("MSCI reklasifikasi")

    # Rotasi sektor -> breadth (sebagai konteks) kalau belum ada
    if rot and len(dipilih) < maks and "Breadth pasar" not in dipilih:
        dipilih.append("Breadth pasar")

    out = []
    for nama in dipilih:
        for e in EDUKASI:
            if e["istilah"] == nama:
                out.append(e)
                break
        if len(out) >= maks:
            break
    return out


# ---------------------------------------------------------------------------
# Bangun payload lengkap
# ---------------------------------------------------------------------------
def build(insight: Dict[str, Any], latest: Dict[str, Any],
          external: Dict[str, Any]) -> Dict[str, Any]:
    """
    Hasilkan payload Tab Insight lengkap: ringkasan poin, kalender, edukasi.
    """
    poin: List[Dict[str, Any]] = []

    # Urutan naratif: kondisi hari ini -> rezim -> rotasi -> makro -> risiko -> skor
    for item in (
        _poin_breadth(insight),
        _poin_rezim(insight, external),
        _poin_rotasi(insight),
        _poin_volume(insight),
        _poin_makro(external),
        _poin_event(external),
        _poin_skor(latest),
    ):
        if item:
            poin.append(item)

    kalender = build_kalender(external)
    edukasi = pilih_edukasi(insight, external, latest)

    return {
        "versi_skema": "1.0.0",
        "dibuat_pada": datetime.now(WIB).isoformat(),
        "jumlah_poin": len(poin),
        "ringkasan": poin,
        "kalender": kalender,
        "edukasi": edukasi,
        "disclaimer": (
            "Semua angka dihitung dari data historis. Ini bukan rekomendasi beli/jual. "
            "Tidak ada target harga tunggal dan tidak ada klaim kepastian arah harga."
        ),
    }


def simpan(payload: Dict[str, Any], path: Optional[str] = None) -> str:
    path = path or os.path.join(DATA_DIR, "insight_ringkas.json")
    tmp = f"{path}.tmp.{os.getpid()}"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, separators=(",", ":"))
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)
    return path


if __name__ == "__main__":
    base = os.path.dirname(DATA_DIR)
    insight = json.load(open(os.path.join(DATA_DIR, "insight.json")))
    latest = json.load(open(os.path.join(DATA_DIR, "latest.json")))
    external = json.load(open(os.path.join(DATA_DIR, "external.json")))
    res = build(insight, latest, external)
    p = simpan(res)
    print(f"Disimpan: {p}")
    print(json.dumps(res, ensure_ascii=False, indent=1)[:4000])