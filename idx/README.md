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

`latest.json` memuat `versi_skema` (saat ini `1.1.0`) dan peta `satuan`.
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
