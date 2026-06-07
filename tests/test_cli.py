"""Tests for the ebag CLI and client.

Network is mocked with httpx's MockTransport so these run offline and fast.
"""

from __future__ import annotations

import json

import httpx
import pytest
from typer.testing import CliRunner

from ebag_cli.cli import app
from ebag_cli.client import (
    EbagClient,
    EbagError,
    build_path,
    filter_categories,
    is_in_stock,
)

runner = CliRunner()


# -- build_path -----------------------------------------------------------


def test_build_path_substitutes_positional_args():
    assert (
        build_path("categories/%0/products/json", 4609)
        == "/categories/4609/products/json"
    )


def test_build_path_strips_leading_slash_and_defaults_to_bg():
    assert build_path("/orders/list/json") == "/orders/list/json"


def test_build_path_en_prefix():
    assert build_path("orders/list/json", lang="en") == "/en/orders/list/json"


def test_build_path_leaves_unmatched_placeholder_when_arg_missing():
    assert build_path("a/%0/b") == "/a/%0/b"


# -- client (mocked transport) -------------------------------------------


def _client_with(handler):
    transport = httpx.MockTransport(handler)
    http = httpx.Client(transport=transport, base_url="https://www.ebag.bg")
    return EbagClient(client=http)


def test_is_in_stock_trusts_quantity_not_is_available():
    # The classic trap: is_available true but really out of stock.
    assert (
        is_in_stock({"is_available": True, "status": 0, "available_quantity": 0})
        is False
    )
    assert (
        is_in_stock({"is_available": True, "status": 3, "available_quantity": 0})
        is False
    )
    assert (
        is_in_stock({"is_available": False, "status": 3, "available_quantity": 5})
        is True
    )
    assert is_in_stock({"status": 3, "available_quantity": 52.0}) is True
    assert is_in_stock({}) is False


def test_filter_categories_matches_name_en_and_slug_case_insensitively():
    cats = [
        {"id": 1, "name": "Мюсли", "name_en": "Muesli", "url_slug": "miusli"},
        {"id": 2, "name": "Сирене", "name_en": "Cheese", "url_slug": "sirene"},
        {
            "id": 3,
            "name": "Био гранола",
            "name_en": "Organic granola",
            "url_slug": "bio-granola",
        },
    ]
    assert {c["id"] for c in filter_categories(cats, "MUESLI")} == {1}
    assert {c["id"] for c in filter_categories(cats, "гранол")} == {3}
    assert {c["id"] for c in filter_categories(cats, "bio-")} == {3}


def test_categories_uses_and_writes_cache(tmp_path):
    cache = tmp_path / "categories.json"
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(200, json={"categories": [{"id": 7, "name": "X"}]})

    def make_client():
        return EbagClient(
            cache_path=cache,
            client=httpx.Client(
                transport=httpx.MockTransport(handler), base_url="https://www.ebag.bg"
            ),
        )

    with make_client() as c1:
        assert c1.categories() == [{"id": 7, "name": "X"}]  # fetched + cached
    assert cache.exists()
    with make_client() as c2:
        assert c2.categories() == [{"id": 7, "name": "X"}]  # served from cache
    assert calls["n"] == 1  # second call hit the cache, no new request

    with make_client() as c3:
        c3.categories(refresh=True)
    assert calls["n"] == 2  # refresh forced a re-download


def test_categories_unwraps_envelope():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/categories/json"
        return httpx.Response(200, json={"categories": [{"id": 1, "name": "All"}]})

    with _client_with(handler) as client:
        cats = client.categories()
    assert cats == [{"id": 1, "name": "All"}]


def test_category_products_passes_page_param():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/categories/4609/products/json"
        assert request.url.params.get("page") == "2"
        return httpx.Response(200, json={"count": 0, "results": []})

    with _client_with(handler) as client:
        data = client.category_products(4609, page=2)
    assert data["count"] == 0


def test_http_error_becomes_ebag_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"detail": "missing"})

    with _client_with(handler) as client:
        with pytest.raises(EbagError):
            client.categories()


def test_non_json_response_becomes_ebag_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="<html>not json</html>")

    with _client_with(handler) as client:
        with pytest.raises(EbagError):
            client.recommendations()


# -- CLI ------------------------------------------------------------------


