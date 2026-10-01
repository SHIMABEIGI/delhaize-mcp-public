#!/usr/bin/env python3
"""Command line front end for the Delhaize receipt fetcher.

  delhaize-receipts login            open a window, log in once
  delhaize-receipts status           is the saved session still valid
  delhaize-receipts list  [--months N]   show receipts, flag which are new
  delhaize-receipts fetch [--months N]   save new receipt images to receipts_inbox
  delhaize-receipts backfill [--months N]  save the missing photos of receipts already on record

`fetch` is the one you run routinely. It never transcribes or records anything:
it drops the new receipt images into receipts_inbox for you (or an assistant) to
read and record.

British English, no em dashes.
"""
import argparse
import sys

from . import core as C
from . import browser as B


def cmd_login(args):
    ok = B.login()
    return 0 if ok else 1


def cmd_status(args):
    ok = B.auth_status()
    print('Logged in: the saved session is valid.' if ok
          else 'Not logged in. Run:  delhaize-receipts login')
    return 0 if ok else 1


def cmd_list(args):
    metas = B.list_receipts(args.months)
    keys = C.existing_keys(C.receipts_all_path())
    if not metas:
        print('No receipts found for the selected window.')
        return 0
    new = 0
    for m in metas:
        flag = 'NEW ' if C.is_new(m, keys) else '  . '
        new += C.is_new(m, keys)
        sv = f'  saved {m.savings:.2f}' if m.savings else ''
        print(f'{flag}{m.date}  {m.store:<24}  EUR {m.total:>7.2f}  '
              f'{m.points:>3} pts{sv}')
    print(f'\n{len(metas)} receipt(s), {new} new (not yet in receipts_all.json).')
    return 0


def cmd_backfill(args):
    """Photos only, for receipts already in the record that have none. Saved into
    receipts_backfill/, never receipts_inbox, so nothing is ingested twice."""
    missing = C.missing_photo_keys()
    if not missing:
        print('Every Delhaize receipt on record already has its photo.')
        return 0
    metas = B.list_receipts(args.months)
    todo = [m for m in metas if C.needs_backfill(m, missing)]
    listed = {(m.date, round(float(m.total), 2)) for m in metas}
    gone = sorted(k for k in missing if k not in listed)
    print(f'{len(missing)} receipt(s) on record without a photo; {len(todo)} found in your account.')
    saved, missed = [], []
    if todo:
        images = B.fetch_images(todo, args.months)
        for m in todo:
            src = images.get(m.key())
            if not src:
                missed.append(m)
                continue
            saved.append(C.save_receipt_image(src, m, C.backfill_path(), C.backfill_name(m)))
    for path in saved:
        print(f'  saved  {path}')
    for m in missed:
        print(f'  MISSED {m.date} {m.store} EUR {m.total:.2f} (listed, but its image would not open)')
    for d, t in gone:
        print(f'  not in the account window: {d} EUR {t:.2f}')
    print(f'\nSaved {len(saved)} photo(s) into receipts_backfill. '
          'Each is named by date, store and total, ready to attach to its record.')
    return 0 if not missed else 2


def cmd_fetch(args):
    metas = B.list_receipts(args.months)
    keys = C.existing_keys(C.receipts_all_path())
    new = [m for m in metas if C.is_new(m, keys)]
    if not new:
        print('Nothing new. receipts_all.json already has every receipt in the window.')
        return 0
    print(f'{len(new)} new receipt(s) to fetch:')
    for m in new:
        print(f'  {m.date}  {m.store}  EUR {m.total:.2f}')
    images = B.fetch_images(new, args.months)
    saved, missed = [], []
    for m in new:
        src = images.get(m.key())
        if not src:
            missed.append(m)
            continue
        path = C.save_receipt_image(src, m, C.inbox_path())
        saved.append((m, path))
    print()
    for m, path in saved:
        print(f'  saved  {path}')
    for m in missed:
        print(f'  MISSED {m.date} {m.store} EUR {m.total:.2f} (could not open its image)')
    print(f'\nSaved {len(saved)} of {len(new)} into receipts_inbox.')
    if saved:
        print('Next: read each image and record it in receipts_all.json '
              '(check that its lines sum to the printed total).')
    return 0 if not missed else 2


def cmd_promos(args):
    out_dir = C.promos_inbox_path()
    print(f'Reading this week\'s Delhaize promotions into {out_dir} ...')
    s = B.fetch_promotions(out_dir, args.max_pages)
    print(f'Saved {s["pages"]} page(s): {s["tiles"]} product tiles, '
          f'{s["api_bodies"]} data responses. Stopped: {s["stopped"]}.')
    if not s['pages']:
        print('Nothing was captured. The listing may have changed; the page may need a look.')
        return 1
    print('Next: in the Claude app say "fetch promotions" so it builds the Near me panel.')
    return 0


def main():
    ap = argparse.ArgumentParser(description='Fetch Delhaize receipts into receipts_inbox.')
    sub = ap.add_subparsers(dest='cmd', required=True)
    sub.add_parser('login').set_defaults(func=cmd_login)
    sub.add_parser('status').set_defaults(func=cmd_status)
    pp = sub.add_parser('promos', help='save this week\'s promotions listing into promos_inbox')
    pp.add_argument('--max-pages', type=int, default=40)
    pp.set_defaults(func=cmd_promos)
    bp = sub.add_parser('backfill', help='save the missing photos of receipts already on record')
    bp.add_argument('--months', type=int, default=3)
    bp.set_defaults(func=cmd_backfill)
    for name in ('list', 'fetch'):
        p = sub.add_parser(name)
        p.add_argument('--months', type=int, default=1,
                       help='how many months back to include (default 1, i.e. this month and last)')
        p.set_defaults(func={'list': cmd_list, 'fetch': cmd_fetch}[name])
    args = ap.parse_args()
    return args.func(args)


if __name__ == '__main__':
    sys.exit(main())
