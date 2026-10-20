#!/usr/bin/env python3
"""
Tes frontend Kelompok B — chart harga/MA20/MA50/volume dari history.json.

Dijalankan lewat headless browser (Playwright/Chrome DevTools Protocol).
Nilai balik: JSON ke stdout, exit 0 = semua lulus.

Cara pakai:
    python3 idx/test_frontend_B.py [base_url]
Default base_url: http://127.0.0.1:8099/index.html
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import textwrap

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8099/index.html"

# ── skrip yang dievaluasi di dalam browser ──────────────────────────────
JS = r"""
(async () => {
  const out = {errors: [], checks: []};
  const cek = (nama, ok, detail) => out.checks.push({nama, ok: !!ok, detail: detail===undefined?'':String(detail)});
  window.addEventListener('error', e => out.errors.push('error: ' + e.message));
  const oe = console.error; console.error = (...a) => { out.errors.push('console.error: ' + a.join(' ')); oe(...a); };

  // 1) history.json harus bisa diambil & punya bentuk yang benar
  let hist = null;
  try {
    const r = await fetch('data/history.json');
    cek('history.json HTTP 200', r.ok, r.status);
    hist = await r.json();
  } catch (e) { cek('history.json bisa diambil', false, e.message); }
  if (!hist) return out;
  cek('history punya key emiten', !!(hist && hist.emiten));
  cek('history punya >=1 emiten', hist.emiten && Object.keys(hist.emiten).length >= 1,
      hist.emiten ? Object.keys(hist.emiten).length : 0);
  const kodeAda = Object.keys(hist.emiten);
  // struktur kolom
  const s = hist.emiten[kodeAda[0]];
  cek('kolom d/c/v lengkap', !!(s && s.d && s.c && s.v), s ? Object.keys(s).join(',') : '-');
  cek('panjang d == c == v', s.d.length === s.c.length && s.d.length === s.v.length);
  cek('maks 120 sesi', s.d.length <= 120, s.d.length);
  cek('tanggal urut naik', s.d.join() === s.d.slice().sort().join());
  cek('tanpa tanggal duplikat', new Set(s.d).size === s.d.length);
  cek('volume null bukan 0 (kalau ada null)',
      !s.v.some(v => v === 0) || s.v.every(v => v !== null));

  // 2) buka modal untuk emiten yang ADA di history -> chart harus tergambar
  const r1 = DATA.emiten.find(e => kodeAda.includes(e.kode));
  cek('emiten history ada di tabel', !!r1, kodeAda.slice(0,5).join(','));
  bukaDetail(r1.kode);
  await new Promise(res => setTimeout(res, 1200));
  const cv = document.querySelector('#chart');
  cek('canvas #chart ada', !!cv);
  if (cv) {
    const ctx = cv.getContext('2d');
    const px = ctx.getImageData(0, 0, cv.width, cv.height).data;
    const hit = {harga: 0, ma20: 0, ma50: 0, volume: 0};
    const target = {'37,99,235':'harga', '224,160,32':'ma20', '139,92,246':'ma50'};
    for (let i = 0; i < px.length; i += 4) {
      if (px[i+3] === 0) continue;
      for (const k in target) {
        const [rr,gg,bb] = k.split(',').map(Number);
        if (Math.abs(px[i]-rr) < 26 && Math.abs(px[i+1]-gg) < 26 && Math.abs(px[i+2]-bb) < 26) {
          hit[target[k]]++; break;
        }
      }
    }
    // panel volume = piksel di paruh bawah canvas
    let volPanel = 0, hargaPanel = 0;
    for (let y = 0; y < cv.height; y++) for (let x = 0; x < cv.width; x++) {
      const i = (y*cv.width + x)*4;
      if (px[i+3] === 0) continue;
      if (px[i] > 40 || px[i+1] > 40 || px[i+2] > 40) { if (y < 200) hargaPanel++; else volPanel++; }
    }
    cek('garis harga tergambar', hit.harga > 100, hit.harga);
    cek('garis MA20 tergambar', hit.ma20 > 50, hit.ma20);
    cek('garis MA50 tergambar', hit.ma50 > 50, hit.ma50);
    cek('panel volume ada isi', volPanel > 100, volPanel);
    cek('panel harga ada isi', hargaPanel > 100, hargaPanel);
  }

  // 3) legenda harus JUJUR: hanya seri yang benar-benar ada
  const info = (document.querySelector('#chartInfo') || {}).textContent || '';
  cek('chartInfo terisi', info.length > 10, info);
  cek('chartInfo menyebut jumlah sesi', /\d+ sesi/.test(info), info);
  cek('chartInfo jujur soal MA50',
      (hist.emiten[r1.kode].d.length >= 50) ? info.includes('MA50 (mulai') : info.includes('MA50 tidak ada'), info);
  const legendHtml = (document.querySelector('.legend') || {}).innerHTML || '';
  cek('legenda tidak klaim data snapshot',
      !legendHtml.includes('snapshot terakhir'), legendHtml.slice(0,80));

  // 4) MA frontend == MA pipeline (latest.json) — toleransi pembulatan
  const ent = hist.emiten[r1.kode];
  const ma = (vals, n) => {
    const o = new Array(vals.length).fill(null);
    for (let i = n-1; i < vals.length; i++) {
      let t = 0; for (let k = i-n+1; k <= i; k++) t += vals[k]; o[i] = t/n;
    }
    return o;
  };
  const row = DATA.emiten.find(e => e.kode === r1.kode);
  const m20 = ma(ent.c, 20), m50 = ma(ent.c, 50);
  const d20 = Math.abs(m20[m20.length-1] - row.ma20);
  const d50 = m50[m50.length-1] != null ? Math.abs(m50[m50.length-1] - row.ma50) : 0;
  cek('MA20 chart == MA20 latest.json (<0.01)', d20 < 0.01, 'selisih ' + d20.toFixed(5));
  cek('MA50 chart == MA50 latest.json (<0.01)', d50 < 0.01, 'selisih ' + d50.toFixed(5));

  // 5) cache: history.json hanya di-fetch SEKALI walau modal dibuka berkali-kali.
  //    Catatan: `HISTORY` dideklarasikan dengan `let`, jadi BUKAN properti window
  //    dan tidak bisa di-reset dari luar. Ukur lewat performance resource timing:
  //    setelah cache panas, membuka modal lagi tidak boleh menambah request.
  const nHist = () => performance.getEntriesByType('resource')
      .filter(e => (e.name || '').includes('history.json')).length;
  const sebelum = nHist();
  bukaDetail(r1.kode); await new Promise(res => setTimeout(res, 500));
  bukaDetail(r1.kode); await new Promise(res => setTimeout(res, 500));
  bukaDetail(r1.kode); await new Promise(res => setTimeout(res, 500));
  const tambahan = nHist() - sebelum;
  cek('history.json di-cache (0 fetch tambahan untuk 3x buka)', tambahan === 0,
      tambahan + ' fetch tambahan (total ' + nHist() + ')');

  // 6) emiten TANPA history -> pesan jelas, bukan crash
  const noHist = DATA.emiten.find(e => !kodeAda.includes(e.kode));
  if (noHist) {
    bukaDetail(noHist.kode);
    // poll sampai canvas menggambar / chartInfo terisi (jangan asumsi 1 delay cukup)
    let i2 = '';
    for (let t = 0; t < 40; t++) {
      await new Promise(res => setTimeout(res, 100));
      i2 = (document.querySelector('#chartInfo') || {}).textContent || '';
      if (i2) break;
    }
    cek('emiten tanpa history -> pesan jelas',
        i2.includes('belum tersedia') || i2.includes('tidak tersedia'),
        noHist.kode + ': ' + i2);
  }

  // 7) disclaimer wajib masih ada (teks baku: "bukan rekomendasi beli")
  cek('disclaimer ada', (document.body.innerText || '').includes('bukan rekomendasi beli'));

  out.errors = out.errors.filter(e => !/favicon/i.test(e));
  return out;
})()
"""


def main() -> int:
    # Cari browser headless yang tersedia
    chrome = None
    for c in ("chromium", "chromium-browser", "google-chrome", "google-chrome-stable"):
        p = subprocess.run(["which", c], capture_output=True, text=True)
        if p.returncode == 0:
            chrome = p.stdout.strip()
            break
    if not chrome:
        print(json.dumps({"error": "tidak ada chromium/chrome"}))
        return 2

    port = 9222
    with tempfile.TemporaryDirectory() as td:
        prof = os.path.join(td, "prof")
        proc = subprocess.Popen(
            [chrome, "--headless=new", f"--remote-debugging-port={port}",
             f"--user-data-dir={prof}", "--no-sandbox", "--disable-gpu",
             "--disable-dev-shm-usage", "--window-size=1280,900", "about:blank"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            import time
            import urllib.request
            ws = None
            for _ in range(40):
                try:
                    with urllib.request.urlopen(
                            f"http://127.0.0.1:{port}/json/list", timeout=2) as r:
                        tabs = json.load(r)
                    if tabs:
                        ws = tabs[0]["webSocketDebuggerUrl"]
                        break
                except Exception:
                    time.sleep(0.25)
            if not ws:
                print(json.dumps({"error": "CDP tidak merespons"}))
                return 2

            import websocket  # websocket-client

            # Chrome menolak handshake WebSocket dari origin http://127.0.0.1:PORT
            # (403). Kirim origin devtools://devtools agar diterima.
            origin = f"http://127.0.0.1:{port}"
            try:
                wsc = websocket.create_connection(
                    ws, timeout=60, origin=origin,
                    suppress_origin=True)  # type: ignore[call-arg]
            except Exception:
                wsc = websocket.create_connection(
                    ws, timeout=60, origin="devtools://devtools",
                    suppress_origin=True)  # type: ignore[call-arg]
            mid = [0]

            def send(method, params=None):
                mid[0] += 1
                wsc.send(json.dumps({"id": mid[0], "method": method, "params": params or {}}))
                while True:
                    msg = json.loads(wsc.recv())
                    if msg.get("id") == mid[0]:
                        return msg

            send("Page.enable")
            send("Runtime.enable")
            send("Page.navigate", {"url": BASE})
            time.sleep(4)
            send("Runtime.evaluate", {"expression": "window.DATA && DATA.emiten ? 'siap' : 'belum'"})
            time.sleep(1)
            res = send("Runtime.evaluate", {
                "expression": JS, "awaitPromise": True, "returnByValue": True})
            val = res.get("result", {}).get("result", {}).get("value")
            wsc.close()
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()

    if val is None:
        print(json.dumps({"error": "evaluasi JS tidak mengembalikan nilai",
                          "raw": str(res)[:500]}, ensure_ascii=False))
        return 2

    checks = val.get("checks", [])
    gagal = [c for c in checks if not c["ok"]]
    print(json.dumps(val, ensure_ascii=False, indent=1))
    print(f"\n== {len(checks) - len(gagal)}/{len(checks)} tes frontend B lulus ==")
    if val.get("errors"):
        print("!! error JS:", val["errors"])
    return 0 if not gagal and not val.get("errors") else 1


if __name__ == "__main__":
    sys.exit(main())
