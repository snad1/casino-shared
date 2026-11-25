"""Casino API keys kept encrypted on the bot host, one file per key.

Each key lives in `<root>/<service>/<key_id>.json` (0600, directories 0700),
sealed with AES-256-GCM under a 32-byte key-encryption key in `kek_path`
(0600, created on first write). The file binds its service and id as
associated data, so a file copied under another name fails to open. A file or
key file another user could read, or one owned by someone else, is refused.

Defaults: root `~/.casino-vault`, key file `~/.config/casino-agent/vault.key`;
`CASINO_VAULT_DIR` and `CASINO_VAULT_KEY_FILE` override them. Error messages
name the key id and the problem, never the secret.
"""

import base64
import json
import logging
import os
import re
import stat
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import List, Optional

KEY_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")
SERVICE_RE = re.compile(r"^[a-z0-9_-]{1,32}$")
LABEL_MAX = 64
KINDS = ("key", "proxy", "telegram")
SECRET_MAX = 4096
_KEK_BYTES = 32
_NONCE_BYTES = 12
_FORMAT = 1


KEY_SECRET_RE = re.compile(r"^[\x21-\x7e]{8,4096}$")
KEY_SECRET_HELP = "the key must be 8-4096 printable characters with no spaces"
TELEGRAM_TOKEN_RE = re.compile(r"^\d{5,12}:[A-Za-z0-9_-]{30,64}$")


def check_secret(secret: str, kind: str = "key") -> None:
    """Raise ValueError when `secret` can't be stored as `kind`; the message never repeats it."""
    if not isinstance(secret, str) or not KEY_SECRET_RE.match(secret):
        raise ValueError(KEY_SECRET_HELP)
    if kind == "telegram" and not TELEGRAM_TOKEN_RE.match(secret):
        raise ValueError("That doesn't look like a Telegram bot token.")
    if kind == "proxy":
        from .net import validate_proxy
        try:
            ok = validate_proxy(secret) is not None
        except ValueError:
            ok = False
        if not ok:
            raise ValueError("a proxy must be an http://, https:// or socks5:// URL")


class VaultError(Exception):
    """The vault can't be used or a key can't be read or written; never carries a secret."""


@dataclass(frozen=True)
class KeyInfo:
    key_id: str
    label: str
    kind: str
    owner_user_id: Optional[int]
    last4: str
    created_at: str
    rotated_at: Optional[str]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _check_private(path: str, what: str) -> None:
    st = os.stat(path)
    if st.st_uid != os.geteuid():
        raise VaultError(f"{what} is owned by another user")
    if stat.S_IMODE(st.st_mode) & 0o077:
        raise VaultError(f"{what} must be readable by its owner only (chmod 600)")


def _private_dir(path: str) -> None:
    os.makedirs(path, mode=0o700, exist_ok=True)
    os.chmod(path, 0o700)


def _write_private(path: str, data: bytes) -> None:
    """Write `data` to `path` atomically, 0600 from the first byte."""
    tmp = f"{path}.{os.getpid()}.tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


