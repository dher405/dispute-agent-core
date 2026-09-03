import re

# Keywords that indicate a strong, specific, verifiable claim (higher weight)
STRONG_SIGNAL_KEYWORDS = [
    "hours late", "cancelled my flight", "flight cancelled", "no compensation",
    "denied boarding", "bumped", "lost luggage", "outage for", "no internet for",
    "billed twice", "withheld my deposit", "never refunded"
]

WEAK_SIGNAL_KEYWORDS = [
    "delay", "delayed", "stuck", "stranded", "diverted", "outage", "credit"
]

DOLLAR_AMOUNT_RE = re.compile(r"\$\s?\d{2,}")
DATE_HINT_RE = re.compile(r"\b(\d{1,2}[/-]\d{1,2}([/-]\d{2,4})?|january|february|march|april|may|june|july|august|september|october|november|december)\b", re.IGNORECASE)

PLATFORM_WEIGHTS = {
    "reddit": 10,
    "bluesky": 8,
    "twitter": 8,
    "hackernews": 6,
    "review_site": 12,
    "direct_inbound": 20,
    "easyclaim_landing_page": 20,
}


def compute_lead_score(eval_result: dict, raw_text: str, source_platform: str = "reddit") -> int:
    """
    Compute a 0-100 lead priority score from signal strength.
    Higher score = higher-confidence, higher-value, more-specific claim.
    Deterministic and cheap -- does not call any external API.
    """
    text = (raw_text or "").lower()
    score = 0

    # Base weight from which platform surfaced the lead
    score += PLATFORM_WEIGHTS.get((source_platform or "").lower(), 5)

    # Keyword specificity
    strong_hits = sum(1 for kw in STRONG_SIGNAL_KEYWORDS if kw in text)
    weak_hits = sum(1 for kw in WEAK_SIGNAL_KEYWORDS if kw in text)
    score += min(strong_hits * 12, 36)
    score += min(weak_hits * 4, 12)

    # Specificity signals: a dollar figure or a concrete date mentioned
    if DOLLAR_AMOUNT_RE.search(text):
        score += 10
    if DATE_HINT_RE.search(text):
        score += 8

    # Signal from the Gemini evaluation itself, if it produced a dollar estimate
    try:
        est = float((eval_result or {}).get("estimated_compensation") or 0)
        if est >= 700:
            score += 20
        elif est >= 300:
            score += 12
        elif est > 0:
            score += 6
    except (TypeError, ValueError):
        pass

    # A named carrier/regulatory framework increases confidence this is a real, actionable case
    if (eval_result or {}).get("carrier_name"):
        score += 6
    if (eval_result or {}).get("regulatory_framework"):
        score += 6

    return max(0, min(100, score))
