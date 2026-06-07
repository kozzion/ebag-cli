"""``ebag`` command-line interface.

Presentation layer only: argument parsing and rendering. All network logic
lives in :mod:`ebag_cli.client`.
"""

from __future__ import annotations

import getpass
import os

import typer
from dotenv import find_dotenv, load_dotenv
from rich.console import Console
from rich.table import Table

from ebag_cli.client import (
    EbagClient,
    EbagError,
    default_cache_path,
    default_session_path,
    filter_categories,
    is_in_stock,
)

app = typer.Typer(
    help="Unofficial command-line client for the ebag.bg grocery store.",
    no_args_is_help=True,
    add_completion=False,
)


@app.callback()
def _bootstrap() -> None:
    """Run before every command: load a local ``.env`` if present.

    Searches the current working directory (and upwards) so the ``.env`` sits
    where the user runs ``ebag``, not next to the installed package. Real
    environment variables take precedence (``override=False``), so an explicit
    ``EBAG_PASSWORD=… ebag login`` still wins.
    """
    load_dotenv(find_dotenv(usecwd=True), override=False)


cart_app = typer.Typer(help="View and modify your shopping cart.", no_args_is_help=True)
app.add_typer(cart_app, name="cart")
console = Console()
err_console = Console(stderr=True)

# Shared options ----------------------------------------------------------

LangOpt = typer.Option("bg", "--lang", help="Language: 'bg' or 'en'.")


def _client(lang: str) -> EbagClient:
    # Persist the session so the (guest) cart is stable across invocations,
    # and cache the category tree so we don't re-download it every time.
    return EbagClient(
        lang=lang,
        session_path=default_session_path(),
        cache_path=default_cache_path(),
    )


def _fail(message: str) -> None:
    err_console.print(f"[bold red]error:[/] {message}")
    raise typer.Exit(code=1)


def _format_price(product: dict) -> str:
    """Render BGN price, showing the promo price when one is set."""
    price = product.get("price")
    promo = product.get("price_promo")
    if promo and promo != price:
        return f"[strike]{price}[/] [green]{promo}[/] лв"
    return f"{price} лв"


# Commands ----------------------------------------------------------------


@app.command()
def categories(
    lang: str = LangOpt,
    search: str | None = typer.Option(
        None, "--search", "-s", help="Filter by name (bg/en) or slug substring."
    ),
    parent: int | None = typer.Option(
        None, "--parent", help="Show only children of this category id."
    ),
    top: bool = typer.Option(False, "--top", help="Show only top-level categories."),
    refresh: bool = typer.Option(
        False, "--refresh", help="Re-download the category tree (refresh the cache)."
    ),
) -> None:
    """List catalog categories.

    The tree is downloaded once and cached locally; use --refresh to update it.
    """
    try:
        with _client(lang) as client:
            cats = client.categories(refresh=refresh)
    except EbagError as exc:
        _fail(str(exc))

    if search:
        cats = filter_categories(cats, search)
    if top:
        # Top-level = those whose parent is not itself a listed category.
        ids = {c["id"] for c in cats}
        cats = [c for c in cats if c.get("parent_id") not in ids]
    elif parent is not None:
        cats = [c for c in cats if c.get("parent_id") == parent]

    table = Table(title=f"ebag categories ({len(cats)})")
    table.add_column("id", justify="right", style="cyan")
    table.add_column("parent", justify="right", style="dim")
    table.add_column("name")
    table.add_column("slug", style="dim")
    for c in cats:
        name = c.get("name_en") if lang == "en" and c.get("name_en") else c.get("name")
        table.add_row(
            str(c.get("id")),
            str(c.get("parent_id") or ""),
            name or "",
            c.get("url_slug", ""),
        )
    console.print(table)


@app.command()
def products(
    category_id: int = typer.Argument(..., help="Category id (see `ebag categories`)."),
    page: int = typer.Option(1, "--page", "-p", help="Result page (1-based)."),
    in_stock: bool = typer.Option(
        False,
        "--in-stock",
        "-a",
        help="Show only items that can actually be added to the cart.",
    ),
    lang: str = LangOpt,
) -> None:
    """List products in a category.

    The 'stock' column reflects real availability (``status``/
    ``available_quantity``), not the catalog's unreliable ``is_available`` flag.
    """
    try:
        with _client(lang) as client:
            data = client.category_products(category_id, page=page)
    except EbagError as exc:
        _fail(str(exc))

    results = data.get("results", [])
    if in_stock:
        results = [p for p in results if is_in_stock(p)]
    count = data.get("count", len(results))
    has_next = bool(data.get("next"))

    table = Table(title=f"category {category_id} — {count} products (page {page})")
    table.add_column("id", justify="right", style="cyan")
    table.add_column("name")
    table.add_column("price", justify="right")
    table.add_column("stock", justify="center")
    for p in results:
        table.add_row(
            str(p.get("id")),
            p.get("name", ""),
            _format_price(p),
            "[green]yes[/]" if is_in_stock(p) else "[red]no[/]",
        )
    console.print(table)
    if has_next:
        console.print(f"[dim]more results: --page {page + 1}[/]")


