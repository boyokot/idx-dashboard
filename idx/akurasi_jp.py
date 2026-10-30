#!/usr/bin/env python3
"""
J4 — snapshot bulanan Skor JP dan pengukuran hasil 3/6/12 bulan.

Prinsip:
- Hanya memakai data yang benar-benar sudah diterbitkan pada snapshot.
- Tidak mengisi harga/skor yang hilang dengan 0.
- Tidak mengklaim backtest. Ini pemantauan hasil setelah snapshot nyata.
- Snapshot bulanan pertama tidak ditimpa oleh run berikutnya pada bulan yang
  sama, supaya harga awal tidak bergeser tanpa jejak.
"""
from __future__ import annotations

import datetime as dt
import json
import math
import os
import statistics
from pathlib import Path
from typing import Any, Dict, List, Optional

BASE = Path(__file__).resolve().parent.parent
DATA_DIR = BASE / "data"
SNAP_DIR = DATA_DIR / "snapshots_jp"
INDEX_FILE = SNAP_DIR / "index.json"
AKURASI_FILE = DATA_DIR / "akurasi_jp.json"
VERSI = "1.0.0"
HORIZON = (3, 6, 12)


def _now_wib() -> dt.datetime:
    return dt.datetime.now(dt.timezone(dt.timedelta(hours=7)))


def _stamp() -> str:
    return _now_wib().isoformat(timespec="seconds")


