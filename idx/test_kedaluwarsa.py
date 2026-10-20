"""
Unit test logika "data basi" (A7) — versi JS dip faithfully di Python.

Logika frontend di index.html: cekKedaluwarsa()
  - hari bursa = bukan akhir pekan DAN bukan hari libur bursa
  - data basi bila sudah ada >= 1 SESI bursa yang selesai sejak data terakhir
  - sesi hari ini dihitung hanya bila sudah lewat jam tutup (16:30)

Jalankan:  python3 /root/scripts/idx/test_kedaluwarsa.py
"""
import datetime as dt
import sys

LIBUR = {
    '2026-01-01', '2026-01-16', '2026-02-16', '2026-02-17', '2026-03-18',
    '2026-03-19', '2026-03-20', '2026-03-23', '2026-03-24', '2026-04-03',
    '2026-05-01', '2026-05-14', '2026-05-15', '2026-05-27', '2026-05-28',
    '2026-06-01', '2026-06-16', '2026-08-17', '2026-08-25', '2026-12-25',
    '2026-12-31',
    '2027-01-01', '2027-01-05', '2027-03-10', '2027-03-11', '2027-03-26',
    '2027-05-06', '2027-05-17', '2027-06-01', '2027-08-17', '2027-09-15',
}
JAM_TUTUP = 16 * 60 + 30  # 16:30


def is_bursa_day(d: dt.date) -> bool:
    return d.weekday() < 5 and d.isoformat() not in LIBUR


def prev_bursa_day(d: dt.date) -> dt.date:
    x = d
    for _ in range(15):
        if is_bursa_day(x):
            return x
        x -= dt.timedelta(days=1)
    return x


def hitung_sesi(a: dt.date, b: dt.date) -> int:
    n = 0
    x = a
    while x < b:
        x += dt.timedelta(days=1)
        if is_bursa_day(x):
            n += 1
    return n


def basi(data_ts: dt.datetime, now: dt.datetime):
    """Return (bool, jumlah_sesi) — sama persis dengan frontend."""
    data_day = prev_bursa_day(data_ts.date())
    batas = now.date()
    menit = now.hour * 60 + now.minute
    if is_bursa_day(batas) and menit < JAM_TUTUP:
        batas -= dt.timedelta(days=1)
    today_bursa = prev_bursa_day(batas)
    diff = hitung_sesi(data_day, today_bursa)
    return diff >= 1, diff


def main() -> int:
    # Senin 2026-10-19 17:00 WIB (sesi sudah tutup)
    senin_malam = dt.datetime(2026, 10, 19, 17, 0)
    # Selasa 2026-10-20 21:00 WIB
    selasa_malam = dt.datetime(2026, 10, 20, 21, 0)
    # Selasa 2026-10-20 10:00 WIB (sesi BELUM tutup)
    selasa_pagi = dt.datetime(2026, 10, 20, 10, 0)
    # Jumat 2026-10-16 17:00
    jumat = dt.datetime(2026, 10, 16, 17, 0)

    cases = [
        # (nama, data_ts, now, expect_basi, expect_sesi)
        ('data hari ini (sesi sudah tutup)', selasa_malam, selasa_malam, False, 0),
        ('data hari ini (sesi belum tutup)', selasa_pagi, selasa_pagi, False, 0),
        ('Jumat -> Selasa, 2 sesi lewat', jumat, selasa_malam, True, 2),
        ('Senin -> Selasa, 1 sesi lewat', senin_malam, selasa_malam, True, 1),
        ('libur IDX: Senin 2026-08-17 (libur) -> Selasa, 1 sesi (Selasa) lewat',
         dt.datetime(2026, 8, 14, 17, 0), dt.datetime(2026, 8, 18, 17, 0), True, 1),
        ('1 libur IDX: Jumat 2026-08-14 -> Senin 2026-08-17', dt.datetime(2026, 8, 14, 17, 0),
         dt.datetime(2026, 8, 17, 17, 0), False, 0),
        ('akhir pekan: Jumat 17:00 -> Senin 10:00 (belum tutup)', jumat,
         dt.datetime(2026, 10, 19, 10, 0), False, 0),
        ('akhir pekan: Jumat 17:00 -> Senin 17:00', jumat, senin_malam, True, 1),
    ]

    gagal = 0
    for nama, data_ts, now, exp_basi, exp_sesi in cases:
        got_basi, got_sesi = basi(data_ts, now)
        ok = (got_basi == exp_basi) and (got_sesi == exp_sesi)
        status = 'PASS' if ok else 'FAIL'
        if not ok:
            gagal += 1
        print(f'[{status}] {nama}: basi={got_basi} (exp {exp_basi}), '
              f'sesi={got_sesi} (exp {exp_sesi})')

    print(f'\n{len(cases) - gagal}/{len(cases)} PASS')
    return 1 if gagal else 0


if __name__ == '__main__':
    sys.exit(main())
