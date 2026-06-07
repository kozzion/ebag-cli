# ebag.bg API — reverse-engineering notes

> Status: initial recon (2026-06-06). Endpoints confirmed by probing the live
> site + reading the desktop JS bundle. No official/public API exists; this is
> the site's own internal JSON API. Treat as unstable — paths may change when
> the frontend is rebuilt.

## Architecture

- **Backend:** Django + **Django REST Framework** (DRF).
  - Confirmed by the `csrftoken` cookie (Django default) and DRF-style error
    bodies, e.g. `{"detail": "Метод \"GET\" не е позволен."}` and
    field-validation maps like `{"product_name": ["Това поле е задължително."]}`.
- **Hosting:** Cloudflare in front (`Server: cloudflare`, `CF-RAY` header).
- **Hosts:** `www.ebag.bg` (desktop) and `m.ebag.bg` (mobile) — same backend,
  same cookies, same JSON endpoints.
- **API style:** No `/api/` prefix and no GraphQL. JSON endpoints live at the
  **same origin** under normal-looking paths, conventionally suffixed `/json`
  (e.g. `/orders/list/json`). Some endpoints are GET, some POST.
- **Language:** Default is Bulgarian (`content-language: bg`). English variant
  is served by prefixing the path with `/en/` (see URL builder below).

## URL construction (from the bundle)

The frontend builds every request URL with a helper (`kj` in module `24823`):

```js
// kj("categories/%0/products/json") -> fn(catId) => "/categories/<catId>/products/json"
function w(path) {
  let base = "/";
  // if site language is "en" AND path isn't in a whitelist, base becomes "/en/"
  if (language === "en" && notWhitelisted(path)) base += "en/";
  base += path;
  // returned fn replaces %0, %1, ... positionally with provided args
  return (...args) => args.reduce((s, a, i) =>
    a == null ? s : s.replace("%" + i, String(a)), base);
}
```

Takeaways for the client:
- Prepend `https://www.ebag.bg/` (or `/en/...` for English).
- `%0`, `%1` are positional path params.

## Auth / session model

Standard Django session + CSRF, **not** token/bearer:

1. `GET /` → sets `csrftoken` cookie (1-year expiry) + a session cookie.
2. For unsafe methods (POST/PUT/DELETE), send the CSRF token back as the
   **`X-CSRFToken` header** (value = the `csrftoken` cookie). Also send
   `Referer` and `X-Requested-With: XMLHttpRequest` to look like the SPA.
3. **Login** (✅ confirmed): `POST /login/complete` with JSON
   `{"email": ..., "password": ...}`. `/login` itself is GET-only (the page).
   Response is `200` either way: `{"valid": true}` on success (session cookie
   set), or `{"valid": false, "error": "...", "not_verified_account": bool,
   "deactivated_account": bool}` on failure. No OTP in the basic sign-in —
   the `session-verification/*` endpoints are **step-up** auth for sensitive
   actions and require an already-authenticated session (`login_required`).
4. **Logout:** `POST /logout/json` → `204 No Content`.
5. **Current user / whoami:** `GET /user/json` →
   `{"user": {"is_authenticated": bool, "first_name", "last_name", ...},
   "user_agent": {...}}`.

**CSRF flow verified:** a POST with cookie jar + matching `X-CSRFToken` got past
CSRF (returned a 400 *validation* error, not a 403), confirming the scheme.