def _baca_json(path: Path) -> Optional[Dict[str, Any]]:
    try:
        with path.open(encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def _tulis_atomic(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=1, allow_nan=False)
        f.write("\n")
    os.replace(tmp, path)


def _bersih(v: Any) -> Any:
    """JSON aman: NaN/Infinity tidak boleh masuk file."""
    if isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
        return None
    if isinstance(v, dict):
        return {str(k): _bersih(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [_bersih(x) for x in v]
    return v


def _bulan(tanggal: str) -> Optional[str]:
    s = str(tanggal or "")[:10]
    if len(s) < 7 or s[4] != "-" or s[7:8] not in ("", "-"):
        return None
    try:
        dt.date.fromisoformat(s)
    except ValueError:
        return None
    return s[:7]


def _tambah_bulan(bulan: str, n: int) -> str:
    y, m = (int(x) for x in bulan.split("-"))
    total = y * 12 + (m - 1) + int(n)
    return f"{total // 12:04d}-{total % 12 + 1:02d}"


def _angka(v: Any) -> Optional[float]:
    if v is None or isinstance(v, bool):
        return None
    try:
        n = float(v)
    except (TypeError, ValueError):
        return None
    if math.isnan(n) or math.isinf(n):
        return None
    return n


def _harga(v: Any) -> Optional[float]:
    n = _angka(v)
    return n if n is not None and n > 0 else None


def _ringkas_index() -> Dict[str, Any]:
    files = sorted(p.name for p in SNAP_DIR.glob("????-??.json")
                   if p.is_file())
    return {
        "files": files,
        "jumlah": len(files),
        "terlama": files[0][:7] if files else None,
        "terbaru": files[-1][:7] if files else None,
        "diperbarui_pada": _stamp(),
    }


def bangun_index(tulis: bool = True) -> Dict[str, Any]:
    info = _ringkas_index()
    if tulis:
        _tulis_atomic(INDEX_FILE, info)
    return info


def simpan_snapshot_bulanan(payload: Dict[str, Any],
                            tulis: bool = True) -> Dict[str, Any]:
    """Simpan satu snapshot per bulan data. Run berikutnya tidak menimpa."""
    tanggal = str(payload.get("tanggal_data") or "")
    bulan = _bulan(tanggal)
    if not bulan:
        return {"status": "dilewati", "alasan": "tanggal_data tidak valid"}

    rows: List[Dict[str, Any]] = []
    for r in payload.get("emiten") or []:
        if r.get("skor_jp") is None:
            continue
        rows.append({
            "kode": r.get("kode"),
            "nama": r.get("nama"),
            "sektor": r.get("sektor"),
            "skor_jp": _angka(r.get("skor_jp")),
            "peringkat_jp": r.get("peringkat_jp"),
            "diperingkat": bool(r.get("diperingkat")),
            "lolos_gerbang": bool(r.get("lolos_gerbang")),
            "cakupan_bobot": _angka(r.get("cakupan_bobot")),
            "harga": _harga(r.get("harga")),
        })
    if not rows:
        return {"status": "kosong", "bulan": bulan,
                "alasan": "tidak ada emiten dengan skor JP"}

    ihsg_raw = ((payload.get("ringkasan_pasar") or {}).get("ihsg") or {})
    ihsg = {
        "nilai": _angka(ihsg_raw.get("nilai")),
        "tersedia": bool(ihsg_raw.get("tersedia")) and _angka(ihsg_raw.get("nilai")) is not None,
    }
    snap = {
        "versi": VERSI,
        "bulan": bulan,
        "tanggal_data": tanggal,
        "diperbarui_pada": _stamp(),
        "ihsg": ihsg,
        "jumlah_emiten": len(rows),
        "jumlah_diperingkat": sum(1 for r in rows if r.get("diperingkat")),
        "sumber": "latest.json (J2 skor_jp)",
        "emiten": rows,
    }
    snap = _bersih(snap)
    path = SNAP_DIR / f"{bulan}.json"
    if path.exists():
        info = bangun_index(tulis=tulis)
        return {"status": "sudah_ada", "bulan": bulan, "file": path.name,
                "index": info}
    if tulis:
        _tulis_atomic(path, snap)
    info = bangun_index(tulis=tulis)
    return {"status": "ditulis", "bulan": bulan, "file": path.name,
            "jumlah": len(rows), "index": info}


def _baca_snapshots() -> Dict[str, Dict[str, Any]]:
    out: Dict[str, Dict[str, Any]] = {}
    for p in sorted(SNAP_DIR.glob("????-??.json")):
        j = _baca_json(p)
        if not isinstance(j, dict):
            continue
        bulan = str(j.get("bulan") or p.name[:7])
        if len(bulan) != 7:
            continue
        out[bulan] = j
    return out


def _top20(snap: Dict[str, Any]) -> List[Dict[str, Any]]:
    rows = [r for r in (snap.get("emiten") or [])
            if r.get("diperingkat") and r.get("peringkat_jp") is not None]
    rows.sort(key=lambda r: (r.get("peringkat_jp") or 10**9))
    return rows[:20]


def _hasil_satu(snap_awal: Dict[str, Any], snap_akhir: Dict[str, Any],
                horizon: int) -> Dict[str, Any]:
    top = _top20(snap_awal)
    akhir = {str(r.get("kode")): r for r in (snap_akhir.get("emiten") or [])
             if r.get("kode")}
    rets: List[float] = []
    sektor_pick: List[float] = []
    sektor_semua: Dict[str, List[float]] = {}
    n_harga = 0
    for r in (snap_awal.get("emiten") or []):
        p0 = _harga(r.get("harga"))
        r2 = akhir.get(str(r.get("kode")))
        p1 = _harga((r2 or {}).get("harga"))
        if p0 is None or p1 is None:
            continue
        ret = (p1 / p0 - 1.0) * 100.0
        sektor = str(r.get("sektor") or "")
        if sektor:
            sektor_semua.setdefault(sektor, []).append(ret)
        n_harga += 1
    for r in top:
        p0 = _harga(r.get("harga"))
        p1 = _harga((akhir.get(str(r.get("kode"))) or {}).get("harga"))
        if p0 is not None and p1 is not None:
            rets.append((p1 / p0 - 1.0) * 100.0)
    # Rata-rata sektor memakai minimal 3 emiten pada snapshot awal.
    bench = {s: statistics.fmean(v) for s, v in sektor_semua.items() if len(v) >= 3}
    for r in top:
        s = str(r.get("sektor") or "")
        if s in bench:
            sektor_pick.append(bench[s])

    ih0 = _harga((snap_awal.get("ihsg") or {}).get("nilai"))
    ih1 = _harga((snap_akhir.get("ihsg") or {}).get("nilai"))
    ihsg_ret = ((ih1 / ih0 - 1.0) * 100.0) if ih0 and ih1 else None
    porto = statistics.fmean(rets) if rets else None
    med = statistics.median(rets) if rets else None
    sektor_avg = statistics.fmean(sektor_pick) if sektor_pick else None
    excess_ihsg = (porto - ihsg_ret) if porto is not None and ihsg_ret is not None else None
    excess_sektor = (porto - sektor_avg) if porto is not None and sektor_avg is not None else None
    catatan: List[str] = []
    if len(rets) < len(top):
        catatan.append(f"Hanya {len(rets)} dari {len(top)} emiten Top 20 yang punya harga awal dan akhir.")
    if len(sektor_pick) < len(rets):
        catatan.append(f"Pembanding sektor tersedia untuk {len(sektor_pick)} dari {len(rets)} emiten (minimal 3 emiten per sektor).")
    if len(rets) < 10:
        catatan.append("Sampel sangat kecil; hasil belum bisa digeneralisasi.")
    return {
        "mulai": snap_awal.get("bulan"),
        "selesai": snap_akhir.get("bulan"),
        "horizon_bulan": horizon,
        "tanggal_mulai": snap_awal.get("tanggal_data"),
        "tanggal_selesai": snap_akhir.get("tanggal_data"),
        "status": "ok" if rets else "tanpa_harga",
        "n_top20": len(top),
        "n_top20_berharga": len(rets),
        "n_sektor": len(sektor_pick),
        "n_emiten_harga": n_harga,
        "top20_return_pct": porto,
        "top20_median_pct": med,
        "ihsg_return_pct": ihsg_ret,
        "sektor_return_pct": sektor_avg,
        "excess_ihsg_pct": excess_ihsg,
        "excess_sektor_pct": excess_sektor,
        "catatan": catatan,
    }


def hitung_akurasi(tulis: bool = True) -> Dict[str, Any]:
    snaps = _baca_snapshots()
    bulan_list = sorted(snaps)
    hasil: List[Dict[str, Any]] = []
    pending: List[Dict[str, Any]] = []
    for bulan in bulan_list:
        for h in HORIZON:
            target = _tambah_bulan(bulan, h)
            if target not in snaps:
                pending.append({
                    "mulai": bulan, "horizon_bulan": h,
                    "target": target, "status": "belum_ada",
                    "alasan": f"snapshot {target} belum tersedia",
                })
                continue
            hasil.append(_hasil_satu(snaps[bulan], snaps[target], h))

    ringkasan: List[Dict[str, Any]] = []
    for h in HORIZON:
        rows = [x for x in hasil if x.get("horizon_bulan") == h and x.get("status") == "ok"]
        if not rows:
            continue
        def _avg(key: str) -> Optional[float]:
            vals = [x.get(key) for x in rows if x.get(key) is not None]
            return statistics.fmean(vals) if vals else None
        ringkasan.append({
            "horizon_bulan": h,
            "jumlah_periode": len(rows),
            "top20_return_pct": _avg("top20_return_pct"),
            "ihsg_return_pct": _avg("ihsg_return_pct"),
            "sektor_return_pct": _avg("sektor_return_pct"),
            "excess_ihsg_pct": _avg("excess_ihsg_pct"),
            "excess_sektor_pct": _avg("excess_sektor_pct"),
            "n_top20_berharga": sum(int(x.get("n_top20_berharga") or 0) for x in rows),
        })

    info_snap = _ringkas_index()
    if not info_snap["jumlah"]:
        status = "belum_ada"
    elif not hasil:
        status = "belum_cukup_data"
    else:
        status = "ok"
    catatan = [
        "Ini bukan backtest: return dihitung dari snapshot bulanan yang benar-benar diterbitkan lebih dulu, bukan dari data masa depan.",
        "Top 20 = peringkat_jp 1–20 pada snapshot awal; harga awal/akhir adalah close pada tanggal data snapshot.",
        "Pembanding IHSG memakai level IHSG pada snapshot yang sama. Rata-rata sektor = rata-rata return emiten satu sektor pada snapshot; minimal 3 emiten.",
        "Tidak ada klaim akurasi sebelum horizon 3/6/12 bulan benar-benar tersedia. Sampel kecil tidak bisa digeneralisasi.",
        "Harga snapshot bukan harga eksekusi; biaya transaksi, slippage, dan dividen tidak dihitung.",
    ]
    payload = {
        "versi": VERSI,
        "dihitung_pada": _stamp(),
        "status": status,
        "snapshot": info_snap,
        "horizon_bulan": list(HORIZON),
        "hasil": _bersih(hasil),
        "pending": _bersih(pending),
        "ringkasan": _bersih(ringkasan),
        "catatan": catatan,
        "metode": "snapshot bulanan -> return Top 20 vs IHSG dan rata-rata sektor; hanya pasangan bulan yang sudah ada",
    }
    if tulis:
        _tulis_atomic(AKURASI_FILE, payload)
    return payload


def main() -> int:
    latest = _baca_json(DATA_DIR / "latest.json")
    if not isinstance(latest, dict):
        print("[akurasi_jp] latest.json tidak terbaca")
        return 1
    s = simpan_snapshot_bulanan(latest)
    a = hitung_akurasi(tulis=True)
    print(f"[akurasi_jp] snapshot: {s}")
    print(f"[akurasi_jp] status: {a['status']} | hasil: {len(a['hasil'])} | pending: {len(a['pending'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
