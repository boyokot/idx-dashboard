# IDX Stock Screener — Pipeline & Dashboard

Pipeline harian screening saham IDX (Sectors v2 → yfinance fallback) + dashboard
HTML statis di `/root/idx-dashboard`.

> ⚠️ **Hasil screening otomatis, bukan rekomendasi buy.**

## Struktur

| Path | Isi |
|---|---|
| `/root/scripts/idx/providers.py` | Pengambilan data (Sectors v2, yfinance), **normalisasi satuan** |
| `/root/scripts/idx/scoring.py` | Skoring teknikal (40) / fundamental (40) / katalis (20) |
| `/root/scripts/idx/pipeline.py` | Orkestrasi run, tulis `latest.json` + snapshot harian |
| `/root/scripts/idx/calendar_id.py` | Kalender bursa IDX (libur nasional + cuti bersama) |
| `/root/scripts/idx/snapshot_index.py` | Membangun `data/snapshots/index.json` |
| `/root/scripts/idx/notify.py` | Notifikasi Telegram |
| `/root/scripts/idx/run_daily.py` | Wrapper cron (holiday guard) |
| `/root/scripts/idx/history_store.py` | Menyimpan `data/history.json` (riwayat harga semua emiten) |
| `/root/idx-dashboard/index.html` | Dashboard (self-contained, tanpa dependensi build) |
| `/root/idx-dashboard/data/latest.json` | Output pipeline terakhir |
| `/root/idx-dashboard/data/history.json` | Riwayat harga 120 sesi/emiten (SATU file) |
| `/root/idx-dashboard/data/snapshots/` | Snapshot harian (skor per emiten per tanggal) |

## Satuan data (baku)

**Semua sumber dinormalisasi ke satuan di bawah ini di satu fungsi:**
`providers.normalisasi_satuan_fundamental()`. Tidak ada konversi satuan lain
di pipeline maupun di dashboard.

| Field | Satuan | Contoh | Catatan |
|---|---|---|---|
| `harga`, `open`, `high`, `low`, `close` | `IDR_per_lembar` | `1250` | Rupiah per lembar saham |
| `ma20`, `ma50` | `IDR_per_lembar` | `1230.5` | Moving average harga |
| `chg_pct` | `persen` | `3.7` | **Bukan fraksi.** 3.7 = +3,7% |
| `volume` | `lembar_saham` | `12500000` | **Jumlah lembar**, bukan lot, bukan rupiah. `None` = tidak dilaporkan sumber (bukan 0) |
| `rsi` | `indeks_0_100` | `58.4` | RSI(14) |
| `per`, `pbv` | `rasio_x` | `12.3` | Sudah rasio di semua sumber |
| `roe` | `fraksi_0_1` | `0.12814` | **FRAKSI** = 12,814%. UI `roePct()` mengalikan ×100 |
| `der` | `rasio_x` | `0.7689` | Rasio hutang atas modal (0,77×) |
| `earnings_growth` | `fraksi_0_1` | `0.152` | 15,2% |
| `revenue_growth` | `fraksi_0_1` | `0.089` | 8,9% |
| `skor` | `poin_0_100` | `78.4` | Total 40/40/20 |

### Aturan konversi per sumber

- **Sectors v2** — `roe`/`earnings_growth`/`revenue_growth` bisa persen (`15.2`)
  atau fraksi (`0.152`). Deteksi ambang: `|v| > 1.5` → persen → dibagi 100.
  `der` > 12 dianggap persen → dibagi 100.
- **yfinance `.info`** — `returnOnEquity`, `earningsGrowth`, `revenueGrowth`
  **selalu fraksi** → tidak dikonversi. `debtToEquity` **selalu persen**
  (`10.5` = 10,5×) → dibagi 100 agar menjadi rasio.

### Nilai kosong

Nilai yang tidak tersedia **selalu `None`/`null`**, tidak pernah `0` dan tidak
pernah ditebak. Dashboard menampilkan `–` untuk nilai kosong. Ini berlaku juga
untuk `volume`: sumber yang tidak melaporkan volume menghasilkan `null`, bukan 0.

### Kontrak skema

`latest.json` memuat `versi_skema` (saat ini `1.4.0`) dan peta `satuan`.
Naikkan `VERSI_SKEMA` di `pipeline.py` setiap kali nama field atau satuan berubah.

## Riwayat harga (Kelompok B)

`data/history.json` — **SATU file** berisi riwayat harga semua emiten, maks
**120 sesi bursa** per emiten. Format kolom (ringkas, ~3 MB untuk 975 emiten):

```json
{
  "versi_skema": "1.2.0",
  "sesi_maks": 120,
  "jumlah_emiten": 975,
  "jumlah_sesi": 117000,
  "emiten": { "AALI": { "d": ["2026-04-23", "..."], "c": [8025.0, "..."], "v": [5180000, "..."] } }
}
```

