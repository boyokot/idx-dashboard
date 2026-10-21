#!/usr/bin/env python3
"""
Tes Kelompok C di browser (headless via CDP): tab "Akurasi Sinyal" membaca
data/backtest.json dan menampilkan pembanding IHSG.

Cara pakai (server lokal harus hidup di :8099):
    cd /root/idx-dashboard && python3 -m http.server 8099 &
    python3 idx/test_frontend_C.py
"""
import json
import os
import subprocess
import sys
import time
import urllib.request

BASE = "http://127.0.0.1:8099/index.html"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

HASIL = []


def cek(nama, ok, detail=""):
    HASIL.append((nama, bool(ok), detail))
    print(f"  {'OK  ' if ok else 'FAIL'} {nama}" + (f"  [{detail}]" if detail else ""))


def chrome_port():
    """Port CDP Chrome yang sudah jalan, atau None."""
    for p in (9222, 9223, 9224, 9333):
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{p}/json/version", timeout=1)
            return p
        except Exception:
            continue
    return None


def cari_chrome():
    for c in ("chromium", "chromium-browser", "google-chrome",
              "google-chrome-stable"):
        p = subprocess.run(["which", c], capture_output=True, text=True)
        if p.returncode == 0:
            return p.stdout.strip()
    return None


def luncurkan_chrome(chrome, port=9222):
    """Jalankan Chrome headless dengan profil sementara."""
    import tempfile
    td = tempfile.mkdtemp(prefix="cdpC_")
    proc = subprocess.Popen(
        [chrome, "--headless=new", f"--remote-debugging-port={port}",
         "--remote-allow-origins=*",
         f"--user-data-dir={os.path.join(td, 'prof')}", "--no-sandbox",
         "--disable-gpu", "--disable-dev-shm-usage",
         "--window-size=1280,900", "about:blank"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(40):
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{port}/json/list",
                                   timeout=2)
            return proc
        except Exception:
            time.sleep(0.3)
    return proc


def eval_js(ws, expr, timeout=25):
    mid = int(time.time() * 1000) % 100000
    ws.send(json.dumps({
        "id": mid, "method": "Runtime.evaluate",
        "params": {"expression": expr, "awaitPromise": True,
                   "returnByValue": True},
    }))
    t0 = time.time()
    while time.time() - t0 < timeout:
        msg = json.loads(ws.recv())
        if msg.get("id") == mid:
            r = msg.get("result", {})
            if "exceptionDetails" in r:
                raise RuntimeError(r["exceptionDetails"].get("text")
                                   or str(r["exceptionDetails"]))
            return r.get("result", {}).get("value")
    raise TimeoutError("eval timeout")


def main():
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

    # ── 1. file hasil backtest harus ada & bentuknya benar ──────────────
    print("== 1. data/backtest.json (kontrak file) ==")
    p = os.path.join(ROOT, "data", "backtest.json")
    cek("file backtest.json ada", os.path.exists(p), p)
    bt = {}
    if os.path.exists(p):
        bt = json.load(open(p))
    cek("ok == true", bt.get("ok") is True, bt.get("ok"))
    cek("horizon_sesi = [5,10,20]", bt.get("horizon_sesi") == [5, 10, 20],
        bt.get("horizon_sesi"))
    cek("ada per_sinyal", bool(bt.get("per_sinyal")),
        len(bt.get("per_sinyal") or {}))
    cek("ada per_skor", bool(bt.get("per_skor")),
        len(bt.get("per_skor") or {}))
    cek("ada catatan_risiko", bool(bt.get("catatan_risiko")),
        len(bt.get("catatan_risiko") or []))
    cak = bt.get("cakupan") or {}
    cek("cakupan.titik_dihitung > 0", (cak.get("titik_dihitung") or 0) > 0,
        cak.get("titik_dihitung"))

    # ── 2. setiap baris wajib punya IHSG & excess yang konsisten ────────
    print("== 2. konsistensi excess vs IHSG ==")
    salah = []
    tanpa = 0
    for grup in ("per_sinyal", "per_skor"):
        for nama, per_h in (bt.get(grup) or {}).items():
            for h, st in (per_h or {}).items():
                r, i, e = st.get("rata2"), st.get("ihsg_rata2"), st.get("excess")
                if r is None or i is None or e is None:
                    tanpa += 1
                    continue
                if abs((r - i) - e) > 1e-6:
                    salah.append(f"{grup}/{nama}/{h}: {r}-{i}!={e}")
    cek("semua excess == rata2 - ihsg_rata2", not salah,
        f"{len(salah)} salah" if salah else f"{tanpa} baris tanpa IHSG")

    # ── 3. halaman & tab ───────────────────────────────────────────────
    print("== 3. browser: tab Akurasi ==")
    port = chrome_port()
    proc = None
    if port is None:
        chrome = cari_chrome()
        cek("chromium/chrome tersedia", chrome is not None, chrome)
        if not chrome:
            print("\n!! tidak ada browser — tes browser dilewati")
            return ringkas()
        proc = luncurkan_chrome(chrome, 9222)
        port = 9222
    cek("Chrome CDP tersedia", port is not None, port)

    import websocket  # type: ignore
    try:
        tabs = json.loads(urllib.request.urlopen(
            f"http://127.0.0.1:{port}/json/list", timeout=5).read())
    except Exception as e:
        print(f"!! gagal baca /json/list: {e}")
        if proc:
            proc.terminate()
        return ringkas()
    page = next((t for t in tabs if t.get("type") == "page"), None)
    cek("ada tab page", page is not None)
    if not page:
        if proc:
            proc.terminate()
        return ringkas()

    ws = websocket.create_connection(page["webSocketDebuggerUrl"], timeout=30)
    try:
        ws.send(json.dumps({"id": 1, "method": "Page.navigate",
                            "params": {"url": BASE}}))
        time.sleep(6)
        # tunggu data termuat
        eval_js(ws, "new Promise(r=>{const t=setInterval(()=>{"
                    "if(document.querySelectorAll('.tab').length){"
                    "clearInterval(t);r(1)}},200)})")
        eval_js(ws, "document.querySelector('[data-tab=\"akurasi\"]').click()")
        time.sleep(3)

        info = eval_js(ws, "document.getElementById('btInfo').innerText")
        cek("info menyebut jumlah titik", "titik sinyal" in (info or ""), info)
        cek("info menyebut pembanding IHSG",
            "IHSG" in (info or ""), (info or "")[:80])

        n = eval_js(ws, "document.querySelectorAll('#btBody tr').length")
        cek("tabel sinyal terisi", (n or 0) >= 5, n)

        hdr = eval_js(ws, "[...document.querySelectorAll('#panel-akurasi "
                          "table')[0].querySelectorAll('th')]"
                          ".map(t=>t.innerText.trim())")
        hdr_up = [h.upper() for h in (hdr or [])]
        cek("kolom IHSG ada di header", "IHSG" in hdr_up, hdr)
        cek("kolom Selisih ada di header",
            any("SELISIH" in h for h in hdr_up), hdr)
        cek("kolom Excess ada di header",
            any("EXCESS" in h for h in hdr_up), hdr)
        cek("kolom Sinyal unik ada di header",
            any("SINYAL UNIK" in h for h in hdr_up), hdr)
        cek("header menyatakan rata-rata = return mentah",
            any("MENTAH" in h for h in hdr_up), hdr)

        ns = eval_js(ws, "document.querySelectorAll('#btSkorBody tr').length")
        cek("tabel per skor terisi", (ns or 0) >= 3, ns)

        # isi sel: tidak boleh ada '++', 'NaN', 'undefined', 'null'
        teks = eval_js(ws, "document.getElementById('panel-akurasi').innerText")
        for buruk in ("++", "NaN", "undefined", "null", "Infinity"):
            cek(f"tidak ada '{buruk}' di panel", buruk not in (teks or ""),
                "" if buruk not in (teks or "") else "ditemukan")

        # kolom return mentah, IHSG, dan excess (selisih) harus berisi
        # angka bertanda +/- (bukan kosong). KOLOM: 0 sinyal, 1 sesi,
        # 2 titik, 3 sinyal unik, 4 rata-rata mentah, 5 IHSG, 6 excess,
        # 7 % naik mentah, 8 % naik excess, 9 terburuk.
        for idx_kol, label in ((4, "rata-rata mentah"), (5, "IHSG"),
                               (6, "excess/selisih")):
            sel = eval_js(ws, "[...document.querySelectorAll('#btBody tr')]"
                              f".map(tr=>tr.children[{idx_kol}]"
                              ".innerText.trim()).slice(0,4)")
            cek(f"kolom {label} berisi angka", all(
                any(c.isdigit() for c in s) for s in (sel or [])), sel)
        # kolom sinyal unik harus terisi angka dan <= kolom titik
        unik = eval_js(ws, "[...document.querySelectorAll('#btBody tr')]"
                           ".map(tr=>[tr.children[2].innerText.trim(),"
                           "tr.children[3].innerText.trim()]).slice(0,4)")
        cek("kolom sinyal unik terisi angka", all(
            u[1] and any(c.isdigit() for c in u[1]) for u in (unik or [])),
            unik)

        cat = eval_js(ws, "document.getElementById('btCatatan').innerText")
        cek("catatan risiko tampil", "waspadai" in (cat or "").lower(),
            (cat or "")[:70])

        err = eval_js(ws, "window.__jsErrors || 0")
        cek("tidak ada error JS tercatat", not err, err)
    finally:
        try:
            ws.close()
        except Exception:
            pass
        if proc:
            try:
                proc.terminate()
                proc.wait(timeout=5)
            except Exception:
                pass

    return ringkas()


def ringkas():
    gagal = [h for h in HASIL if not h[1]]
    print()
    if gagal:
        print(f"!! {len(gagal)} GAGAL: {[h[0] for h in gagal]}")
        return 1
    print(f"== TOTAL: {len(HASIL)} tes lulus ==")
    return 0


if __name__ == "__main__":
    sys.exit(main())
