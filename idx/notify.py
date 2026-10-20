"""
Notifikasi Telegram untuk IDX screener.

Aturan anti-spam (sesuai permintaan):
  * Maksimal SATU pesan ringkasan harian (dikirim saat pipeline selesai).
  * Ditambah alert penting saja: breakout bervolume besar, tembus support,
    dan berita penting — itulah isi ringkasan, jadi tidak dikirim terpisah
    per emiten.
  * Kalau tidak ada apa-apa yang penting, tidak kirim apa pun.

Token & chat id dari environment:
  TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
Tidak pernah ditulis ke kode/repo.
"""

from __future__ import annotations

import json
import os
import urllib.parse
import urllib.request
from typing import Any, Dict, List


def _token() -> str:
    return (os.environ.get("TELEGRAM_BOT_TOKEN")
            or os.environ.get("IDX_TELEGRAM_TOKEN") or "").strip()


def _chat() -> str:
    return (os.environ.get("TELEGRAM_CHAT_ID")
            or os.environ.get("IDX_TELEGRAM_CHAT") or "").strip()


def tersedia() -> bool:
    return bool(_token() and _chat())


def _kirim(teks: str) -> bool:
    tok, chat = _token(), _chat()
    if not tok or not chat:
        return False
    url = f"https://api.telegram.org/bot{tok}/sendMessage"
    data = urllib.parse.urlencode({
        "chat_id": chat,
        "text": teks,
        "parse_mode": "HTML",
        "disable_web_page_preview": "true",
    }).encode()
    try:
        req = urllib.request.Request(url, data=data, method="POST")
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.status == 200
    except Exception:
        return False


def _fmt(v: Any, d: int = 0) -> str:
    if v is None:
        return "–"
    try:
        s = f"{float(v):,.{d}f}"
        return s.replace(",", "X").replace(".", ",").replace("X", ".")
    except (TypeError, ValueError):
        return "–"


def _pct(v: Any) -> str:
    if v is None:
        return "–"
    try:
        f = float(v)
        return ("+" if f > 0 else "") + f"{f:.2f}".replace(".", ",") + "%"
    except (TypeError, ValueError):
        return "–"


def bangun_pesan(payload: Dict[str, Any], watchlist: List[str] | None = None) -> str | None:
    """
    Susun SATU pesan ringkasan. Kembalikan None kalau tidak ada yang layak
    dikirim (supaya tidak spam).
    """
    rows = payload.get("emiten") or []
    if not rows:
        return None
    wl = {w.upper() for w in (watchlist or [])}
    mp = payload.get("ringkasan_pasar") or {}
    tm = payload.get("top10") or []

    breakout = [r for r in rows if r.get("sinyal") == "Breakout" and not r.get("waspada")]
    tembus = [r for r in rows if r.get("sinyal") == "Tembus Support"]
    wl_break = [r for r in breakout if r["kode"] in wl]
    wl_tembus = [r for r in tembus if r["kode"] in wl]

    # kalau tidak ada sinyal penting dan watchlist kosong -> diam
    if not breakout and not tembus and not (wl_break or wl_tembus):
        return None

    L: List[str] = ["<b>📊 IDX Screener</b>"]
    upd = payload.get("diperbarui_pada_teks") or payload.get("diperbarui_pada") or "–"
    L.append(f"<i>{upd}</i>")

    ih = mp.get("ihsg") or {}
    if ih.get("tersedia"):
        L.append(f"IHSG {_fmt(ih.get('nilai'), 2)} ({_pct(ih.get('chg_pct'))})")
    else:
        L.append("IHSG: data tidak tersedia")

    sk = [r.get("skor") for r in rows if r.get("skor") is not None]
    if sk:
        L.append(f"Emiten dinilai: {len(sk)} · tertinggi {max(sk):.1f} · "
                 f"waspada {payload.get('jumlah_waspada', 0)}")

    if wl_break or wl_tembus:
        L.append("\n<b>⭐ Watchlist</b>")
        for r in wl_break[:5]:
            L.append(f"🟢 <b>{r['kode']}</b> breakout {_fmt(r.get('harga'))} "
                     f"({_pct(r.get('chg_pct'))})")
        for r in wl_tembus[:5]:
            L.append(f"🔴 <b>{r['kode']}</b> tembus support {_fmt(r.get('harga'))} "
                     f"({_pct(r.get('chg_pct'))})")

    if breakout:
        L.append(f"\n<b>Breakout volume besar</b> ({len(breakout)})")
        for r in sorted(breakout, key=lambda x: -(x.get("skor") or 0))[:5]:
            L.append(f"• {r['kode']} skor {r.get('skor'):.0f} · {_pct(r.get('chg_pct'))}"
                     if r.get("skor") is not None else f"• {r['kode']}")

    if tm:
        L.append("\n<b>Top 5 Emiten</b>")
        for r in tm[:5]:
            L.append(f"{r['peringkat']}. {r['kode']} — {r.get('skor'):.1f}"
                     if r.get("skor") is not None else f"{r['peringkat']}. {r['kode']}")

    L.append("\n<i>Hasil screening otomatis, bukan rekomendasi beli.</i>")
    return "\n".join(L)


def kirim_ringkasan(payload: Dict[str, Any], watchlist: List[str] | None = None) -> Dict[str, Any]:
    """Kirim ringkasan harian (sekali). Return status untuk log."""
    if not tersedia():
        return {"terkirim": False, "alasan": "TELEGRAM_BOT_TOKEN/CHAT_ID tidak diset"}
    msg = bangun_pesan(payload, watchlist)
    if msg is None:
        return {"terkirim": False, "alasan": "tidak ada sinyal penting hari ini"}
    ok = _kirim(msg)
    return {"terkirim": ok, "alasan": "ok" if ok else "gagal kirim"}


def kirim_dari_payload(path: str | None = None) -> Dict[str, Any]:
    """
    Baca payload dari file JSON lalu kirim ringkasannya ke Telegram.
    Dipakai oleh run_daily.py setelah pipeline selesai.
    """
    if not path:
        path = "/root/idx-dashboard/data/latest.json"
    try:
        with open(path, encoding="utf-8") as f:
            payload = json.load(f)
    except (OSError, ValueError) as e:
        return {"terkirim": False, "alasan": f"gagal baca payload: {e}"}
    return kirim_ringkasan(payload)


if __name__ == "__main__":
    import sys
    p = json.load(open(sys.argv[1] if len(sys.argv) > 1
                        else "/root/idx-dashboard/data/latest.json"))
    m = bangun_pesan(p)
    print(m or "(tidak ada pesan — tidak ada sinyal penting)")
