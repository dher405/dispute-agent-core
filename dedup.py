import hashlib
import re

def normalize_string(s: str) -> str:
    """Normalize a string for deduplication: lowercase, strip whitespace, remove special chars."""
    if not s:
        return ""
    s = s.lower().strip()
    s = re.sub(r'[^a-z0-9]', '', s)
    return s

def compute_dedup_key(username: str = None, email: str = None, phone: str = None) -> str:
    """
    Compute a normalized dedup key from username, email, and phone.
    Returns a SHA256 hex digest.
    """
    parts = []
    if username:
        parts.append(normalize_string(username))
    if email:
        parts.append(normalize_string(email))
    if phone:
        parts.append(normalize_string(phone))

    combined = "|".join(filter(None, parts))
    if not combined:
        return None

    return hashlib.sha256(combined.encode('utf-8')).hexdigest()
