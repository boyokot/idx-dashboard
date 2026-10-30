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

`latest.json` memuat `versi_skema` (saat ini `1.7.0`) dan peta `satuan`.
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

## Validasi & publikasi (Kelompok E)

### Kenapa ada

Sebelum Kelompok E, `latest.json` ditulis **tanpa satu pun pemeriksaan**.
Run yang menghasilkan 3 emiten, atau harga kosong semua, atau nilai dengan
satuan kacau, langsung menimpa dashboard yang sehat. Tidak ada yang tahu
sampai membuka dashboard dan melihat angkanya aneh. Snapshot harian punya
masalah yang sama: run "penuh" yang separuh harganya gagal diambil tetap
menimpa snapshot pagi yang lengkap.

Sekarang data diperiksa dulu. Kalau tidak lolos, **file lama dipertahankan**
dan kegagalannya dikabarkan.

### `idx/validate.py`

`validasi(payload, total_universe, run_penuh)` mengembalikan
`{"ok", "errors", "warnings", "stats"}`. Fungsi ini **tidak memutasi
payload** (ada tesnya).

**ERROR — publish ditolak:**

| Pemeriksaan | Ambang |
|---|---|
| `emiten` kosong / bukan list | — |
| jumlah emiten | `< MIN_EMITEN` (20) |
| kode duplikat (case-insensitive) | > 0 |
| cakupan harga | `< 50%` emiten punya harga |
| `tanggal_data` kosong | — |
| `skor` / `rsi` di luar rentang | 0–100 |
| `chg_pct` | \|x\| > 100% |
| `volume` negatif | > 0 baris |
| `der` | > 100 |
| `\|roe\|` (fraksi) | > 10 |

**WARNING — publish tetap jalan, tapi dilaporkan:**

- cakupan harga < 95% saat run penuh
- fundamental kosong pada banyak emiten
- **DER negatif** — ini bukan data rusak. Artinya total ekuitas negatif,
  yang memang terjadi pada emiten distress (LAPD, CNKO, MYTX, BATA, …).
  ROE mereka `None` karena laba/ekuitas negatif tidak menghasilkan rasio
  bermakna. Menolak publish karena ini akan **membekukan dashboard pada
  data yang benar** — jadi hanya warning.
- tidak ada emiten berskor penuh (indikasi komponen katalis kosong)
- `alerts.total == 0`

Ambang batas ini bukan selera: setiap nilai di luar rentang di atas berarti
salah satuan (mis. `roe` dikirim sebagai persen 12.81 alih-alih fraksi
0.1281), bukan emiten yang kebetulan ekstrem.

### Perlindungan snapshot

`snapshot_layak_timpa(path, rows)` — fungsi murni, bisa diuji tanpa
menjalankan pipeline:

- Belum ada snapshot → boleh tulis.
- Cakupan harga baru **< lama** → **jangan timpa**. Snapshot pagi yang
  lebih lengkap dipertahankan; run parsial tidak boleh menghapusnya.
- Cakupan sama atau lebih tinggi → boleh tulis.
- Snapshot lama rusak / `emiten` kosong → anggap tidak ada, boleh tulis.
- Nol harga tidak akan pernah menimpa snapshot sehat.

Run parsial (`< total_universe`) tetap tidak menulis snapshot sama sekali,
seperti sebelumnya.

### Notifikasi kegagalan

`notify.kirim_gagal(judul, alasan, stats)` mengirim pesan pendek berisi
penyebab konkret — bukan "terjadi kesalahan". Kegagalan notifikasi **tidak
pernah** mengubah exit code pipeline: kalau Telegram mati, run yang sukses
tetap exit 0 (kalau dibalik, cron akan mengira sukses sebagai gagal).

`run_daily.py` sekarang mengabarkan tiga jenis kegagalan:

1. pipeline melempar exception
2. **publish ditolak validasi** (data lama dipertahankan)
3. pipeline selesai tapi `ok == False`

