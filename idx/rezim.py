"""
Deteksi rezim pasar — sumber tunggal untuk pipeline & data eksternal.

Rezim pasar memberi konteks: saat pasar sedang stres, skor saham tidak
boleh dibaca sebagai "normal". Karena itu rezim ini:
  1. disimpan di external.json (rezim_pasar),
  2. ditempel ke payload latest.json (konteks_pasar) sebagai flag skor
     "pasar stres",
  3. ditampilkan sebagai banner di dashboard.

Prinsip: TIDAK MENYEMBUNYIKAN data. Semua angka pemicu disimpan, ambang
dipublikasikan, dan kalau datanya tidak cukup rezim = "data tidak
tersedia" (bukan "normal" — itu menyesatkan).

Fungsi ini MURNI (tidak ada jaringan) supaya bisa diuji.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

TIDAK_ADA = "data tidak tersedia"

# Ambang ditetapkan eksplisit agar bisa diaudit, bukan angka tersembunyi.
AMBANG_REZIM: Dict[str, float] = {
    "ihsg_turun_5h_pct": -5.0,    # IHSG turun > 5% dalam 5 sesi
    "ihsg_turun_1h_pct": -3.0,    # atau turun > 3% dalam 1 sesi
    "ihsg_turun_20h_pct": -10.0,  # atau > 10% dalam 20 sesi
}

# Bukti trading halt harus berupa catatan BERTAUTAN. Tanpa sumber yang
# bisa dibaca otomatis, halt TIDAK diklaim (lebih baik "tidak ada bukti"
# daripada klaim palsu).
EVENT_HALT: List[Dict[str, Any]] = []


def _angka(v: Any) -> Optional[float]:
    try:
        if v is None or v == "":
            return None
        return float(v)
    except (TypeError, ValueError):
        return None


def _ret_seri(close: List[float], n: int) -> Optional[float]:
    """Return close->close atas n sesi terakhir (persen)."""
    if len(close) < n + 1:
        return None
    awal, akhir = close[-n - 1], close[-1]
    if not awal:
        return None
    return (akhir / awal - 1) * 100


def deteksi_rezim(ihsg_rows: List[Dict[str, Any]],
                  event_halt: Optional[List[Dict[str, Any]]] = None
                  ) -> Dict[str, Any]:
    """Klasifikasi rezim pasar dari deret close IHSG.

    Argumen:
      ihsg_rows  : [{date, close, ...}, ...] urutan kronologis
      event_halt : catatan trading halt bertautan (opsional)

    Keluaran (selalu punya kunci yang sama, apa pun kondisinya):
      rezim          : "stres" | "normal" | "positif" | "data tidak tersedia"
      pasar_stres    : True/False/None (None = tidak bisa dinilai)
      pemicu         : daftar angka pemicu (aman ditampilkan)
      trading_halt   : status + bukti
      ambang         : ambang yang dipakai (untuk audit)
    """
    if event_halt is None:
        event_halt = EVENT_HALT

    close = [_angka(r.get("close")) for r in (ihsg_rows or [])]
    close = [c for c in close if c is not None]
    if len(close) < 6:
        return {
            "rezim": TIDAK_ADA,
            "pasar_stres": None,
            "alasan": f"butuh >= 6 bar IHSG, tersedia {len(close)}",
            "pemicu": [],
            "ihsg_ret_1h_pct": None,
            "ihsg_ret_5h_pct": None,
            "ihsg_ret_20h_pct": None,
            "ihsg_terakhir": None,
            "trading_halt": {"terdeteksi": False, "bukti": [],
                             "alasan": "tidak dievaluasi (data IHSG kurang)"},
            "ambang": dict(AMBANG_REZIM),
            "sumber": "^JKSE (yfinance)",
            "catatan": ("Rezim tidak bisa dinilai dengan data kurang dari 6 bar "
                        "IHSG; sengaja TIDAK di-default 'normal'."),
        }

    r1 = _ret_seri(close, 1)
    r5 = _ret_seri(close, 5)
    r20 = _ret_seri(close, 20)
    pemicu: List[Dict[str, Any]] = []

    def cek(nama: str, nilai: Optional[float], ambang: float, label: str) -> None:
        if nilai is None:
            return
        if nilai <= ambang:
            pemicu.append({
                "faktor": nama,
                "nilai_pct": round(nilai, 3),
                "ambang_pct": ambang,
                "keterangan": label,
            })

    cek("ihsg_5h", r5, AMBANG_REZIM["ihsg_turun_5h_pct"],
        "IHSG turun lebih dari 5% dalam 5 sesi")
    cek("ihsg_1h", r1, AMBANG_REZIM["ihsg_turun_1h_pct"],
        "IHSG turun lebih dari 3% dalam 1 sesi")
    cek("ihsg_20h", r20, AMBANG_REZIM["ihsg_turun_20h_pct"],
        "IHSG turun lebih dari 10% dalam 20 sesi")

    # Trading halt hanya diklaim kalau ada bukti eksplisit & bertautan.
    halt: Dict[str, Any] = {"terdeteksi": False, "bukti": [], "alasan": TIDAK_ADA}
    if event_halt:
        halt["terdeteksi"] = True
        halt["bukti"] = list(event_halt)
        halt["alasan"] = "ada catatan trading halt"
        pemicu.append({"faktor": "trading_halt", "nilai_pct": None,
                       "ambang_pct": None,
                       "keterangan": "trading halt tercatat"})
    else:
        halt["alasan"] = ("tidak ada sumber resmi trading halt yang bisa "
                          "dibaca otomatis; tidak diklaim tanpa bukti")

    if pemicu:
        rezim = "stres"
    elif r5 is not None and r5 > 0:
        rezim = "positif"
    else:
        rezim = "normal"

    return {
        "rezim": rezim,
        "pasar_stres": len(pemicu) > 0,
        "ihsg_ret_1h_pct": None if r1 is None else round(r1, 3),
        "ihsg_ret_5h_pct": None if r5 is None else round(r5, 3),
        "ihsg_ret_20h_pct": None if r20 is None else round(r20, 3),
        "ihsg_terakhir": round(close[-1], 2),
        "pemicu": pemicu,
        "trading_halt": halt,
        "ambang": dict(AMBANG_REZIM),
        "sumber": "^JKSE (yfinance)",
        "catatan": ("Rezim 'stres' menandai skor sebagai 'pasar stres'; angka "
                    "pemicu tetap disimpan dan ditampilkan, tidak "
                    "disembunyikan."),
    }


def ringkas_konteks(rezim: Dict[str, Any]) -> Dict[str, Any]:
    """Ringkasan rezim untuk ditempel ke payload latest.json."""
    if not isinstance(rezim, dict) or not rezim.get("rezim"):
        return {"rezim": TIDAK_ADA, "pasar_stres": None,
                "label": "data tidak tersedia",
                "pemicu": [], "alasan": "rezim tidak dihitung"}
    stres = rezim.get("pasar_stres")
    return {
        "rezim": rezim.get("rezim"),
        "pasar_stres": stres,
        "label": ("pasar stres" if stres else
                  ("rezim " + str(rezim.get("rezim")))),
        "pemicu": rezim.get("pemicu") or [],
        "ihsg_ret_5h_pct": rezim.get("ihsg_ret_5h_pct"),
        "ihsg_ret_20h_pct": rezim.get("ihsg_ret_20h_pct"),
        "ambang": rezim.get("ambang") or {},
        "catatan": ("Skor TIDAK diubah otomatis karena rezim stres; angka "
                    "skor tetap apa adanya dan konteksnya ditampilkan "
                    "terpisah."),
    }
