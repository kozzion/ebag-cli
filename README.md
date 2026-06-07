# ebag-cli

An unofficial command-line client for the Bulgarian online grocery store
[ebag.bg](https://www.ebag.bg). Browse the catalog, look up products and
prices, and (eventually) manage your cart and orders — all from the terminal,
headlessly, with no browser required.

> **Unofficial.** ebag.bg has no public API. This tool talks to the same
> internal JSON endpoints the website's own frontend uses. They can change
> without notice. See [docs/ebag-api-notes.md](docs/ebag-api-notes.md) for the
> reverse-engineering notes.

## Status

Working today:

- `ebag categories` — browse/search the catalog category tree (downloaded once
  and cached locally; `--search` filters by name in BG/EN or slug)
- `ebag products <category-id>` — list products with prices and **real stock**
  (`--in-stock` hides items that can't actually be added)
- `ebag recommend` — show ebag's currently-suggested products
- `ebag cart show|add|remove` — view and modify your cart (guest cart works
  without login; the session is persisted between runs)
- `ebag login` / `ebag logout` / `ebag whoami` — account session

**Checkout** is intentionally a browser hand-off: the tool builds your cart
to completion, then you place the order and pay on ebag.bg (this client never
handles your payment details). Delivery serviceability and pricing are
documented in [docs/ebag-api-notes.md](docs/ebag-api-notes.md).

Planned: product-level search (lives in a lazy JS chunk — needs a live
capture), and reading order history/status.

## Quick start

```bash
git clone https://github.com/kozzion/ebag-cli.git
cd ebag-cli

python -m venv .venv
# Windows:
.venv\Scripts\activate
# macOS / Linux:
source .venv/bin/activate

pip install -e .
```

## Usage

```bash
# Browse / search categories (tree is cached after first download)
ebag categories --top                 # top-level categories
ebag categories --parent 4727         # children of a category
ebag categories --search "гранол"     # search by name (BG/EN) or slug
ebag categories --search granola       # English works too
ebag categories --refresh             # re-download the cached tree

# Products in a category (paginated; real availability)
ebag products 4609
ebag products 4609 --page 2
ebag products 5779 --in-stock         # only items that can actually be added

# What ebag is suggesting right now
ebag recommend

# English labels where available
ebag categories --top --lang en

# Cart (works as a guest; session persists between runs)
ebag cart add 637382 --qty 2
ebag cart show
ebag cart remove 637382

# Log in to use your own account cart
ebag login                 # prompts for email + password (or reads .env)
ebag whoami
ebag logout
```

Prices are shown in BGN. When a product is on promotion, both the regular and
promo price are displayed.

### A typical shopping session

There's no product-level search yet (it lives in a lazy-loaded JS chunk), so
the workflow is **find the category, then list its products**:

```bash
ebag login                            # 1. authenticate (uses .env)
ebag categories --search "хумус"      # 2. find the category id, e.g. 1832
ebag products 1832 --in-stock         # 3. list addable products + their ids
ebag cart add 597507                  # 4. add by product id
# ... repeat for everything you need ...
ebag cart show                        # 5. review the basket + total
```

Then finish in the browser: open <https://www.ebag.bg/cart/> (signed in),
**Checkout**, pick your delivery address + a time slot, and pay.

> **Tip — real stock:** ebag's catalog `is_available` flag is unreliable; the
> CLI instead trusts `status`/`available_quantity` (`--in-stock`). If a product
> still 400s on `cart add` with "unavailable", it's genuinely out of stock.

### Delivery (Bulgaria)

ebag delivers within a set of polygons (mostly Sofia + larger towns, and some
resort areas). Before checkout you can sanity-check coverage: an address only
validates if its coordinates fall inside a delivery polygon. Order minimums and
free-delivery thresholds vary by city (e.g. Sofia: 30 лв min, free over 70 лв;
some resort zones: 40 лв min, free over 90 лв). See the notes doc for the
endpoints (`addresses/delivery-polygons`, `addresses/validate-position/json`,
`addresses/delivery-prices-by-city/json`).

### Logging in

Credentials are never read from command-line arguments (they would leak into
shell history and process listings). `ebag login` takes the email from
`--email` or `$EBAG_EMAIL` (else prompts), and the password from
`$EBAG_PASSWORD` or a hidden prompt. Only the resulting session cookie is
stored — at `~/.ebag/session.json` (override with `$EBAG_SESSION_FILE`).

The easiest setup is a **`.env` file** in your working directory. Copy the
template and fill it in:

```bash
cp .env.example .env
# edit .env:
#   EBAG_EMAIL=you@example.com
#   EBAG_PASSWORD=your-password
ebag login        # picks creds up from .env automatically
```

`.env` is git-ignored — it never gets committed. Real environment variables
override `.env` values if both are set.

## Development

```bash
pip install -e ".[dev]"

pytest            # tests (network is mocked — runs offline)
ruff check .      # lint
ruff format .     # format
```

The networking lives in `ebag_cli/client.py` (the `EbagClient` class) and is
kept free of CLI concerns so it can be unit-tested without a subprocess. The
CLI (`ebag_cli/cli.py`) is presentation only.

## Disclaimer

This software is provided **"as is"**, without warranty of any kind, express or
implied. It's an unofficial client for an API that can change without notice, so
things may break. Use at your own risk.

To the best of the author's knowledge, using this CLI will **probably not turn
you into a hamster** — but this cannot be guaranteed. No liability is accepted
for any rodent-related transformations, partial or complete.

## License

[MIT](LICENSE) © 2026 Jaap Oosterbroek
