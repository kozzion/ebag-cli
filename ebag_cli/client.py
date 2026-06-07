"""HTTP client for ebag.bg's internal JSON API.

ebag.bg has no public API. This talks to the same Django + DRF endpoints the
website's own frontend uses (see ``docs/ebag-api-notes.md``). Auth is Django
session + CSRF over cookies, not bearer tokens.

This module is deliberately free of any CLI / presentation concerns so the
logic can be unit-tested without spawning a subprocess.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import httpx

DEFAULT_BASE_URL = "https://www.ebag.bg"


def default_session_path() -> Path:
    """Where to persist the session cookie jar between CLI invocations.

    Override with the ``EBAG_SESSION_FILE`` environment variable.
    """
    override = os.environ.get("EBAG_SESSION_FILE")
    if override:
        return Path(override)
    return Path.home() / ".ebag" / "session.json"


def default_cache_path() -> Path:
    """Where to cache the downloaded category tree (~1 MB).

    Override with the ``EBAG_CATEGORY_CACHE`` environment variable.
    """
    override = os.environ.get("EBAG_CATEGORY_CACHE")
    if override:
        return Path(override)
    return Path.home() / ".ebag" / "categories.json"


def is_in_stock(product: dict[str, Any]) -> bool:
    """Whether a product can actually be added to the cart.

    The catalog's ``is_available`` flag is **unreliable** — items with
    ``is_available: true`` routinely fail ``cart/add`` with
    ``unavailable_product``. The real signal (present in both the product
    listing and the detail endpoint) is ``status == 3`` together with a
    positive ``available_quantity``.
    """
    return product.get("status") == 3 and (product.get("available_quantity") or 0) > 0


def filter_categories(
    categories: list[dict[str, Any]], term: str
) -> list[dict[str, Any]]:
    """Return categories whose name (bg/en) or slug contains ``term``.

    Case-insensitive; works for Cyrillic and Latin alike via casefolding.
    """
    needle = term.casefold()
    out = []
    for c in categories:
        haystack = " ".join(
            str(c.get(k) or "") for k in ("name", "name_en", "url_slug")
        ).casefold()
        if needle in haystack:
            out.append(c)
    return out


# Browser-ish headers. ebag sits behind Cloudflare; a plain default httpx
# User-Agent gets through today, but mimicking the SPA keeps us closer to a
# real client and is cheap insurance.
_DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/125.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "X-Requested-With": "XMLHttpRequest",
}


class EbagError(RuntimeError):
    """Raised when the ebag API returns an error or unexpected response."""


def build_path(template: str, *args: Any, lang: str = "bg") -> str:
    """Mirror the site's own URL builder.

    Positional ``%0``, ``%1`` ... placeholders in ``template`` are replaced by
    ``args``. For English, the path is prefixed with ``/en/`` (the real site
    keeps a small whitelist exempt from this; we don't need it for the JSON
    endpoints we call). The leading slash is always normalised.

    >>> build_path("categories/%0/products/json", 4609)
    '/categories/4609/products/json'
    >>> build_path("orders/list/json", lang="en")
    '/en/orders/list/json'
    """
    path = template.lstrip("/")
    for i, value in enumerate(args):
        if value is not None:
            path = path.replace(f"%{i}", str(value))
    prefix = "/en/" if lang == "en" else "/"
    return prefix + path


class EbagClient:
    """Thin synchronous client over ebag.bg's JSON endpoints.

    Usage::

        with EbagClient() as client:
            for cat in client.categories():
                print(cat["id"], cat["name"])
    """

    def __init__(
        self,
        base_url: str = DEFAULT_BASE_URL,
        *,
        lang: str = "bg",
        timeout: float = 30.0,
        session_path: Path | str | None = None,
        cache_path: Path | str | None = None,
        client: httpx.Client | None = None,
    ) -> None:
        self.lang = lang
        self._session_path = Path(session_path) if session_path else None
        self._cache_path = Path(cache_path) if cache_path else None
        self._client = client or httpx.Client(
            base_url=base_url,
            headers=_DEFAULT_HEADERS,
            timeout=timeout,
            follow_redirects=True,
        )
        if self._session_path:
            self._load_cookies()

    # -- lifecycle ---------------------------------------------------------

    def __enter__(self) -> EbagClient:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def close(self) -> None:
        if self._session_path:
            self._save_cookies()
        self._client.close()

    # -- session persistence ----------------------------------------------

    def _load_cookies(self) -> None:
        path = self._session_path
        if not path or not path.exists():
            return
        try:
            saved = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return  # corrupt/unreadable jar — start fresh rather than crash
        for c in saved:
            self._client.cookies.set(
                c["name"],
                c["value"],
                domain=c.get("domain", ""),
                path=c.get("path", "/"),
            )

    def _save_cookies(self) -> None:
        path = self._session_path
        if not path:
            return
        jar = [
            {"name": c.name, "value": c.value, "domain": c.domain, "path": c.path}
            for c in self._client.cookies.jar
        ]
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(jar), encoding="utf-8")

    # -- low-level ---------------------------------------------------------

    def _ensure_csrf(self) -> str:
        """Return the CSRF token, priming the session by hitting ``/`` once."""
        token = self._client.cookies.get("csrftoken")
        if not token:
            self._client.get("/")
            token = self._client.cookies.get("csrftoken")
        if not token:
            raise EbagError("could not obtain a csrftoken cookie from ebag.bg")
        return token

    def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        headers = dict(kwargs.pop("headers", {}))
        if method.upper() != "GET":
            # Django expects the csrftoken cookie echoed back as a header on
            # unsafe methods.
            headers["X-CSRFToken"] = self._ensure_csrf()
            headers.setdefault("Referer", str(self._client.base_url))
        try:
            response = self._client.request(method, path, headers=headers, **kwargs)
        except httpx.HTTPError as exc:  # network/transport-level failure
            raise EbagError(f"request to {path} failed: {exc}") from exc

        if response.status_code >= 400:
            raise EbagError(
                f"{method} {path} -> HTTP {response.status_code}: {response.text[:200]}"
            )
        if response.status_code == 204 or not response.content:
            return None  # e.g. logout returns 204 No Content
        try:
            return response.json()
        except ValueError as exc:
            raise EbagError(
                f"{method} {path} did not return JSON "
                f"(content-type={response.headers.get('content-type')!r})"
            ) from exc

    def get_json(self, template: str, *args: Any, **params: Any) -> Any:
        path = build_path(template, *args, lang=self.lang)
        return self._request("GET", path, params=params or None)

    def post_json(self, template: str, *args: Any, payload: Any | None = None) -> Any:
        path = build_path(template, *args, lang=self.lang)
        return self._request("POST", path, json=payload)

    # -- high-level: catalog ----------------------------------------------

    def categories(self, *, refresh: bool = False) -> list[dict[str, Any]]:
        """Return the full category tree (flat list with ``parent_id``).

        Cached to ``cache_path`` when set: subsequent calls read the local copy
        instead of re-downloading the ~1 MB tree. Pass ``refresh=True`` to force
        a re-download and rewrite the cache.
        """
        if self._cache_path and not refresh and self._cache_path.exists():
            try:
                return json.loads(self._cache_path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                pass  # corrupt cache — fall through and re-fetch
        data = self.get_json("categories/json")
        cats = data["categories"] if isinstance(data, dict) else data
        if self._cache_path:
            self._cache_path.parent.mkdir(parents=True, exist_ok=True)
            self._cache_path.write_text(json.dumps(cats), encoding="utf-8")
        return cats

    def category_products(
        self, category_id: int | str, *, page: int = 1
    ) -> dict[str, Any]:
        """Return one DRF page of products in a category.

        Keys: ``count``, ``next``, ``previous``, ``results``.
        """
        return self.get_json("categories/%0/products/json", category_id, page=page)

    def recommendations(self) -> list[dict[str, Any]]:
        """Return the site's currently-suggested products."""
        return self.get_json("products/suggested/json")

    # -- high-level: auth --------------------------------------------------
    #
    # Login is email + password to ``/login/complete``; the session cookie it
    # sets is what authenticates subsequent calls. Persist it (``session_path``)
    # to stay logged in across invocations. The ``session-verification`` OTP
    # endpoints are *step-up* auth for sensitive actions, not part of sign-in.

    def login(self, email: str, password: str) -> dict[str, Any]:
        """Authenticate. Raises :class:`EbagError` if credentials are rejected.

        The password is sent straight to ebag and never stored by this client;
        only the resulting session cookie is persisted.
        """
        resp = self.post_json(
            "login/complete", payload={"email": email, "password": password}
        )
        if not resp.get("valid"):
            reason = resp.get("error") or "invalid email or password"
            if resp.get("not_verified_account"):
                reason += " (account not verified)"
            if resp.get("deactivated_account"):
                reason += " (account deactivated)"
            raise EbagError(f"login failed: {reason}")
        return resp

    def logout(self) -> None:
        """End the session server-side (returns 204)."""
        self.post_json("logout/json")

    def whoami(self) -> dict[str, Any]:
        """Return the current user object (``is_authenticated``, name, ...)."""
        data = self.get_json("user/json")
        return data.get("user", {}) if isinstance(data, dict) else {}

    def is_authenticated(self) -> bool:
        """Whether the current session is logged in."""
        return bool(self.whoami().get("is_authenticated"))

    # -- high-level: cart --------------------------------------------------
    #
    # The cart is keyed to the session cookie, so it works without logging in
    # (a guest cart). Persist the session (``session_path``) to keep the same
    # cart across invocations.

    def get_cart(self) -> dict[str, Any]:
        """Return the current cart: ``{id, currency, totals_and_savings, items}``."""
        return self.get_json("cart/json")

    def add_to_cart(self, product_id: int, quantity: int = 1) -> dict[str, Any]:
        """Add ``quantity`` of a product to the cart (increments if present)."""
        return self.post_json(
            "cart/add", payload={"product_id": product_id, "quantity": quantity}
        )

    def set_cart_quantity(self, product_id: int, quantity: int) -> dict[str, Any]:
        """Set the absolute quantity of a product (``0`` removes it)."""
        return self.post_json(
            "cart/update", payload={"product_id": product_id, "quantity": quantity}
        )

    def remove_from_cart(self, product_id: int, quantity: int = 1) -> dict[str, Any]:
        """Remove ``quantity`` of a product from the cart."""
        return self.post_json(
            "cart/remove", payload={"product_id": product_id, "quantity": quantity}
        )
