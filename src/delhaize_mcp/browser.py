#!/usr/bin/env python3
"""Drive a headless browser to read the Delhaize 'Mes tickets de caisse' page.

Why a browser and not an API call: Delhaize renders each receipt as a base64
JPEG inside a client-side app, behind a persisted, cached GraphQL layer that is
awkward and brittle to replay. Letting the real app render and reading the
finished DOM is the sturdy path, and it is exactly how a person reads the page.

Auth is a PERSISTENT PROFILE, not a password. You log in once, in a window this
script opens (the `login` step), and the session is kept in a local profile
folder. Nothing here ever sees or stores your password; later runs reuse the
profile headlessly. If the session lapses, you simply run `login` again.

This module is synchronous (Playwright sync API). The MCP server calls it from a
worker thread. British English, no em dashes.
"""
from __future__ import annotations

import os
import time
from datetime import date

from .core import ReceiptMeta, meta_from_fields

TICKETS_URL = 'https://www.delhaize.be/fr/my-account/loyalty/tickets'

FR_MONTHS = ['janvier', 'février', 'mars', 'avril', 'mai', 'juin', 'juillet',
             'août', 'septembre', 'octobre', 'novembre', 'décembre']


def profile_dir() -> str:
    """Where the logged-in browser profile lives: ~/.delhaize-mcp/profile, or set
    DELHAIZE_PROFILE. It holds your session cookies: never commit or share it."""
    env = os.environ.get('DELHAIZE_PROFILE')
    if env:
        return env
    return os.path.join(os.path.expanduser('~'), '.delhaize-mcp', 'profile')


def month_labels(months_back: int) -> list[str]:
    """French 'month yyyy' labels for this month and the previous months_back
    months, most recent first, e.g. ['septembre 2026', 'août 2026']."""
    y, mo = date.today().year, date.today().month
    out = []
    for _ in range(months_back + 1):
        out.append(f'{FR_MONTHS[mo - 1]} {y}')
        mo -= 1
        if mo == 0:
            mo = 12
            y -= 1
    return out


# ---------------------------------------------------------------------------

def _make_context(pw, headless: bool):
    os.makedirs(profile_dir(), exist_ok=True)
    args = ['--disable-blink-features=AutomationControlled']
    kwargs = dict(headless=headless, locale='fr-BE', args=args)
    if headless:
        # a tall fixed viewport headless, so the whole receipts table renders
        kwargs['viewport'] = {'width': 1280, 'height': 1600}
    else:
        # headed: use the real, maximised window so a cookie banner is never
        # pushed off screen where its buttons cannot be reached
        args.append('--start-maximized')
        kwargs['no_viewport'] = True
    return pw.chromium.launch_persistent_context(profile_dir(), **kwargs)


def _page(ctx):
    return ctx.pages[0] if ctx.pages else ctx.new_page()


# The cookie consent overlay Delhaize shows on a fresh profile. We choose the
# privacy-preserving option (refuse non-essential); only if no refuse control is
# found do we fall back to accept, purely to unblock the window. The choice is
# stored in the profile, so it does not reappear on later headless runs.
_COOKIE_REFUSE = [
    '#onetrust-reject-all-handler',
    'button:has-text("Continuer sans accepter")',
    'button:has-text("Tout refuser")',
    'button:has-text("Refuser tout")',
    'button:has-text("Refuser")',
    'button:has-text("Alleen noodzakelijke")',
    'button:has-text("Alles weigeren")',
    'button:has-text("Weigeren")',
]
_COOKIE_ACCEPT = [
    '#onetrust-accept-btn-handler',
    'button:has-text("Tout accepter")',
    'button:has-text("Accepter")',
    'button:has-text("Alles accepteren")',
    'button:has-text("Accepteren")',
]


def _dismiss_cookies(page) -> bool:
    """Best effort: click the cookie banner away (refuse first) so it stops
    blocking the login. Searches the page and any consent iframe. Harmless if no
    banner is present."""
    for sel in _COOKIE_REFUSE + _COOKIE_ACCEPT:
        for frame in page.frames:
            try:
                el = frame.query_selector(sel)
                if el and el.is_visible():
                    el.click(timeout=2000)
                    page.wait_for_timeout(500)
                    return True
            except Exception:
                continue
    return False


def _on_tickets_and_ready(page, timeout_ms=15000) -> bool:
    """Return True if the receipts table has rendered (so we are logged in)."""
    try:
        page.wait_for_selector('[data-testid="my-receipts-list-table"], '
                               '[data-testid="my-receipts-list-row"]',
                               timeout=timeout_ms)
        return True
    except Exception:
        return False