def test_recommend_command_renders(monkeypatch):
    sample = [{"id": 42, "name": "Boza", "brand_name": "Acme"}]
    monkeypatch.setattr(EbagClient, "recommendations", lambda self: sample)
    monkeypatch.setattr(EbagClient, "close", lambda self: None)

    result = runner.invoke(app, ["recommend"])
    assert result.exit_code == 0
    assert "Boza" in result.stdout


def test_add_to_cart_posts_expected_payload():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST" and request.url.path == "/cart/add":
            seen["body"] = json.loads(request.content)
            seen["csrf"] = request.headers.get("X-CSRFToken")
            return httpx.Response(200, json={"cart_id": 1, "item": {}})
        # CSRF priming GET /
        return httpx.Response(200, headers={"set-cookie": "csrftoken=abc; Path=/"})

    with _client_with(handler) as client:
        client.add_to_cart(637382, 2)

    assert seen["body"] == {"product_id": 637382, "quantity": 2}
    assert seen["csrf"] == "abc"  # cookie echoed back as header


def test_session_cookies_persist_round_trip(tmp_path):
    path = tmp_path / "session.json"

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, headers={"set-cookie": "sessionid=xyz; Path=/"}, json={}
        )

    # First client picks up the cookie and saves it on close.
    c1 = EbagClient(
        session_path=path,
        client=httpx.Client(
            transport=httpx.MockTransport(handler), base_url="https://www.ebag.bg"
        ),
    )
    c1.get_cart()
    c1.close()
    assert "sessionid" in path.read_text()

    # Second client loads it back without any new request.
    c2 = EbagClient(
        session_path=path,
        client=httpx.Client(
            transport=httpx.MockTransport(handler), base_url="https://www.ebag.bg"
        ),
    )
    assert c2._client.cookies.get("sessionid") == "xyz"
    c2.close()


def test_login_success_posts_credentials():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/login/complete":
            seen["body"] = json.loads(request.content)
            return httpx.Response(200, json={"valid": True})
        return httpx.Response(200, headers={"set-cookie": "csrftoken=abc; Path=/"})

    with _client_with(handler) as client:
        client.login("me@example.com", "hunter2")

    assert seen["body"] == {"email": "me@example.com", "password": "hunter2"}


def test_login_failure_raises_with_reason():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/login/complete":
            return httpx.Response(
                200,
                json={
                    "valid": False,
                    "error": "bad creds",
                    "deactivated_account": True,
                },
            )
        return httpx.Response(200, headers={"set-cookie": "csrftoken=abc; Path=/"})

    with _client_with(handler) as client:
        with pytest.raises(EbagError) as excinfo:
            client.login("me@example.com", "wrong")
    assert "bad creds" in str(excinfo.value)
    assert "deactivated" in str(excinfo.value)


def test_is_authenticated_reads_user_json():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/user/json"
        return httpx.Response(200, json={"user": {"is_authenticated": True}})

    with _client_with(handler) as client:
        assert client.is_authenticated() is True


def test_logout_handles_204_no_content():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/logout/json":
            return httpx.Response(204)
        return httpx.Response(200, headers={"set-cookie": "csrftoken=abc; Path=/"})

    with _client_with(handler) as client:
        assert client.logout() is None  # must not raise on empty body


def test_login_command_uses_env_credentials(monkeypatch):
    captured = {}

    def fake_login(self, email, password):
        captured["creds"] = (email, password)
        return {"valid": True}

    monkeypatch.setattr(EbagClient, "login", fake_login)
    monkeypatch.setattr(EbagClient, "whoami", lambda self: {"first_name": "Jaap"})
    monkeypatch.setattr(EbagClient, "close", lambda self: None)
    monkeypatch.setenv("EBAG_EMAIL", "env@example.com")
    monkeypatch.setenv("EBAG_PASSWORD", "envpass")

    result = runner.invoke(app, ["login"])
    assert result.exit_code == 0
    assert captured["creds"] == ("env@example.com", "envpass")
    assert "Jaap" in result.stdout


def test_products_command_reports_error(monkeypatch):
    def boom(self, category_id, *, page=1):
        raise EbagError("nope")

    monkeypatch.setattr(EbagClient, "category_products", boom)
    monkeypatch.setattr(EbagClient, "close", lambda self: None)

    result = runner.invoke(app, ["products", "4609"])
    assert result.exit_code == 1
    assert "nope" in (result.stderr + result.stdout)
