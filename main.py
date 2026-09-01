import os
import json
import logging
from typing import Optional, Dict, Any
from decimal import Decimal

from fastapi import FastAPI, HTTPException, BackgroundTasks, Depends, status, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
import psycopg2
from psycopg2.extras import RealDictCursor
from google import genai
from google.genai import types

from sms_dispatcher import notify_claim_event
from carrier_dispatcher import dispatch_demand_email
from letter_generator import StatutoryDemandGenerator

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

DATABASE_URL = os.getenv("DATABASE_URL")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

app = FastAPI(title="Dispute Agent Core Engine", version="2.5.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])

def get_db():
    if not DATABASE_URL:
        raise HTTPException(status_code=500, detail="DATABASE_URL not configured")
    conn = psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)
    try:
        yield conn
    finally:
        conn.close()

class RawSignalPayload(BaseModel):
    source_platform: str = Field(..., example="reddit")
    platform_user_id: Optional[str] = None
    username: Optional[str] = None
    post_url: Optional[str] = None
    raw_post_text: str = Field(...)

class CarrierWebhookPayload(BaseModel):
    carrier_name: str
    vertical: str = "flight_disruption"
    claim_id: Optional[str] = None
    incident_identifier: Optional[str] = None
    decision: str
    payout_offered: Decimal = Decimal("0.00")
    resolution_notes: Optional[str] = None
    raw_metadata: Dict[str, Any] = Field(default_factory=dict)

class ClaimSubmissionPayload(BaseModel):
    lead_id: str
    claimant_name: str
    claimant_email: str
    claimant_phone: Optional[str] = None
    claimant_address: Optional[str] = None
    pnr: Optional[str] = None
    account_number: Optional[str] = None
    incident_date: Optional[str] = None
    digital_signature: str

class SettlementPayload(BaseModel):
    lead_id: str
    recovery_amount: Decimal

def trigger_carrier_demand_pipeline(lead_id: str):
    try:
        conn = psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM leads WHERE id::text = %s;", (lead_id,))
            lead = cur.fetchone()
            if not lead:
                return
            pdf_bytes = StatutoryDemandGenerator.generate_pdf(lead)
            success, note = dispatch_demand_email(lead, pdf_bytes)
            if success:
                cur.execute("UPDATE leads SET status = 'dispatched', updated_at = NOW() WHERE id::text = %s;", (lead_id,))
                cur.execute("""
                    INSERT INTO carrier_inbound_events (lead_id, carrier_name, vertical, event_type, settlement_amount, parsed_notes, raw_payload)
                    VALUES (%s, %s, %s, %s, %s, %s, %s);
                """, (lead_id, lead.get("carrier_name") or "Carrier", lead.get("vertical") or "flight_disruption", "demand_dispatched", 0.00, note, psycopg2.extras.Json({"note": note})))
                conn.commit()
        conn.close()
    except Exception as e:
        logger.error(f"Error in demand pipeline: {e}")

def evaluate_multi_vertical_signal(text: str) -> Dict[str, Any]:
    client = genai.Client(api_key=GEMINI_API_KEY)
    prompt = f"""Analyze consumer complaint for statutory compensation:
Verticals:
- 'flight_disruption': UK261/EU261, US DOT 14 CFR Part 260
- 'isp_outage': PUC Utility Tariffs for >4hr downtime
- 'security_deposit': State landlord deposit return penalty
- 'class_action': Active settlement funds
- 'other': Ineligible

Post: "{text}"
Return JSON:
{{
    "is_viable": true/false,
    "vertical": "flight_disruption"|"isp_outage"|"security_deposit"|"class_action"|"other",
    "carrier_name": "Name",
    "incident_identifier": "Flight/Ticket/null",
    "estimated_compensation": 0.00,
    "regulatory_framework": "Legal Citation",
    "ai_reasoning": "Reasoning breakdown",
    "outreach_copy": "Outreach text under 250 chars"
}}"""
    try:
        res = client.models.generate_content(model="gemini-3.6-flash", contents=prompt, config=types.GenerateContentConfig(response_mime_type="application/json", temperature=0.1))
        return json.loads(res.text)
    except Exception:
        fallback = client.models.generate_content(model="gemini-2.0-flash", contents=prompt, config=types.GenerateContentConfig(response_mime_type="application/json", temperature=0.1))
        return json.loads(fallback.text)

@app.get("/health")
def health_check():
    return {"status": "healthy", "service": "dispute-api"}

@app.post("/api/v1/leads/evaluate", status_code=status.HTTP_201_CREATED)
def intake_and_evaluate(payload: RawSignalPayload, conn=Depends(get_db)):
    ev = evaluate_multi_vertical_signal(payload.raw_post_text)
    if not ev.get("is_viable", False):
        return {"status": "ignored", "reason": "Not viable under statutory frameworks"}
    with conn.cursor() as cur:
        cur.execute("""
            INSERT INTO leads (vertical, source_platform, platform_user_id, username, post_url, raw_post_text, carrier_name, incident_identifier, estimated_compensation, regulatory_framework, ai_reasoning, outreach_copy, status)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'staged_for_review')
            RETURNING id::text, vertical, carrier_name, estimated_compensation, status;
        """, (ev.get("vertical", "flight_disruption"), payload.source_platform, payload.platform_user_id, payload.username, payload.post_url, payload.raw_post_text, ev.get("carrier_name"), ev.get("incident_identifier"), ev.get("estimated_compensation", 0.00), ev.get("regulatory_framework"), ev.get("ai_reasoning"), ev.get("outreach_copy")))
        inserted = cur.fetchone()
        conn.commit()
    return {"status": "staged", "lead": inserted}