Sebelumnya ketiganya hanya jadi `print()` di log.

### Metadata skema & sumber

`latest.json` sekarang memuat:

- `tanggal_data` — tanggal **sesi bursa** data ini, bukan jam pipeline
  jalan. Pipeline yang jalan 07:47 pada hari kerja belum punya bar sesi
  hari itu, jadi membandingkannya dengan `diperbarui_pada` selalu gagal
  palsu. **Jangan** pakai `diperbarui_pada` untuk menentukan tanggal data.
- `validasi` — `{ok, errors, warnings, stats}`
- `sumber_data_ringkas` — sebaran asal data per field (`harga`,
  `fundamental`, `gabungan`), plus `emiten_tanpa_data`. `sumber_utama`
  saja menyembunyikan fakta bahwa sebagian emiten tidak punya data sama
  sekali.
- `versi_skema` — `1.7.0`

Snapshot harian memuat `tanggal_data`, `versi_skema`, `jumlah_emiten`, dan
`jumlah_ada_harga` — supaya bisa diperiksa tanpa memuat seluruh array.
Snapshot juga mencatat `skor_jp`, `cakupan_bobot`, `lolos_gerbang`, dan
`zona_timing` (label); snapshot JP bulanan J4 disimpan terpisah di
`data/snapshots_jp/`.

UI menampilkan metadata ini di bawah "Terakhir diperbarui". Warning
validasi muncul sebagai penanda berwarna dengan isi lengkap di `title` —
**tidak disembunyikan**, karena dashboard yang menyembunyikan warning
tampak lebih sehat daripada datanya.

## Skor Investasi Jangka Panjang (J2)

Skor JP **terpisah** dari skor momentum/trading. Field lama (`skor`,
`skor_detail`, `top10`, `kriteria_top10`) tidak diubah. `versi_skema`
naik ke `1.7.0`.

Ambang final dikonfirmasi **30 Oktober 2026**: median nilai transaksi
**60 sesi ≥ Rp1 miliar/hari** dan market cap **≥ Rp200 miliar**.

Alur data:

| File / modul | Isi |
|---|---|
| `idx/fundamental_historical.py` (J1) | laporan 3–4 tahun → `data/fundamental.json` |
| `idx/pasar_jp.py` | harga bulanan 5 tahun + harian 260 sesi, kurs USD/IDR → `data/harga_jp.json`; info yfinance → `data/info_jp.json` |
| `idx/skor_jp.py` | gerbang, persentil sektor, enam pilar, ranking → field JP di `data/latest.json` + `data/skor_jp_ringkasan.json` |
| `config/bobot_jp.json` | bobot pilar, bobot metrik, ambang gerbang |

Gerbang: median nilai transaksi **60 sesi ≥ Rp1 miliar** (minimal 20 sesi),
market cap **≥ Rp200 miliar**, laba positif **3 dari 4 tahun** (atau 2 dari 3
bila hanya 3 tahun), ekuitas **> 0**, bukan waspada tingkat tinggi, dan
**cakupan bobot ≥ 70%**. Yang gagal ditandai `data_kurang`/alasan, bukan
dipaksa masuk peringkat.

Bobot pilar: kualitas profitabilitas 25%, pertumbuhan 20%, kesehatan
keuangan 20%, valuasi 20%, dividen 10%, risiko/tata kelola 5%. Skor tiap
metrik adalah **persentil dalam sektor**; sektor dengan <5 emiten memakai
persentil gabungan dan diberi catatan. Bank/asuransi/lembaga keuangan tidak
memakai DER/interest coverage — kesehatan memakai ROE dan PBV; NPL/CAR hanya
dipakai bila tersedia. Valuasi membandingkan PER/PBV dengan median sektor
**dan** rata-rata 3–5 tahun sendiri. PER rendah akibat laba sekali-jadi
dibatasi skornya. Payout >200% dianggap tidak wajar.

