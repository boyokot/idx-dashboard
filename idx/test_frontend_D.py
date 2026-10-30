#!/usr/bin/env python3
"""
Tes Kelompok D di browser (headless via CDP): tab "Alert" — ringkasan pasar,
daftar alert + filter, cakupan data skor, median valuasi sektor, dan layout
mobile 390px.

Cara pakai (server lokal harus hidup di :8099):
    cd /root/idx-dashboard && python3 -m http.server 8099 &
    python3 idx/test_frontend_D.py
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
    print(f"  {'OK  ' if ok else 'FAIL'} {nama}" +
          (f"  [{detail}]" if detail != "" else ""))


def chrome_port():
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
    import tempfile
    td = tempfile.mkdtemp(prefix="cdpD_")
    proc = subprocess.Popen(
        [chrome, "--headless=new", f"--remote-debugging-port={port}",
         "--remote-allow-origins=*",
         f"--user-data-dir={os.path.join(td, 'prof')}", "--no-sandbox",
         "--disable-gpu", "--disable-dev-shm-usage",
         "--window-size=1280,900", "about:blank"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(40):
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{port}/json/list", timeout=2)
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

    # ── 1. kontrak data D di latest.json ────────────────────────────────
    print("== 1. latest.json: field Kelompok D ==")
    p = os.path.join(ROOT, "data", "latest.json")
    d = json.load(open(p)) if os.path.exists(p) else {}
    a = d.get("alerts") or {}
    cek("alerts ada & tersedia", a.get("tersedia") is True, a.get("tersedia"))
    cek("alerts.total > 0", (a.get("total") or 0) > 0, a.get("total"))
    cek("alerts.daftar terisi", bool(a.get("daftar")), len(a.get("daftar") or []))
    cek("per_severity lengkap",
        set(a.get("per_severity") or {}) == {"kritis", "perhatian", "info"},
        a.get("per_severity"))
    cek("per_jenis terisi", bool(a.get("per_jenis")), a.get("per_jenis"))

    # jumlah per_severity HARUS sama dengan total
    sv = a.get("per_severity") or {}
    cek("jumlah per_severity == total",
        sum(sv.values()) == a.get("total"), (sum(sv.values()), a.get("total")))

    sp = d.get("skor_parsial") or {}
    cek("skor_parsial ada", bool(sp), list(sp.keys()))
    cek("skor_parsial total == penuh+sebagian+tanpa",
        sp.get("total") == (sp.get("penuh", 0) + sp.get("sebagian", 0)
                            + sp.get("tanpa_skor", 0)),
        (sp.get("total"), sp.get("penuh"), sp.get("sebagian"),
         sp.get("tanpa_skor")))
    cek("skor_parsial jujur soal data sebagian",
        sp.get("sebagian", 0) == 0 or bool(sp.get("catatan")), sp.get("catatan"))

    mv = d.get("median_valuasi_sektor") or {}
    cek("median_valuasi_sektor ada", bool(mv), list(mv.keys()) if mv else None)
    ps = mv.get("per_sektor") or []
    cek("per_sektor terisi", len(ps) > 0, len(ps))
    cek("sektor_dengan_median <= jumlah_sektor",
        (mv.get("sektor_dengan_median") or 0) <= (mv.get("jumlah_sektor") or 0),
        (mv.get("sektor_dengan_median"), mv.get("jumlah_sektor")))

    # sektor tipis TIDAK boleh diberi median
    tipis = [x for x in ps if (x.get("jumlah_emiten") or 0) < 3]
    cek("sektor tipis tidak diberi median",
        all(x.get("median_per") is None for x in tipis), len(tipis))

    # median sektor harus BENAR-BENAR median (bukan rata-rata)
    import statistics
    emiten = d.get("emiten") or []
    from collections import defaultdict
    per_sec = defaultdict(list)
    for e in emiten:
        v = e.get("per")
        if isinstance(v, (int, float)) and not isinstance(v, bool) and v > 0:
            per_sec[(e.get("sektor") or "LAINNYA").upper()].append(float(v))
    cek_sample = 0
    salah_median = []
    for x in ps:
        s = x.get("sektor")
        med = x.get("median_per")
        vals = per_sec.get(s) or []
        if med is None or len(vals) < 3:
            continue
        hitung = statistics.median(vals)
        cek_sample += 1
        if abs(hitung - med) > 0.01:
            salah_median.append(f"{s}: json={med} hitung={round(hitung, 2)}")
    cek("median_per di json == median hitung ulang",
        not salah_median, salah_median[:3] if salah_median else cek_sample)

    # ── 2. browser ──────────────────────────────────────────────────────
    print("== 2. browser: tab Alert ==")
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
        eval_js(ws, "new Promise(r=>{const t=setInterval(()=>{"
                    "if(document.querySelectorAll('.tab').length){"
                    "clearInterval(t);r(1)}},200)})")
        # DATA harus terisi sebelum tab diklik
        # DATA pakai `let` (bukan properti window), jadi rujuk langsung.
        eval_js(ws, "new Promise(r=>{const t=setInterval(()=>{"
                    "if(typeof DATA!=='undefined'&&DATA&&DATA.emiten&&"
                    "DATA.emiten.length){clearInterval(t);r(1)}},200)})",
                timeout=40)

        # klik LEWAT JS: label tab berisi <span> sehingga klik koordinat
        # bisa mendarat di span, bukan button.
        eval_js(ws, "document.querySelector('[data-tab=\"alert\"]').click()")
        time.sleep(1.5)

        cek("panel alert aktif",
            eval_js(ws, "document.getElementById('panel-alert')"
                        ".classList.contains('active')") is True)
        cek("panel alert kelihatan (tinggi > 0)",
            (eval_js(ws, "document.getElementById('panel-alert').offsetHeight")
             or 0) > 500)

        # ── ringkasan pasar ──────────────────────────────────────────
        mk = eval_js(ws, "document.getElementById('mkInfo').innerText") or ""
        cek("ringkasan pasar menyebut IHSG", "IHSG" in mk, mk[:60])
        cek("ringkasan pasar ada angka", any(c.isdigit() for c in mk), mk[:60])
        for el, label in (("mkStrong", "sektor terkuat"),
                          ("mkWeak", "sektor terlemah")):
            t = eval_js(ws, f"document.getElementById('{el}').innerText") or ""
            cek(f"{label} terisi (bukan '—')", t.strip() not in ("", "—"), t[:40])
        nmk = eval_js(ws, "document.querySelectorAll('#mkSecBody tr').length") or 0
        cek("tabel sektor terisi", nmk >= 5, nmk)

        # ── tabel alert ─────────────────────────────────────────────
        nal = eval_js(ws, "document.querySelectorAll('#alBody tr').length") or 0
        cek("tabel alert terisi", nal >= 10, nal)

        badge = eval_js(ws, "[...document.querySelectorAll('#alBody tr')]"
                            ".map(tr=>tr.children[0].innerText.trim())"
                            ".slice(0,20)")
        cek("badge prioritas tampil",
            all(b in ("KRITIS", "PERHATIAN", "INFO") for b in (badge or [])),
            (badge or [])[:4])

        ai = eval_js(ws, "document.getElementById('alInfo').innerText") or ""
        cek("info alert menyebut kritis/perhatian/info",
            all(k in ai for k in ("kritis", "perhatian", "info")), ai[:80])

        # filter severity: jumlah baris harus = jumlah di per_severity
        nk = eval_js(ws, "(()=>{const s=document.getElementById('alSev');"
                         "s.value='kritis';drawAlerts();"
                         "const n=document.querySelectorAll('#alBody tr').length;"
                         "s.value='';drawAlerts();return n;})()")
        # daftar alert sengaja dipotong per tingkat (mis. kritis maks 80),
        # jadi jumlah baris = min(per_severity, batas_per_tingkat, daftar).
        batas_k = (a.get("batas_per_tingkat") or {}).get("kritis")
        ekspektasi = min(x for x in (sv.get("kritis") or 0,
                                     len(a.get("daftar") or []),
                                     batas_k) if x is not None)
        cek("filter 'kritis' == per_severity.kritis (dibatasi)",
            nk == ekspektasi,
            (nk, sv.get("kritis"), len(a.get("daftar") or []), batas_k))

        # filter jenis
        njen = eval_js(ws, "(()=>{const s=document.getElementById('alJenis');"
                           "const o=[...s.options].find(x=>x.value&&x.value!=='');"
                           "if(!o) return -1; s.value=o.value; drawAlerts();"
                           "const n=document.querySelectorAll('#alBody tr').length;"
                           "s.value='';drawAlerts();return n;})()")
        cek("filter jenis mengubah jumlah baris", njen is not None and njen >= 0, njen)

        # ── cakupan skor ────────────────────────────────────────────
        spi = eval_js(ws, "document.getElementById('spInfo').innerText") or ""
        cek("cakupan skor menyebut 'penuh' & 'sebagian'",
            "penuh" in spi.lower() and "sebagian" in spi.lower(), spi[:80])
        nsp = eval_js(ws, "document.querySelectorAll('#spBody tr').length") or 0
        cek("tabel cakupan skor terisi atau ada pesan kosong", nsp >= 1, nsp)

        # ── median valuasi sektor ───────────────────────────────────
        mvi = eval_js(ws, "document.getElementById('mvInfo').innerText") or ""
        cek("info median menyebut jumlah sektor",
            "sektor" in mvi.lower(), mvi[:70])
        nmv = eval_js(ws, "document.querySelectorAll('#mvBody tr').length") or 0
        cek("tabel median terisi", nmv >= 5, nmv)

        # sektor tipis harus tampil "–" di kolom median PER
        tipis_ui = eval_js(ws, """(()=>{
            const rows=[...document.querySelectorAll('#mvBody tr')];
            const out=[];
            for(const tr of rows){
              const emiten=parseInt(tr.children[1].innerText.trim(),10);
              const per=tr.children[2].innerText.trim();
              const pbv=tr.children[3].innerText.trim();
              if(emiten<3 && (per!=='–'||pbv!=='–')) out.push(tr.children[0].innerText.trim()+':'+per+'/'+pbv);
            }
            return out;})()""")
        cek("sektor tipis tampil '–' (bukan angka)",
            not tipis_ui, (tipis_ui or [])[:3])

        # ── tidak ada nilai rusak ───────────────────────────────────
        teks = eval_js(ws, "document.getElementById('panel-alert').innerText") or ""
        for buruk in ("NaN", "undefined", "null", "Infinity", "[object"):
            cek(f"tidak ada '{buruk}' di panel alert",
                buruk not in teks,
                "" if buruk not in teks else "DITEMUKAN")

        # ── layout mobile 390px ─────────────────────────────────────
        print("== 3. layout mobile 390px ==")
        ws.send(json.dumps({"id": 90, "method":
                            "Emulation.setDeviceMetricsOverride",
                            "params": {"width": 390, "height": 844,
                                       "deviceScaleFactor": 2,
                                       "mobile": True}}))
        time.sleep(2)
        eval_js(ws, "window.dispatchEvent(new Event('resize'))")
        time.sleep(1)

        lebar = eval_js(ws, "document.documentElement.scrollWidth")
        cek("tidak ada scroll horizontal di 390px",
            (lebar or 0) <= 400, lebar)

        tbl_w = eval_js(ws, "(()=>{const r=document.querySelector('#alBody tr');"
                             "return r?Math.round(r.getBoundingClientRect()"
                             ".width):0;})()")
        cek("kartu alert muat dalam 390px", 0 < (tbl_w or 0) <= 390, tbl_w)

        tab_ok = eval_js(ws, "(()=>{const t=[...document.querySelectorAll('.tab')]"
                             ".find(x=>x.dataset.tab==='alert');"
                             "return t?t.getBoundingClientRect().width:0;})()")
        cek("tab Alert punya lebar wajar", (tab_ok or 0) > 40, tab_ok)

        # kolom Detail tidak boleh nol lebar (terdorong keluar)
        # (Review 21 Okt) Kolom Prio ditambahkan sebelum Detail, jadi
        # indeks kolom Detail bergeser dari 3 -> 4.
        det_w = eval_js(ws, "(()=>{const tr=document.querySelector('#alBody tr');"
                             "if(!tr) return -1;"
                             "const c=tr.children[4];"
                             "return c?Math.round(c.getBoundingClientRect().width):-2;})()")
        cek("kolom Detail kelihatan di mobile", (det_w or 0) > 30, det_w)

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