class Vault:
    """The encrypted keys of one service on this host."""

    def __init__(self, service: str, root: Optional[str] = None,
                 kek_path: Optional[str] = None) -> None:
        if not SERVICE_RE.match(service or ""):
            raise VaultError("service name must be 1-32 lowercase letters, digits, _ or -")
        self.service = service
        self.root = os.path.expanduser(
            root or os.environ.get("CASINO_VAULT_DIR") or "~/.casino-vault")
        self.kek_path = os.path.expanduser(
            kek_path or os.environ.get("CASINO_VAULT_KEY_FILE")
            or "~/.config/casino-agent/vault.key")
        self.dir = os.path.join(self.root, service)

    def put(self, key_id: str, secret: str, label: str,
            owner_user_id: Optional[int] = None, kind: str = "key") -> KeyInfo:
        """Store `secret` as `key_id` (a casino `key`, a `proxy` URL or a `telegram` bot token);
        a replace keeps created."""
        self._check_id(key_id)
        if kind not in KINDS:
            raise VaultError(f"key {key_id!r}: kind must be one of {', '.join(KINDS)}")
        if not isinstance(secret, str) or not secret or len(secret) > SECRET_MAX:
            raise VaultError(f"key {key_id!r}: the secret must be 1-{SECRET_MAX} characters")
        label = (label or "").strip()[:LABEL_MAX] or key_id
        old = self.info(key_id)
        now = _now()
        aesgcm = self._cipher(create=True)
        nonce = os.urandom(_NONCE_BYTES)
        sealed = aesgcm.encrypt(nonce, secret.encode(), self._aad(key_id))
        doc = {
            "v": _FORMAT,
            "key_id": key_id,
            "label": label,
            "kind": kind,
            "owner_user_id": owner_user_id,
            "last4": secret[-4:],
            "nonce": base64.b64encode(nonce).decode(),
            "ciphertext": base64.b64encode(sealed).decode(),
            "created_at": old.created_at if old else now,
            "rotated_at": now if old else None,
        }
        _private_dir(self.root)
        _private_dir(self.dir)
        _write_private(self._path(key_id), json.dumps(doc, indent=2).encode())
        return self._info(doc)

    def secret(self, key_id: str) -> Optional[str]:
        """The decrypted secret of `key_id`, or None when the vault has no such key."""
        doc = self._read(key_id)
        if doc is None:
            return None
        try:
            nonce = base64.b64decode(doc["nonce"])
            sealed = base64.b64decode(doc["ciphertext"])
        except (KeyError, TypeError, ValueError):
            raise VaultError(f"key {key_id!r}: the vault file is damaged") from None
        from cryptography.exceptions import InvalidTag
        try:
            return self._cipher(create=False).decrypt(nonce, sealed, self._aad(key_id)).decode()
        except InvalidTag:
            raise VaultError(f"key {key_id!r}: wrong vault key or damaged file") from None

    def info(self, key_id: str) -> Optional[KeyInfo]:
        """What is known about `key_id` without decrypting it, or None when it is absent."""
        doc = self._read(key_id)
        return None if doc is None else self._info(doc)

    def keys(self) -> List[KeyInfo]:
        """Every key that opens, sorted by id; a damaged or unreadable file is left out."""
        try:
            names = sorted(os.listdir(self.dir))
        except FileNotFoundError:
            return []
        out = []
        for name in names:
            key_id = name[:-5] if name.endswith(".json") else ""
            if not KEY_ID_RE.match(key_id):
                continue
            try:
                info = self.info(key_id)
            except VaultError:
                continue
            if info is not None:
                out.append(info)
        return out

    def delete(self, key_id: str) -> bool:
        """Remove `key_id`; False when the vault has no such key."""
        self._check_id(key_id)
        try:
            os.remove(self._path(key_id))
        except FileNotFoundError:
            return False
        return True

    def _check_id(self, key_id: str) -> None:
        if not isinstance(key_id, str) or not KEY_ID_RE.match(key_id):
            raise VaultError("key id must be 1-32 lowercase letters, digits, _ or -")

    def _path(self, key_id: str) -> str:
        return os.path.join(self.dir, f"{key_id}.json")

    def _aad(self, key_id: str) -> bytes:
        return f"casino-vault:v{_FORMAT}:{self.service}:{key_id}".encode()

    def _read(self, key_id: str) -> Optional[dict]:
        self._check_id(key_id)
        path = self._path(key_id)
        if not os.path.exists(path):
            return None
        _check_private(path, f"vault file for key {key_id!r}")
        try:
            with open(path, encoding="utf-8") as f:
                doc = json.load(f)
        except (OSError, ValueError):
            raise VaultError(f"key {key_id!r}: the vault file can't be read") from None
        if not isinstance(doc, dict) or doc.get("v") != _FORMAT or doc.get("key_id") != key_id:
            raise VaultError(f"key {key_id!r}: the vault file is damaged")
        return doc

    def _info(self, doc: dict) -> KeyInfo:
        owner = doc.get("owner_user_id")
        return KeyInfo(
            key_id=doc["key_id"],
            label=str(doc.get("label") or doc["key_id"]),
            kind=doc.get("kind") if doc.get("kind") in KINDS else "key",
            owner_user_id=owner if isinstance(owner, int) else None,
            last4=str(doc.get("last4") or ""),
            created_at=str(doc.get("created_at") or ""),
            rotated_at=doc.get("rotated_at"),
        )

    def _cipher(self, create: bool):
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        return AESGCM(self._kek(create))

    def _kek(self, create: bool) -> bytes:
        if not os.path.exists(self.kek_path):
            if not create:
                raise VaultError("the vault key file is missing")
            _private_dir(os.path.dirname(self.kek_path))
            # Written whole under a temporary name, then linked: a lane starting at the
            # same moment never reads a half-written key, and the first one wins.
            tmp = f"{self.kek_path}.{os.getpid()}.tmp"
            _write_private(tmp, os.urandom(_KEK_BYTES))
            try:
                os.link(tmp, self.kek_path)
            except FileExistsError:
                pass
            finally:
                os.unlink(tmp)
        _check_private(self.kek_path, "the vault key file")
        with open(self.kek_path, "rb") as f:
            kek = f.read()
        if len(kek) != _KEK_BYTES:
            raise VaultError("the vault key file is damaged")
        return kek


def vault_info(service: str, key_id: str,
               log: Optional[logging.Logger] = None) -> Optional[KeyInfo]:
    """What `service`'s vault knows about `key_id` (no secret), or None when it is absent, a
    bad id, or the vault is unusable (logged on `log`, without the secret)."""
    if not KEY_ID_RE.match(key_id or ""):
        return None
    try:
        return Vault(service).info(key_id)
    except (VaultError, OSError) as e:
        (log or logging.getLogger(__name__)).warning("Key vault unavailable: %s", e)
        return None


def vault_secret(service: str, key_id: str, kind: str = "key",
                 log: Optional[logging.Logger] = None) -> Optional[str]:
    """`service`'s vault entry `key_id` when it is of `kind`, else None: absent, another kind,
    a bad id, or the vault is unusable (logged on `log`, without the secret)."""
    if not KEY_ID_RE.match(key_id or ""):
        return None
    try:
        vault = Vault(service)
        info = vault.info(key_id)
        return vault.secret(key_id) if info is not None and info.kind == kind else None
    except (VaultError, OSError) as e:
        (log or logging.getLogger(__name__)).warning("Key vault unavailable: %s", e)
        return None
