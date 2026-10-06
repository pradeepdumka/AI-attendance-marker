"""Argon2id password hashing.

Callers store only the string returned by `hash_password` on
`User.password_hash`. This module never writes a user row, and
`verify_password` never treats a stored value as a plain password.
"""

import logging

from pwdlib import PasswordHash
from pwdlib.exceptions import PwdlibError

logger = logging.getLogger(__name__)

# Argon2id with a random salt. The same password hashes differently each time.
_hasher = PasswordHash.recommended()


def hash_password(password: str) -> str:
    """Return an Argon2id hash safe to store in `users.password_hash`."""
    if password == "":
        raise ValueError("Password must not be empty")
    return _hasher.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    """Return whether `password` matches a previously stored hash.

    An unrecognized or corrupt hash is a failed check. It is not compared
    to the plain password, and the hash itself is not logged.
    """
    try:
        return _hasher.verify(password, password_hash)
    except PwdlibError:
        return False
    except Exception:
        logger.warning("Password hash could not be verified")
        return False