@app.command()
def recommend(lang: str = LangOpt) -> None:
    """Show the products ebag is currently suggesting."""
    try:
        with _client(lang) as client:
            items = client.recommendations()
    except EbagError as exc:
        _fail(str(exc))

    table = Table(title=f"ebag recommendations ({len(items)})")
    table.add_column("id", justify="right", style="cyan")
    table.add_column("name")
    table.add_column("brand", style="dim")
    for p in items:
        table.add_row(str(p.get("id")), p.get("name", ""), p.get("brand_name", ""))
    console.print(table)


@app.command()
def login(
    email: str | None = typer.Option(
        None,
        "--email",
        "-e",
        help="Account email. Falls back to $EBAG_EMAIL, then prompts.",
    ),
    lang: str = LangOpt,
) -> None:
    """Log in to your ebag.bg account.

    The password is never read from the command line (it would leak into shell
    history and process listings). It comes from $EBAG_PASSWORD or a hidden
    prompt, and is sent straight to ebag — only the session cookie is stored.
    """
    email = email or os.environ.get("EBAG_EMAIL") or typer.prompt("Email")
    password = os.environ.get("EBAG_PASSWORD")
    if not password:
        password = getpass.getpass("Password: ")
    if not email or not password:
        _fail("email and password are required")

    try:
        with _client(lang) as client:
            client.login(email, password)
            user = client.whoami()
    except EbagError as exc:
        _fail(str(exc))

    name = (user.get("first_name") or "").strip() or email
    console.print(f"[green]Logged in[/] as {name}")


@app.command()
def logout(lang: str = LangOpt) -> None:
    """Log out and clear the local session."""
    try:
        with _client(lang) as client:
            client.logout()
    except EbagError as exc:
        _fail(str(exc))
    # Drop the persisted cookie jar so we don't keep a stale session around.
    session_file = default_session_path()
    if session_file.exists():
        session_file.unlink()
    console.print("[green]Logged out.[/]")


@app.command()
def whoami(lang: str = LangOpt) -> None:
    """Show whether the current session is logged in, and as whom."""
    try:
        with _client(lang) as client:
            user = client.whoami()
    except EbagError as exc:
        _fail(str(exc))
    if not user.get("is_authenticated"):
        console.print("[yellow]Not logged in[/] (guest session).")
        return
    name = " ".join(
        p for p in (user.get("first_name"), user.get("last_name")) if p
    ).strip()
    console.print(f"[green]Logged in[/] as {name or 'authenticated user'}")


def _render_cart(cart: dict) -> None:
    items = cart.get("items", [])
    if not items:
        console.print("[yellow]Cart is empty.[/]")
        return
    table = Table(title=f"cart #{cart.get('id')}")
    table.add_column("id", justify="right", style="cyan")
    table.add_column("name")
    table.add_column("qty", justify="right")
    table.add_column("line", justify="right")
    for it in items:
        product = it.get("product", {})
        line = it.get("total_price") or it.get("price") or ""
        table.add_row(
            str(product.get("id")),
            product.get("name", ""),
            str(it.get("quantity", "")),
            f"{line} лв" if line else "",
        )
    console.print(table)
    total = cart.get("totals_and_savings", {}).get("total")
    if total is not None:
        console.print(f"[bold]total: {total} лв[/]")


@cart_app.command("show")
def cart_show(lang: str = LangOpt) -> None:
    """Show the current cart."""
    try:
        with _client(lang) as client:
            cart = client.get_cart()
    except EbagError as exc:
        _fail(str(exc))
    _render_cart(cart)


@cart_app.command("add")
def cart_add(
    product_id: int = typer.Argument(..., help="Product id (see product listings)."),
    qty: int = typer.Option(1, "--qty", "-q", min=1, help="Quantity to add."),
    lang: str = LangOpt,
) -> None:
    """Add a product to the cart."""
    try:
        with _client(lang) as client:
            resp = client.add_to_cart(product_id, qty)
            cart = client.get_cart()
    except EbagError as exc:
        _fail(str(exc))
    name = resp.get("item", {}).get("product", {}).get("name", product_id)
    console.print(f"[green]Added[/] {qty}× {name}")
    _render_cart(cart)


@cart_app.command("remove")
def cart_remove(
    product_id: int = typer.Argument(..., help="Product id to remove."),
    qty: int = typer.Option(1, "--qty", "-q", min=1, help="Quantity to remove."),
    lang: str = LangOpt,
) -> None:
    """Remove a product (or some of its quantity) from the cart."""
    try:
        with _client(lang) as client:
            client.remove_from_cart(product_id, qty)
            cart = client.get_cart()
    except EbagError as exc:
        _fail(str(exc))
    console.print(f"[green]Removed[/] {qty}× product {product_id}")
    _render_cart(cart)


if __name__ == "__main__":
    app()
