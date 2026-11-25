"""The host key vault: sealed per key, private files only, never a secret in an error."""

import json
import os
import stat

import pytest

from shared.core.vault import Vault, VaultError

SECRET = "wb_live_0123456789abcdefSECRET9876"


def _mode(path) -> int:
    return stat.S_IMODE(os.stat(path).st_mode)


@pytest.fixture
def vault(tmp_path):
    return Vault("wolfbet", root=str(tmp_path / "vault"), kek_path=str(tmp_path / "agent/vault.key"))


def test_round_trip_keeps_only_safe_facts_in_the_clear(vault):
    info = vault.put("default", SECRET, "Main account")
    assert (info.key_id, info.label, info.last4, info.rotated_at) == (
        "default", "Main account", "9876", None)
    assert vault.secret("default") == SECRET
    raw = open(os.path.join(vault.dir, "default.json")).read()
    assert SECRET not in raw and SECRET[:-4] not in raw
    assert vault.info("default") == info


def test_files_and_dirs_are_private(vault):
    vault.put("default", SECRET, "x")
    assert _mode(vault.kek_path) == 0o600
    assert _mode(os.path.join(vault.dir, "default.json")) == 0o600
    assert _mode(vault.dir) == 0o700 and _mode(vault.root) == 0o700
    assert _mode(os.path.dirname(vault.kek_path)) == 0o700


def test_replacing_a_key_keeps_created_and_sets_rotated(vault):
    first = vault.put("default", SECRET, "x")
    second = vault.put("default", "wb_live_other_key_0000", "x")
    assert second.created_at == first.created_at and second.rotated_at is not None
    assert vault.secret("default") == "wb_live_other_key_0000"


def test_a_missing_key_is_none(vault):
    assert vault.secret("friend-a") is None and vault.info("friend-a") is None


def test_the_wrong_vault_key_is_refused(vault, tmp_path):
    vault.put("default", SECRET, "x")
    other = Vault("wolfbet", root=vault.root, kek_path=str(tmp_path / "other/vault.key"))
    other.put("spare", "spare-secret-value", "x")
    with pytest.raises(VaultError, match="wrong vault key or damaged file") as e:
        other.secret("default")
    assert SECRET not in str(e.value)