@app.get("/api/v1/leads")
def list_leads(status: Optional[str] = Query(None), conn=Depends(get_db)):
    query = "SELECT id::text AS id, vertical, source_platform, username, carrier_name, incident_identifier, estimated_compensation, recovery_amount, fee_collected, regulatory_framework, ai_reasoning, outreach_copy, status, created_at FROM leads"
    params = []
    if status:
        query += " WHERE status = %s"
        params.append(status)
    query += " ORDER BY created_at DESC LIMIT 100;"
    with conn.cursor() as cur:
        cur.execute(query, tuple(params))
        return cur.fetchall()

@app.get("/api/v1/claims/track/{lead_id}")
def get_claim_tracking(lead_id: str, conn=Depends(get_db)):
    with conn.cursor() as cur:
        cur.execute("SELECT id::text AS id, vertical, carrier_name, incident_identifier, estimated_compensation, recovery_amount, fee_collected, regulatory_framework, status, created_at, updated_at FROM leads WHERE id::text = %s;", (lead_id,))
        claim = cur.fetchone()
    if not claim:
        raise HTTPException(status_code=404, detail="Claim not found")
    return claim

@app.post("/api/v1/claims/submit")
def submit_claim(payload: ClaimSubmissionPayload, bg: BackgroundTasks, conn=Depends(get_db)):
    with conn.cursor() as cur:
        cur.execute("""
            UPDATE leads SET claimant_name=%s, claimant_email=%s, claimant_phone=%s, claimant_address=%s, pnr=%s, account_number=%s, incident_date=%s, digital_signature=%s, status='opted_in', updated_at=NOW()
            WHERE id::text=%s RETURNING id::text, status, claimant_name;
        """, (payload.claimant_name, payload.claimant_email, payload.claimant_phone, payload.claimant_address, payload.pnr, payload.account_number, payload.incident_date, payload.digital_signature, payload.lead_id))
        updated = cur.fetchone()
        conn.commit()
    if not updated:
        raise HTTPException(status_code=404, detail="Lead not found")
    if payload.claimant_phone:
        bg.add_task(notify_claim_event, payload.lead_id, "opt_in_confirmation")
    bg.add_task(trigger_carrier_demand_pipeline, payload.lead_id)
    return {"status": "opted_in", "claim": updated}

@app.post("/api/v1/claims/settle")
def settle_claim(payload: SettlementPayload, bg: BackgroundTasks, conn=Depends(get_db)):
    fee = (payload.recovery_amount * Decimal("0.25")).quantize(Decimal("0.01"))
    with conn.cursor() as cur:
        cur.execute("UPDATE leads SET status='settled', recovery_amount=%s, fee_collected=%s, updated_at=NOW() WHERE id::text=%s RETURNING id::text, status, recovery_amount, fee_collected;", (payload.recovery_amount, fee, payload.lead_id))
        settled = cur.fetchone()
        conn.commit()
    if not settled:
        raise HTTPException(status_code=404, detail="Lead not found")
    bg.add_task(notify_claim_event, payload.lead_id, "settlement_alert")
    return {"status": "settled", "claim": settled}

@app.post("/api/v1/webhooks/carrier/inbound")
def inbound_carrier_webhook(payload: CarrierWebhookPayload, bg: BackgroundTasks, conn=Depends(get_db)):
    matched_id = None
    with conn.cursor() as cur:
        if payload.claim_id:
            cur.execute("SELECT id::text FROM leads WHERE id::text = %s;", (str(payload.claim_id),))
            r = cur.fetchone()
            if r: matched_id = r["id"]
        elif payload.incident_identifier:
            cur.execute("SELECT id::text FROM leads WHERE incident_identifier = %s AND carrier_name ILIKE %s ORDER BY created_at DESC LIMIT 1;", (payload.incident_identifier, f"%{payload.carrier_name}%"))
            r = cur.fetchone()
            if r: matched_id = r["id"]

        cur.execute("""
            INSERT INTO carrier_inbound_events (lead_id, carrier_name, vertical, event_type, settlement_amount, parsed_notes, raw_payload)
            VALUES (%s, %s, %s, %s, %s, %s, %s);
        """, (matched_id, payload.carrier_name, payload.vertical, payload.decision, float(payload.payout_offered), payload.resolution_notes, json.dumps(payload.raw_metadata)))

        if matched_id and payload.decision == "approved" and payload.payout_offered > 0:
            rec = payload.payout_offered
            fee = (rec * Decimal("0.25")).quantize(Decimal("0.01"))
            cur.execute("UPDATE leads SET status='settled', recovery_amount=%s, fee_collected=%s, updated_at=NOW() WHERE id::text=%s;", (rec, fee, matched_id))
            bg.add_task(notify_claim_event, matched_id, "settlement_alert")
        conn.commit()
    return {"status": "processed", "lead_matched": matched_id is not None, "lead_id": matched_id}
