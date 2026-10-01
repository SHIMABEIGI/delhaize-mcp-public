#!/usr/bin/env python3
"""Unit tests for delhaize_core. No browser, no network. Run: python3 test_core.py"""
import json
import os
import tempfile

import delhaize_core as C


def check(name, cond):
    print(('  ok  ' if cond else 'FAIL  ') + name)
    if not cond:
        check.failed += 1
check.failed = 0


# --- date ---
check('date 01/09/2026', C.parse_fr_date('01/09/2026') == '2026-09-01')
check('date inside text', C.parse_fr_date('le 15/08/2026 à 15:08') == '2026-08-15')
try:
    C.parse_fr_date('nope'); check('date rejects junk', False)
except ValueError:
    check('date rejects junk', True)

# --- euro ---
check('eur sign+comma', C.parse_eur('€23,45') == 23.45)
check('eur bare comma', C.parse_eur('0,50') == 0.50)
check('eur trailing word', C.parse_eur('€0,50 épargnés') == 0.50)
check('eur negative', C.parse_eur('-0,22') == -0.22)
check('eur thousands', C.parse_eur('€1.234,56') == 1234.56)
check('eur none', C.parse_eur('points') is None)

# --- points ---
check('points 10', C.parse_points('10 points') == 10)
check('points none', C.parse_points('') == 0)

# --- store / filename ---
check('store trim', C.clean_store('  Proxy   Delhaize Centre ') == 'Proxy Delhaize Centre')
check('safe filename accents', C.safe_filename('Épicerie Château') == 'Epicerie Chateau')

# --- meta + naming ---
m = C.meta_from_fields('01/09/2026', 'Proxy Delhaize Centre', '€23,45', '10 points', '€0,50 épargnés')
check('meta date', m.date == '2026-09-01')
check('meta total', m.total == 23.45)
check('meta points', m.points == 10)
check('meta savings', m.savings == 0.50)
check('image name', m.image_name() == '2026-09-01 Proxy Delhaize Centre.jpeg')
check('meta key', m.key() == ('2026-09-01', 'proxy delhaize centre', 23.45))

# --- dedup against a fake receipts_all.json ---
with tempfile.TemporaryDirectory() as d:
    ra = os.path.join(d, 'receipts_all.json')
    json.dump([
        {'date': '2026-08-18', 'store': 'Delhaize Centre', 'total': 12.34, 'items': []},
        {'date': '2026-08-15', 'total': 7.88, 'items': []},   # store-less older row
    ], open(ra, 'w'))
    keys = C.existing_keys(ra)
    old1 = C.meta_from_fields('18/08/2026', 'Delhaize Centre', '€12,34')
    old2 = C.meta_from_fields('15/08/2026', "L'Archenterre", '€7,88')  # store differs, total+date match
    new1 = C.meta_from_fields('01/09/2026', 'Proxy Delhaize Centre', '€23,45')
    check('dedup catches exact', not C.is_new(old1, keys))
    check('dedup store-blind catches total+date', not C.is_new(old2, keys))
    check('dedup lets new through', C.is_new(new1, keys))

# --- data url decode + save ---
# a 1x1 red pixel jpeg, base64
PIX = ('data:image/jpeg;base64,/9j/4AAQSkZJRgABAQEAYABgAAD/2wBDAAgGBgcGBQgHBwcJCQgK'
       'DBQNDAsLDBkSEw8UHRofHh0aHBwgJC4nICIsIxwcKDcpLDAxNDQ0Hyc5PTgyPC4zNDL/wAALCAAB'
       'AAEBAREA/8QAFAABAAAAAAAAAAAAAAAAAAAAAv/EABQQAQAAAAAAAAAAAAAAAAAAAAD/2gAIAQEA'
       'AD8AfwD/2Q==')
with tempfile.TemporaryDirectory() as d:
    raw, ext = C.decode_data_url(PIX)
    check('decode ext', ext == 'jpeg')
    check('decode bytes', raw[:2] == b'\xff\xd8')   # JPEG SOI marker
    p = C.save_receipt_image(PIX, m, d)
    check('saved file exists', os.path.exists(p))
    check('saved name', os.path.basename(p) == '2026-09-01 Proxy Delhaize Centre.jpeg')
    # saving identical bytes again returns same path, no duplicate
    p2 = C.save_receipt_image(PIX, m, d)
    check('idempotent save', p2 == p and len(os.listdir(d)) == 1)

try:
    C.decode_data_url('data:text/plain;base64,aGk='); check('decode rejects non-image', False)
except ValueError:
    check('decode rejects non-image', True)

# --- backfill: photos only for receipts already on record without one ---
with tempfile.TemporaryDirectory() as td:
    os.makedirs(os.path.join(td, 'photos')); open(os.path.join(td, 'photos', 'x.jpeg'), 'wb').write(b'x')
    os.makedirs(os.path.join(td, 'receipts_backfill'))
    open(os.path.join(td, 'receipts_backfill', '2026-08-12 Delhaize Centre 7.77.jpeg'), 'wb').write(b'x')
    ra = os.path.join(td, 'receipts_all.json')
    json.dump([
        {'date': '2026-08-11', 'store': 'Delhaize Centre', 'total': 11.11},
        {'date': '2026-08-30', 'store': 'Delhaize Centre', 'total': 18.90, 'image': 'photos/x.jpeg'},
        {'date': '2026-08-12', 'store': 'Delhaize Centre', 'total': 7.77},
        {'date': '2026-09-05', 'store': 'Market', 'total': 2.95, 'source': 'paper'}], open(ra, 'w'))
    miss = C.missing_photo_keys(ra, os.path.join(td, 'receipts_backfill'))
    check('backfill lists only receipts without any photo', miss == {('2026-08-11', 11.11)})
    m = C.ReceiptMeta(date='2026-08-11', store='Delhaize Centre', total=11.11)
    check('backfill picks the listed receipt', C.needs_backfill(m, miss))
    check('backfill skips one whose image exists',
          not C.needs_backfill(C.ReceiptMeta(date='2026-08-30', store='Delhaize Centre', total=18.90), miss))
    check('backfill name carries date, store and total', C.backfill_name(m) == '2026-08-11 Delhaize Centre 11.11')
    png = 'data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg=='
    out = C.save_receipt_image(png, m, os.path.join(td, 'receipts_backfill'), C.backfill_name(m))
    check('backfill saves under its own name, outside receipts_inbox',
          out.endswith('receipts_backfill/2026-08-11 Delhaize Centre 11.11.png'))
    check('a saved backfill photo is no longer missing',
          C.missing_photo_keys(ra, os.path.join(td, 'receipts_backfill')) == set())
    check('missing record file gives an empty set', C.missing_photo_keys(os.path.join(td, 'nope.json')) == set())

print()
print('ALL PASS' if check.failed == 0 else f'{check.failed} FAILED')
raise SystemExit(1 if check.failed else 0)
