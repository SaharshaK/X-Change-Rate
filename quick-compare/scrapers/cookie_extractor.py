from __future__ import annotations
"""
Extracts and decrypts cookies from the running Chrome browser on macOS.
Uses the macOS Keychain for the AES-128-CBC decryption key.
"""
import hashlib
import os
import shutil
import sqlite3
import subprocess
import tempfile
from typing import List

from Crypto.Cipher import AES

CHROME_COOKIE_DB = os.path.expanduser(
    "~/Library/Application Support/Google/Chrome/Default/Cookies"
)


def _get_encryption_key() -> bytes:
    result = subprocess.run(
        ["security", "find-generic-password", "-w", "-s", "Chrome Safe Storage"],
        capture_output=True, text=True, check=True,
    )
    password = result.stdout.strip().encode()
    return hashlib.pbkdf2_hmac("sha1", password, b"saltysalt", 1003, dklen=16)


def _decrypt(encrypted_value: bytes, key: bytes) -> str:
    if not encrypted_value:
        return ""
    if not encrypted_value.startswith(b"v10"):
        return encrypted_value.decode("utf-8", errors="ignore")

    # Chrome (v127+) prepends 32 random bytes to the plaintext before AES-128-CBC,
    # encrypts with a fixed IV of 0x20 (space) * 16.
    # Decrypt then strip first 32 bytes + trailing PKCS7 padding.
    payload = encrypted_value[3:]
    iv = b" " * 16

    if len(payload) % 16 != 0 or len(payload) < 48:
        return ""

    cipher = AES.new(key, AES.MODE_CBC, IV=iv)
    decrypted = cipher.decrypt(payload)

    pad = decrypted[-1]
    if 1 <= pad <= 16:
        decrypted = decrypted[:-pad]

    return decrypted[32:].decode("utf-8", errors="ignore")


def extract_cookies(domains: List[str]) -> List[dict]:
    """Return Playwright-compatible cookie dicts for the given domains."""
    tmp = tempfile.mktemp(suffix=".db")
    shutil.copy2(CHROME_COOKIE_DB, tmp)

    key = _get_encryption_key()
    cookies: List[dict] = []

    try:
        conn = sqlite3.connect(tmp)
        conditions = " OR ".join(["host_key LIKE ?" for _ in domains])
        params = [f"%{d}" for d in domains]

        cursor = conn.execute(
            f"""SELECT host_key, name, encrypted_value, path,
                       expires_utc, is_secure, is_httponly, samesite
                FROM cookies WHERE {conditions}""",
            params,
        )

        # Chrome samesite: -1=unspecified, 0=no_restriction(None), 1=Lax, 2=Strict
        samesite_map = {-1: "Lax", 0: "None", 1: "Lax", 2: "Strict"}

        for host_key, name, enc_val, path, expires_utc, is_secure, is_httponly, samesite in cursor:
            try:
                value = _decrypt(enc_val, key)
                if not value:
                    continue

                cookie: dict = {
                    "name": name,
                    "value": value,
                    "domain": host_key,
                    "path": path or "/",
                    "secure": bool(is_secure),
                    "httpOnly": bool(is_httponly),
                    "sameSite": samesite_map.get(samesite, "Lax"),
                }

                # Convert Chrome epoch (microseconds since 1601-01-01) to Unix timestamp
                if expires_utc and expires_utc > 0:
                    unix_ts = (expires_utc - 11644473600000000) / 1_000_000
                    if unix_ts > 0:
                        cookie["expires"] = unix_ts

                cookies.append(cookie)
            except Exception:
                pass

        conn.close()
    finally:
        os.unlink(tmp)

    return cookies
