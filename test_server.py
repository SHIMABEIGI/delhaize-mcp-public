#!/usr/bin/env python3
"""End-to-end test of the server tool logic with a stubbed MCP and a mocked
browser. No Playwright, no network, no real mcp package. Run: python3 test_server.py

It proves the important behaviour: existing receipts are skipped, genuinely new
ones have their images decoded and written into receipts_inbox, and the tool
reports honestly.
"""
import asyncio
import json
import os
import sys
import tempfile
import types


def install_fake_mcp():
    """Put a minimal fake `mcp.server.fastmcp.FastMCP` on sys.modules so server.py
    imports without the real package. tool() is an identity decorator."""
    pkg = types.ModuleType('mcp')
    server_mod = types.ModuleType('mcp.server')
    fastmcp_mod = types.ModuleType('mcp.server.fastmcp')

    class FastMCP:
        def __init__(self, name):
            self.name = name
        def tool(self, *a, **k):
            def deco(fn):
                return fn
            return deco
        def run(self, *a, **k):
            pass

    fastmcp_mod.FastMCP = FastMCP
    server_mod.fastmcp = fastmcp_mod
    pkg.server = server_mod
    sys.modules['mcp'] = pkg
    sys.modules['mcp.server'] = server_mod
    sys.modules['mcp.server.fastmcp'] = fastmcp_mod


PIX = ('data:image/jpeg;base64,/9j/4AAQSkZJRgABAQEAYABgAAD/2wBDAAgGBgcGBQgHBwcJCQgK'
       'DBQNDAsLDBkSEw8UHRofHh0aHBwgJC4nICIsIxwcKDcpLDAxNDQ0Hyc5PTgyPC4zNDL/wAALCAAB'
       'AAEBAREA/8QAFAABAAAAAAAAAAAAAAAAAAAAAv/EABQQAQAAAAAAAAAAAAAAAAAAAAD/2gAIAQEA'
       'AD8AfwD/2Q==')

failed = 0
def check(name, cond):
    global failed
    print(('  ok  ' if cond else 'FAIL  ') + name)
    if not cond:
        failed += 1


def main():
    global failed
    install_fake_mcp()

    with tempfile.TemporaryDirectory() as root:
        # a food root with one existing receipt and an empty inbox
        json.dump([{'date': '2026-08-30', 'store': 'Delhaize Centre', 'total': 18.90, 'items': []}],
                  open(os.path.join(root, 'receipts_all.json'), 'w'))
        os.makedirs(os.path.join(root, 'receipts_inbox'))
        os.environ['DELHAIZE_DATA_DIR'] = root

        import delhaize_core as C
        import delhaize_browser as B
        import server

        # mock the browser: one already-recorded receipt, two genuinely new ones
        existing = C.meta_from_fields('30/08/2026', 'Delhaize Centre', '€18,90', '12 points')
        new1 = C.meta_from_fields('31/08/2026', 'Proxy Delhaize Centre', '€9,87', '8 points')
        new2 = C.meta_from_fields('01/09/2026', 'Proxy Delhaize Centre', '€23,45', '10 points')

        B.list_receipts = lambda months_back=1: [existing, new1, new2]
        # only new2 opens successfully; new1 fails to open (to test 'missed' reporting)
        B.fetch_images = lambda targets, months_back=1: {new2.key(): PIX}

        # list tool flags the right ones as new
        listing = asyncio.run(server.delhaize_list_receipts(1))
        check('list count', listing['count'] == 3)
        check('list new_count', listing['new_count'] == 2)
        flags = {r['date']: r['is_new'] for r in listing['receipts']}
        check('existing not new', flags['2026-08-30'] is False)
        check('new1 is new', flags['2026-08-31'] is True)

        # fetch tool saves the openable new receipt and reports the missed one
        res = asyncio.run(server.delhaize_fetch_new_receipts(1))
        check('fetch new_count', res['new_count'] == 2)
        check('one saved', len(res['saved']) == 1)
        check('one missed', len(res['missed']) == 1)
        saved_path = res['saved'][0]['path']
        check('saved file on disk', os.path.exists(saved_path))
        check('saved into inbox', os.path.basename(os.path.dirname(saved_path)) == 'receipts_inbox')
        check('saved name', os.path.basename(saved_path) == '2026-09-01 Proxy Delhaize Centre.jpeg')
        check('missed is new1', res['missed'][0]['date'] == '2026-08-31')

        # idempotent: nothing new the second time (still only same file, no dup)
        res2 = asyncio.run(server.delhaize_fetch_new_receipts(1))
        # new1 still missing image, new2 already saved but still 'new' vs receipts_all
        # (it only enters receipts_all after ingest); saving identical bytes must not duplicate
        files = os.listdir(os.path.join(root, 'receipts_inbox'))
        check('no duplicate image files', files.count('2026-09-01 Proxy Delhaize Centre.jpeg') == 1)

    print()
    print('ALL PASS' if failed == 0 else f'{failed} FAILED')
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main())