The **cart is a guest cart** keyed to the session cookie — no login required to
build one. Persisting the cookie jar keeps the same cart across runs. There is
**no shareable cart URL**: the cart page is `/cart/` (renders whatever the
session's cookies map to) and the cart is not addressable by id. The only
share mechanism on the site is public **lists** (`/lists/public/%0/...`).

## Endpoint catalog (relative to origin)

`%0`/`%1` = path params. ✅ = probed live, ⬜ = found in bundle, not yet exercised.

### Catalog / search
| Method | Path | Notes |
|---|---|---|
| GET ✅ | `/products/suggested/json` | Recommended products. Returns `[{id, name, url_slug, brand_name, main_image_id}]`. |
| GET ⬜ | `/categories/%0/products/json` | Products in a category. |
| GET ⬜ | `/categories/%0/%1` | Category page (HTML). |
| GET ⬜ | `/categories/offers-products/json` | Promo products. |
| GET ⬜ | `/brands/%0/products/json` | Products by brand. |
| GET ⬜ | `/products/hard-coded/recommendations/json` | Curated recs. |
| GET ⬜ | `/products/gift-vouchers/json` | Gift vouchers. |
| GET 301 | `/search/` , `/search/<term>` | Search **page** (HTML); product results load via a sub-call — exact JSON param TBD via live capture. |
| POST ⬜ | `/search/empty-search/json` | Empty-search state. |
| ⬜ | `/filters/json`, `/offers-filters/json` | Facet filters. |

> ⚠️ **`/products/suggest/json` is NOT search autocomplete** — it's a
> "suggest a product for us to stock" form (POST expects `product_name`,
> `brand`, `email`).

### Cart / basket  (✅ confirmed)
| Method | Path | Body | Notes |
|---|---|---|---|
| GET ✅ | `/cart/json` | — | Current cart: `{id, currency, totals_and_savings, items:[{product, quantity, total_price}]}`. Empty guest cart: `{"id": null, "items": []}`. |
| POST ✅ | `/cart/add` | `{product_id, quantity}` | Adds (increments if present). Returns `{cart_id, item}`. |
| POST ✅ | `/cart/update` | `{product_id, quantity}` | Sets absolute quantity (`0` removes). |
| POST ✅ | `/cart/remove` | `{product_id, quantity}` | Removes that quantity. |
| POST ⬜ | `/cart/clear`, `/cart/clear-category/%0` | — | Clear whole cart / one category. |

### Orders & checkout
| Method | Path | Notes |
|---|---|---|
| GET ✅ | `/orders/list/json` | Order history (DRF page; empty account → `count: 0`). |
| GET ✅ | `/user-current-order/json` | The active order (`{}` if none yet). |
| GET ✅ | `/orders/get-time-slots` | `{date: [{key, start, end, is_available, load_percent, cutoff_after}]}`. ~4 days ahead, hourly. Reflects the **default (Sofia) zone**; query params do *not* reparameterize it. |
| GET ⬜ | `/orders/%0/timeslots/json` | Zone-specific slots once an address is on the order. |
| POST ⬜ | `/orders/create-order` | Convert cart → order. |
| ⬜ | `/orders/%0/checkout/address-contact-info` | Set delivery address + contact. |
| ⬜ | `/orders/%0/checkout/delivery-date-time` | Choose the slot. |
| ⬜ | `/orders/%0/checkout/payment-method` | Choose payment. |
| ⬜ | `/orders/%0/checkout/finish-order` | **Place the order** (payment). Not automated — browser hand-off. |
| GET ⬜ | `/orders/%0/checkout/json`, `/orders/%0/details/json` | Checkout state / order detail. |
| ⬜ | `/orders/%0/checkout/{apply,remove}-promo-code`, `/orders/voucher/%0/download` | Promo / voucher. |

### Delivery (✅ confirmed)
| Method | Path | Body / params | Notes |
|---|---|---|---|
| GET ✅ | `/addresses/delivery-polygons` | — | ~24 polygons of `[lat,lng]` rings. Point-in-polygon = serviceable. |
| POST ✅ | `/addresses/validate-position/json` | `{latitude, longitude}` | `{is_valid, neighbourhood_key, city_key}`. Use `latitude`/`longitude` keys exactly. |
| GET ✅ | `/addresses/delivery-prices-by-city/json` | `?city_key=<n>` | Order min, shipping tiers, free-delivery threshold (vary by city). |
| ⬜ | `/addresses`, `/addresses/add/json`, `/addresses/%0/{update,remove,change-default}/json` | | Address book (`/addresses` is the HTML page). |

### Auth / account
| Method | Path | Body | Notes |
|---|---|---|---|
| POST ✅ | `/login/complete` | `{email, password}` | Sign in. `{valid: bool, error, not_verified_account, deactivated_account}`. |
| GET ✅ | `/login` | — | Login *page* (HTML), not the auth POST. |
| POST ✅ | `/logout/json` | — | `204 No Content`. |
| GET ✅ | `/user/json` | — | Current user / `is_authenticated`. |
| ⬜ | `/login/social/providers-data` | | Social/SSO login data. |
| ⬜ | `/session-verification/{send-code,verify/by-code,user/phone-numbers}/json` | | **Step-up** OTP for sensitive actions; requires being logged in. |
| ⬜ | `/delete-account`, `/change-password/json` | | Account management. |

### Lists (favorites / shopping lists)
`/lists/`, `/lists/add`, `/lists/%0`, `/lists/%0/items/json`,
`/lists/%0/items/add/from-cart`, `/lists/%0/items/remove`,
`/lists/public/%0/items/json`.

### Misc / utility
`/delivery-prices-by-city/json`, `/notifications/json`,
`/user-notifications/json`, `/timeslots/json`, `/subscriptions/json`,
`/is-subscribed/json`, `/gift-vouchers/json`, `/prices-reports/json`.

## Open questions

1. ~~Exact login payload~~ — ✅ resolved: `POST /login/complete {email, password}`,
   no OTP for basic sign-in.
2. ~~Cart endpoint prefix~~ — ✅ resolved: `/cart/{json,add,update,remove,clear}`.
3. The real **search-results JSON** endpoint and its query param (lives in a
   lazy-loaded `.chunk.js`; needs a browser capture or webpack-manifest dive).
4. Delivery serviceability + economics — ✅ resolved (polygons +
   `validate-position` + per-city pricing). The **checkout write flow**
   (`create-order` → address → date-time → payment → finish-order) is mapped but
   intentionally **not automated**: placing/paying is a browser hand-off.
5. Whether Cloudflare applies bot challenges to scripted clients over time.

## Reproduction snippet (CSRF flow)

```bash
curl -s -c cj.txt https://www.ebag.bg/ -o /dev/null            # get csrftoken
CSRF=$(grep csrftoken cj.txt | awk '{print $NF}')
curl -s -b cj.txt -H "X-CSRFToken: $CSRF" \
     -H "X-Requested-With: XMLHttpRequest" -H "Referer: https://www.ebag.bg/" \
     -H "Content-Type: application/json" \
     -X POST https://www.ebag.bg/<path>/json --data '{...}'
```
