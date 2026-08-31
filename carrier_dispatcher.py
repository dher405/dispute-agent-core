import os
import base64
import resend
from letter_generator import generate_demand_pdf

resend.api_key = os.getenv("RESEND_API_KEY", "")
FROM_EMAIL = os.getenv("DISPATCH_FROM_EMAIL", "claims@disputeagent.com")

CARRIER_CLAIM_INBOXES = {
    "united": "customer.relations@united.com",
    "delta": "ticketreceipt@delta.com",
    "american": "customer.relations@aa.com",
    "southwest": "support@southwest.com",
    "default": os.getenv("FALLBACK_CLAIMS_EMAIL", "legal-claims@disputeagent.com")
}

def get_carrier_email(carrier_name: str) -> str:
    carrier_key = (carrier_name or "").lower().strip()
    for k, v in CARRIER_CLAIM_INBOXES.items():
        if k in carrier_key:
            return v
    return CARRIER_CLAIM_INBOXES["default"]

def dispatch_demand_letter_email(claim_data: dict) -> dict:
    if not resend.api_key:
        print("[CARRIER DISPATCH] RESEND_API_KEY not configured. Skipping email dispatch.", flush=True)
        return {"status": "skipped", "reason": "No API key"}

    carrier = claim_data.get("carrier_name", "Airline Carrier")
    target_email = get_carrier_email(carrier)
    full_name = claim_data.get("full_name", "Claimant")
    flight_num = claim_data.get("incident_identifier", "Disrupted Flight")
    statute = claim_data.get("governing_statute", "Applicable Air Passenger Rights Regulations")

    pdf_bytes = generate_demand_pdf(claim_data)
    pdf_base64 = base64.b64encode(pdf_bytes).decode("utf-8")

    subject = f"FORMAL STATUTORY DEMAND: Passenger Disruption Claim - {flight_num} ({full_name})"
    
    html_body = f"""
    <div style="font-family: Arial, sans-serif; line-height: 1.6; color: #1e293b;">
        <h2 style="color: #0f172a;">Statutory Passenger Compensation Demand</h2>
        <p>Dear {carrier} Customer Relations & Legal Department,</p>
        <p>Please find attached the formal statutory claim package and digital power of attorney for passenger <strong>{full_name}</strong> regarding flight <strong>{flight_num}</strong> pursuant to <strong>{statute}</strong>.</p>
        <p><strong>Claim Details:</strong></p>
        <ul>
            <li><strong>Passenger Name:</strong> {full_name}</li>
            <li><strong>Flight / Incident:</strong> {flight_num}</li>
            <li><strong>Demand Amount:</strong> ${claim_data.get(claimed_amount, 0):,.2f}</li>
            <li><strong>Statutory Basis:</strong> {statute}</li>
        </ul>
        <p>Please acknowledge receipt and remit compensation to the claimant escrow account within statutory deadlines.</p>
        <hr style="border: none; border-top: 1px solid #cbd5e1; margin: 20px 0;" />
        <p style="font-size: 12px; color: #64748b;">Dispute Agent Core | Automated Aviation Regulatory Enforcement</p>
    </div>
    """

    params = {
        "from": FROM_EMAIL,
        "to": [target_email],
        "subject": subject,
        "html": html_body,
        "attachments": [
            {
                "filename": f"Demand_Letter_{claim_data.get(lead_id, claim)}.pdf",
                "content": list(pdf_bytes)
            }
        ]
    }

    try:
        response = resend.Emails.send(params)
        print(f"[CARRIER DISPATCH SUCCESS] Dispatched demand package to {target_email}: {response}", flush=True)
        return {"status": "sent", "carrier_email": target_email, "resend_id": response.get("id")}
    except Exception as e:
        print(f"[CARRIER DISPATCH ERROR] Failed to send email to {target_email}: {e}", flush=True)
        return {"status": "error", "error": str(e)}
