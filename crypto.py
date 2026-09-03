import os
import logging
from cryptography.fernet import Fernet

logger = logging.getLogger(__name__)

_cipher = None

def _get_cipher():
    """Get or initialize the Fernet cipher with the encryption key."""
    global _cipher
    if _cipher is not None:
        return _cipher

    key = os.getenv("SETTINGS_ENCRYPTION_KEY")
    if not key:
        # Generate a new key and warn the user
        new_key = Fernet.generate_key().decode('utf-8')
        logger.warning(
            f"SETTINGS_ENCRYPTION_KEY not configured. A new key has been generated. "
            f"Set SETTINGS_ENCRYPTION_KEY={new_key} in your .env file to persist encryption across restarts. "
            f"Without this, encrypted values will be unrecoverable on restart!"
        )
        _cipher = Fernet(new_key.encode('utf-8'))
    else:
        try:
            _cipher = Fernet(key.encode('utf-8'))
        except Exception as e:
            logger.error(f"Failed to initialize cipher with SETTINGS_ENCRYPTION_KEY: {e}. Generating new key.")
            new_key = Fernet.generate_key().decode('utf-8')
            logger.warning(f"Generated new key: {new_key}")
            _cipher = Fernet(new_key.encode('utf-8'))

    return _cipher

def encrypt_value(plaintext: str) -> str:
    """Encrypt a plaintext value and return the ciphertext as a string."""
    if not plaintext:
        return plaintext
    cipher = _get_cipher()
    ciphertext = cipher.encrypt(plaintext.encode('utf-8'))
    return ciphertext.decode('utf-8')

def decrypt_value(ciphertext: str) -> str:
    """Decrypt a ciphertext value and return the plaintext."""
    if not ciphertext:
        return ciphertext
    try:
        cipher = _get_cipher()
        plaintext = cipher.decrypt(ciphertext.encode('utf-8'))
        return plaintext.decode('utf-8')
    except Exception as e:
        logger.error(f"Failed to decrypt value: {e}. Returning ciphertext as-is.")
        return ciphertext