| Key | Isi | Satuan |
|---|---|---|
| `d` | Tanggal sesi (ISO) | `YYYY-MM-DD` |
| `c` | Harga close | `IDR_per_lembar` |
| `v` | Volume | `lembar_saham`, `null` = tidak dilaporkan sumber |

Aturan:
- Data diambil dari baris harga yang **sudah diunduh** `analyze_one` — tidak
  menambah request jaringan sama sekali.
- Emitten yang gagal diambil pada suatu run **dipertahankan** dari file lama
  (`merge_lama`), jadi `history.json` tidak pernah bolong.
- `volume` kosong tetap `null`, bukan `0`.
- Ditulis **atomik** (file sementara → rename), sama seperti `latest.json`.
- MA20/MA50 dihitung ulang di frontend dari seri `c` yang **sama** dengan
  pipeline (`scoring.sma`), jadi angka chart dan angka tabel tidak berbeda.
  Diverifikasi identik oleh `idx/test_history.py` (bagian 8).

## Backtest sinyal vs IHSG (Kelompok C)

`data/backtest.json` — dihitung pipeline dari `history.json` + bar `^JKSE`,
lalu ditampilkan di tab **Akurasi Sinyal**.

```json
{
  "versi_skema": "1.3.0",
  "ok": true,
  "horizon_sesi": [5, 10, 20],
  "cakupan": { "emiten_history": 916, "emiten_dipakai": 916,
               "titik_dihitung": 69333, "titik_tanpa_ihsg": 0 },
  "per_sinyal": { "Breakout": { "20": { "n": 3041, "rata2": 0.0489,
                  "ihsg_rata2": -0.0037, "excess": 0.0526,
                  "menang": 48.7, "terburuk": -0.9648 } } },
  "per_skor": { "tinggi": { "20": { "n": 5748, "excess": 0.0459 } } },
  "catatan_risiko": ["..."]
}
```

Aturan yang **tidak boleh dilanggar** (semua diuji `idx/test_backtest.py`):

| Aturan | Kenapa |
|---|---|
| Horizon = **sesi bursa**, bukan hari kalender | 5 sesi = 7 hari kalender (melompati Sabtu-Minggu). Hari libur bursa dilewati. |
| Pembanding IHSG pada **tanggal yang sama** | Kalau IHSG diambil dari tanggal lain, `excess` jadi omong kosong. |
| Titik tanpa IHSG **dibuang**, bukan diisi 0 | Mengisi 0 membuat `excess` seolah-olah mengalahkan pasar. |
| **Tidak ada look-ahead** | Label di titik *i* hanya memakai bar ≤ *i*. Diuji dengan dua seri identik s/d *i* lalu berbeda setelahnya. |
| `excess == rata2 − ihsg_rata2` | Diuji ulang di frontend; kalau tidak sama, tabel menampilkan angka palsu. |
| Label sinyal dari `scoring.label_sinyal()` | Satu definisi untuk pipeline **dan** backtest, supaya tidak melenceng. |

`^JKSE` dan simbol berakhiran `.JK` **tidak** ditambah suffix `.JK` lagi
(bug lama: `^JKSE.JK` tidak ada). Prioritas: `get_market_index()` untuk KPI,
`yfinance_history()` untuk seri harian backtest.

Kalau backtest gagal, pipeline tetap mempublikasikan `latest.json` — backtest
hanya pelengkap, bukan syarat. Frontend punya fallback ke snapshot harian lama
kalau `backtest.json` belum ada, jadi fitur lama tidak hilang.

## Alert & ringkasan pasar (Kelompok D)

`idx/alert.py` — mesin alert **murni turunan** indikator yang sudah dihitung
pipeline. Modul ini **tidak melakukan request jaringan sama sekali**: tidak
ada `requests`, tidak ada `yfinance`. Semua masukannya berasal dari baris
`latest.json` (`rsi`, `volume`, `ma20`, `skor`, `sinyal`, `_bars`).

Karena itu modul ini aman dijalankan kapan pun dan tidak menambah beban
jaringan. Ini juga alasan alert tidak bisa "menemukan" hal yang tidak ada di
data: kalau indikatornya kosong, alert untuk indikator itu tidak dibuat.

### Tiga field baru di `latest.json`

```json
{
  "tanggal_data": "2026-10-21",
  "alerts": {
    "total": 804, "ditampilkan": 200, "dipotong": true,
    "per_severity": {"kritis": 52, "perhatian": 101, "info": 651},
    "per_jenis": {"likuiditas_tipis": 439, "rsi_oversold": 102,
                  "support_break": 52, "rsi_jenuh": 53,
                  "lonjakan_harga": 40, "breakout": 6,
                  "konflik_skor": 5, "breakout_tanpa_volume": 3},
    "daftar": [{"kode": "BBMD", "severity": "kritis",
                "jenis": "support_break", "pesan": "...", "detail": "..."}]
  },
  "skor_parsial": {
    "penuh": 0, "sebagian": 916, "tanpa_skor": 59, "total": 975,
    "bobot_terisi_min": 0.8, "komponen_sering_hilang": {"katalis": 916}
  }
}
```

