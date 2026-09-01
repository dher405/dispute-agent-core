import os
import json
import threading
from datetime import datetime
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
import psycopg2
from psycopg2.extras import RealDictCursor

# Dual SDK fallback strategy
try:
    from google import genai
    from google.genai import types
    USE_NEW_SDK = True
except ImportError:
    import google.generativeai as legacy_genai
    USE_NEW_SDK = False

try:
    from carrier_dispatcher import dispatch_demand_letter_email
except Exception as e:
    print(f"[IMPORT WARNING] carrier_dispatcher disabled: {e}", flush=True)
    dispatch_demand_letter_email = None

try:
    from sms_dispatcher import send_claim_confirmation_sms
except Exception as e:
    print(f"[IMPORT WARNING] sms_dispatcher disabled: {e}", flush=True)
    send_claim_confirmation_sms = None

app = FastAPI(title="Dispute Agent Core Engine", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://dispute_admin:yrygJVS7bLFhQNWk9DxurqJrZEXWx6oi@dpg-daaqt50ae00c73f1voig-a.oregon-postgres.render.com/dispute_db_f372")

if USE_NEW_SDK and GEMINI_API_KEY:
    ai_client = genai.Client(api_key=GEMINI_API_KEY)
elif not USE_NEW_SDK and GEMINI_API_KEY:
    legacy_genai.configure(api_key=GEMINI_API_KEY)
    ai_client = legacy_genai.GenerativeModel("gemini-1.5-flash")
else:
    ai_client = None

def get_db_connection():
    try:
        return psycopg2.connect(DATABASE_URL)
    except Exception as e:
        print(f"[DB ERROR] Connection failed: {e}", flush=True)
        return None

@app.on_event("startup")
def startup_event():
    try:
        import reddit_scraper
        threading.Thread(target=reddit_scraper.poll_reddit_rss, daemon=True).start()
        print("[POLISHER] Background Reddit poller thread started.", flush=True)
    except Exception as err:
        print(f"[STARTUP ERROR] Scraper start failure: {err}", flush=True)

@app.get("/")
def health_check():
    return {
        "status": "online",
        "timestamp": str(datetime.utcnow()),
        "sdk": "google-genai" if USE_NEW_SDK else "google-generativeai"
    }

@app.get("/api/v1/leads")
def get_leads(status: str = "staged_for_review"):
    conn = get_db_connection()
    if not conn:
        raise HTTPException(status_code=500, detail="Database connection failed")
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
    username = payload.get("username", "anonymous")
    post_url = payload.get("post_url", "")
    platform = payload.get("source_platform", "reddit")

    if not ai_client:
        return {"status": "ignored", "reason": "Gemini API key not configured"}

    prompt = f"""
    Evaluate this passenger flight disruption post for statutory cash compensation eligibility:
    Post: "{post_text}"

    Rules:
    1. UK261 / EU261: Flights departing UK/EU or operated by UK/EU airlines with delay >= 3 hours (mechanical/operational reasons, not weather). Value: £520 / €600 (~$650).
    2. US DOT 14 CFR Part 260: Significant delays (>3h domestic, >6h intl) or cancellations where passenger refused alternate travel.
    3. Montreal Convention: Delayed/damaged/lost baggage expenses.

    Respond ONLY with JSON matching:
    {{
      "eligible": true,
      "carrier": "Carrier Name",
      "flight_number": "e.g. UA 949",
      "estimated_compensation": 660.00,
      "statutory_basis": "UK261 / EU261 or 14 CFR Part 260",
      "reasoning": "Clear explanation",
      "outreach_copy": "Statutory demand outreach copy"
    }}
    """

    eval_data = None
    # Try models in order of availability
    for model_name in ["gemini-2.5-flash", "gemini-1.5-flash"]:
        try:
            if USE_NEW_SDK:
                response = ai_client.models.generate_content(
                    model=model_name,
                    contents=prompt,
                    config=types.GenerateContentConfig(response_mime_type="application/json")
                )
                eval_data = json.loads(response.text)
            else:
                legacy_model = legacy_genai.GenerativeModel(model_name)
                response = legacy_model.generate_content(prompt)
                raw_txt = response.text.replace("```json", "").replace("```", "").strip()
                eval_data = json.loads(raw_txt)
            if eval_data:
                break
        except Exception as err:
            print(f"[MODEL RETRY] {model_name} failed: {err}", flush=True)
            continue

    if not eval_data:
        return {"status": "ignored", "reason": "Evaluation model unreachable"}

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
                source_platform, username, post_url, raw_post_text,
                carrier_name, incident_identifier, estimated_compensation,
                regulatory_framework, ai_reasoning, outreach_copy, status, created_at, updated_at
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'staged_for_review', NOW(), NOW())
            RETURNING id::text AS lead_id
            """,
            (
                platform, username, post_url, post_text,
                eval_data.get("carrier", "Carrier"),
                eval_data.get("flight_number", "Flight"),
                float(eval_data.get("estimated_compensation", 650.0)),
                eval_data.get("statutory_basis", "UK261 / EU261"),
                eval_data.get("reasoning", ""),
                eval_data.get("outreach_copy", "")
            )
        )
        inserted = cur.fetchone()
        conn.commit()
        cur.close()
        conn.close()

        lead_id = inserted["lead_id"]
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
                   incident_identifier, estimated_compensation, recovery_amount, 
                   regulatory_framework, claimant_name, updated_at, created_at
            FROM leads
            WHERE id::text = %s
            LIMIT 1
            """,
            (lead_id,)
        )
        row = cur.fetchone()
        cur.close()
        conn.close()

        if not row:
            raise HTTPException(status_code=404, detail="Claim record not found")

        amount = float(row.get("recovery_amount") or row.get("estimated_compensation") or 0.0)
        return {
            "lead_id": row.get("lead_id"),
            "status": row.get("status", "pending"),
            "carrier": row.get("carrier_name") or "Airline Carrier",
            "flight": row.get("incident_identifier") or "Disrupted Flight",
            "amount": amount,
            "statute": row.get("regulatory_framework") or "Air Passenger Rights",
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
            WHERE id::text = %s
            RETURNING *, id::text AS lead_id
            """,
            (full_name, email, phone, address, pnr, flight_date, signature, lead_id)
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
            "carrier_name": updated_lead.get("carrier_name") or "Carrier",
            "incident_identifier": updated_lead.get("incident_identifier") or "Flight",
            "governing_statute": updated_lead.get("regulatory_framework") or "UK261 / EU261",
            "claimed_amount": float(updated_lead.get("estimated_compensation") or 650.0),
            "passenger_address": address,
            "booking_reference": pnr,
            "incident_date": flight_date,
            "incident_narrative": updated_lead.get("raw_post_text") or "",
            "digital_signature": signature
        }
        
        if dispatch_demand_letter_email:
            try:
                threading.Thread(target=dispatch_demand_letter_email, args=(claim_payload,), daemon=True).start()
            except Exception as e:
                print(f"[DISPATCH ERROR] {e}", flush=True)

        if phone and send_claim_confirmation_sms:
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

    if not lead_id:
        raise HTTPException(status_code=400, detail="Missing lead_id")

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
            WHERE id::text = %s
            RETURNING id::text AS lead_id, claimant_name
            """,
            (settled_amount, contingency_fee, lead_id)
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
        print(f"[SETTLE ERROR] {e}", flush=True)
        raise HTTPException(status_code=500, detail=str(e))
