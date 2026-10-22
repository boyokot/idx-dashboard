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


_ENV_DIMUAT = False


def _muat_env() -> None:
    """Muat /root/.hermes/.env sekali, tanpa menimpa environment yang sudah ada.

    Cron menjalankan run_daily.py sebagai proses terpisah yang tidak mewarisi
    .env milik Hermes, jadi TELEGRAM_BOT_TOKEN tidak terlihat dan notifikasi
    gagal senyap. Membaca file ini membuat notifikasi benar-benar terkirim
    tanpa menaruh token di kode/repo.
    """
    global _ENV_DIMUAT
    if _ENV_DIMUAT:
        return
    _ENV_DIMUAT = True
    path = os.environ.get("IDX_ENV_FILE") or os.path.expanduser(
        "~/.hermes/.env")
    try:
        with open(path, encoding="utf-8") as f:
            for baris in f:
                baris = baris.strip()
                if not baris or baris.startswith("#") or "=" not in baris:
                    continue
                k, _, v = baris.partition("=")
                k, v = k.strip(), v.strip().strip('"').strip("'")
                if k and v and not os.environ.get(k):
                    os.environ[k] = v
    except OSError:
        pass


def _token() -> str:
    _muat_env()
    return (os.environ.get("TELEGRAM_BOT_TOKEN")
            or os.environ.get("IDX_TELEGRAM_TOKEN") or "").strip()


def _chat() -> str:
    _muat_env()
    return (os.environ.get("TELEGRAM_CHAT_ID")
            or os.environ.get("IDX_TELEGRAM_CHAT")
            or os.environ.get("TELEGRAM_HOME_CHANNEL") or "").strip()


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

    breakout = [r for r in rows if r.get("sinyal") == "Breakout"
                and not r.get("waspada_diskualifikasi")]
    tembus = [r for r in rows if r.get("sinyal") == "Tembus Support"]
    wl_break = [r for r in breakout if r["kode"] in wl]
    wl_tembus = [r for r in tembus if r["kode"] in wl]

    # (Review 21 Okt) Alert yang dipush HANYA tingkat kritis, dibatasi
    # MAKS_TELEGRAM. Sebelumnya seluruh 695 alert ikut terkirim.
    al = payload.get("alerts") or {}
    tg = al.get("untuk_telegram") or {}
    kritis = tg.get("daftar") or []

    # kalau tidak ada sinyal penting dan watchlist kosong -> diam
    if not breakout and not tembus and not (wl_break or wl_tembus) and not kritis:
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
        # (Review 21 Okt) "tertinggi" harus sama definisinya dengan Top 10
        # (likuid & bukan waspada tinggi), bukan maksimum seluruh pasar —
        # kalau tidak, angka di pesan tidak cocok dengan daftar di bawahnya.
        sk_top = [t.get("skor") for t in tm if t.get("skor") is not None]
        tertinggi = max(sk_top) if sk_top else None
        ekor = (f" · tertinggi Top 10 {tertinggi:.1f}" if tertinggi is not None
                else " · Top 10 kosong")
        L.append(f"Emiten dinilai: {len(sk)}{ekor} · "
                 f"waspada {payload.get('jumlah_waspada', 0)}")

    # (Review 21 Okt) Hanya alert kritis yang masuk pesan; perhatian & info
    # tidak dipush supaya tidak tenggelam.
    if kritis:
        alasan = tg.get("dilewati") or 0
        judul = f"\n<b>🔴 Kritis</b> ({tg.get('jumlah', len(kritis))}"
        if alasan:
            judul += f", +{alasan} lain tidak dipush"
        judul += ")"
        L.append(judul)
        for a in kritis:
            L.append(f"• <b>{a.get('kode')}</b> — {a.get('judul')}"
                     + (f": {a.get('detail')}" if a.get("detail") else ""))

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


