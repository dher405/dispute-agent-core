import os
from twilio.rest import Client

TWILIO_ACCOUNT_SID = os.getenv("TWILIO_ACCOUNT_SID", "")
TWILIO_AUTH_TOKEN = os.getenv("TWILIO_AUTH_TOKEN", "")
TWILIO_PHONE_NUMBER = os.getenv("TWILIO_PHONE_NUMBER", "")
PUBLIC_TRACKING_BASE_URL = os.getenv("PUBLIC_TRACKING_BASE_URL", "https://dispute-admin.onrender.com/track")

def send_claim_confirmation_sms(to_phone: str, full_name: str, flight_num: str, lead_id: str, claimed_amount: float) -> dict:
    if not (TWILIO_ACCOUNT_SID and TWILIO_AUTH_TOKEN and TWILIO_PHONE_NUMBER):
        print("[TWILIO SMS] Credentials missing. Skipping SMS dispatch.", flush=True)
        return {"status": "skipped", "reason": "Missing credentials"}

    if not to_phone:
        print("[TWILIO SMS] No recipient phone number provided.", flush=True)
        return {"status": "skipped", "reason": "No phone number"}

    # Basic normalization to E.164 if missing country code
    clean_phone = "".join(c for c in to_phone if c.isdigit() or c == "+")
    if not clean_phone.startswith("+"):
        if len(clean_phone) == 10:
            clean_phone = "+1" + clean_phone
        else:
            clean_phone = "+" + clean_phone

    tracking_url = f"{PUBLIC_TRACKING_BASE_URL}?claim_id={lead_id}"
    message_body = (
        f"Hi {full_name}, your compensation claim of ${claimed_amount:,.2f} for flight {flight_num} "
        f"has been submitted to the carrier. Track progress live here: {tracking_url}"
    )

    try:
        client = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)
        message = client.messages.create(
            body=message_body,
            from_=TWILIO_PHONE_NUMBER,
            to=clean_phone
        )
        print(f"[TWILIO SMS SUCCESS] Sent confirmation to {clean_phone} (SID: {message.sid})", flush=True)
        return {"status": "sent", "sid": message.sid}
    except Exception as e:
        print(f"[TWILIO SMS ERROR] Failed to send SMS to {clean_phone}: {e}", flush=True)
        return {"status": "error", "error": str(e)}
