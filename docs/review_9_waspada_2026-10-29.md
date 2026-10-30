# Review 9 — Rincian 429 Waspada Tinggi (baseline 29 Okt 2026)

Status: **DITERAPKAN 30 Okt 2026** setelah disetujui pengguna. Baseline
29 Okt tetap dicatat di bawah untuk audit. Tidak ada perubahan skor
momentum; yang berubah hanya klasifikasi waspada dan akibatnya pada hak
masuk Top 10. Semua flag tetap disimpan dan ditampilkan.

## Angka baseline yang diminta review

- Total emiten: **975**
- Waspada tinggi (aturan lama): **429** (44,0% universe; 46,8% dari 916 emiten yang punya skor)
- Waspada ringan: **123** (status akhir; total waspada = 552)
- Satu emiten bisa punya beberapa kriteria, jadi jumlah per kriteria tidak
  sama dengan jumlah emiten.

### Per kriteria TINGGI (boleh tumpang tindih)

| Kriteria | Jumlah emiten |
|---|---:|
| volume sangat kecil (<100rb) | 148 |
| ROE negatif | 146 |
| penny stock | 118 |
| tembus support 20-hari | 84 |
| lonjakan harga tanpa berita | 3 |

### Kombinasi kriteria TINGGI terbanyak

| Kombinasi | Jumlah |
|---|---:|
| volume sangat kecil saja | 128 |
| ROE negatif saja | 89 |
| penny stock saja | 84 |
| tembus support saja | 60 |
| penny stock + ROE negatif | 30 |
| ROE negatif + volume sangat kecil | 11 |
| ROE negatif + tembus support | 11 |
| tembus support + volume sangat kecil | 6 |
| lonjakan saja | 3 |
| ROE negatif + tembus support + volume sangat kecil | 3 |

Distribusi jumlah kriteria tinggi per emiten: **1 kriteria = 364**,
**2 kriteria = 60**, **3 kriteria = 5**. Artinya mayoritas (364 dari 429)
masuk TINGGI karena satu ciri saja.

## Mengapa 429 terlalu luas

- 128 emiten hanya karena `volume sangat kecil` — ini lebih tepat menjadi
  catatan likuiditas/data, bukan bukti fundamental buruk.
- 84 emiten hanya karena `penny stock`, dan 60 hanya karena `tembus support`;
  keduanya ciri teknikal/harga, bukan bukti laba atau ekuitas rusak.
- Kriteria fundamental berat (`ROE negatif`) hanya 146 emiten; `PER negatif`
  tidak muncul sebagai pemicu tinggi pada payload ini.

## Aturan yang diterapkan (target 15–20% universe)

**Aturan berlaku sejak 30 Okt 2026:**

> Waspada TINGGI hanya jika:
> - ROE negatif / PER negatif, **atau**
> - penny stock **dan** volume sangat kecil, **atau**
> - tembus support **dan** volume sangat kecil.
>
> Ciri tinggi tunggal lainnya (volume sangat kecil saja, penny stock saja,
> tembus support saja, lonjakan saja) turun menjadi RINGAN.

Hasil pada baseline 29 Okt: **152 emiten = 15,6% universe**. Artinya
**277 emiten** turun dari TINGGI ke RINGAN, tetapi tetap tampil di tabel.
Pada data 30 Okt (kondisi pasar berubah), aturan yang sama menghasilkan
**157 emiten = 16,1%**; dashboard menampilkan angka aktual dari payload
terbaru, bukan angka 429 yang bersifat baseline review.

Catatan penting: RINGAN **boleh masuk Top 10**. Perubahan ini sudah
disetujui dan diterapkan; hasil screening (Top 10/Top 20 JP) karena itu
berubah. Rincian sebelum/sesudah tetap disimpan di
`waspada_ringkasan.tinggi_ringkasan.sebelum_penyempitan`.

## Di mana angkanya terlihat

- `data/latest.json → waspada_ringkasan.tinggi_ringkasan`
- `data/latest.json → waspada_ringkasan.usulan_penyempitan`
  (status `DITERAPKAN 30 Okt 2026`)
- Dashboard tab **Alert** → kartu **Rincian Waspada per Kriteria**
  (bagian “Waspada tinggi” dan kotak “Aturan penyempitan”).
