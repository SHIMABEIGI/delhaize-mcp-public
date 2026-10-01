#!/usr/bin/env python3
"""Pure, browser-free logic for the Delhaize receipt fetcher.

Everything here can be unit tested without a browser or a network: parsing the
little strings the tickets page renders, deciding which receipts are genuinely
new, naming the image files, and decoding a data: URL to bytes on disk. The
browser driver (browser.py) and the MCP server (server.py) both lean on
these functions so there is exactly one place where each rule lives.

Design notes, in the spirit of the rest of the pipeline:
  - A receipt is identified for de-duplication by (date, store, total). The till
    time is not known until the image is transcribed, so it cannot be used here.
    Total is precise to the cent, so a second, different shop on the same day at
    the same store is still distinguishable by its total.
  - Nothing here transcribes items or invents categories. It only gets the image
    onto disk and hands the metadata across; what you do with the images next is
    up to your own pipeline.

British English, no em dashes.
"""
from __future__ import annotations

import base64
import json
import os
import re
import unicodedata
from dataclasses import dataclass, asdict, field


# ---------------------------------------------------------------------------
# Small string parsers. Each takes the raw text the page shows and returns a
# clean value. They are deliberately forgiving: the page uses a comma decimal,
# a euro sign, and French month words, and any of those can be absent.
# ---------------------------------------------------------------------------

def parse_fr_date(s: str) -> str:
    """'01/09/2026' -> '2026-09-01'. Raises ValueError on anything unexpected."""
    m = re.search(r'(\d{2})/(\d{2})/(\d{4})', s or '')
    if not m:
        raise ValueError(f'no DD/MM/YYYY date found in {s!r}')
    day, month, year = m.group(1), m.group(2), m.group(3)
    return f'{year}-{month}-{day}'


def parse_eur(s: str):
    """'€23,45' or '23,45' or '€0,50 épargnés' -> 23.45 / 0.50. Handles a
    leading minus. Returns None if there is no number at all."""
    if s is None:
        return None
    t = str(s).replace(' ', ' ')
    m = re.search(r'(-?)\s*€?\s*(\d{1,4}(?:[.,]\d{3})*[.,]\d{2}|\d+)', t)
    if not m:
        return None
    sign = -1.0 if m.group(1) == '-' else 1.0
    num = m.group(2)
    # normalise thousands and decimal separators: last separator is the decimal
    num = num.replace(' ', '')
    if ',' in num and '.' in num:
        # both present: the last one is the decimal separator
        if num.rfind(',') > num.rfind('.'):
            num = num.replace('.', '').replace(',', '.')
        else:
            num = num.replace(',', '')
    else:
        num = num.replace(',', '.')
    return round(sign * float(num), 2)


def parse_points(s: str) -> int:
    """'10 points' -> 10. Returns 0 if none found."""
    m = re.search(r'(\d+)', s or '')
    return int(m.group(1)) if m else 0


def clean_store(s: str) -> str:
    """Trim and collapse whitespace in a store name; keep it human readable."""
    return re.sub(r'\s+', ' ', (s or '').strip())


def safe_filename(s: str) -> str:
    """Make a string safe as a file name component: keep letters, digits, spaces,
    dash and underscore; strip accents so the shell is never surprised."""
    s = unicodedata.normalize('NFKD', s or '').encode('ascii', 'ignore').decode('ascii')
    s = re.sub(r'[^A-Za-z0-9 _-]', '', s).strip()
    s = re.sub(r'\s+', ' ', s)
    return s or 'receipt'


# ---------------------------------------------------------------------------
# The receipt as the page shows it, before any transcription.
# ---------------------------------------------------------------------------

@dataclass
class ReceiptMeta:
    date: str            # ISO YYYY-MM-DD
    store: str
    total: float
    points: int = 0
    savings: float | None = None
    raw_date: str = ''   # the DD/MM/YYYY the page showed, kept for the record

    def key(self):
        """De-duplication identity: same day, same store, same money."""
        return (self.date, clean_store(self.store).lower(), round(float(self.total), 2))

    def image_name(self, ext: str = 'jpeg') -> str:
        """A clear, sortable file name, e.g. '2026-09-01 Proxy Delhaize Centre.jpeg'.
        Any name is fine for receipts_inbox; this one reads well in a listing."""
        return f'{self.date} {safe_filename(self.store)}.{ext}'

    def to_dict(self):
        return asdict(self)


def meta_from_fields(raw_date: str, store: str, total_txt: str,
                     points_txt: str = '', savings_txt: str = '') -> ReceiptMeta:
    """Build a ReceiptMeta from the raw strings pulled off the page."""
    return ReceiptMeta(
        date=parse_fr_date(raw_date),
        store=clean_store(store),
        total=parse_eur(total_txt),
        points=parse_points(points_txt),
        savings=parse_eur(savings_txt),
        raw_date=(raw_date or '').strip(),
    )


# ---------------------------------------------------------------------------
# Knowing what is already in the record, so nothing is fetched or ingested twice.
# ---------------------------------------------------------------------------

def existing_keys(receipts_all_path: str) -> set:
    """Return the set of (date, store, total) keys already in receipts_all.json.

    receipts_all.json does not always carry a store, and older rows use a
    French till date only in the image, so we key primarily on (date, total)
    and treat store as a secondary, optional discriminator. To stay safe we add
    BOTH a store-aware key and a store-blind key for every existing receipt, so
    a new fetch is considered 'already present' if either matches.
    """
    keys = set()
    if not os.path.exists(receipts_all_path):
        return keys
    try:
        data = json.load(open(receipts_all_path, encoding='utf-8'))
    except (json.JSONDecodeError, OSError):
        return keys
    for r in data:
        try:
            d = r.get('date')
            tot = round(float(r.get('total')), 2)
        except (TypeError, ValueError):
            continue
        store = clean_store(r.get('store', '')).lower()
        keys.add((d, store, tot))
        keys.add((d, '', tot))          # store-blind fallback
    return keys


