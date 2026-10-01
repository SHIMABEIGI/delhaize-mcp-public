# delhaize-mcp

An unofficial [Model Context Protocol](https://modelcontextprotocol.io) server that lets an AI assistant fetch your own **Delhaize (Belgium) till receipts** and this week's **promotions**, using its own headless browser, without driving your everyday browser and without ever seeing your password.

> Unofficial and not affiliated with or endorsed by Delhaize. It reads only your own account, at your request, the way you would read it yourself. Check that your use fits Delhaize's terms of use, keep the request rate low, and do not use it to collect other people's data.

## What it does

| Tool | What it does | Writes |
|---|---|---|
| `delhaize_auth_status` | Is the saved login still valid? | nothing |
| `delhaize_list_receipts` | Lists recent receipts and flags which are not in your record yet | nothing |
| `delhaize_fetch_new_receipts` | Saves the image of each **new** receipt | `data/receipts_inbox/` |
| `delhaize_backfill_images` | Saves the **missing photos** of receipts already in your record | `data/receipts_backfill/` |
| `delhaize_fetch_promotions` | Saves the raw pages of the weekly promotions listing | `data/promos_inbox/<date>/` |

It never transcribes items, never edits your record, and never handles your password. It only puts files on disk. Reading the images and recording them is left to you or your assistant, ideally with a check that each receipt's lines sum to its printed total.

Receipts are identified by **(date, store, total)**, so nothing is fetched twice.

## Requirements

- Python 3.10+
- A Delhaize account with receipts linked to your loyalty card

```bash
pip install -r requirements.txt
python3 -m playwright install chromium
```

## Set up

1. **Log in once.** A browser window opens, and you sign in yourself:

   ```bash
   python3 fetch_receipts.py login
   ```

   The session is saved in a local `.profile/` folder beside the code. Later runs reuse it headlessly. If it lapses, run `login` again. **`.profile/` holds your session cookies: never commit or share it** (it is in `.gitignore`).

2. **Choose a data folder** (optional). By default everything is written to `./data`. Set `DELHAIZE_DATA_DIR` to use another folder. Your record of receipts is `receipts_all.json` in that folder, a JSON list such as:

   ```json
   [{"date": "2026-08-11", "store": "Delhaize Centre", "total": 11.11, "image": "photos/2026-08-11.jpeg"}]
   ```

   `image` is optional. A receipt without an existing image is what `backfill` looks for.

3. **Connect it to your assistant.**

   Claude Code:

   ```bash
   claude mcp add delhaize-receipts -- python3 /path/to/delhaize-mcp/server.py
   ```

   Claude Desktop (`claude_desktop_config.json`):

   ```json
   {
     "mcpServers": {
       "delhaize-receipts": {
         "command": "python3",
         "args": ["/path/to/delhaize-mcp/server.py"],
         "env": {"DELHAIZE_DATA_DIR": "/path/to/your/data"}
       }
     }
   }
   ```

   Use the full path to the Python that has the packages installed if you have several.

## Command line

The same actions work without an assistant:

```bash
python3 fetch_receipts.py status
python3 fetch_receipts.py list     --months 1   # this month and last
python3 fetch_receipts.py fetch    --months 1
python3 fetch_receipts.py backfill --months 3
python3 fetch_receipts.py promos
```

## How it works

Delhaize renders each receipt as an image inside a client-side app, so the server lets the real page render in a headless Chromium (Playwright) and reads the finished page, exactly as a person would. All parsing rules (French dates, euro amounts, de-duplication, file naming) live in `delhaize_core.py`, which has no browser dependency and is fully unit-tested.

## Tests

No browser or network needed:

```bash
python3 test_core.py
python3 test_server.py
```

## Privacy

- Nothing leaves your machine except the normal page requests to delhaize.be.
- No password is read or stored. The login is a browser profile you create yourself.
- `.profile/` and `data/` are git-ignored. Keep it that way.

## Limitations

- Built against the French-language Belgian site (`delhaize.be/fr`). Page changes on Delhaize's side can break it; the parsers are deliberately forgiving, and the tools report what they missed rather than failing silently.
- The promotions capture saves raw page data only; interpreting it is up to you.

## Licence

MIT. See `LICENSE`.
