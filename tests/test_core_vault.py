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