def bangun_pesan_gagal(judul: str, alasan: List[str] | str,
                       stats: Dict[str, Any] | None = None) -> str:
    """Pesan kegagalan pipeline.

    Sebelum Kelompok E, kegagalan hanya jadi `print()` di log — tidak ada
    yang tahu dashboard berhenti diperbarui sampai membuka log. Pesan ini
    sengaja pendek dan memuat penyebab konkret, bukan "terjadi kesalahan".
    """
    L: List[str] = [f"<b>⚠️ {judul}</b>"]
    if isinstance(alasan, str):
        alasan = [alasan]
    for a in alasan[:6]:
        L.append(f"• {a}")
    if len(alasan) > 6:
        L.append(f"• … dan {len(alasan) - 6} alasan lain")
    if stats:
        L.append("")
        L.append(f"<i>emiten {stats.get('emiten', 0)} · "
                 f"harga {stats.get('ada_harga', 0)} · "
                 f"fundamental {stats.get('ada_fundamental', 0)}</i>")
    L.append("")
    L.append("<i>Data lama dipertahankan; dashboard tidak diperbarui.</i>")
    return "\n".join(L)


_STATE_NYAMPAI = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "data", ".notify_state.json")


def _hari_ini() -> str:
    """Tanggal WIB (UTC+7). Server jalan di WIB, tapi jangan bergantung itu."""
    import datetime as _dt
    return (_dt.datetime.now(_dt.timezone.utc)
            + _dt.timedelta(hours=7)).strftime("%Y-%m-%d")


def _sudah_dikirim(kunci: str) -> bool:
    try:
        with open(_STATE_NYAMPAI, encoding="utf-8") as f:
            st = json.load(f)
    except (OSError, ValueError):
        return False
    return st.get("tanggal") == _hari_ini() and kunci in (st.get("kunci") or [])


def _tandai_terkirim(kunci: str) -> None:
    """Catat kunci yang sudah dikirim HARI INI. Tanggal berubah = reset.

    Kegagalan menulis state tidak boleh menggagalkan pengiriman: lebih baik
    dobel sekali daripada tidak pernah mengabari.
    """
    hari = _hari_ini()
    try:
        with open(_STATE_NYAMPAI, encoding="utf-8") as f:
            st = json.load(f)
    except (OSError, ValueError):
        st = {}
    if st.get("tanggal") != hari:
        st = {"tanggal": hari, "kunci": []}
    kunci_list = st.setdefault("kunci", [])
    if kunci not in kunci_list:
        kunci_list.append(kunci)
    st["kunci"] = kunci_list[-20:]          # jangan tumbuh tanpa batas
    try:
        os.makedirs(os.path.dirname(_STATE_NYAMPAI), exist_ok=True)
        tmp = _STATE_NYAMPAI + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(st, f)
        os.replace(tmp, _STATE_NYAMPAI)
    except OSError:
        pass


def kirim_gagal(judul: str, alasan: List[str] | str,
                stats: Dict[str, Any] | None = None,
                sekali_per_hari: bool = True) -> Dict[str, Any]:
    """Kirim notifikasi kegagalan. Tidak pernah melempar exception.

    Anti-spam: kegagalan yang SAMA hanya dikabarkan sekali per hari WIB.
    Tanpa ini, pipeline intraday yang gagal 3x akan mengirim 3 pesan
    identik — dan orang berhenti membaca notifikasi. Kegagalan berbeda
    tetap dikirim; besok, kegagalan yang sama dikirim lagi sekali
    (supaya masalah yang tidak diperbaiki tidak jadi senyap).
    """
    if not tersedia():
        return {"terkirim": False, "alasan": "telegram tidak dikonfigurasi"}
    import hashlib
    isi = bangun_pesan_gagal(judul, alasan, stats)
    kunci = hashlib.sha1(isi.encode("utf-8")).hexdigest()[:16]
    if sekali_per_hari and _sudah_dikirim(kunci):
        return {"terkirim": False, "alasan": "sudah dikirim hari ini (anti-spam)",
                "kunci": kunci}
    try:
        ok = _kirim(isi)
    except Exception as e:                      # noqa: BLE001
        return {"terkirim": False, "alasan": f"{type(e).__name__}: {e}"}
    if ok:
        _tandai_terkirim(kunci)
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