def login(open_seconds: int = 300) -> bool:
    """Open a visible window on the tickets page and wait for you to log in.
    Returns True once the receipts table appears (or False if it times out)."""
    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        ctx = _make_context(pw, headless=False)
        page = _page(ctx)
        page.goto(TICKETS_URL, wait_until='domcontentloaded')
        page.wait_for_timeout(1500)
        _dismiss_cookies(page)                 # clear the consent overlay for the user
        print('A browser window is open. Log in to Delhaize if asked, then wait.')
        print('The cookie banner is dismissed automatically; if one lingers, click it away.')
        deadline = time.time() + open_seconds
        ok = False
        while time.time() < deadline:
            _dismiss_cookies(page)             # in case it appears after the login redirect
            if _on_tickets_and_ready(page, timeout_ms=3000):
                ok = True
                break
            time.sleep(2)
        # give the profile a moment to flush cookies to disk
        time.sleep(2)
        ctx.close()
        print('Logged in and profile saved.' if ok else 'Timed out waiting for login.')
        return ok


def auth_status() -> bool:
    """Headlessly check whether the saved profile is still logged in."""
    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        ctx = _make_context(pw, headless=True)
        page = _page(ctx)
        try:
            page.goto(TICKETS_URL, wait_until='domcontentloaded')
            _dismiss_cookies(page)
            return _on_tickets_and_ready(page, timeout_ms=12000)
        finally:
            ctx.close()


# ---------------------------------------------------------------------------

def _read_rows_current_month(page) -> list[ReceiptMeta]:
    """Read every receipt row currently displayed (one month) into ReceiptMeta."""
    metas = []
    rows = page.query_selector_all('[data-testid="my-receipts-list-row"]')
    for row in rows:
        def txt(sel):
            el = row.query_selector(sel)
            return el.inner_text() if el else ''
        raw_date = txt('[data-testid="my-receipts-date"]')
        store = txt('[data-testid="my-receipts-store"]')
        total = txt('[data-testid="my-receipts-list-button"]')
        points = txt('[data-testid="my-receipts-points"]')
        savings = txt('[data-testid="my-receipts-savings"]')
        if not raw_date or total in ('', None):
            continue
        try:
            metas.append(meta_from_fields(raw_date, store, total, points, savings))
        except ValueError:
            continue
    return metas


def _click_month(page, label: str) -> bool:
    """Click the month selector button whose text is `label`. Returns True if a
    matching button was found and clicked."""
    buttons = page.query_selector_all('button')
    target = label.strip().lower()
    for b in buttons:
        try:
            if (b.inner_text() or '').strip().lower() == target:
                b.click()
                page.wait_for_timeout(1200)
                return True
        except Exception:
            continue
    return False


def list_receipts(months_back: int = 1) -> list[ReceiptMeta]:
    """Return receipts for this month and the previous months_back months."""
    from playwright.sync_api import sync_playwright
    seen = {}
    with sync_playwright() as pw:
        ctx = _make_context(pw, headless=True)
        page = _page(ctx)
        try:
            page.goto(TICKETS_URL, wait_until='domcontentloaded')
            _dismiss_cookies(page)
            if not _on_tickets_and_ready(page):
                raise RuntimeError('not logged in, or the tickets page did not load. '
                                   'Run the login step first.')
            for i, label in enumerate(month_labels(months_back)):
                if i > 0 and not _click_month(page, label):
                    continue          # month not offered (no receipts that month)
                for m in _read_rows_current_month(page):
                    seen[m.key()] = m
        finally:
            ctx.close()
    return sorted(seen.values(), key=lambda m: (m.date, m.store))


# ---------------------------------------------------------------------------
# Promotions: the public weekly promotions listing. No login is needed to read it,
# but the saved profile carries your chosen store, so prices match your shop. This
# only CAPTURES raw page data; interpreting it is left to your own code.

PROMOS_URL = 'https://www.delhaize.be/fr/promotions'

# JS run in the page: every element whose data-testid mentions a product and whose
# text carries a euro price is treated as a product tile. Generic on purpose, so a
# small markup change does not silently return nothing.
_TILE_JS = r"""
() => {
  const out = [];
  const seen = new Set();
  document.querySelectorAll('[data-testid]').forEach(el => {
    const id = el.getAttribute('data-testid') || '';
    if (!/product/i.test(id)) return;
    const txt = (el.innerText || '').trim();
    if (!txt || txt.indexOf('€') < 0 || txt.length > 900) return;
    if (seen.has(txt)) return;
    // skip wrappers that contain several priced tiles
    if ((txt.match(/€/g) || []).length > 6) return;
    seen.add(txt);
    const a = el.querySelector('a[href]');
    out.push({testid: id, text: txt, href: a ? a.href : ''});
  });
  return out;
}
"""


