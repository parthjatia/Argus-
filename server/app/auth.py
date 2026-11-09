"""API keys: generation and hashing. (The FastAPI auth dependency comes with /v1/ingest.)"""

import hashlib
import secrets

API_KEY_PREFIX = "argus_"


def generate_api_key() -> str:
    # 32 random bytes = 256 bits of entropy. The prefix makes keys recognisable
    # in logs and by secret scanners (e.g. GitHub push protection).
    return API_KEY_PREFIX + secrets.token_urlsafe(32)


def hash_api_key(raw_key: str) -> str:
    # Plain SHA-256 is enough here (no bcrypt/argon2): those slow hashes protect
    # low-entropy human passwords from guessing; a 256-bit random key can't be
    # guessed. A fast, deterministic hash also lets us look the key up by its
    # hash with the UNIQUE index on api_keys.key_hash.
    return hashlib.sha256(raw_key.encode("utf-8")).hexdigest()
