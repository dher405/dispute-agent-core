import os
import json
import logging
from typing import Optional, Dict, Any, List
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

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - [%(filename)s:%(lineno)d] - %(message)s")
logger = logging.getLogger(__name__)

DATABASE_URL = os.getenv("DATABASE_URL")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

app = FastAPI(
    title="Dispute Agent Core Engine",
    description="Autonomous multi-vertical statutory dispute recovery platform.",
    version="2.7.0"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

def get_db():
    if not DATABASE_URL:
        raise HTTPException(status_code=500, detail="DATABASE_URL not configured")
    conn = psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)
    try:
        yield conn
    finally:
        conn.close()

# --- Schemas ---

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

# --- Background Task Routines ---

def trigger_carrier_demand_pipeline(lead_id: str):
    """Generates statutory PDF and emails respondent legal desk."""
    try:
        conn = psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)
        with conn.cursor() as cur:
            cur.execute("""
                SELECT 
                    id::text AS id,
                    vertical,
                    carrier_name,
                    incident_identifier,
                    account_number,
                    outage_duration_hours,
                    tier_speed_tier,
                    estimated_compensation,
                    recovery_amount,
                    regulatory_framework,
                    ai_reasoning,
                    status,
                    claimant_name,
                    claimant_email,
                    claimant_phone,
                    claimant_address,
                    pnr,
                    incident_date,
                    digital_signature
                FROM leads
                WHERE id::text = %s;
            """, (lead_id,))
            lead = cur.fetchone()

            if not lead:
                return

            pdf_bytes = StatutoryDemandGenerator.generate_pdf(lead)
            success, note = dispatch_demand_email(lead, pdf_bytes)

            if success:
                cur.execute("""
                    UPDATE leads 
                    SET status = 'dispatched', updated_at = NOW() 
                    WHERE id::text = %s;
                """, (lead_id,))
                
                cur.execute("""
                    INSERT INTO carrier_inbound_events (
                        lead_id, carrier_name, vertical, event_type, settlement_amount, parsed_notes, raw_payload
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s);
                """, (
                    lead_id,
                    lead.get("carrier_name") or "Carrier",
                    lead.get("vertical") or "flight_disruption",
                    "demand_dispatched",
                    0.00,
                    f"PDF demand served: {note}",
                    psycopg2.extras.Json({"note": note})
                ))
                conn.commit()
        conn.close()
    except Exception as e:
        logger.error(f"[BACKGROUND TASK ERROR] Demand pipeline error: {e}")

# --- AI Evaluation Gateway ---

def evaluate_multi_vertical_signal(text: str) -> Dict[str, Any]:
    client = genai.Client(api_key=GEMINI_API_KEY)
    prompt = f"""
You are the Lead Consumer Recovery Advocate for Dispute Agent.
Analyze the following public complaint to determine if the consumer is owed statutory compensation, bill credits, or refunds.

Core Rules for outreach_copy:
1. THIS MESSAGE IS WRITTEN TO THE AFFECTED CONSUMER / PASSENGER, NOT TO THE AIRLINE OR VENDOR.
2. NEVER start with 'Dear [Carrier] Customer Care' or 'I am writing to claim'.
3. Address the consumer directly ('You may be entitled to...', 'Because [Carrier] delayed flight [Flight]...').
4. Clearly state what statutory regulation protects them and the exact estimated dollar amount owed to them.
5. Keep outreach_copy under 220 characters so the authorization tracking link can be appended cleanly.

Verticals & Default Statutory Baselines:
- 'flight_disruption': UK261/EU261 (£520 / €600 / ~$650 USD for delays >3hrs from UK/EU or on EU/UK carriers), US DOT 14 CFR Part 260 (mandatory cash refunds for cancellations or significant delays >3hrs domestic, >6hrs international).
- 'isp_outage': Regional utility/telecom tariffs and state SLA mandates (baseline standard: $50.00-$150.00 for sustained outages >4-24hrs).
- 'security_deposit': Statutory landlord penalties (2x to 3x deposit) for failure to return/itemize within statutory deadlines (e.g., 30-60 days).
- 'class_action': Active court-approved restitution funds or FTC restitution pools.
- 'other': Ineligible or non-statutory.

Post Text:
"{text}"

Return strictly valid JSON matching this exact structure:
{{
    "is_viable": true/false,
    "vertical": "flight_disruption" | "isp_outage" | "security_deposit" | "class_action" | "other",
    "carrier_name": "Identified Carrier/ISP/Entity name or Unknown",
    "incident_identifier": "Flight number, Ticket ID, Account Ref, or null",
    "estimated_compensation": 650.00,
    "regulatory_framework": "e.g., UK261 / EU261 | US DOT 14 CFR Part 260 | State PUC Tariff Rule 21 | C.R.S. § 38-12-103",
    "ai_reasoning": "Clear statutory breakdown explaining why the respondent owes liquidated restitution.",
    "outreach_copy": "Consumer-facing outreach text under 220 characters informing them of the violation and statutory compensation amount."
}}
"""
    try:
        response = client.models.generate_content(
            model="gemini-3.6-flash",
            contents=prompt,
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                temperature=0.1,
            ),
        )
        return json.loads(response.text)
    except Exception as e:
        logger.warning(f"Primary model failure: {e}. Falling back to gemini-2.0-flash.")
        fallback = client.models.generate_content(
            model="gemini-2.0-flash",
            contents=prompt,
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                temperature=0.1,
            ),
        )
        return json.loads(fallback.text)

