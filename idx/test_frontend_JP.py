#!/usr/bin/env python3
"""
Tes frontend J3 — tab Jangka Panjang sebagai tampilan utama, Top 20,
tabel/filter Skor JP, dan Kartu Investasi.

Dijalankan dari root repo:
    python3 idx/test_frontend_JP.py
Server statis 8099 dinyalakan otomatis bila belum ada.
Exit 0 hanya bila GAGAL == 0.
"""
from __future__ import annotations

import subprocess
import sys
import time
import urllib.request
from pathlib import Path

from playwright.sync_api import sync_playwright

URL = "http://127.0.0.1:8099/index.html"
ROOT = Path(__file__).resolve().parent.parent
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


def server_siap() -> bool:
    try:
        urllib.request.urlopen(URL, timeout=2)
        return True
    except Exception:
        return False


def main() -> int:
    server = None
    if not server_siap():
        server = subprocess.Popen(
            [sys.executable, "-m", "http.server", "8099"],
            cwd=str(ROOT), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for _ in range(30):
            if server_siap():
                break
            time.sleep(0.3)

    print("== J3: tab Jangka Panjang, Top 20, tabel, Kartu Investasi ==")
    with sync_playwright() as pw:
        browser = pw.chromium.launch(
            headless=True, executable_path="/usr/bin/chromium-browser",
            args=["--no-sandbox"])
        page = browser.new_page(viewport={"width": 1440, "height": 1000})
        errors: list[str] = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(URL, wait_until="networkidle")
        page.wait_for_timeout(800)

        data = page.evaluate("""(() => {
          const e = DATA.emiten || [];
          return {
            n: e.length,
            scored: e.filter(r => r.skor_jp != null).length,
            ranked: e.filter(r => r.diperingkat === true).length,
            kurang: e.filter(r => r.skor_jp != null && r.data_kurang).length,
            tanpa: e.filter(r => r.skor_jp == null).length,
            top20: (DATA.top20_jp || []).length,
            first: (DATA.top20_jp || [])[0] ? DATA.top20_jp[0].kode : null,
          };
        })()""")
        cek("DATA termuat", isinstance(data, dict) and data.get("n", 0) > 0, str(data))
        if not isinstance(data, dict):
            browser.close()
            return 1

        cek("tab Jangka Panjang aktif secara default",
            page.locator(".tab.active").get_attribute("data-tab") == "jp")
        cek("label tab lama menjadi Momentum/Trading",
            "Momentum/Trading" in page.locator('.tab[data-tab="screener"]').inner_text())
        cek("panel Jangka Panjang terlihat", page.locator("#panel-jp").is_visible())

        cek("Top 20 tampil di area utama", page.locator("#topJPCard").is_visible())
        ncard = page.locator("#topJPList .jp-card").count()
        cek("jumlah kartu Top 20 sesuai data", ncard == min(20, data["top20"]),
            f"{ncard} vs {data['top20']}")
        first_card = page.locator("#topJPList .jp-card .jp-code").first.inner_text()
        cek("kartu pertama sesuai peringkat", first_card == (data.get("first") or ""), first_card)
        cek("kartu menampilkan cakupan",
            "cakupan" in page.locator("#topJPList .jp-card").first.inner_text())

        nrow = page.locator("#jpTbody tr").count()
        cek("tabel default hanya menampilkan yang diperingkat",
            nrow == data["ranked"], f"{nrow} vs {data['ranked']}")
        cek("KPI Jangka Panjang tampil",
            page.locator("#kpisJP .kpi").count() == 7)

        cek("sidebar navigasi tampil", page.locator(".sidebar").is_visible())
        vis = page.locator("#jpTbody tr:visible").count()
        cek("tabel memakai pagination (maks 15 baris terlihat)",
            vis == min(15, data["ranked"]), f"{vis} terlihat")
        first_visible = page.locator("#jpTbody tr:visible").first.inner_text()
        page.click("#jpNext")
        page.wait_for_timeout(120)
        second_visible = page.locator("#jpTbody tr:visible").first.inner_text()
        cek("tombol halaman berikutnya mengganti baris",
            first_visible != second_visible and "2 /" in page.locator("#jpPageNum").inner_text())
        page.click("#jpPrev")
        page.wait_for_timeout(120)
        cek("tombol halaman sebelumnya kembali ke halaman 1",
            page.locator("#jpPageNum").inner_text().strip().startswith("1 /"))
        page.locator("#jpThead th .info-btn").first.click()
        page.wait_for_timeout(120)
        cek("ikon info indikator membuka penjelasan",
            page.locator("#indicatorInfoModal").is_visible()
            and bool(page.locator("#indicatorInfoBody").inner_text().strip()))
        page.locator("#indicatorInfoModal .close").click()

        for status, key in (("scored", "scored"), ("kurang", "kurang"), ("tanpa", "tanpa")):
            page.select_option("#jpStatus", status)
            page.wait_for_timeout(80)
            n = page.locator("#jpTbody tr").count()
            cek(f"filter status {status}", n == data[key], f"{n} vs {data[key]}")

        page.select_option("#jpStatus", "ranked")
        page.fill("#jpSearch", "bank")
        page.wait_for_timeout(80)
        nbank = page.locator("#jpTbody tr").count()
        cek("pencarian teks berfungsi", 0 < nbank < data["ranked"], str(nbank))
        page.fill("#jpSearch", "")
        page.select_option("#jpSektor", "BANK")
        page.wait_for_timeout(80)
        nsek = page.locator("#jpTbody tr").count()
        cek("filter sektor berfungsi", 0 < nsek <= nbank, str(nsek))
        page.select_option("#jpSektor", "")

        page.evaluate("bukaDetailJP(DATA.top20_jp[0].kode)")
        page.wait_for_timeout(250)
        txt = page.locator("#detailBody").inner_text()
        cek("Kartu Investasi terbuka", page.locator("#detailModal").is_visible())
        cek("Kartu memuat pilar, valuasi, timing, risiko",
            all(x in txt for x in ("Pilar skor", "Valuasi historis",
                                   "Timing (informasi)", "Penanda risiko (informasi)")))
        cek("Kartu JP tidak memberi target harga tunggal",
            "Saran target" not in txt and "Saran cut loss" not in txt)
        cek("Kartu menyatakan bukan rekomendasi", "bukan rekomendasi" in txt.lower())
        page.evaluate("document.querySelector('#detailModal').classList.remove('show')")

        page.click('.tab[data-tab="screener"]')
        page.wait_for_timeout(250)
        cek("tab Momentum/Trading menampilkan panel lama",
            page.locator("#panel-screener").is_visible())
        cek("Top 10 momentum tampil di tab momentum",
            page.locator("#top10Card").is_visible())
        cek("Top 20 JP tetap tampil di area utama", page.locator("#topJPCard").is_visible())
        page.evaluate("bukaDetail('DMAS')")
        page.wait_for_timeout(250)
        mtxt = page.locator("#detailBody").inner_text()
        cek("detail momentum tidak lagi memakai target harga",
            "Saran target" not in mtxt and "Saran cut loss" not in mtxt)
        cek("detail momentum menautkan Kartu Investasi JP",
            "Buka Kartu Investasi JP" in mtxt)
        page.evaluate("document.querySelector('#detailModal').classList.remove('show')")

        info = page.evaluate("document.querySelector('#infoModal').innerText") or ""
        cek("Penjelasan Skor memuat Skor JP dan bobot pilar",
            "Skor Investasi Jangka Panjang" in info and "kualitas profitabilitas 25%" in info)
        cek("Penjelasan Skor menyatakan tidak ada target harga",
            "Tidak ada target harga tunggal" in info)

        page.click('.tab[data-tab="akurasi"]')
        page.wait_for_timeout(500)
        ak = page.evaluate("document.querySelector('#panel-akurasi').innerText") or ""
        cek("Akurasi JP tampil di tab Akurasi", page.locator("#jpAkurasiCard").is_visible())
        cek("Akurasi JP jujur menyebut bukan backtest", "bukan backtest" in ak.lower())
        cek("Akurasi JP tidak berisi NaN/undefined",
            "NaN" not in ak and "undefined" not in ak and "Infinity" not in ak)
        cek("tidak ada error JS saat render", not errors, " | ".join(errors[:3]))

        # ── review 1–9: freshness, kekuatan relatif, waspada tinggi ──────
        page.click('.tab[data-tab="alert"]')
        page.wait_for_timeout(600)
        ext = page.evaluate("""(() => ({
          tglExt: EXTERNAL && EXTERNAL.tanggal_data,
          tglUtama: DATA && DATA.tanggal_data,
          basi: EXTERNAL && EXTERNAL.__basi,
          badge: (document.getElementById('makroTanggal')||{}).className,
          info: (document.getElementById('makroInfo')||{}).innerText,
          cpo: (document.getElementById('makroBody')||{}).innerText,
          pengingat: (document.getElementById('makroPengingat')||{}).innerText,
          judulSens: (document.querySelector('#sensitivitasCard b')||{}).innerText,
          ringkasSens: (document.getElementById('sensitivitasRingkas')||{}).innerText,
          barisSens: document.querySelectorAll('#sensitivitasBody tr').length,
          tinggi: (document.getElementById('wsHighInfo')||{}).innerText,
          usulan: (document.getElementById('wsUsulan')||{}).innerText,
          usulanTampil: (document.getElementById('wsUsulan')||{}).offsetHeight > 0,
          event: EXTERNAL && EXTERNAL.event_risiko && EXTERNAL.event_risiko.events,
          kal: EXTERNAL && EXTERNAL.kalender_bi && EXTERNAL.kalender_bi.berikutnya,
          strong: EXTERNAL && EXTERNAL.kekuatan_sektor && EXTERNAL.kekuatan_sektor.sektor_terkuat
            && EXTERNAL.kekuatan_sektor.sektor_terkuat.sektor,
          cpoBasi: EXTERNAL && EXTERNAL.makro && EXTERNAL.makro.cpo && EXTERNAL.makro.cpo.basi,
        }))()""")
        harus_basi = bool(ext.get("tglExt") and ext.get("tglUtama")
                          and ext["tglExt"] < ext["tglUtama"])
        cek("freshness guard: flag basi konsisten dengan tanggal",
            bool(ext.get("basi")) == harus_basi, str(ext.get("tglExt")) + " vs " + str(ext.get("tglUtama")))
        cek("freshness guard: badge kuning saat external basi",
            ("meta-warn" in (ext.get("badge") or "")) == harus_basi,
            str(ext.get("badge")))
        cek("freshness guard: info menyebut data basi saat perlu",
            ("Data eksternal basi" in (ext.get("info") or "")) == harus_basi,
            (ext.get("info") or "")[:120])
        cek("CPO/iron date stale diberi tanda saat source lebih lama",
            ("⚠" in (ext.get("cpo") or "")) if ext.get("cpoBasi") else True,
            (ext.get("cpo") or "")[:200])
        cek("pengingat inflasi bulanan tampil",
            "Inflasi terakhir" in (ext.get("pengingat") or "")
            and "September 2026" in (ext.get("pengingat") or ""),
            (ext.get("pengingat") or "")[:150])
        cek("kartu sensitivitas sudah berganti nama",
            (ext.get("judulSens") or "") == "Kekuatan Relatif Sektor vs IHSG",
            str(ext.get("judulSens")))
        cek("tabel kekuatan relatif benar-benar terisi",
            (ext.get("barisSens") or 0) >= 5, str(ext.get("barisSens")))
        strong_key = (ext.get("strong") or "").split(" ")[0]
        cek("ringkasan sektor terkuat memakai rel 20s + tanggal",
            bool(strong_key) and strong_key in (ext.get("ringkasSens") or "")
            and "Rel 20s vs IHSG" in (ext.get("ringkasSens") or ""),
            (ext.get("ringkasSens") or "")[:180])
        n_high = page.evaluate("(DATA.waspada_ringkasan && DATA.waspada_ringkasan.tinggi_ringkasan && DATA.waspada_ringkasan.tinggi_ringkasan.jumlah) || 0")
        cek("rincian waspada tinggi sesuai payload",
            str(n_high) in (ext.get("tinggi") or ""),
            f"payload={n_high} ui={(ext.get('tinggi') or '')[:120]}")
        cek("aturan penyempitan 15–20% tampil dan ditandai sudah diterapkan",
            ext.get("usulanTampil") is True
            and "15–20%" in (ext.get("usulan") or "")
            and "DITERAPKAN" in (ext.get("usulan") or ""),
            (ext.get("usulan") or "")[:200])
        cek("event risiko memuat trading halt 16 Okt 2026",
            any(x.get("id") == "trading_halt_16okt2026"
                for x in (ext.get("event") or [])),
            str([x.get("id") for x in (ext.get("event") or [])]))
        cek("kalender BI berikutnya dihitung dari tanggal data",
            bool(ext.get("kal")) and "hari_lagi" in (ext.get("kal") or {}),
            str(ext.get("kal"))[:150])

        print("\n== mobile 390px ==")
        m = browser.new_page(viewport={"width": 390, "height": 844}, is_mobile=True)
        m.goto(URL, wait_until="networkidle")
        m.wait_for_timeout(700)
        sw = m.evaluate("document.documentElement.scrollWidth")
        iw = m.evaluate("innerWidth")
        cek("mobile: tidak ada overflow horizontal", sw <= iw, f"{sw} > {iw}")
        cek("mobile: tab JP tetap default",
            m.locator(".tab.active").get_attribute("data-tab") == "jp")
        cek("mobile: Top 20 tetap 20 kartu",
            m.locator("#topJPList .jp-card").count() == min(20, data["top20"]))
        m.close()
        browser.close()

    if server is not None:
        server.terminate()
        try:
            server.wait(timeout=5)
        except Exception:
            server.kill()

    print(f"\n== TOTAL: {LULUS} lulus, {GAGAL} gagal ==")
    return 1 if GAGAL else 0


if __name__ == "__main__":
    sys.exit(main())