Timing (`zona_timing`) dan penanda risiko (`penanda_risiko`) hanya informasi;
keduanya `dipakai_skor: false`. Peta sensitivitas sektor juga tetap
`dipakai_skor: false`.

Field baru di `latest.json`: `skor_jp`, `peringkat_jp`, `diperingkat`,
`lolos_gerbang`, `alasan_gagal_gerbang`, `gerbang_detail`, `cakupan_bobot`,
`data_kurang`, `skor_jp_rincian`, `zona_timing`, `penanda_risiko`,
`valuasi_historis`, `valuasi_relatif`, `top20_jp`, dan `skor_jp_meta`
(termasuk `sumber_data`/tanggal pembaruan tiap file).

### Indikator diskon/overprice relatif

`valuasi_relatif` adalah indikator **informasi**, bukan komponen Skor JP
(`dipakai_skor: false`). Tujuannya menjawab “harga sekarang relatif mahal
atau murah dibanding sektor dan riwayat emiten”, bukan menentukan target
harga absolut.

Rumus:

```text
PER_wajar   = 60% × median PER sektor + 40% × median PER historis
PBV_wajar   = 60% × median PBV sektor + 40% × median PBV historis
Harga_PER   = harga sekarang × (PER_wajar / PER_sekarang)
Harga_PBV   = harga sekarang × (PBV_wajar / PBV_sekarang)
Harga_rel   = 55% × Harga_PER + 45% × Harga_PBV   (non-keuangan)
             30% × Harga_PER + 70% × Harga_PBV   (bank/lembaga keuangan)
Diskon (%)  = (Harga_rel / harga sekarang − 1) × 100
```

Positif = diskon relatif; negatif = overprice. Jika salah satu komponen
tidak tersedia, bobotnya dinormalisasi dan status menjadi `sebagian`.
Median historis memakai 2–4 tahun terbaru dari `valuasi_historis.tahun[]`;
bukan rata-rata, agar satu tahun ekstrem tidak mendominasi. Sektor dengan
<5 emiten/observasi valid tidak memakai median sektor (memakai riwayat bila ada).

Perlindungan data: PER di luar 1–100× dan PBV di luar 0,10–20× tidak
dipakai; EPS ≤0, laba sekali-jadi, atau ekuitas ≤0 juga tidak dipakai.
Selisih di atas 100% diberi `ekstrem: true` dan catatan untuk memeriksa
split/data. Label: `Diskon dalam` (≥20%), `Diskon` (≥10%), `Wajar`
(−10% s/d +10%), `Overprice` (≤−10%), `Overprice berat` (≤−20%);
`Sinyal campuran` bila arah PER dan PBV berbeda jauh. Tidak ada target
harga dan tidak ada klaim kepastian.

Field `valuasi_relatif` memuat `status`, `dipakai_skor` (selalu `false`),
`diskon_pct`, `label`, `label_komposit`, `campuran`, `ekstrem`, `harga`, `harga_relatif`,
`komponen.per`, `komponen.pbv`, `bobot_efektif`, `n_sektor`, `n_histori`,
`metode`, dan `catatan`.

### Tampilan J3

Tab **Jangka Panjang** adalah tampilan pertama dan default. Menu navigasi
berada di sidebar kiri pada desktop dan menjadi bar horizontal di mobile;
tab lama menjadi **Momentum/Trading** dan skor momentum tidak diubah.
**Top 20 Investasi Jangka Panjang** berada di area utama. Tabel JP punya
filter status (diperingkat, punya skor, data kurang, tanpa laporan), sektor,
batas skor, pencarian, sortir, unduh CSV, dan pagination 15/25/50/100 baris
supaya tidak memanjang ke bawah. Tabel menampilkan kolom **Diskon/Over**
(positif = diskon, negatif = overprice); cakupan data tetap tersedia di kartu
dan CSV. Ikon info **(i)** pada kartu KPI dan header kolom membuka penjelasan
indikator. Klik kartu/kode membuka **Kartu Investasi** berisi skor pilar,
rincian metrik dan persentil, valuasi vs median sektor + rata-rata historis,
rincian diskon/overprice (termasuk komponen PER/PBV), zona timing, serta
penanda risiko. Data yang tidak tersedia tetap ditulis “data tidak tersedia”,
tidak diisi 0. Zona timing dan penanda risiko tidak mengubah skor. Tidak ada
target harga tunggal di UI maupun detail momentum.