**`tanggal_data` vs `diperbarui_pada`** — dua hal berbeda dan sering
tertukar:

| Field | Arti |
|---|---|
| `diperbarui_pada` | Jam **pipeline jalan** (mis. `07:47` pagi hari kerja) |
| `tanggal_data` | Tanggal **sesi bursa** data ini berasal (`2026-10-20`) |

Pipeline yang jalan pagi belum punya bar sesi hari itu, jadi
`diperbarui_pada[:10]` bisa **lebih maju satu hari** dari tanggal data.
Membandingkan tanggal bar terakhir dengan `diperbarui_pada` menghasilkan
kegagalan palsu — ini bug nyata yang ditemukan dan diperbaiki di tes
integritas. **Selalu pakai `tanggal_data`** untuk pertanyaan "data ini
tanggal berapa".

### Aturan alert

| Aturan | Kenapa |
|---|---|
| Tidak ada request jaringan di `idx/alert.py` | Alert hanya turunan indikator; pipeline sudah mengambil data. |
| Kegagalan modul alert **tidak** menggagalkan publish | `_alerts_payload()` mengembalikan struktur aman. `latest.json` tetap terbit. |
| Maksimum 200 alert ditampilkan, `dipotong: true` | 804 alert tidak berguna sebagai daftar datar; filter ada di UI. |
| Nilai kosong → `None`, bukan 0 | "Tidak ada data" ≠ "nol". |
| Tidak ada order / eksekusi | Dashboard hanya memantau dan melaporkan. |

### `skor_parsial` — kenapa 916 emiten "sebagian"

Bukan bug. Bobot skor: teknikal 0,5 · fundamental 0,3 · **katalis 0,2**.
Komponen katalis butuh API berita berkey yang belum tersedia, sehingga
`bobot_terisi` maksimum **0,8** untuk **semua** emiten. Artinya skor saat ini
belum memakai 20% bobot yang direncanakan.

UI menampilkan ini sebagai peringatan eksplisit, bukan menyembunyikannya.
Kalau katalis diaktifkan (butuh kredensial), `penuh` akan naik sendiri.

### Median valuasi sektor

`sector_medians()` mengembalikan `{sektor: {"per": .., "pbv": .., "n": ..}}`.
Bug lama: fungsi membaca `r["fundamental"]["per"]`, padahal baris pipeline
menyimpan `per` di **top-level** → median **selalu kosong sejak awal**.
Sekarang menerima **dua bentuk** (top-level dan nested).

Sektor dengan **< 3 emiten berdata** sengaja **tidak** diberi median — median
dari 1-2 sampel menyesatkan, dan `None` lebih jujur daripada angka tebakan.
Di UI sel seperti itu tampil `–`.

### Layout mobile

Di bawah 700px tabel Alert berubah jadi **kartu bertumpuk**, bukan tabel
yang di-scroll horizontal. Alasannya: kolom Detail tadinya terdorong keluar
layar (548px pada viewport 390px). Sekarang kartu 342px, tanpa scroll
horizontal. Diuji otomatis di `idx/test_frontend_D.py`.

### Tes

```bash
python3 idx/test_alert.py        # 45 tes
python3 idx/test_frontend_D.py   # 44 tes (termasuk viewport 390px)
```

## Menjalankan

```bash
# run penuh (hanya run PENUH yang menulis snapshot harian)
python3 /root/scripts/idx/pipeline.py

# run uji: tidak menimpa latest.json, tidak menimpa snapshot
python3 /root/scripts/idx/pipeline.py 20 --out test_latest.json

# jalankan sebagai cron harian
python3 /root/scripts/idx/run_daily.py
```

Aturan penting:
- Ada lock (`data/.pipeline.lock`) — run paralel ditolak.
- Tulis atomic (file sementara → rename), jadi `latest.json` tidak pernah setengah jadi.
- **Run parsial tidak boleh menulis `latest.json` production maupun snapshot.**
- Snapshot harian hanya ditulis run lengkap (975 emiten).

## Auth

- `SECTORS_API_KEY` — environment variable. Header `Authorization` berisi key
  **tanpa** prefiks `Bearer`.
- `TELEGRAM_BOT_TOKEN` / `TELEGRAM_CHAT_ID` — untuk notifikasi.

## Timeline

- Dashboard: https://boyokot.github.io/idx-dashboard/
- Data JSON harus ikut ter-deploy; kalau `index.html` 200 tapi `data/latest.json`
  404, masalahnya ada di sisi deploy (cache/CDN/branch), bukan di pipeline.
