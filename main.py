import os
import json
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, EmailStr
import psycopg2
from psycopg2.extras import RealDictCursor
import google.generativeai as genai
import stripe
from db import get_db_connection

app = FastAPI(title="Autonomous Dispute & Claim Engine")

os.makedirs("static", exist_ok=True)
app.mount("/static", StaticFiles(directory="static"), name="static")

genai.configure(api_key=os.getenv("GEMINI_API_KEY"))
model = genai.GenerativeModel("gemini-3.6-flash")
stripe.api_key = os.getenv("STRIPE_SECRET_KEY")

class SocialSignalIngest(BaseModel):
    source_platform: str
    username: str
    user_id: str
    post_url: str
    post_text: str

class CustomerOptIn(BaseModel):
    lead_id: str
    full_name: str
    email: EmailStr
    phone: str
    stripe_payment_method_id: str
    consent_given: bool

class InboundCustomerMessage(BaseModel):
    lead_id: str
    channel: str
    sender: str
    message_body: str

class SettlementTrigger(BaseModel):
    lead_id: str
    actual_recovered_amount: float

@app.get("/")
def home():
    return {"status": "Dispute Resolution Engine Online"}

@app.get("/claim", response_class=HTMLResponse)
def serve_claim_portal():
    return FileResponse("static/claim.html")

@app.get("/api/v1/leads/{lead_id}")
def get_lead_details(lead_id: str):
    conn = get_db_connection()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT * FROM v_staged_leads_for_review WHERE lead_id = %s;", (lead_id,))
            lead = cur.fetchone()
            if not lead:
                raise HTTPException(status_code=404, detail="Lead not found")
            return lead
    finally:
        conn.close()

@app.post("/api/v1/leads/evaluate")
def evaluate_and_stage_lead(payload: SocialSignalIngest):
    prompt = f"""
    You are an automated legal dispute analyzer. Extract claim parameters from this social post:
    \"{payload.post_text}\"

    Output ONLY valid JSON matching this schema:
    {{
      \"has_dispute_intent\": bool,
      \"flight_number\": string or null,
      \"airline\": string or null,
      \"delay_hours\": float or null,
      \"is_eligible\": bool,
      \"estimated_recovery_usd\": float or null,
      \"legal_basis\": string or null,
      \"confidence_score\": float,
      \"draft_outreach_reply\": string
    }}
    """
    response = model.generate_content(prompt, generation_config={"response_mime_type": "application/json"})
    analysis = json.loads(response.text)

    if not analysis.get("has_dispute_intent") or not analysis.get("is_eligible"):
        return {"status": "ignored", "reason": "Not eligible"}

    conn = get_db_connection()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("""
                INSERT INTO incidents (incident_type, identifier, metadata)
                VALUES (%s, %s, %s)
                RETURNING id;
            """, ('flight_delay', f"{analysis.get('flight_number', 'UNKNOWN')}_{payload.source_platform}", json.dumps(analysis)))
            incident_id = cur.fetchone()['id']

            cur.execute("""
                INSERT INTO leads (incident_id, source_platform, platform_user_id, platform_username, post_url, raw_post_text, status)
                VALUES (%s, %s, %s, %s, %s, %s, 'staged_for_review')
                RETURNING id;
            """, (incident_id, payload.source_platform, payload.user_id, payload.username, payload.post_url, payload.post_text))
            lead_id = cur.fetchone()['id']

            cur.execute("""
                INSERT INTO dispute_evaluations (lead_id, is_eligible, estimated_recovery_amount, governing_statute, ai_reasoning, outreach_copy_draft, confidence_score)
                VALUES (%s, %s, %s, %s, %s, %s, %s);
            """, (
                lead_id,
                analysis.get('is_eligible'),
                analysis.get('estimated_recovery_usd'),
                analysis.get('legal_basis'),
                f"Extracted flight {analysis.get('flight_number')} with {analysis.get('delay_hours')}h delay.",
                analysis.get('draft_outreach_reply'),
                analysis.get('confidence_score')
            ))

            cur.execute("INSERT INTO lead_contacts (lead_id) VALUES (%s);", (lead_id,))
            conn.commit()
            return {"status": "staged", "lead_id": str(lead_id), "recovery_amount": analysis.get("estimated_recovery_usd")}
    finally:
        conn.close()

