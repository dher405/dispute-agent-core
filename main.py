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

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

DATABASE_URL = os.getenv("DATABASE_URL")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

app = FastAPI(
    title="Dispute Agent Core Engine",
    description="Multi-vertical statutory dispute evaluation, carrier webhook reconciliation, and claim orchestration.",
    version="2.0.0"
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
    platform_user_id: Optional[str] = Field(None, example="u_tech_user_44")
    username: Optional[str] = Field(None, example="user_frontrange")
    post_url: Optional[str] = Field(None, example="https://reddit.com/r/comcast/comments/123")
    raw_post_text: str = Field(..., example="My Xfinity fiber in Denver has been completely down for 36 hours. Support is useless.")

class CarrierWebhookPayload(BaseModel):
    carrier_name: str = Field(..., example="Xfinity / Comcast")
    vertical: str = Field(..., example="isp_outage")
    claim_id: Optional[str] = Field(None, example="6ba7b810-9dad-11d1-80b4-00c04fd430c8")
    incident_identifier: Optional[str] = Field(None, example="TKT-DEN-994812")
    decision: str = Field(..., example="approved")
    payout_offered: Decimal = Field(default=Decimal("0.00"), example=65.00)
    resolution_notes: Optional[str] = Field(None, example="Automatic credit approved per State SLA tariff for >24hr continuous outage.")
    raw_metadata: Dict[str, Any] = Field(default_factory=dict)

class ClaimSubmissionPayload(BaseModel):
    lead_id: str = Field(..., example="6ba7b810-9dad-11d1-80b4-00c04fd430c8")
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
    recovery_amount: Decimal = Field(..., example=600.00)

# --- AI Evaluation Gateway ---

def evaluate_multi_vertical_signal(text: str) -> Dict[str, Any]:
    client = genai.Client(api_key=GEMINI_API_KEY)
    prompt = f"""
Analyze the following consumer post for statutory compensation, bill credits, or regulatory refund eligibility.
Supported Verticals:
1. 'flight_disruption': Governed by EU261/UK261 (€250-€600), US DOT 14 CFR Part 260 (full refund for >3hr domestic or >6hr international delay/cancellation), Montreal Convention.
2. 'isp_outage': Regional utility/telecom tariffs and state SLA mandates (e.g., Comcast/AT&T/Charter prorated bill credits + statutory disruption offsets for >4hr continuous outages).
3. 'security_deposit': Statutory landlord penalties (2x to 3x deposit) for failure to return/itemize deposits within state legal windows (e.g., 30-60 days).
4. 'class_action': Active settlement funds or FTC restitution pools.
5. 'other': Ineligible or unsupported.

Text:
"{text}"

Return JSON matching this exact structure:
{{
    "is_viable": true/false,
    "vertical": "flight_disruption" | "isp_outage" | "security_deposit" | "class_action" | "other",
    "carrier_name": "Identified Carrier/ISP/Entity name or Unknown",
    "incident_identifier": "Flight number, ISP Ticket ID, Account Ref, or null",
    "estimated_compensation": 0.00,
    "regulatory_framework": "e.g., US DOT 14 CFR Part 260 | State PUC Tariff Rule 21 | UK261/EU261 | CRS 38-12-103",
    "ai_reasoning": "Clear statutory breakdown of why this compensation is legally owed.",
    "outreach_copy": "Empathetic, actionable outreach fragment (under 250 chars) directing them to claim."
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

# --- Endpoints ---

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
def submit_claim(payload: ClaimSubmissionPayload, conn=Depends(get_db)):
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
    RETURNING id::text, status, claimant_name, claimant_email;
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
    return {"status": "opted_in", "claim": updated}

@app.post("/api/v1/claims/settle")
def settle_claim(payload: SettlementPayload, conn=Depends(get_db)):
    fee = (payload.recovery_amount * Decimal("0.25")).quantize(Decimal("0.01"))
    query = """
    UPDATE leads
    SET status = 'settled',
        recovery_amount = %s,
        fee_collected = %s,
        updated_at = NOW()
    WHERE id::text = %s
    RETURNING id::text, status, recovery_amount, fee_collected;
    """
    with conn.cursor() as cur:
        cur.execute(query, (payload.recovery_amount, fee, payload.lead_id))
        settled = cur.fetchone()
        conn.commit()

    if not settled:
        raise HTTPException(status_code=404, detail="Lead ID not found.")
    return {"status": "settled", "claim": settled}

@app.post("/api/v1/webhooks/carrier/inbound", status_code=status.HTTP_200_OK)
def inbound_carrier_response(payload: CarrierWebhookPayload, conn=Depends(get_db)):
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
