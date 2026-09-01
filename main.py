import os
import threading
from datetime import datetime
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
import psycopg2
from psycopg2.extras import RealDictCursor
try:
    from google import genai
    from google.genai import types
    USE_MODERN_SDK = True
except ImportError:
    import google.generativeai as legacy_genai
    USE_MODERN_SDK = False

from carrier_dispatcher import dispatch_demand_letter_email
from sms_dispatcher import send_claim_confirmation_sms

app = FastAPI(title="Dispute Agent Core Engine", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
ai_client = genai.Client(api_key=GEMINI_API_KEY) if GEMINI_API_KEY else None
DATABASE_URL = os.getenv("DATABASE_URL", "")

def get_db_connection():
    if not DATABASE_URL:
        return None
    try:
        return psycopg2.connect(DATABASE_URL)
    except Exception as e:
        print(f"[DB ERROR] Connection failed: {e}", flush=True)
        return None

@app.on_event("startup")
def startup_event():
    import reddit_scraper
    threading.Thread(target=reddit_scraper.poll_reddit_rss, daemon=True).start()
    print("[POLISHER] Background Reddit poller thread started.", flush=True)

@app.get("/")
def health_check():
    return {"status": "online", "timestamp": str(datetime.utcnow())}

@app.get("/api/v1/leads")
def get_leads(status: str = "staged_for_review"):
    conn = get_db_connection()
    if not conn:
        raise HTTPException(status_code=500, detail="Database connection unavailable")
    try:
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute(
            """
            SELECT id::text AS lead_id, *
            FROM leads
            WHERE status = %s
            ORDER BY created_at DESC
            """,
            (status,)
        )
        leads = cur.fetchall()
        cur.close()
        conn.close()
        return leads
    except Exception as e:
        if conn:
            conn.close()
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/v1/leads/evaluate")
def evaluate_lead(payload: dict):
    post_text = payload.get("post_text", "")
    user_id = payload.get("user_id", "")
    username = payload.get("username", "")
    post_url = payload.get("post_url", "")
    platform = payload.get("source_platform", "reddit")

    if not ai_client:
        return {"status": "ignored", "reason": "Gemini API key not configured"}

    prompt = f"""
    Analyze this air travel post to determine if the passenger has a valid statutory cash claim:
    Post: "{post_text}"

    Rules:
    - UK261/EU261: Flights departing EU/UK or operated by EU/UK airlines delayed >3 hours (mechanical/operational, not weather). Statutory value is £520 / €600 (~$650).
    - US DOT 14 CFR Part 260: Significant delays/cancellations where carrier refused refund or duty of care.
    - Montreal Convention: Baggage loss or documented consequential expenses.

    Respond ONLY with JSON matching:
    {{
      "eligible": true/false,
      "carrier": "Airline Name",
      "flight_number": "e.g. UA 949",
      "estimated_compensation": 650.00,
      "statutory_basis": "UK261 / EU261 or 14 CFR Part 260",
      "reasoning": "Brief explanation of statutory eligibility",
      "outreach_copy": "Empathetic comment explaining passenger statutory right to compensation"
    }}
    """

    try:
        response = ai_client.models.generate_content(
            model="gemini-2.5-flash",
            contents=prompt,
            config=types.GenerateContentConfig(
                response_mime_type="application/json"
            )
        )
        import json
        eval_data = json.loads(response.text)
    except Exception as err:
        print(f"[EVAL ERROR] Gemini evaluation failed: {err}", flush=True)
        return {"status": "ignored", "reason": "Evaluation parsing error"}

    if not eval_data.get("eligible"):
        return {"status": "ignored", "reason": "Not eligible"}

    conn = get_db_connection()
    if not conn:
        return {"status": "ignored", "reason": "Database connection failed"}

    try:
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute(
            """
            INSERT INTO leads (
                source_platform, username, user_id, post_url, raw_post_text,
                carrier_name, incident_identifier, estimated_compensation,
                regulatory_framework, ai_reasoning, outreach_copy, status, created_at, updated_at
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'staged_for_review', NOW(), NOW())
            ON CONFLICT (user_id) DO UPDATE
            SET status = 'staged_for_review', updated_at = NOW()
            RETURNING id::text AS lead_id
            """,
            (
                platform, username, user_id, post_url, post_text,
                eval_data.get("carrier"), eval_data.get("flight_number"),
                eval_data.get("estimated_compensation", 650.0),
                eval_data.get("statutory_basis"), eval_data.get("reasoning"),
                eval_data.get("outreach_copy")
            )
        )
        inserted = cur.fetchone()
        conn.commit()
        cur.close()
        conn.close()

        lead_id = inserted["lead_id"] if inserted else user_id
        return {
            "status": "staged",
            "lead_id": lead_id,
            "recovery_amount": float(eval_data.get("estimated_compensation", 650.0))
        }
    except Exception as e:
        if conn:
            conn.close()
        print(f"[DB INSERT ERROR] {e}", flush=True)
        return {"status": "ignored", "reason": str(e)}

@app.get("/api/v1/claims/track/{lead_id}")
def get_claim_tracking_status(lead_id: str):
    conn = get_db_connection()
    if not conn:
        raise HTTPException(status_code=500, detail="Database connection failed")
    try:
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute(
            """
            SELECT id::text AS lead_id, status, source_platform, carrier_name, 
                   incident_identifier, estimated_compensation, recovery_amount, regulatory_framework,
                   claimant_name, updated_at, created_at
            FROM leads
            WHERE id::text = %s OR user_id = %s
            LIMIT 1
            """,
            (lead_id, lead_id)
        )
        row = cur.fetchone()
        cur.close()
        conn.close()

        if not row:
            raise HTTPException(status_code=404, detail="Claim record not found")

        amount = float(row.get("recovery_amount") or row.get("estimated_compensation") or 0.0)
        return {
            "lead_id": row.get("lead_id"),
            "status": row.get("status"),
            "carrier": row.get("carrier_name") or "Airline Carrier",
            "flight": row.get("incident_identifier") or "Disrupted Flight",
            "amount": amount,
            "statute": row.get("regulatory_framework") or "14 CFR Part 260 / Montreal Convention",
            "name": row.get("claimant_name") or "Authorized Passenger",
            "last_updated": str(row.get("updated_at") or row.get("created_at"))
        }
    except Exception as e:
        if conn:
            conn.close()
        print(f"[TRACK ERROR] {e}", flush=True)
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/v1/claims/submit")
def submit_authorized_claim(req: dict):
    lead_id = req.get("lead_id")
    full_name = req.get("full_name")
    signature = req.get("digital_signature")
    email = req.get("email")
    phone = req.get("phone")
    address = req.get("address")
    pnr = req.get("pnr")
    flight_date = req.get("flight_date")

    if not lead_id or not full_name or not signature:
        raise HTTPException(status_code=400, detail="Missing required claim authorization fields")

    conn = get_db_connection()
    if not conn:
        raise HTTPException(status_code=500, detail="Database connection failed")

    try:
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute(
            """
            UPDATE leads
            SET status = 'opted_in',
                claimant_name = %s,
                claimant_email = %s,
                claimant_phone = %s,
                claimant_address = %s,
                pnr = %s,
                incident_date = %s,
                digital_signature = %s,
                updated_at = NOW()
            WHERE id::text = %s OR user_id = %s
            RETURNING *, id::text AS pk_id
            """,
            (full_name, email, phone, address, pnr, flight_date, signature, lead_id, lead_id)
        )
        updated_lead = cur.fetchone()
        conn.commit()
        cur.close()
        conn.close()

        if not updated_lead:
            raise HTTPException(status_code=404, detail="Lead ID not found in database")

        claim_payload = {
            "lead_id": lead_id,
            "full_name": full_name,
            "carrier_name": updated_lead.get("carrier_name", "Carrier"),
            "incident_identifier": updated_lead.get("incident_identifier", "Flight"),
            "governing_statute": updated_lead.get("regulatory_framework", "UK261 / EU261"),
            "claimed_amount": float(updated_lead.get("estimated_compensation", 650.0)),
            "passenger_address": address,
            "booking_reference": pnr,
            "incident_date": flight_date,
            "incident_narrative": updated_lead.get("raw_post_text", ""),
            "digital_signature": signature
        }
        
        try:
            threading.Thread(target=dispatch_demand_letter_email, args=(claim_payload,), daemon=True).start()
        except Exception as e:
            print(f"[DISPATCH ERROR] {e}", flush=True)

        if phone:
            try:
                threading.Thread(
                    target=send_claim_confirmation_sms,
                    args=(phone, full_name, claim_payload["incident_identifier"], lead_id, claim_payload["claimed_amount"]),
                    daemon=True
                ).start()
            except Exception as e:
                print(f"[SMS ERROR] {e}", flush=True)

        return {
            "status": "success",
            "message": "Claim authorized and demand letter queued",
            "lead_id": lead_id,
            "tracking_url": f"https://dispute-admin.onrender.com/?claim_id={lead_id}"
        }
    except Exception as e:
        if conn:
            conn.close()
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/v1/claims/settle")
def settle_claim(payload: dict):
    lead_id = payload.get("lead_id")
    settled_amount = float(payload.get("settled_amount", 0.0))
    payout_ref = payload.get("payout_reference", "DIRECT_DEPOSIT")
    
    conn = get_db_connection()
    if not conn:
        raise HTTPException(status_code=500, detail="Database connection failed")
    try:
        cur = conn.cursor(cursor_factory=RealDictCursor)
        contingency_fee = round(settled_amount * 0.25, 2)
        claimant_payout = round(settled_amount - contingency_fee, 2)

        cur.execute(
            """
            UPDATE leads
            SET status = 'settled',
                recovery_amount = %s,
                fee_collected = %s,
                updated_at = NOW()
            WHERE id::text = %s OR user_id = %s
            RETURNING id::text AS lead_id, claimant_name, carrier_name
            """,
            (settled_amount, contingency_fee, lead_id, lead_id)
        )
        updated = cur.fetchone()
        conn.commit()
        cur.close()
        conn.close()

        if not updated:
            raise HTTPException(status_code=404, detail="Lead ID not found")

        return {
            "status": "settled",
            "lead_id": lead_id,
            "total_recovery": settled_amount,
            "contingency_fee_collected": contingency_fee,
            "net_claimant_disbursement": claimant_payout,
            "payout_reference": payout_ref
        }
    except Exception as e:
        if conn:
            conn.close()
        raise HTTPException(status_code=500, detail=str(e))