```bash
python3 -m idx.fundamental_historical   # J1, jaringan
python3 idx/pasar_jp.py --workers 8     # harga + info, jaringan
python3 idx/skor_jp.py                  # hitung & tulis latest.json
python3 idx/test_skor_jp.py             # 70 tes offline
python3 idx/test_frontend_JP.py         # 61 tes UI J3 (desktop + mobile)
```

## Akurasi Skor JP (J4)

`idx/akurasi_jp.py` membuat **satu snapshot per bulan data** di
`data/snapshots_jp/YYYY-MM.json` dan menghitung hasil nyata **3/6/12 bulan**
di `data/akurasi_jp.json`. Snapshot pertama adalah **2026-10**; snapshot
bulan yang sama tidak ditimpa agar harga awal tidak bergeser. Run parsial
tidak membuat snapshot bulanan.

Isi snapshot: kode, sektor, skor/peringkat JP, cakupan, harga close, dan level
IHSG. Perhitungan memakai **Top 20 peringkat JP pada snapshot awal**,
dibandingkan dengan IHSG dan rata-rata sektor (minimal 3 emiten per sektor;
harga yang hilang tidak diisi 0). Horizon hanya muncul setelah snapshot bulan
target benar-benar ada. Ini **bukan backtest** dan tidak ada klaim akurasi
sebelum hasilnya terkumpul; biaya transaksi, slippage, dan dividen tidak
dihitung. UI menampilkan status “belum tersedia” secara eksplisit di tab
**Akurasi Sinyal** (bagian Akurasi Skor JP).

```bash
python3 idx/akurasi_jp.py               # snapshot bulan ini + hitung akurasi
python3 idx/test_akurasi_jp.py          # 21 tes offline
```

Pipeline harian memanggil `skor_jp.sisipkan()` sebelum validasi. Kalau J2
gagal, pipeline momentum tetap terbit dan `skor_jp_meta.status` mencatat
error. `harga_jp.json`/`info_jp.json` perlu diperbarui berkala; umur datanya
terlihat di `skor_jp_meta.sumber_data`. Keterbatasan yang disengaja: NPL/CAR
tidak tersedia dari yfinance, rincian mata uang utang tidak tersedia (hanya
mata uang laporan), dan valuasi historis memakai harga mentah sehingga aksi
split dapat mendistorsi rasio.

### Menjalankan tes

```bash
cd /root/idx-dashboard
python3 idx/test_validate.py        # 99 tes — validasi, snapshot, notifikasi
python3 idx/test_skor_jp.py         # 70 tes — skor JP, gerbang, pilar, ranking
python3 idx/test_akurasi_jp.py      # 21 tes — snapshot bulanan + akurasi J4
python3 idx/test_waspada.py         # 25 tes — aturan tinggi + penyempitan 15–20%
python3 idx/test_frontend_JP.py     # 61 tes UI J3/J4 (desktop + mobile)
python3 idx/test_frontend_E.py      # 27 tes — metadata UI, desktop + mobile
```

Butuh server lokal di port 8099 untuk tes frontend:
`python3 -m http.server 8099`.

## Faktor eksternal — Tahap 1: data (Kelompok F)

