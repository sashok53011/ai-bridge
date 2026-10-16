"""Шифрование секретов устройства: на диске не открытым текстом.

Ключи провайдеров — настоящие секреты (§7 CONTEXT.md): на сервер они
не уходят, но и на диске лежать открытым текстом не должны. Каждое
значение запаковано в AES-256-GCM; мастер-ключ живёт рядом, в
``master.key``:

  * на Windows мастер-ключ привязан к учётной записи через DPAPI —
    скопированный на другую машину или случайно закоммиченный
    ``keys.json`` без той же учётной записи не читается;
  * на POSIX это файл 0600: защита от выноса одного файла —
    без ``master.key`` шифротекст бесполезен.

Честное соглашение: потерять ``master.key`` — значит потерять ключи.
Поэтому сломанный или чужой мастер-ключ даёт громкую ошибку
(``SecretBoxError``), а не молчаливое «провайдер без ключа».
"""

from __future__ import annotations

import base64
import os
import secrets
import sys
from pathlib import Path

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

#: Имя файла мастер-ключа рядом с keys.json.
MASTER_KEY_FILE = "master.key"

_NONCE_BYTES = 12
_KEY_BYTES = 32


class SecretBoxError(RuntimeError):
    """Секреты не читаются: мастер-ключ потерян, сломан или чужой."""


if sys.platform == "win32":  # pragma: no cover - платформенный код
    import ctypes
    import ctypes.wintypes as wintypes

    class _DataBlob(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

    _crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _crypt32.CryptProtectData.restype = wintypes.BOOL
    _crypt32.CryptUnprotectData.restype = wintypes.BOOL
    _kernel32.LocalFree.restype = ctypes.c_void_p

    def _protect(data: bytes) -> bytes:
        source = ctypes.create_string_buffer(data)
        in_blob = _DataBlob(len(data), ctypes.cast(source, ctypes.POINTER(ctypes.c_char)))
        out_blob = _DataBlob()
        if not _crypt32.CryptProtectData(
            ctypes.byref(in_blob), None, None, None, None, 0, ctypes.byref(out_blob)
        ):
            raise SecretBoxError("DPAPI не смог защитить мастер-ключ")
        try:
            return ctypes.string_at(out_blob.pbData, out_blob.cbData)
        finally:
            _kernel32.LocalFree(out_blob.pbData)

    def _unprotect(data: bytes) -> bytes:
        source = ctypes.create_string_buffer(data)
        in_blob = _DataBlob(len(data), ctypes.cast(source, ctypes.POINTER(ctypes.c_char)))
        out_blob = _DataBlob()
        if not _crypt32.CryptUnprotectData(
            ctypes.byref(in_blob), None, None, None, None, 0, ctypes.byref(out_blob)
        ):
            raise SecretBoxError(
                "мастер-ключ не открывается этой учётной записью Windows "
                "(файл скопирован с другой машины или учётной записи?)"
            )
        try:
            return ctypes.string_at(out_blob.pbData, out_blob.cbData)
        finally:
            _kernel32.LocalFree(out_blob.pbData)

    _PREFIX = "dpapi"
else:

    def _protect(data: bytes) -> bytes:
        return data

    def _unprotect(data: bytes) -> bytes:
        return data

    _PREFIX = "raw"


class SecretBox:
    """Шифрует и расшифровывает строки мастер-ключом каталога устройства."""

    def __init__(self, directory: Path) -> None:
        self._path = Path(directory) / MASTER_KEY_FILE
        self._key = _load_or_create(self._path)

    def seal(self, plaintext: str) -> str:
        """Непрозрачная строка: base64 от nonce + шифротекст AES-GCM."""
        nonce = os.urandom(_NONCE_BYTES)
        box = AESGCM(self._key).encrypt(nonce, plaintext.encode("utf-8"), None)
        return base64.b64encode(nonce + box).decode("ascii")

    def open(self, blob: str) -> str:
        """Обратная операция; чужой или битый шифротекст — SecretBoxError."""
        try:
            raw = base64.b64decode(blob.encode("ascii"), validate=True)
            nonce, box = raw[:_NONCE_BYTES], raw[_NONCE_BYTES:]
            data = AESGCM(self._key).decrypt(nonce, box, None)
        except (ValueError, InvalidTag) as exc:
            raise SecretBoxError(f"не открывается: {exc}") from exc
        return data.decode("utf-8")


def _load_or_create(path: Path) -> bytes:
    """Мастер-ключ: прочитать, а если файла нет — создать.

    Сломанный файл не перезаписывается молча: новый ключ сделал бы
    старые секреты невосстановимыми без предупреждения.
    """
    if not path.exists():
        return _create(path)

    try:
        text = path.read_text(encoding="ascii")
    except (OSError, UnicodeDecodeError) as exc:
        raise SecretBoxError(f"мастер-ключ не читается ({exc})") from exc

    prefix, sep, payload = text.partition("\n")
    if not sep or prefix != _PREFIX:
        raise SecretBoxError(
            f"master.key повреждён или из другого окружения "
            f"(записан как «{prefix or 'пусто'}», здесь нужен «{_PREFIX.strip()}»)"
        )
    try:
        raw = base64.b64decode(payload.encode("ascii"), validate=True)
    except ValueError as exc:
        raise SecretBoxError(f"master.key повреждён ({exc})") from exc
    return _unprotect(raw)


def _create(path: Path) -> bytes:
    key = secrets.token_bytes(_KEY_BYTES)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        _PREFIX + "\n" + base64.b64encode(_protect(key)).decode("ascii"),
        encoding="ascii",
    )
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    return key