def fetch_promotions(out_dir: str, max_pages: int = 40) -> dict:
    """Page through the promotions listing headlessly and save each page's raw
    data (product JSON the app loads, plus product-tile text) as
    out_dir/page_NN.json. Returns a summary. Stops when a page brings nothing new."""
    import json as _json
    from playwright.sync_api import sync_playwright
    os.makedirs(out_dir, exist_ok=True)
    summary = {'pages': 0, 'tiles': 0, 'api_bodies': 0, 'out_dir': out_dir, 'stopped': ''}
    with sync_playwright() as pw:
        ctx = _make_context(pw, headless=True)
        page = _page(ctx)
        captured = []

        def _on_response(resp):
            try:
                ct = (resp.headers or {}).get('content-type', '')
                if 'json' not in ct:
                    return
                body = resp.text()
                if '"price' in body and '"name' in body and len(body) < 5_000_000:
                    captured.append({'url': resp.url, 'body': body})
            except Exception:
                pass

        page.on('response', _on_response)
        prev_names = None
        try:
            for n in range(max_pages):
                captured.clear()
                url = f'{PROMOS_URL}?pageNumber={n}'
                page.goto(url, wait_until='domcontentloaded')
                _dismiss_cookies(page)
                page.wait_for_timeout(2500)
                # scroll to the bottom so lazily loaded tiles render
                for _ in range(8):
                    page.mouse.wheel(0, 2400)
                    page.wait_for_timeout(350)
                page.wait_for_timeout(800)
                tiles = page.evaluate(_TILE_JS) or []
                names = sorted(t['text'][:60] for t in tiles)
                rec = {'url': url, 'page_number': n, 'captured_at': time.strftime('%Y-%m-%dT%H:%M:%S'),
                       'tiles': tiles, 'api': list(captured)}
                if not tiles and not captured:
                    summary['stopped'] = f'page {n} brought no products'
                    break
                if prev_names is not None and names and names == prev_names:
                    summary['stopped'] = f'page {n} repeated the previous page (end of listing)'
                    break
                with open(os.path.join(out_dir, f'page_{n:02d}.json'), 'w', encoding='utf-8') as f:
                    _json.dump(rec, f, ensure_ascii=False)
                summary['pages'] += 1
                summary['tiles'] += len(tiles)
                summary['api_bodies'] += len(captured)
                prev_names = names
            else:
                summary['stopped'] = f'reached the {max_pages}-page cap'
        finally:
            ctx.close()
    return summary


def _dismiss_modal(page):
    try:
        page.keyboard.press('Escape')
        page.wait_for_timeout(400)
    except Exception:
        pass


def fetch_images(targets: list[ReceiptMeta], months_back: int = 1) -> dict:
    """For each ReceiptMeta in `targets`, open its receipt and return its image
    data URL. Returns {key: data_url}. Rows are matched by date and total so the
    right ticket is opened. Missing ones are simply absent from the result."""
    from playwright.sync_api import sync_playwright
    want = {t.key(): t for t in targets}
    got: dict = {}
    with sync_playwright() as pw:
        ctx = _make_context(pw, headless=True)
        page = _page(ctx)
        try:
            page.goto(TICKETS_URL, wait_until='domcontentloaded')
            _dismiss_cookies(page)
            if not _on_tickets_and_ready(page):
                raise RuntimeError('not logged in. Run the login step first.')
            for i, label in enumerate(month_labels(months_back)):
                if i > 0:
                    _click_month(page, label)
                rows = page.query_selector_all('[data-testid="my-receipts-list-row"]')
                for row in rows:
                    d = row.query_selector('[data-testid="my-receipts-date"]')
                    b = row.query_selector('[data-testid="my-receipts-list-button"]')
                    s = row.query_selector('[data-testid="my-receipts-store"]')
                    if not (d and b):
                        continue
                    try:
                        m = meta_from_fields(d.inner_text(),
                                             s.inner_text() if s else '',
                                             b.inner_text())
                    except ValueError:
                        continue
                    if m.key() not in want or m.key() in got:
                        continue
                    b.click()
                    try:
                        img = page.wait_for_selector('img[src^="data:image/jpeg"], '
                                                     'img[src^="data:image/png"]',
                                                     timeout=8000)
                        src = img.get_attribute('src')
                        if src:
                            got[m.key()] = src
                    except Exception:
                        pass
                    _dismiss_modal(page)
        finally:
            ctx.close()
    return got
