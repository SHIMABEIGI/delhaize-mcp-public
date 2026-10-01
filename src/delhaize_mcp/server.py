#!/usr/bin/env python3
"""Delhaize receipts MCP server.

Exposes four tools so an assistant can fetch your Delhaize till receipts and
this week's promotions on request, without driving your real browser:

  - delhaize_auth_status        is the saved login still valid
  - delhaize_list_receipts      list recent receipts and flag which are new
  - delhaize_fetch_new_receipts save new receipt images into receipts_inbox
  - delhaize_backfill_images    save missing photos of receipts already on record
  - delhaize_fetch_promotions   save this week's promotions listing into promos_inbox

It never transcribes items, never edits receipts_all.json, and never touches your
password. It only deposits image and JSON files into the data folder; what you
do with them next (transcription, your own records) is up to you. Auth is a local, logged-in browser profile you
create once with:  delhaize-receipts login

Transport: stdio. Run standalone with:  delhaize-mcp
British English, no em dashes.
"""
from __future__ import annotations

import asyncio

from . import core as C
from . import browser as B

try:
    from mcp.server.fastmcp import FastMCP
except ImportError as e:                                    # pragma: no cover
    raise SystemExit('The `mcp` package is not installed. Run: pip install "mcp[cli]"') from e

mcp = FastMCP('delhaize-receipts')


@mcp.tool()
async def delhaize_auth_status() -> dict:
    """Check whether the saved Delhaize browser profile is still logged in.

    Returns {logged_in: bool, message: str}. If logged_in is false, the user must
    run `delhaize-receipts login` once to refresh the session; this server
    cannot and must not perform the login itself.
    """
    ok = await asyncio.to_thread(B.auth_status)
    return {
        'logged_in': ok,
        'message': ('Session valid.' if ok else
                    'Not logged in. Ask the user to run: delhaize-receipts login'),
    }


@mcp.tool()
async def delhaize_list_receipts(months_back: int = 1) -> dict:
    """List Delhaize receipts for the current month and the previous `months_back`
    months, flagging which are not yet in receipts_all.json.

    Read only: opens nothing and writes nothing. Use this to preview before
    fetching. months_back=1 (the default) covers this month and last, which is
    enough to catch a month boundary.

    Returns {count, new_count, receipts:[{date, store, total, points, savings,
    is_new}]}.
    """
    metas = await asyncio.to_thread(B.list_receipts, months_back)
    keys = C.existing_keys(C.receipts_all_path())
    rows = []
    for m in metas:
        d = m.to_dict()
        d['is_new'] = C.is_new(m, keys)
        rows.append(d)
    return {
        'count': len(rows),
        'new_count': sum(r['is_new'] for r in rows),
        'receipts': rows,
    }


@mcp.tool()
async def delhaize_fetch_new_receipts(months_back: int = 1) -> dict:
    """Save the images of any NEW Delhaize receipts (this month and the previous
    `months_back` months) into receipts_inbox, ready for the existing pipeline.

    Not read only: it writes image files into receipts_inbox. It is non
    destructive: it never overwrites, never deletes, and never edits
    receipts_all.json. A receipt already present in receipts_all.json is skipped.

    After it runs, the images can be read (by the assistant or any OCR) and recorded
    in receipts_all.json by your own process; checking that the lines sum to the
    printed total before recording is strongly recommended.

    Returns {new_count, saved:[{date, store, total, path}], missed:[...],
    next_step}.
    """
    metas = await asyncio.to_thread(B.list_receipts, months_back)
    keys = C.existing_keys(C.receipts_all_path())
    new = [m for m in metas if C.is_new(m, keys)]
    if not new:
        return {'new_count': 0, 'saved': [], 'missed': [],
                'next_step': 'Nothing new. receipts_all.json already has every receipt in the window.'}
    images = await asyncio.to_thread(B.fetch_images, new, months_back)
    saved, missed = [], []
    for m in new:
        src = images.get(m.key())
        if not src:
            missed.append(m.to_dict())
            continue
        path = await asyncio.to_thread(C.save_receipt_image, src, m, C.inbox_path())
        saved.append({'date': m.date, 'store': m.store, 'total': m.total, 'path': path})
    return {
        'new_count': len(new),
        'saved': saved,
        'missed': missed,
        'next_step': ('Read each new image and record it in receipts_all.json, checking that '
                      'its lines sum to the printed total.'),
    }


@mcp.tool()
async def delhaize_backfill_images(months_back: int = 3) -> dict:
    """Save the missing PHOTOS of Delhaize receipts that are already in the record
    but have no image. A receipt lacks a photo when its record has no existing
    `image` path and no matching file is already in receipts_backfill/.

    Not read only: it writes image files into receipts_backfill/. It never edits
    receipts_all.json and never uses receipts_inbox, so nothing is ingested twice.
    Each file is named '<date> <store> <total>' so it pairs with its receipt.

    Returns {missing, found, saved:[...], missed:[...], not_in_window:[...], next_step}.
    """
    missing = C.missing_photo_keys()
    if not missing:
        return {'missing': 0, 'found': 0, 'saved': [], 'missed': [], 'not_in_window': [],
                'next_step': 'Every Delhaize receipt on record already has its photo.'}
    metas = await asyncio.to_thread(B.list_receipts, months_back)
    todo = [m for m in metas if C.needs_backfill(m, missing)]
    listed = {(m.date, round(float(m.total), 2)) for m in metas}
    saved, missed = [], []
    if todo:
        images = await asyncio.to_thread(B.fetch_images, todo, months_back)
        for m in todo:
            src = images.get(m.key())
            if not src:
                missed.append(m.to_dict())
                continue
            path = await asyncio.to_thread(C.save_receipt_image, src, m, C.backfill_path(), C.backfill_name(m))
            saved.append({'date': m.date, 'store': m.store, 'total': m.total, 'path': path})
    return {'missing': len(missing), 'found': len(todo), 'saved': saved, 'missed': missed,
            'not_in_window': [{'date': d, 'total': t} for d, t in sorted(missing - listed)],
            'next_step': 'Each image is named by date, store and total; attach it to the matching record.'}


@mcp.tool()
async def delhaize_fetch_promotions(max_pages: int = 40) -> dict:
    """Read this week's public Delhaize promotions listing (store context from the
    saved profile) and save each page's raw data into promos_inbox/<today>/.

    Not read only: it writes JSON files into promos_inbox. It never edits
    anything else and never judges products; parsing the capture is left to your
    own code.

    Returns {pages, tiles, api_bodies, out_dir, stopped, next_step}.
    """
    out_dir = C.promos_inbox_path()
    summary = await asyncio.to_thread(B.fetch_promotions, out_dir, max_pages)
    summary['next_step'] = ('Parse the saved pages (tiles text and api bodies) with your own code.')
    return summary


def main():
    mcp.run()


if __name__ == '__main__':
    main()