def test_a_file_copied_under_another_id_does_not_open(vault):
    vault.put("default", SECRET, "x")
    doc = json.load(open(os.path.join(vault.dir, "default.json")))
    doc["key_id"] = "friend-a"
    path = os.path.join(vault.dir, "friend-a.json")
    fd = os.open(path, os.O_WRONLY | os.O_CREAT, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(doc, f)
    with pytest.raises(VaultError, match="wrong vault key or damaged file"):
        vault.secret("friend-a")


def test_a_tampered_ciphertext_is_refused(vault):
    vault.put("default", SECRET, "x")
    path = os.path.join(vault.dir, "default.json")
    doc = json.load(open(path))
    doc["ciphertext"] = doc["ciphertext"][:-4] + ("AAAA" if doc["ciphertext"][-4:] != "AAAA" else "BBBB")
    with open(path, "w") as f:
        json.dump(doc, f)
    with pytest.raises(VaultError):
        vault.secret("default")


@pytest.mark.parametrize("which", ["kek", "file"])
def test_loose_permissions_are_refused(vault, which):
    vault.put("default", SECRET, "x")
    path = vault.kek_path if which == "kek" else os.path.join(vault.dir, "default.json")
    os.chmod(path, 0o644)
    with pytest.raises(VaultError, match="chmod 600"):
        vault.secret("default")


def test_a_missing_vault_key_is_refused_on_read(vault):
    vault.put("default", SECRET, "x")
    os.remove(vault.kek_path)
    with pytest.raises(VaultError, match="vault key file is missing"):
        vault.secret("default")


@pytest.mark.parametrize("key_id", ["", "Default", "../x", "a/b", "-x", "a" * 33, None])
def test_bad_key_ids_are_refused(vault, key_id):
    with pytest.raises(VaultError, match="key id"):
        vault.put(key_id, SECRET, "x")


@pytest.mark.parametrize("service", ["", "Wolf", "a/b", "x" * 33])
def test_bad_service_names_are_refused(service, tmp_path):
    with pytest.raises(VaultError, match="service name"):
        Vault(service, root=str(tmp_path))


def test_an_empty_secret_is_refused_without_echoing_it(vault):
    with pytest.raises(VaultError, match="secret must be") as e:
        vault.put("default", "", "x")
    assert "default" in str(e.value)


def test_paths_default_to_the_environment(monkeypatch, tmp_path):
    monkeypatch.setenv("CASINO_VAULT_DIR", str(tmp_path / "v"))
    monkeypatch.setenv("CASINO_VAULT_KEY_FILE", str(tmp_path / "k/vault.key"))
    v = Vault("wolfbet")
    v.put("default", SECRET, "x")
    assert os.path.exists(tmp_path / "v/wolfbet/default.json")
    assert os.path.exists(tmp_path / "k/vault.key")


def test_keys_lists_every_key_without_secrets_and_skips_a_damaged_file(vault):
    vault.put("friend-a", "friend-secret-0001", "Friend A", owner_user_id=7)
    vault.put("default", SECRET, "Main")
    with open(os.path.join(vault.dir, "broken.json"), "w") as f:
        f.write("{")
    os.chmod(os.path.join(vault.dir, "broken.json"), 0o600)
    keys = vault.keys()
    assert [k.key_id for k in keys] == ["default", "friend-a"]
    assert keys[1].owner_user_id == 7 and keys[1].last4 == "0001"
    assert SECRET not in repr(keys)


def test_an_empty_vault_lists_nothing(vault):
    assert vault.keys() == []


def test_delete_removes_one_key(vault):
    vault.put("friend-a", "friend-secret-0001", "Friend A")
    assert vault.delete("friend-a") is True
    assert vault.secret("friend-a") is None and vault.delete("friend-a") is False


def test_a_proxy_entry_keeps_its_kind_and_older_files_read_as_keys(vault):
    vault.put("friend-a-proxy", "socks5://u:p@10.0.0.9:1080", "Friend proxy", kind="proxy")
    vault.put("default", SECRET, "Main")
    path = os.path.join(vault.dir, "default.json")
    doc = json.load(open(path))
    doc.pop("kind")
    with open(path, "w") as f:
        json.dump(doc, f)
    assert [(k.key_id, k.kind) for k in vault.keys()] == [
        ("default", "key"), ("friend-a-proxy", "proxy")]


def test_a_telegram_bot_token_is_its_own_kind(vault):
    vault.put("telegram", "123456789:AAH-telegram-bot-token-x9Yz0123456789ab", "Telegram bot",
              kind="telegram")
    [info] = vault.keys()
    assert (info.key_id, info.kind, info.last4) == ("telegram", "telegram", "89ab")
    assert vault.secret("telegram").startswith("123456789:")


def test_an_unknown_kind_is_refused(vault):
    with pytest.raises(VaultError, match="kind must be"):
        vault.put("x", SECRET, "x", kind="cookie")


@pytest.fixture
def host_vault(tmp_path, monkeypatch):
    """The default-path vault the bots open, pointed at tmp."""
    monkeypatch.setenv("CASINO_VAULT_DIR", str(tmp_path / "vault"))
    monkeypatch.setenv("CASINO_VAULT_KEY_FILE", str(tmp_path / "agent/vault.key"))
    return Vault("wolfbet")


def test_the_client_reads_an_entry_of_the_asked_kind_only(host_vault):
    from shared.core.vault import vault_info, vault_secret
    host_vault.put("default", SECRET, "Main")
    host_vault.put("px", "http://u:p@10.0.0.2:3128", "Proxy", kind="proxy")
    assert vault_secret("wolfbet", "default") == SECRET
    assert vault_secret("wolfbet", "default", "proxy") is None
    assert vault_secret("wolfbet", "px", "proxy") == "http://u:p@10.0.0.2:3128"
    assert vault_secret("wolfbet", "missing") is None
    assert vault_secret("wolfbet", "Bad Id!") is None
    assert vault_info("wolfbet", "px").kind == "proxy"
    assert vault_info("wolfbet", "missing") is None and vault_info("wolfbet", "") is None


def test_an_unusable_vault_is_logged_without_the_secret(host_vault, caplog):
    import logging

    from shared.core.vault import vault_info, vault_secret
    host_vault.put("default", SECRET, "Main")
    os.chmod(host_vault.kek_path, 0o644)
    log = logging.getLogger("a-bot")
    with caplog.at_level(logging.WARNING, logger="a-bot"):
        assert vault_secret("wolfbet", "default", log=log) is None
        assert vault_info("wolfbet", "default", log=log) is not None
    assert "Key vault unavailable" in caplog.text and SECRET not in caplog.text


@pytest.mark.parametrize(("secret", "kind", "message"), [
    ("short", "key", "the key must be 8-4096 printable characters with no spaces"),
    ("has a space in it", "key", "the key must be 8-4096 printable characters with no spaces"),
    ("not-a-bot-token-at-all", "telegram", "That doesn't look like a Telegram bot token."),
    ("ftp://user:pw@host:21", "proxy", "a proxy must be an http://, https:// or socks5:// URL"),
])
def test_a_secret_that_cant_be_stored_is_refused_without_echo(secret, kind, message):
    from shared.core.vault import check_secret
    with pytest.raises(ValueError) as exc:
        check_secret(secret, kind)
    assert str(exc.value) == message and secret not in str(exc.value)


def test_good_secrets_pass():
    from shared.core.vault import check_secret
    check_secret(SECRET)
    check_secret("12345:" + "a" * 35, "telegram")
    check_secret("socks5://user:pw@10.0.0.2:1080", "proxy")


# ── account entries ─────────────────────────────────────────────────────

ACCOUNT = {"access_token": "acc-0123456789abcdef9f3c", "lockdown_token": "lock-0123456789",
           "cookie": "session=abc; cf_clearance=xyz-123; locale=en"}


def test_an_account_round_trips_and_shows_the_primary_last4(vault):
    info = vault.put_account("friend-a", ACCOUNT, "Friend A", primary="access_token")
    assert (info.kind, info.last4) == ("account", "9f3c")
    assert vault.account("friend-a") == ACCOUNT
    raw = open(os.path.join(vault.dir, "friend-a.json")).read()
    assert all(v not in raw for v in ACCOUNT.values())
    assert [k.key_id for k in vault.keys()] == ["friend-a"]


def test_an_account_is_never_read_as_another_kind(vault, monkeypatch, tmp_path):
    from shared.core import vault as vault_mod
    vault.put_account("friend-a", ACCOUNT, "x", primary="access_token")
    vault.put("plain", SECRET, "x")
    assert vault.account("plain") is None and vault.account("nobody") is None
    monkeypatch.setenv("CASINO_VAULT_DIR", vault.root)
    monkeypatch.setenv("CASINO_VAULT_KEY_FILE", vault.kek_path)
    assert vault_mod.vault_secret("wolfbet", "friend-a") is None
    assert vault_mod.vault_account("wolfbet", "friend-a") == ACCOUNT
    assert vault_mod.vault_account("wolfbet", "plain") is None


def test_a_put_of_kind_account_must_use_put_account(vault):
    with pytest.raises(VaultError):
        vault.put("friend-a", SECRET, "x", kind="account")


@pytest.mark.parametrize("fields", [
    {**ACCOUNT, "cookie": "line one\nline two"},
    {**ACCOUNT, "cookie": "x" * 17000},
    {**ACCOUNT, "access_token": ""},
    {**ACCOUNT, "Bad-Name": "v"},
])
def test_a_bad_account_is_refused_without_its_values(vault, fields):
    with pytest.raises(VaultError) as e:
        vault.put_account("friend-a", fields, "x", primary="access_token")
    assert all(v not in str(e.value) for v in fields.values() if len(v) > 4)
    assert vault.info("friend-a") is None


def test_check_account_wants_exactly_the_named_fields():
    from shared.core.vault import check_account
    check_account(ACCOUNT, ["access_token", "lockdown_token", "cookie"])
    with pytest.raises(ValueError):
        check_account({"access_token": "x"}, ["access_token", "lockdown_token", "cookie"])