@app.post("/api/v1/claim/opt-in")
def register_customer_opt_in(payload: CustomerOptIn):
    if not payload.consent_given:
        raise HTTPException(status_code=400, detail="Consent is required.")

    customer_id = None
    if stripe.api_key and payload.stripe_payment_method_id != "pm_card_mock":
        try:
            customer = stripe.Customer.create(
                email=payload.email,
                name=payload.full_name,
                payment_method=payload.stripe_payment_method_id,
                invoice_settings={"default_payment_method": payload.stripe_payment_method_id}
            )
            customer_id = customer.id
        except Exception:
            customer_id = "cus_mock_fallback"
    else:
        customer_id = "cus_mock_local"

    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                UPDATE lead_contacts
                SET full_name = %s,
                    email = %s,
                    phone = %s,
                    stripe_customer_id = %s,
                    stripe_payment_method_id = %s,
                    consent_obtained = TRUE,
                    consent_timestamp = NOW()
                WHERE lead_id = %s;
            """, (payload.full_name, payload.email, payload.phone, customer_id, payload.stripe_payment_method_id, payload.lead_id))

            cur.execute("UPDATE leads SET status = 'opted_in' WHERE id = %s;", (payload.lead_id,))
            conn.commit()
        return {"status": "success", "message": "Claim onboarded."}
    finally:
        conn.close()

@app.post("/api/v1/communication/inbound-webhook")
def handle_customer_inbound_message(payload: InboundCustomerMessage):
    conn = get_db_connection()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT * FROM v_staged_leads_for_review WHERE lead_id = %s;", (payload.lead_id,))
            case_data = cur.fetchone()

        if not case_data:
            raise HTTPException(status_code=404, detail="Case context not found")

        ai_prompt = f"""
        You are an autonomous dispute advocate resolving a case for {case_data['full_name']}.
        Case Info: Incident {case_data['incident_identifier']}, Statute: {case_data['governing_statute']}, Current Status: {case_data['status']}.
        Customer message: \"{payload.message_body}\"

        Draft a concise, supportive, and professional response. Remind them our 25% contingency fee applies only after carrier payout.
        """
        response = model.generate_content(ai_prompt)
        return {"status": "replied", "ai_response": response.text}
    finally:
        conn.close()

@app.post("/api/v1/monetization/settle")
def settle_contingency_commission(payload: SettlementTrigger):
    conn = get_db_connection()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT * FROM v_staged_leads_for_review WHERE lead_id = %s;", (payload.lead_id,))
            lead = cur.fetchone()
            if not lead:
                raise HTTPException(status_code=404, detail="Lead not found.")

            fee_pct = float(lead['fee_percentage'] or 25.0)
            charge_amount = round(payload.actual_recovered_amount * (fee_pct / 100.0), 2)
            charge_amount_cents = int(charge_amount * 100)

            payment_intent_id = "pi_mock_success"
            if stripe.api_key and lead['stripe_customer_id'] and not lead['stripe_customer_id'].startswith("cus_mock"):
                intent = stripe.PaymentIntent.create(
                    amount=charge_amount_cents,
                    currency="usd",
                    customer=lead['stripe_customer_id'],
                    payment_method=lead['stripe_payment_method_id'],
                    off_session=True,
                    confirm=True,
                    description=f"Contingency fee ({fee_pct}%) for settled claim {payload.lead_id}"
                )
                payment_intent_id = intent.id

            cur.execute("UPDATE lead_contacts SET fee_charged_amount = %s WHERE lead_id = %s;", (charge_amount, payload.lead_id))
            cur.execute("UPDATE leads SET status = 'won' WHERE id = %s;", (payload.lead_id,))
            conn.commit()

            return {
                "status": "settled",
                "recovered_amount": payload.actual_recovered_amount,
                "fee_collected": charge_amount,
                "stripe_payment_intent": payment_intent_id
            }
    finally:
        conn.close()

import asyncio
from worker import dispatch_approved_outreach

@app.on_event("startup")
async def start_background_dispatcher():
    async def dispatcher_loop():
        while True:
            try:
                dispatch_approved_outreach()
            except Exception as e:
                print(f"[WORKER LOOP ERROR] {e}")
            await asyncio.sleep(60) # Run every 60 seconds

    asyncio.create_task(dispatcher_loop())
