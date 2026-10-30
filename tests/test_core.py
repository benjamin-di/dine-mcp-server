"""Links, definitions, overrides, state and authentication."""

from __future__ import annotations

import time
from pathlib import Path

import pytest
from conftest import fake_definition

from dine_mcp import links
from dine_mcp.auth import AuthConfig, AuthError, Verifier, add_user, load_users, person_for
from dine_mcp.definitions import (
    DefinitionError,
    effective,
    load_providers,
    load_regions,
    parse_overrides,
    parse_provider,
)
from dine_mcp.errors import CODES, DineError
from dine_mcp.models import AccessMode
from dine_mcp.providers.common import plain
from dine_mcp.state import ALL, Store

# --- links ---------------------------------------------------------------------------------------------


def test_clean_strips_tracking_and_fragment():
    url = "https://WWW.Fake.example/r/amber?utm_source=google&rwg_token=abc&src=x&date=2026-11-06#top"
    assert links.clean(url) == "https://www.fake.example/r/amber?date=2026-11-06"


@pytest.mark.parametrize(
    "url",
    [
        "http://www.fake.example/r/a",
        "https://user:pw@www.fake.example/r/a",
        "https://www.fake.example:8443/r/a",
        "javascript:alert(1)",
        "https://" + "a" * 3000,
    ],
)
def test_clean_refuses_unsafe_urls(url):
    assert links.clean(url) is None


def test_match_requires_host_and_pattern():
    defs = {"fake": fake_definition("fake")}
    assert links.match("https://www.fake.example/r/amber-hk?src=g", defs).ref == "amber-hk"
    assert links.match("https://evil.example/r/amber", defs) is None
    assert links.match("https://www.fake.example.evil.example/r/amber", defs) is None
    assert links.match("https://www.fake.example/other/amber", defs) is None


def test_host_allowed_subdomains_only_with_leading_dot():
    assert links.host_allowed("https://a.b.example/x", (".b.example",))
    assert links.host_allowed("https://b.example/x", (".b.example",))
    assert not links.host_allowed("https://ab.example/x", (".b.example",))
    assert not links.host_allowed("http://b.example/x", ("b.example",))


# --- definitions ------------------------------------------------------------------------------------------


def test_provider_text_is_plain_and_capped():
    assert plain("Harbour\u202e Grill\n\x00 ") == "Harbour Grill"
    assert plain("x" * 500) == "x" * 120
    assert plain("\u200b\t") is None
    assert plain(None) is None


def test_shipped_definitions_and_regions_load():
    definitions = load_providers()
    regions = load_regions(definitions)
    assert {"sevenrooms", "tablecheck", "resdiary"} <= set(definitions)
    assert {"SG", "JP", "GB", "AU"} <= set(regions)
    for definition in definitions.values():
        assert definition.terms and definition.credit is not None


@pytest.mark.parametrize(
    "change",
    [
        {"schema": 2},
        {"key": "Bad Key"},
        {"access_mode": "sometimes"},
        {"countries": ["sg"]},
        {"url_patterns": ["^https://x/(?P<other>.*)"]},
        {"url_patterns": ["^http://x/(?P<ref>.*)"]},
        {"request_interval_seconds": 0.1},
        {"person_daily_share": 0},
        {"cache_ttl_seconds": 5},
    ],
)
def test_bad_definitions_are_refused(change):
    data = {
        "schema": 1,
        "key": "fake",
        "name": "Fake",
        "access_mode": "public",
        "maturity": "beta",
        "countries": ["SG"],
        "hosts": ["www.fake.example"],
        "url_patterns": [r"^https://www\.fake\.example/r/(?P<ref>[a-z]+)"],
        "request_interval_seconds": 1,
        "daily_request_budget": 10,
        "person_daily_share": 0.5,
        "cache_ttl_seconds": 120,
        "terms": "t",
        **change,
    }
    with pytest.raises(DefinitionError):
        parse_provider(data, "fake.toml")


def test_overrides_switch_lower_never_raise():
    definition = fake_definition("fake")
    assert effective(definition, parse_overrides({}, {"fake"}, "o")).enabled
    off = effective(definition, parse_overrides({"providers": {"fake": {"enabled": False}}}, {"fake"}, "o"))
    assert not off.enabled and off.reason
    lowered = effective(definition, parse_overrides({"providers": {"fake": {"access_mode": "listing"}}}, {"fake"}, "o"))
    assert lowered.access_mode is AccessMode.LISTING
    listing = fake_definition("fake", access_mode="listing")
    with pytest.raises(DefinitionError):
        effective(listing, parse_overrides({"providers": {"fake": {"access_mode": "public"}}}, {"fake"}, "o"))
    assert not effective(definition, parse_overrides({"disabled": True}, {"fake"}, "o")).enabled


def test_overrides_reject_unknown_providers_and_settings():
    with pytest.raises(DefinitionError):
        parse_overrides({"providers": {"nope": {"enabled": True}}}, {"fake"}, "o")
    with pytest.raises(DefinitionError):
        parse_overrides({"turbo": True}, {"fake"}, "o")


def test_experimental_providers_are_off_until_enabled():
    definition = fake_definition("fake", maturity="experimental")
    assert not effective(definition, parse_overrides({}, {"fake"}, "o")).enabled
    assert effective(definition, parse_overrides({"providers": {"fake": {"enabled": True}}}, {"fake"}, "o")).enabled