`idx/external.py` mengumpulkan data eksternal harian → **`data/external.json`**
(tulis atomic, timestamp WIB). Cron memanggilnya otomatis setelah pipeline
sukses (`run_daily._kumpulkan_eksternal()`). **Modul ini belum mengubah skor
apa pun** — pemakaian di skor baru ada di Tahap 4.

```bash
python3 -m idx.external          # tulis data/external.json
python3 -m idx.external --dry    # cetak tanpa menulis
python3 idx/test_external.py     # 269 tes (termasuk jaringan)
```

### Yang diambil & sumbernya

| Bagian | Isi | Sumber |
|---|---|---|
| `makro` | IHSG `^JKSE`, USD/IDR `USDIDR=X`, S&P 500 `^GSPC`, minyak `CL=F`, emas `GC=F`, CPO `CPO=F` (**USD**, bukan MYR), biji besi `TIO=F` | yfinance |
| `bi_rate` | BI-Rate terbaru + tanggal | tabel resmi `bi.go.id/id/statistik/indikator/bi-rate.aspx` |
| `inflasi` | Inflasi YoY + periode | **BPS** (Utama) → `bi.go.id/.../data-inflasi.aspx` (fallback) |
| `kalender_bi` | 12 jadwal RDG 2026 | `bi.go.id/id/publikasi/kalender/default.aspx` |
| `kekuatan_sektor` | return 5 & 20 sesi per sektor + selisih vs IHSG | `data/history.json` + `^JKSE` |
| `aliran_dana` | net asing, broker summary | Sectors (**butuh API key**) |
| `kalender_emiten` | rilis lapkeu, RUPS, cum-date dividen | IDX / Sectors |

Setiap nilai membawa `sumber`, `tanggal_sumber`/`url`, dan `alasan` bila gagal.

### Aturan yang dipegang

- **Tidak ada tebakan.** Sumber gagal → `"nilai": "data tidak tersedia"` +
  `alasan`. Tidak pernah diisi `0`, tidak diganti proxy yang mirip.
- **Batubara & nikel memang kosong.** Tidak ada kontraknya di yfinance
  (`COAL=F`, `MTF=F`, `NI=F` semuanya kosong per 21 Okt 2026); LME butuh
  langganan. Ditulis apa adanya, bukan ditebak dari harga emiten.
- **`rel_*` = sektor − IHSG** pada jendela yang sama (5 atau 20 sesi).
- **Return sektor = MEDIAN** return emiten (bukan mean, supaya satu emiten
  liar tidak menggeser seluruh sektor). Sektor dengan < 3 emiten berdata
  ditandai tidak tersedia, bukan diberi angka dari 1-2 saham.
- **Tanggal RDG diambil tanggal TERAKHIR rentang** (hari keputusan rate).
- **`berikutnya` dihitung relatif terhadap `tanggal_data`**, bukan jam run.
  Kalau sumber BI mereset koneksi, ada retry + fallback salinan sukses
  terakhir; kegagalan tidak langsung menghapus kalender yang sudah terbaca.
- **Kesegaran per indikator**: `makro.*.basi` dan
  `kesegaran.item_basi` menandai CPO/iron ore/S&P yang lebih lama dari data
  pasar utama. UI memberi tanda ⚠ pada baris tersebut.
- **Proxy batubara/nikel** menampilkan return relatif sektor vs IHSG sebagai
  angka utama di kolom 5s/20s; return absolut ada di tooltip, karena tidak
  ada harga komoditas yang bisa diambil. Bukan tebakan harga.

### Review 30 Okt — perbaikan tampilan

- Guard kesegaran `external.json` dibanding `latest.json` (badge + catatan
  eksplisit, tidak menyamarkan tanggal run sebagai tanggal data).
- Kartu “Peta Sensitivitas” yang keliru diganti **“Kekuatan Relatif Sektor
  vs IHSG”**, memakai tabel rel 20 sesi, median emiten, min. 3 emiten, dan
  tanggal data. Sektor terkuat/terlemah di `latest.json` memakai definisi
  yang sama dan angkanya cocok dengan `external.json`.