def is_new(meta: ReceiptMeta, keys: set) -> bool:
    """A receipt is new if neither its store-aware nor its store-blind key is
    already present."""
    d, store, tot = meta.key()
    return (d, store, tot) not in keys and (d, '', tot) not in keys


# ---------------------------------------------------------------------------
# Turning the rendered image into a file on disk.
# ---------------------------------------------------------------------------

def decode_data_url(data_url: str) -> tuple[bytes, str]:
    """'data:image/jpeg;base64,...' -> (raw bytes, extension). Raises ValueError
    if the string is not a base64 image data URL."""
    m = re.match(r'data:image/(png|jpe?g|webp);base64,(.*)$', (data_url or '').strip(), re.S)
    if not m:
        raise ValueError('not a base64 image data URL')
    ext = m.group(1).replace('jpg', 'jpeg')
    raw = base64.b64decode(m.group(2))
    return raw, ext


def save_receipt_image(data_url: str, meta: ReceiptMeta, inbox_dir: str, name: str | None = None) -> str:
    """Decode the image and write it into receipts_inbox with a clear name.
    Returns the path written. Does not overwrite a file that already exists with
    identical bytes; if a same-name file exists with different bytes it appends a
    counter so nothing is silently clobbered."""
    raw, ext = decode_data_url(data_url)
    os.makedirs(inbox_dir, exist_ok=True)
    path = os.path.join(inbox_dir, (name + '.' + ext) if name else meta.image_name(ext))
    if os.path.exists(path):
        try:
            if open(path, 'rb').read() == raw:
                return path            # already there, identical, nothing to do
        except OSError:
            pass
        stem, e = os.path.splitext(path)
        i = 2
        while os.path.exists(f'{stem} ({i}){e}'):
            i += 1
        path = f'{stem} ({i}){e}'
    with open(path, 'wb') as f:
        f.write(raw)
    return path


# ---------------------------------------------------------------------------
# Where your data lives. Set DELHAIZE_DATA_DIR to any folder; by default it is
# ~/.delhaize-mcp/data. receipts_all.json (your record of receipts, a JSON
# list of {date, store, total, ...}) and the inbox folders are kept there.
# ---------------------------------------------------------------------------

def food_root() -> str:
    env = os.environ.get('DELHAIZE_DATA_DIR')
    if env:
        return env
    return os.path.join(os.path.expanduser('~'), '.delhaize-mcp', 'data')


def receipts_all_path() -> str:
    return os.path.join(food_root(), 'receipts_all.json')


def inbox_path() -> str:
    return os.path.join(food_root(), 'receipts_inbox')


def promos_inbox_path(day: str | None = None) -> str:
    """Where a promotions capture is saved: promos_inbox/<YYYY-MM-DD>/ in the data
    folder, one JSON file per page."""
    from datetime import date as _d
    return os.path.join(food_root(), 'promos_inbox', day or _d.today().isoformat())


# ---------------------------------------------------------------------------
# Backfill: images for receipts ALREADY in the record that have no photo.
# ---------------------------------------------------------------------------
# Receipts you recorded some other way (typed in, read from the account page) may
# have every line and price but no picture. Backfill fetches only those pictures.
# It never touches receipts_all.json and never uses receipts_inbox, so a backfilled
# image can never be mistaken for a new shop and recorded twice.

def backfill_path() -> str:
    return os.path.join(food_root(), 'receipts_backfill')


def missing_photo_keys(receipts_path: str | None = None, backfill_dir: str | None = None) -> set:
    """(date, total) of every receipt in receipts_all.json that has no photo yet.

    A receipt counts as having a photo when its record carries an `image` path
    that exists (relative paths are read from the data folder), or when a file
    for its date and total is already in receipts_backfill/. Records with
    `"source": "paper"` are not Delhaize online receipts and are skipped."""
    receipts_path = receipts_path or receipts_all_path()
    backfill_dir = backfill_dir or backfill_path()
    root = os.path.dirname(os.path.abspath(receipts_path))
    try:
        rows = json.load(open(receipts_path, encoding='utf-8'))
    except (OSError, json.JSONDecodeError):
        return set()
    have = set(os.listdir(backfill_dir)) if os.path.isdir(backfill_dir) else set()
    out = set()
    for r in rows if isinstance(rows, list) else []:
        if r.get('source') == 'paper':
            continue
        img = r.get('image')
        if img and os.path.exists(img if os.path.isabs(img) else os.path.join(root, img)):
            continue
        try:
            d, t = r['date'], round(float(r['total']), 2)
        except (KeyError, TypeError, ValueError):
            continue
        if any(n.startswith(d + ' ') and os.path.splitext(n)[0].endswith(f' {t:.2f}') for n in have):
            continue
        out.add((d, t))
    return out


def needs_backfill(meta: ReceiptMeta, missing: set) -> bool:
    return (meta.date, round(float(meta.total), 2)) in missing


def backfill_name(meta: ReceiptMeta) -> str:
    """'2026-08-11 Delhaize Centre 12.34' (extension added on save). The total is in
    the name so it can be paired with its receipt when a day had two shops."""
    return f'{meta.date} {safe_filename(meta.store)} {float(meta.total):.2f}'
