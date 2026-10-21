#!/usr/bin/env python3
"""
Tes frontend Kelompok E — metadata skema, sumber, dan warning validasi
di UI, termasuk viewport mobile.

Dijalankan: python3 idx/test_frontend_E.py
Exit 0 hanya bila GAGAL == 0.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.request

import websocket

URL = "http://127.0.0.1:8099/index.html"
PORT = 9335
CHROME = "/usr/bin/chromium-browser"

LULUS = 0
GAGAL = 0


def cek(nama: str, kondisi: bool, detail: str = "") -> None:
    global LULUS, GAGAL
    if kondisi:
        LULUS += 1
        print(f"  OK   {nama}")
    else:
        GAGAL += 1
        print(f"  GAGAL {nama}" + (f" :: {detail}" if detail else ""))


class CDP:
    def __init__(self, port: int, width: int, height: int):
        self.td = tempfile.mkdtemp(prefix="feE_")
        self.p = subprocess.Popen(
            [CHROME, "--headless=new", f"--remote-debugging-port={port}",
             "--remote-allow-origins=*", "--disable-gpu", "--no-sandbox",
             f"--user-data-dir={self.td}", "--hide-scrollbars",
             f"--window-size={width},{height}", "about:blank"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        ws = None
        for _ in range(40):
            try:
                tabs = json.loads(urllib.request.urlopen(
                    f"http://127.0.0.1:{port}/json/list", timeout=3).read())
                pg = [t for t in tabs if t["type"] == "page"]
                if pg:
                    ws = pg[0]["webSocketDebuggerUrl"]
                    break
            except Exception:
                time.sleep(0.5)
        if not ws:
            raise RuntimeError("Chrome tidak siap")
        self.c = websocket.create_connection(ws, timeout=40)
        self.mid = 0
        self.send("Page.enable")
        self.send("Runtime.enable")
        self.send("Emulation.setDeviceMetricsOverride",
                  {"width": width, "height": height, "deviceScaleFactor": 1,
                   "mobile": width < 600})

    def send(self, method, params=None):
        self.mid += 1
        self.c.send(json.dumps({"id": self.mid, "method": method,
                                "params": params or {}}))
        while True:
            m = json.loads(self.c.recv())
            if m.get("id") == self.mid:
                return m

    def ev(self, expr):
        r = self.send("Runtime.evaluate",
                      {"expression": expr, "returnByValue": True,
                       "awaitPromise": True})
        return r.get("result", {}).get("result", {}).get("value")

    def goto(self, url, tunggu=9):
        self.send("Page.navigate", {"url": url})
        time.sleep(tunggu)

    def tutup(self):
        try:
            self.c.close()
        except Exception:
            pass
        self.p.terminate()
        try:
            self.p.wait(timeout=5)
        except Exception:
            self.p.kill()


# ── data payload langsung (uji fungsi tanpa jaringan) ────────────────────
print("== 1. renderMetaSkema dengan data sintetis ==")
d = CDP(PORT, 1280, 900)
d.goto("about:blank")
# muat index.html lalu ganti DATA dengan payload sintetis untuk menguji
# cabang-cabang renderMetaSkema secara terpisah dari isi latest.json.
d.goto(URL)

hasil = d.ev("""(() => {
  const out = {};
  const cases = {
    normal: {tanggal_data:'2026-10-21', versi_skema:'1.4.0',
             sumber_data_ringkas:{harga:{yfinance:916},emiten_total:975,
                                  emiten_tanpa_data:0},
             validasi:{ok:true, warnings:[]}},
    tanpa_data: {tanggal_data:'2026-10-21', versi_skema:'1.4.0',
                 sumber_data_ringkas:{harga:{yfinance:900},emiten_total:975,
                                      emiten_tanpa_data:75},
                 validasi:{ok:true, warnings:[]}},
    ada_warning: {tanggal_data:'2026-10-21', versi_skema:'1.4.0',
                  sumber_data_ringkas:{harga:{yfinance:900},emiten_total:975,
                                       emiten_tanpa_data:0},
                  validasi:{ok:true, warnings:['w1','w2','w3']}},
    gagal: {tanggal_data:'2026-10-21', versi_skema:'1.4.0',
            validasi:{ok:false, warnings:[]}},
    kosong: {}
  };
  for (const [nama, payload] of Object.entries(cases)) {
    renderMetaSkema(payload);
    const el = document.querySelector('#metaSkema');
    out[nama] = {teks: el.innerText, html: el.innerHTML, title: el.title};
  }
  return out;
})()""")

cek("fungsi renderMetaSkema ada", isinstance(hasil, dict), str(hasil)[:200])
if isinstance(hasil, dict):
    n = hasil.get("normal", {})
    cek("normal: tanggal data tampil", "2026-10-21" in n.get("teks", ""),
        n.get("teks", ""))
    cek("normal: versi skema tampil", "1.4.0" in n.get("teks", ""),
        n.get("teks", ""))
    cek("normal: sumber tampil", "yfinance" in n.get("teks", ""),
        n.get("teks", ""))
    cek("normal: rasio emiten tampil", "916/975" in n.get("teks", ""),
        n.get("teks", ""))
    cek("normal: tanpa warning = tanpa penanda",
        "peringatan" not in n.get("teks", "").lower()
        and "tanpa data" not in n.get("teks", "").lower(), n.get("teks", ""))

    t = hasil.get("tanpa_data", {})
    cek("tanpa_data: memperingatkan emiten tanpa data",
        "75" in t.get("teks", "") and "tanpa data" in t.get("teks", ""),
        t.get("teks", ""))
    cek("tanpa_data: pakai kelas meta-warn", "meta-warn" in t.get("html", ""),
        t.get("html", ""))

    w = hasil.get("ada_warning", {})
    cek("warning: jumlah warning tampil", "3" in w.get("teks", ""),
        w.get("teks", ""))
    cek("warning: title memuat isi warning",
        w.get("title", "").count("\n") == 2, repr(w.get("title")))
    cek("warning: pakai kelas meta-warn", "meta-warn" in w.get("html", ""),
        w.get("html", ""))

    g = hasil.get("gagal", {})
    cek("gagal: menyatakan validasi GAGAL", "GAGAL" in g.get("teks", ""),
        g.get("teks", ""))
    cek("gagal: pakai kelas meta-bad", "meta-bad" in g.get("html", ""),
        g.get("html", ""))

    k = hasil.get("kosong", {})
    cek("kosong: tidak melempar, tetap render",
        isinstance(k.get("teks"), str), str(k)[:120])

# ── 2. payload nyata di UI ───────────────────────────────────────────────
print("\n== 2. metadata dari latest.json nyata ==")
teks = d.ev("document.querySelector('#metaSkema').innerText") or ""
# render ulang dengan DATA asli (kasus sebelumnya menimpanya)
d.ev("renderMetaSkema(DATA)")
teks = d.ev("document.querySelector('#metaSkema').innerText") or ""
cek("metadata terisi di UI", len(teks.strip()) > 10, teks)
cek("menyebut data sesi", "Data sesi" in teks, teks)
cek("menyebut skema", "Skema" in teks, teks)
cek("menyebut sumber", "Sumber" in teks, teks)
cek("format angka id-ID pada rasio (titik ribuan)",
    "/" in teks and "." in teks, teks)
data_ui = d.ev("JSON.stringify({tanggal_data:DATA.tanggal_data,"
               "versi_skema:DATA.versi_skema,"
               "n:DATA.jumlah_emiten})")
cek("DATA asli tersedia", isinstance(data_ui, str) and "versi_skema" in data_ui,
    str(data_ui))

err = d.ev("(window.__err||[]).length")
cek("tidak ada error JS saat render metadata",
    err in (0, None) or err == 0, f"errors={err}")

# metadata tidak boleh menutupi elemen lain
h = d.ev("document.querySelector('#metaSkema').getBoundingClientRect().height")
cek("tinggi metadata wajar (< 60px desktop)", isinstance(h, (int, float)) and h < 60,
    str(h))

# ── 3. viewport mobile ───────────────────────────────────────────────────
print("\n== 3. viewport mobile 390px ==")
d.tutup()
m = CDP(PORT + 1, 390, 844)
m.goto(URL)
m.ev("renderMetaSkema(DATA)")
teks_m = m.ev("document.querySelector('#metaSkema').innerText") or ""
cek("mobile: metadata tetap tampil", len(teks_m.strip()) > 10, teks_m)

lebar = m.ev("document.querySelector('#metaSkema').getBoundingClientRect().width")
cek("mobile: lebar metadata <= viewport",
    isinstance(lebar, (int, float)) and lebar <= 390, str(lebar))

scroll_w = m.ev("document.documentElement.scrollWidth")
cek("mobile: tidak ada scroll horizontal akibat metadata",
    isinstance(scroll_w, (int, float)) and scroll_w <= 390, str(scroll_w))

bbox = m.ev("""(() => {
  const r = document.querySelector('#metaSkema').getBoundingClientRect();
  const l = document.querySelector('#lastUpdate').getBoundingClientRect();
  return {meta_top:r.top, meta_bottom:r.bottom, lu_bottom:l.bottom,
          overlap: r.top < l.bottom - 1};
})()""")
cek("mobile: metadata tidak menumpuk lastUpdate",
    isinstance(bbox, dict) and not bbox.get("overlap"), str(bbox))

err_m = m.ev("(window.__err||[]).length")
cek("mobile: tidak ada error JS", err_m in (0, None), f"errors={err_m}")
m.tutup()

print(f"\n== TOTAL: {LULUS} lulus, {GAGAL} gagal ==")
sys.exit(1 if GAGAL else 0)