- Sumber event risiko: MSCI **dan** trading halt 16 Okt 2026, lengkap dengan
  tautan sumber/berita. Event tetap penanda, bukan pengurang skor.
- Pengingat inflasi bulanan dan verifikasi minyak WTI ditampilkan dari
  payload (bukan hardcode).
- Rincian 429 waspada tinggi (baseline 29 Okt) + aturan penyempitan
  15–20% universe: `docs/review_9_waspada_2026-10-29.md`. Aturan **sudah
  diterapkan 30 Okt 2026**; tinggi hanya ROE/PER negatif atau kombinasi
  penny/support + volume sangat kecil. Baseline sebelum/sesudah tetap
  disimpan di payload.

### Kendala sumber (jujur, bukan asumsi)

| Sumber | Status 21 Okt 2026 |
|---|---|
| `www.idx.co.id` (RUPS/dividen/lapkeu) | **HTTP 403** Cloudflare di semua endpoint yang dicoba |
| `SECTORS_API_KEY` | tidak tersedia → net asing & broker summary tidak bisa diambil |

Keduanya ditulis `data tidak tersedia` beserta alasannya. Tata letak tabel
`bi.go.id` bisa berubah sewaktu-waktu — tes memakai fixture HTML asli, jadi
perubahan struktur akan terlihat sebagai tes gagal, bukan data salah diam-diam.

### Bug yang ditemukan & diperbaiki

1. **Suffix `.JK` pada simbol non-emiten** (`idx/providers.py`). Aturan lama
   hanya mengecualikan `^` dan `.JK`, sehingga `USDIDR=X` menjadi
   `USDIDR=X.JK` → 404 → kurs & komoditas dilaporkan "tidak tersedia" padahal
   ada. Sekarang hanya kode emiten polos (`[A-Z0-9]{1,6}`) yang diberi `.JK`.
2. **Regex tanggal RDG** ikut memindai digit tahun: "20-21 Januari 2026"
   terbaca **26 Januari**. Sekarang hanya kelompok hari di depan nama bulan
   yang di-parse.

## Faktor eksternal — Tahap 2: rezim, anomali, sensitivitas sektor

Tahap 2 menambah **konteks** di atas data Tahap 1. Skor **belum** memakai
peta sensitivitas sektor (masih `dipakai_skor: false`, menunggu koreksi
Bukhori) — yang ditambahkan di Tahap 2 adalah rezim pasar, penandaan
anomali, dan kalender.

### Modul baru

| Modul | Fungsi |
|---|---|
| `idx/rezim.py` | **sumber tunggal** deteksi rezim pasar (stres / normal / positif) + pemicunya |
| `idx/anomali.py` | **sumber tunggal** periode anomali + definisi jendela evaluasi |
| `idx/test_rezim_integrasi.py` | 78 tes integrasi rezim → skor → payload → UI → backtest |

### Rezim pasar

`rezim.py` dipakai oleh `external.py` (data) dan `pipeline.py` (payload +
flag skor) — hanya **satu** implementasi, supaya data dan skor tidak
berbeda pendapat. Ambang awal: penurunan IHSG > 5% dalam lima sesi, atau
trading halt.

- Rezim **tidak mengubah angka skor**. Ia ditempel sebagai konteks
  terpisah di `skor_detail` tiap baris emiten dengan flag
  `"pasar stres"` + daftar pemicunya.
- Kalau konteks tidak tersedia → `"data tidak tersedia"`, bukan
  `"normal"`. Tidak ada tebakan.
- Banner di dashboard menampilkan rezim + pemicunya apa adanya.

### Anomali: irisan jendela evaluasi