# --- state ------------------------------------------------------------------------------------------------


def test_venue_ids_are_minted_once_and_aliases_resolve(tmp_path):
    store = Store(tmp_path)
    first = store.save_venue({"channels": []}, ["google:P1"])
    again = store.save_venue({"channels": [{"kind": "phone", "source": "curated"}]}, ["google:P1", "fake:amber"])
    assert first == again and first.startswith("v_")
    assert store.venue_for_alias("fake:amber") == first
    other = store.save_venue({"channels": []}, ["fake:other"])
    store.merge_venues(first, other)
    assert store.get_venue(other)["venue_id"] == first
    assert store.venue_for_alias("fake:other") == first


def test_budget_shares_and_breaker(tmp_path):
    store = Store(tmp_path)
    assert all(store.spend("fake", "alex", 3, 2) for _ in range(2))
    assert not store.spend("fake", "alex", 3, 2)  # alex's share is spent
    assert store.spend("fake", "sam", 3, 2)
    assert not store.spend("fake", "sam", 3, 2)  # everyone's budget is spent
    assert store.used("fake", ALL) == 3
    first = store.trip("fake", "429")
    second = store.trip("fake", "429")
    assert first.is_open() and second.open_until - time.time() > first.open_until - time.time() - 1
    assert Store(tmp_path).breaker("fake").is_open()  # survives a restart


def test_state_files_are_private(tmp_path):
    store = Store(tmp_path / "s")
    assert oct((tmp_path / "s").stat().st_mode & 0o777) == "0o700"
    assert oct(store.path.stat().st_mode & 0o777) == "0o600"


def test_roster(tmp_path):
    store = Store(tmp_path)
    assert store.add_person("alex", "Alex")
    assert not store.add_person("alex")
    with pytest.raises(ValueError):
        store.add_person("../etc")
    assert store.has_person("alex") and not store.has_person("sam")
    assert store.remove_person("alex") and not store.has_person("alex")


# --- auth -------------------------------------------------------------------------------------------------

TOKEN = "t" * 40


def test_auth_modes_from_env(tmp_path):
    assert AuthConfig.from_env({"MCP_BEARER_TOKEN": TOKEN}).mode == "single"
    gateway = AuthConfig.from_env({"MCP_BEARER_TOKEN": TOKEN, "DINE_PERSON_HEADER": "X-Fleet-Person"})
    assert gateway.mode == "gateway"
    with pytest.raises(AuthError):
        AuthConfig.from_env({})
    with pytest.raises(AuthError):
        AuthConfig.from_env({"MCP_BEARER_TOKEN": "short"})
    with pytest.raises(AuthError):
        AuthConfig.from_env({"MCP_BEARER_TOKEN": TOKEN, "DINE_AUTH_MODE": "gateway"})


async def test_gateway_person_needs_bearer_and_valid_header(tmp_path):
    store = Store(tmp_path)
    store.add_person("alex")
    config = AuthConfig.from_env({"MCP_BEARER_TOKEN": TOKEN, "DINE_PERSON_HEADER": "X-Fleet-Person"})
    verifier = Verifier(config)
    assert await verifier.verify_token("wrong" * 10) is None
    token = await verifier.verify_token(TOKEN)
    assert token is not None and token.client_id == "gateway"
    assert person_for(config, token.client_id, {"x-fleet-person": "alex"}, store) == "alex"
    assert person_for(config, token.client_id, {"x-fleet-person": "sam"}, store) == "sam"
    assert store.has_person("sam")
    for headers in ({}, {"x-fleet-person": "../alex"}):
        with pytest.raises(DineError) as raised:
            person_for(config, token.client_id, headers, store)
        assert raised.value.code == "unknown_person"
    # Without the bearer there is no access token, so the header is never read.
    with pytest.raises(DineError):
        person_for(config, None, {"x-fleet-person": "alex"}, store)


async def test_users_mode_tokens_choose_the_person(tmp_path):
    path = tmp_path / "users.toml"
    alex = add_user(path, "alex")
    sam = add_user(path, "sam")
    assert TOKEN not in path.read_text() and alex not in path.read_text()
    assert oct(path.stat().st_mode & 0o777) == "0o600"
    config = AuthConfig.from_env({"DINE_USERS_FILE": str(path)})
    verifier = Verifier(config)
    store = Store(tmp_path / "s")
    for token, person in ((alex, "alex"), (sam, "sam")):
        access = await verifier.verify_token(token)
        assert access is not None
        assert person_for(config, access.client_id, {"x-fleet-person": "other"}, store) == person
    assert await verifier.verify_token("nope" * 10) is None
    with pytest.raises(AuthError):
        add_user(path, "alex")
    assert set(load_users(path)) == {"alex", "sam"}


def test_users_file_is_validated(tmp_path):
    path = Path(tmp_path / "users.toml")
    path.write_text('[users.alex]\ntoken_sha256 = "short"\n')
    with pytest.raises(AuthError):
        load_users(path)


# --- errors -----------------------------------------------------------------------------------------------


def test_every_error_code_is_self_describing():
    for code, (action, retryable, next_step) in CODES.items():
        payload = DineError(code).payload()
        assert payload["code"] == code and payload["next_step"] == next_step
        assert action in {"retry_later", "renewal_pending", "recovery_pending", "human_account_check", "none"}
        assert next_step.endswith(".") and len(next_step) > 20
        assert isinstance(retryable, bool)