# --- Routes ---

@app.get("/health", status_code=status.HTTP_200_OK)
def health_check():
    return {"status": "healthy", "service": "dispute-api"}

@app.post("/api/v1/leads/evaluate", status_code=status.HTTP_201_CREATED)
def intake_and_evaluate(payload: RawSignalPayload, conn=Depends(get_db)):
    eval_result = evaluate_multi_vertical_signal(payload.raw_post_text)
    
    if not eval_result.get("is_viable", False):
        return {"status": "ignored", "reason": "No viable statutory or tariff violation detected."}

    query = """
    INSERT INTO leads (
        vertical,
        source_platform,
        platform_user_id,
        username,
        post_url,
        raw_post_text,
        carrier_name,
        incident_identifier,
        estimated_compensation,
        regulatory_framework,
        ai_reasoning,
        outreach_copy,
        status
    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'staged_for_review')
    RETURNING id::text, vertical, carrier_name, estimated_compensation, status;
    """
    params = (
        eval_result.get("vertical", "flight_disruption"),
        payload.source_platform,
        payload.platform_user_id,
        payload.username,
        payload.post_url,
        payload.raw_post_text,
        eval_result.get("carrier_name"),
        eval_result.get("incident_identifier"),
        eval_result.get("estimated_compensation", 0.00),
        eval_result.get("regulatory_framework"),
        eval_result.get("ai_reasoning"),
        eval_result.get("outreach_copy")
    )

    with conn.cursor() as cur:
        cur.execute(query, params)
        inserted_lead = cur.fetchone()
        conn.commit()

    return {"status": "staged", "lead": inserted_lead}

@app.get("/api/v1/leads")
def list_leads(status: Optional[str] = Query(None), conn=Depends(get_db)):
    query = """
    SELECT 
        id::text AS id,
        vertical,
        source_platform,
        username,
        carrier_name,
        incident_identifier,
        estimated_compensation,
        recovery_amount,
        fee_collected,
        regulatory_framework,
        ai_reasoning,
        outreach_copy,
        status,
        created_at
    FROM leads
    """
    params = []
    if status:
        query += " WHERE status = %s"
        params.append(status)
    query += " ORDER BY created_at DESC LIMIT 100;"

    with conn.cursor() as cur:
        cur.execute(query, tuple(params))
        leads = cur.fetchall()
    return leads

@app.get("/api/v1/claims/track/{lead_id}")
def get_claim_tracking(lead_id: str, conn=Depends(get_db)):
    with conn.cursor() as cur:
        cur.execute("""
            SELECT 
                id::text AS id,
                vertical,
                carrier_name,
                incident_identifier,
                account_number,
                estimated_compensation,
                recovery_amount,
                fee_collected,
                regulatory_framework,
                status,
                created_at,
                updated_at
            FROM leads
            WHERE id::text = %s;
        """, (lead_id,))
        claim = cur.fetchone()

    if not claim:
        raise HTTPException(status_code=404, detail="Dispute claim not found.")
    return claim