Definisi resmi (`idx/anomali.jendela_beririsan`): sebuah titik dihitung
**dalam anomali** kalau **jendela evaluasinya** (tanggal sinyal s.d.
T+h sesi) **beririsan** dengan periode anomali — bukan hanya kalau
tanggal sinyalnya jatuh di dalam periode. Sifatnya **per-horizon**: satu
titik bisa beririsan untuk T+5 tapi tidak untuk T+20.

Periode aktif: **15–21 Okt 2026** (IHSG turun ~11,1% dalam lima sesi,
disertai usulan reklasifikasi MSCI Emerging → Standalone, belum final).

Konsekuensi yang dinyatakan terbuka di `backtest.json` dan di UI:

- **Sinyal bertanggal 15–21 Okt 2026 belum bisa dievaluasi** pada horizon
  5/10/20 sesi karena jendelanya belum selesai. Jadi angka "terpengaruh"
  dihitung dari sinyal **sebelum** periode yang jendelanya menabrak
  periode anomali.
- Backtest melaporkan statistik **DENGAN** dan **TANPA** anomali, per
  horizon, beserta jumlah titik yang terpengaruh.
- Memperpanjang `history.json` bersifat **opsional** — bukan syarat agar
  statistiknya Valid.

Hasil 21 Okt 2026: horizon 5 & 10 → 0 titik terpengaruh; horizon 20 →
**4.491 titik**. Dampak pada rata-rata kelompok skor T+20: skor rendah
+3,71% → +4,88%, sedang +2,75% → +3,82%, tinggi +4,88% → +5,53% (tanpa
anomali). Kesimpulan: efek guncangan 15 Okt depressing rata-rata T+20.

### Backtest: return mentah vs excess, dan jumlah sinyal unik

**`rata2` adalah RETURN MENTAH emiten — BUKAN excess.** Ini penting
dibaca: kalau pasar bullish, return mentah ikut terangkat oleh tren pasar
dan itu **bukan** bukti strateginya bagus. Karena itu keduanya
ditampilkan berdampingan:

| Field | Arti |
|---|---|
| `rata2` | return mentah emiten (persen) |
| `ihsg_rata2` | return IHSG pada titik yang sama |
| `excess` | `rata2 − ihsg_rata2` (selisih dua rata-rata) |
| `excess_rata2` / `excess_median` / `excess_menang` | statistik excess **point-wise** (emiten − IHSG per titik), dipakai untuk win-rate |
| `jenis_rata2` | penanda eksplisit `"return mentah (bukan excess)"` |

**Sinyal unik** (`sinyal_unik`): satu sinyal unik = satu **episode** per
(emiten, label sinyal), dihitung pada **tanggal sinyal pertama** yang
muncul. Sinyal beruntun (mis. Breakout 7 sesi berturut-turut) dihitung
satu episode, bukan 7. Jumlah *titik* (n) menghitung ulang episode yang
sama, jadi selalu ≥ jumlah sinyal unik. Dilaporkan per horizon dan per
kategori (per sinyal & per kelompok skor), dengan versi `tanpa_anomali`.

### Peta sensitivitas 56 sektor — MENUNGGU KOREKSI

`config/sensitivitas_sektor.json` memuat 56 sektor × 9 faktor (USD/IDR,
BI Rate, inflasi, minyak, CPO, emas, bijih besi, risk-off asing, beta IHSG)
dengan arah −3..+3 dan alasan singkat. Nilai 0 = **tidak jelas**, bukan
tebakan.

- Status `usulan` · **`dipakai_skor: false`** — belum masuk skor.
- Ubah `dipakai_skor` menjadi `true` HANYA setelah Bukhori mengoreksi
  nilainya.
- Berkas koreksi: `config/sensitivitas_sektor.csv` (bisa dibuka di
  Sheets) dan `config/sensitivitas_sektor_review.md` (versi markdown).

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
- Modul J1/J2 memakai `realpath(__file__)` untuk menemukan `config/` dan
  `data/`, sehingga tetap benar saat dipanggil lewat symlink
  `/root/scripts/idx`.
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
