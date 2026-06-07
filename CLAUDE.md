# CLAUDE.md

Project-specific guidance for Claude Code working in this repository.

## What this is

`ebag` — an unofficial command-line client for the Bulgarian online grocery
store [ebag.bg](https://www.ebag.bg). It drives the site's **internal** JSON
API (no public API exists) over plain HTTP, headlessly — no browser, no Node.

ebag's backend is **Django + Django REST Framework** behind Cloudflare; auth is
Django **session + CSRF over cookies** (not bearer tokens). The full
reverse-engineering writeup — architecture, the URL-builder semantics, and the
endpoint catalog — is in [docs/ebag-api-notes.md](docs/ebag-api-notes.md).
Keep that doc updated as endpoints are confirmed.

Working today (unauthenticated): category browsing, product listing with
prices, recommendations. Not yet built: search (lives in a lazy JS chunk —
needs a live capture), cart, checkout, order tracking (all need an
authenticated session).

## API quirks (learned the hard way)

- **Product availability:** the catalog's `is_available` flag is **unreliable** —
  items flagged `is_available: true` routinely fail `POST /cart/add` with
  `{"error_code": "unavailable_product"}` ("Продуктът вече не е наличен"). The
  real in-stock signal (present in both the product *listing* and the detail
  endpoint) is `status == 3` **and** `available_quantity > 0`.

  Evidence — diffing a product that added fine against one that 400'd, both
  showing `is_available: true`:

  ```
  GRANOLA (added OK) -> is_available=True, status=3, available_quantity=31.0
  NUTS    (failed)   -> is_available=True, status=0, available_quantity=0.0
  ```

  So: trust `available_quantity`/`status`, never `is_available`. Use the
  `is_in_stock()` helper in `client.py` (`status == 3 and available_quantity > 0`).
  The `ebag products` command surfaces it as its "stock" column, and `--in-stock`
  filters to genuinely-addable items.
- **Product detail / listing** both carry `available_quantity` and `status`, so
  real stock can be shown without an extra request per item.
- **Cart is a guest cart** keyed to the session cookie — no login needed to
  build one. On login, an existing guest cart **merges** into the account cart
  (quantities add up). There is **no shareable cart URL**: `/cart/` renders
  whatever the viewing session's cookies map to; the cart isn't addressable by
  id. The only share mechanism on the site is public **lists**
  (`/lists/public/%0/...`).
- **Prices:** every price has a `_eur` twin and a `price_promo` (BG is on the
  euro changeover, dual pricing). Watch out: `price_promo` is sometimes a bogus
  `0.00` — treat a zero/empty promo as "no promo" when rendering.

### Delivery & checkout (confirmed by reverse-engineering a real order)

- **Coverage is polygon-based, not a city list.** `GET addresses/delivery-polygons`
  returns ~24 polygons of `[lat, lng]` rings (Sofia + larger towns + some resort
  areas, e.g. the SW-Sofia→Razlog/Bansko corridor). An address is only
  serviceable if its coordinates fall inside a polygon (verified with a
  ray-casting point-in-polygon test; Bansko ✓, Varna/London ✗).
- **`POST addresses/validate-position/json`** with body `{"latitude", "longitude"}`
  (those exact keys — `lat`/`lng` return invalid) → `{is_valid, latitude,
  longitude, neighbourhood_key, city_key}`. This is the authoritative
  serviceability + zone check.
- **Per-city economics:** `GET addresses/delivery-prices-by-city/json?city_key=<n>`
  → order minimum, shipping tiers, and free-delivery threshold. They vary by
  city (Sofia/`city_key=0`: min 30 лв, free > 70 лв; Bansko/`city_key=16`:
  min 40 лв, free > 90 лв).
- **Time slots:** `GET orders/get-time-slots` → `{date: [{key, start, end,
  is_available, load_percent, cutoff_after}]}`, only ~4 days ahead, hourly.
  These reflect the session's **default (Sofia) zone**; real zone-specific slots
  only appear once a delivery address is attached to an order
  (`orders/%0/timeslots/json`). Query params don't reparameterize get-time-slots.
- **Checkout flow** (order-scoped): `orders/create-order` (cart → order) →
  `orders/%0/checkout/address-contact-info` → `orders/%0/checkout/delivery-date-time`
  → `orders/%0/checkout/payment-method` → `orders/%0/checkout/finish-order`.
  Helpers: `orders/%0/checkout/json` (state), `apply-promo-code`,
  `user-current-order/json` (active order), `orders/list/json` (history).
- **Boundary:** this client deliberately stops before placing/paying. Browsing,
  cart, and login are automated; the final checkout (address + payment) is a
  browser hand-off — never enter payment credentials or finalize a purchase.

## Stack & conventions

- **Language:** Python 3.11+
- **Style:** PEP 8; format with `ruff format`; lint with `ruff check`
- **Type hints:** use them on all public functions and CLI entry points
- **Tests:** `pytest` (network mocked via `httpx.MockTransport`, runs offline)
- **CLI framework:** `typer` + `rich` (chosen).
- **HTTP client:** `httpx` (sync `httpx.Client`). If Cloudflare ever starts
  challenging the scripted client, the fallback is `curl_cffi` (TLS-fingerprint
  matching) — still pure Python, no browser.
- **Dependency management:** `pyproject.toml` (installable via `pip install -e .`);
  `requirements.txt` is kept as a convenience mirror of the runtime deps.

## Repo layout (target)

```
ebag-cli/
├── ebag_cli/             # Importable package
│   ├── __init__.py
│   ├── client.py         # EbagClient: HTTP/session/CSRF + endpoint methods
│   └── cli.py            # typer CLI (presentation only)
├── tests/
│   └── test_cli.py
├── docs/
│   └── ebag-api-notes.md # reverse-engineering writeup
├── pyproject.toml        # package + console script `ebag`
├── requirements.txt
├── README.md
├── CLAUDE.md
└── .gitignore
```

## When adding code

- Put the entry point under `ebag_cli/cli.py` and expose it via
  `pyproject.toml` console-script (e.g. `ebag = "ebag_cli.cli:main"`).
- Keep network / external-service calls in a separate module from the CLI
  parsing so the underlying logic can be unit-tested without spawning a
  subprocess.
- Never commit secrets (API keys, tokens). Read them from environment
  variables and document the required names in the README.
- Add a corresponding `tests/test_*.py` for every non-trivial change.

## Commits & PRs

- Conventional commit prefixes are nice but not required: `feat:`, `fix:`,
  `chore:`, `docs:`, `test:`.
- One logical change per commit.
- Run `ruff check .` and `pytest` before pushing.

## Owner

`kozzion` (Jaap Oosterbroek).