@app.post("/api/v1/claims/submit")
def submit_claim(payload: ClaimSubmissionPayload, background_tasks: BackgroundTasks, conn=Depends(get_db)):
    query = """
    UPDATE leads
    SET claimant_name = %s,
        claimant_email = %s,
        claimant_phone = %s,
        claimant_address = %s,
        pnr = %s,
        account_number = %s,
        incident_date = %s,
        digital_signature = %s,
        status = 'opted_in',
        updated_at = NOW()
    WHERE id::text = %s
    RETURNING id::text, status, claimant_name, claimant_email, claimant_phone;
    """
    with conn.cursor() as cur:
        cur.execute(query, (
            payload.claimant_name,
            payload.claimant_email,
            payload.claimant_phone,
            payload.claimant_address,
            payload.pnr,
            payload.account_number,
            payload.incident_date,
            payload.digital_signature,
            payload.lead_id
        ))
        updated = cur.fetchone()
        conn.commit()

    if not updated:
        raise HTTPException(status_code=404, detail="Lead ID not found.")

    if payload.claimant_phone:
        background_tasks.add_task(notify_claim_event, payload.lead_id, "opt_in_confirmation")

    background_tasks.add_task(trigger_carrier_demand_pipeline, payload.lead_id)

    return {
        "status": "opted_in",
        "claim": updated,
        "actions_dispatched": ["sms_opt_in_confirmation", "carrier_demand_dispatch"]
    }

@app.post("/api/v1/claims/settle")
def settle_claim(payload: SettlementPayload, background_tasks: BackgroundTasks, conn=Depends(get_db)):
    fee = (payload.recovery_amount * Decimal("0.25")).quantize(Decimal("0.01"))
    query = """
    UPDATE leads
    SET status = 'settled',
        recovery_amount = %s,
        fee_collected = %s,
        updated_at = NOW()
    WHERE id::text = %s
    RETURNING id::text, status, recovery_amount, fee_collected, claimant_phone;
    """
    with conn.cursor() as cur:
        cur.execute(query, (payload.recovery_amount, fee, payload.lead_id))
        settled = cur.fetchone()
        conn.commit()

    if not settled:
        raise HTTPException(status_code=404, detail="Lead ID not found.")

    background_tasks.add_task(notify_claim_event, payload.lead_id, "settlement_alert")

    return {
        "status": "settled",
        "claim": settled,
        "actions_dispatched": ["sms_settlement_alert"]
    }

@app.post("/api/v1/webhooks/carrier/inbound", status_code=status.HTTP_200_OK)
def inbound_carrier_response(payload: CarrierWebhookPayload, background_tasks: BackgroundTasks, conn=Depends(get_db)):
    matched_lead_id = None
    
    with conn.cursor() as cur:
        if payload.claim_id:
            cur.execute("SELECT id::text, status FROM leads WHERE id::text = %s", (str(payload.claim_id),))
            lead = cur.fetchone()
            if lead:
                matched_lead_id = lead["id"]
        elif payload.incident_identifier:
            cur.execute(
                "SELECT id::text, status FROM leads WHERE incident_identifier = %s AND carrier_name ILIKE %s ORDER BY created_at DESC LIMIT 1",
                (payload.incident_identifier, f"%{payload.carrier_name}%")
            )
            lead = cur.fetchone()
            if lead:
                matched_lead_id = lead["id"]

        audit_query = """
        INSERT INTO carrier_inbound_events (
            lead_id, carrier_name, vertical, event_type, settlement_amount, parsed_notes, raw_payload
        ) VALUES (%s, %s, %s, %s, %s, %s, %s);
        """
        cur.execute(audit_query, (
            matched_lead_id,
            payload.carrier_name,
            payload.vertical,
            payload.decision,
            float(payload.payout_offered),
            payload.resolution_notes,
            json.dumps(payload.raw_metadata)
        ))

        if matched_lead_id:
            if payload.decision == "approved" and payload.payout_offered > Decimal("0.00"):
                recovery = payload.payout_offered
                contingency_fee = (recovery * Decimal("0.25")).quantize(Decimal("0.01"))
                
                update_query = """
                UPDATE leads
                SET status = 'settled',
                    recovery_amount = %s,
                    fee_collected = %s,
                    updated_at = NOW()
                WHERE id::text = %s;
                """
                cur.execute(update_query, (recovery, contingency_fee, matched_lead_id))
                background_tasks.add_task(notify_claim_event, matched_lead_id, "settlement_alert")

            elif payload.decision == "rejected":
                cur.execute(
                    "UPDATE leads SET status = 'rejected', updated_at = NOW() WHERE id::text = %s;",
                    (matched_lead_id,)
                )
        conn.commit()

    return {
        "status": "processed",
        "lead_matched": matched_lead_id is not None,
        "lead_id": matched_lead_id,
        "recorded_decision": payload.decision
    }
