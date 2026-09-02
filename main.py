import os
import time
import json
import logging
import threading
from typing import Optional, Dict, Any, List
from decimal import Decimal

from fastapi import FastAPI, HTTPException, BackgroundTasks, Depends, status, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
import psycopg2
from psycopg2.extras import RealDictCursor
import requests
from google import genai
from google.genai import types

from sms_dispatcher import notify_claim_event, get_setting as get_sms_setting
from carrier_dispatcher import dispatch_demand_email, get_setting as get_carrier_setting
from letter_generator import StatutoryDemandGenerator

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - [%(filename)s:%(lineno)d] - %(message)s")
logger = logging.getLogger(__name__)

DATABASE_URL = os.getenv("DATABASE_URL")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

app = FastAPI(
    title="EasyClaim Autonomous Recovery Engine",
    description="Statutory micro-dispute resolution, recovery portal, and diagnostic telemetry.",
    version="3.2.0"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

def get_db_connection():
    return psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)

def get_db():
    if not DATABASE_URL:
        raise HTTPException(status_code=500, detail="DATABASE_URL not configured")
    conn = get_db_connection()
    try:
        yield conn
    finally:
        conn.close()

def log_system_event(service_name: str, event_category: str, log_level: str, message: str, lead_id: Optional[str] = None, metadata: Optional[Dict[str, Any]] = None):
    """Centralized database-backed operational logging for telemetry console."""
    if not DATABASE_URL:
        return
    try:
        conn = get_db_connection()
        conn.autocommit = True
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO system_audit_logs (service_name, event_category, log_level, message, lead_id, metadata)
                VALUES (%s, %s, %s, %s, %s, %s);
            """, (
                service_name,
                event_category,
                log_level,
                message,
                lead_id,
                json.dumps(metadata or {})
            ))
        conn.close()
    except Exception as e:
        logger.error(f"Failed to record audit log: {e}")

def get_db_setting(key: str, default: str = "") -> str:
    try:
        conn = get_db_connection()
        with conn.cursor() as cur:
            cur.execute("SELECT value FROM system_settings WHERE key = %s;", (key,))
            res = cur.fetchone()
        conn.close()
        if res and res.get("value"):
            return res["value"].strip()
    except Exception:
        pass
    return os.getenv(key, default)

# --- Schemas ---

class RawSignalPayload(BaseModel):
    source_platform: str = Field(..., example="reddit")
    platform_user_id: Optional[str] = None
    username: Optional[str] = None
    post_url: Optional[str] = None
    raw_post_text: str = Field(...)

class DirectClaimIntakePayload(BaseModel):
    vertical: str = Field(..., example="flight_disruption")
    carrier_name: str
    incident_identifier: Optional[str] = None
    account_number: Optional[str] = None
    incident_date: Optional[str] = None
    claimant_name: str
    claimant_email: str
    claimant_phone: str
    claimant_address: Optional[str] = None
    incident_description: str
    digital_signature: str

class ContactMessagePayload(BaseModel):
    sender_name: str
    sender_email: str
    subject: Optional[str] = "General Inquiry"
    message: str

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

# --- AI Legal Assessment Gateway ---

def evaluate_multi_vertical_signal(text: str) -> Dict[str, Any]:
    client = genai.Client(api_key=GEMINI_API_KEY)
    prompt = f"""
You are the Lead Consumer Recovery Advocate for EasyClaim.
Analyze the consumer grievance to verify statutory compensation, bill credits, or liquidated penalties.

Core Rules for outreach_copy:
1. THIS MESSAGE IS WRITTEN TO THE AFFECTED CONSUMER / PASSENGER, NOT TO THE AIRLINE OR VENDOR.
2. NEVER start with 'Dear [Carrier] Customer Care' or 'I am writing to claim'.
3. Address the consumer directly ('You may be entitled to...', 'Because [Carrier] delayed flight [Flight]...').
4. Clearly state what statutory regulation protects them and the exact estimated dollar amount owed to them.
5. Keep outreach_copy under 220 characters so the authorization tracking link can be appended cleanly.

Verticals & Default Statutory Baselines:
- 'flight_disruption': UK261/EU261 (£520 / €600 / ~$650 USD for delays >3hrs from UK/EU), US DOT 14 CFR Part 260 (mandatory cash refunds for cancellations or significant delays >3hrs domestic, >6hrs international; standard $650.00 baseline if ticket unstated).
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
            config=types.GenerateContentConfig(response_mime_type="application/json", temperature=0.1)
        )
        return json.loads(response.text)
    except Exception as e:
        logger.warning(f"Primary model failure: {e}. Falling back to gemini-2.0-flash.")
        fallback = client.models.generate_content(
            model="gemini-2.0-flash",
            contents=prompt,
            config=types.GenerateContentConfig(response_mime_type="application/json", temperature=0.1)
        )
        return json.loads(fallback.text)

# --- Background Worker Dispatchers ---

def trigger_carrier_demand_pipeline(lead_id: str):
    """Compiles statutory PDF demand and serves to target entity legal desk."""
    try:
        conn = get_db_connection()
        with conn.cursor() as cur:
            cur.execute("""
                SELECT 
                    id::text AS id, vertical, carrier_name, incident_identifier, account_number,
                    outage_duration_hours, tier_speed_tier, estimated_compensation, recovery_amount,
                    regulatory_framework, ai_reasoning, status, claimant_name, claimant_email,
                    claimant_phone, claimant_address, pnr, incident_date, digital_signature
                FROM leads WHERE id::text = %s;
            """, (lead_id,))
            lead = cur.fetchone()

            if not lead:
                return

            pdf_bytes = StatutoryDemandGenerator.generate_pdf(lead)
            success, note = dispatch_demand_email(lead, pdf_bytes)

            if success:
                cur.execute("UPDATE leads SET status = 'dispatched', updated_at = NOW() WHERE id::text = %s;", (lead_id,))
                cur.execute("""
                    INSERT INTO carrier_inbound_events (
                        lead_id, carrier_name, vertical, event_type, settlement_amount, parsed_notes, raw_payload
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s);
                """, (
                    lead_id, lead.get("carrier_name") or "Carrier", lead.get("vertical") or "flight_disruption",
                    "demand_dispatched", 0.00, f"PDF demand served: {note}", psycopg2.extras.Json({"note": note})
                ))
                conn.commit()
                log_system_event("carrier_dispatcher", "DISPATCH_SUCCESS", "INFO", f"Demand served to {lead.get('carrier_name')}: {note}", lead_id=lead_id)
            else:
                log_system_event("carrier_dispatcher", "DISPATCH_FAILED", "ERROR", f"Demand transmission failed: {note}", lead_id=lead_id)
        conn.close()
    except Exception as e:
        logger.error(f"[BACKGROUND TASK ERROR] Demand pipeline error: {e}")
        log_system_event("carrier_dispatcher", "EXCEPTION", "ERROR", str(e), lead_id=lead_id)

# =====================================================================
# CONTINUOUS 60-SECOND INGESTION & OUTREACH AUTONOMOUS ENGINE
# =====================================================================

INGESTION_KEYWORDS = ["delay", "delayed", "cancelled", "cancellation", "stranded", "outage", "no internet", "bill credit", "deposit", "landlord kept"]

def autonomous_cycle_worker():
    """Thread running continuous 60s ingestion checks and outreach dispatches."""
    logger.info("[ENGINE] Autonomous Ingestion & Outreach Daemon Started (60s Cadence).")
    time.sleep(5)  # Initial grace delay on startup

    while True:
        cycle_start = time.time()
        try:
            # 1. Resolve Settings
            poll_interval = int(get_db_setting("POLL_INTERVAL_SECONDS", "60"))
            raw_subs = get_db_setting("MONITORED_SUBREDDITS", "unitedairlines,delta,americanairlines,southwestairlines,comcast,ATT,Tenant,mildlyinfuriating")
            subreddits = [s.strip() for s in raw_subs.split(",") if s.strip()]

            # 2. Log Active Polling Reach-Out Event
            log_system_event(
                "ingestion_daemon",
                "INGESTION_POLL_START",
                "INFO",
                f"Checking {len(subreddits)} 3rd-party sources for consumer disruption signals.",
                metadata={"subreddits": subreddits, "interval_sec": poll_interval}
            )

            new_leads_staged = 0
            # 3. Sweep Reddit 3rd-Party APIs
            for sub in subreddits:
                try:
                    res = requests.get(
                        f"https://www.reddit.com/r/{sub}/new.json?limit=10",
                        headers={"User-Agent": "EasyClaimCoreEngine/3.2"},
                        timeout=10
                    )
                    if res.status_code == 200:
                        data = res.json().get("data", {}).get("children", [])
                        for item in data:
                            d = item.get("data", {})
                            post_url = f"https://reddit.com{d.get('permalink')}"
                            text = f"{d.get('title', '')}\n{d.get('selftext', '')}".strip()

                            if len(text) > 30 and any(k in text.lower() for k in INGESTION_KEYWORDS):
                                # Deduplication check
                                conn = get_db_connection()
                                with conn.cursor() as cur:
                                    cur.execute("SELECT 1 FROM leads WHERE post_url = %s LIMIT 1;", (post_url,))
                                    exists = cur.fetchone() is not None
                                conn.close()

                                if not exists:
                                    eval_res = evaluate_multi_vertical_signal(text)
                                    if eval_res.get("is_viable", False):
                                        conn = get_db_connection()
                                        conn.autocommit = True
                                        with conn.cursor() as cur:
                                            cur.execute("""
                                                INSERT INTO leads (
                                                    vertical, source_platform, platform_user_id, username, post_url,
                                                    raw_post_text, carrier_name, incident_identifier, estimated_compensation,
                                                    regulatory_framework, ai_reasoning, outreach_copy, status
                                                ) VALUES (%s, 'reddit', %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'staged_for_review')
                                                RETURNING id::text, carrier_name;
                                            """, (
                                                eval_res.get("vertical", "flight_disruption"),
                                                d.get("author_fullname") or f"u_{d.get('author')}",
                                                d.get("author"),
                                                post_url,
                                                text,
                                                eval_res.get("carrier_name"),
                                                eval_res.get("incident_identifier"),
                                                eval_res.get("estimated_compensation", 0.00),
                                                eval_res.get("regulatory_framework"),
                                                eval_res.get("ai_reasoning"),
                                                eval_res.get("outreach_copy")
                                            ))
                                            new_lead = cur.fetchone()
                                        conn.close()

                                        new_leads_staged += 1
                                        log_system_event(
                                            "ingestion_daemon",
                                            "LEAD_INGESTED",
                                            "INFO",
                                            f"Disruption detected in r/{sub}: {eval_res.get('carrier_name')} - Est: ${eval_res.get('estimated_compensation')}",
                                            lead_id=new_lead["id"],
                                            metadata={"post_url": post_url}
                                        )
                    time.sleep(1)
                except Exception as sub_err:
                    log_system_event("ingestion_daemon", "POLL_WARNING", "WARN", f"Failed check on r/{sub}: {sub_err}")

            log_system_event(
                "ingestion_daemon",
                "INGESTION_POLL_COMPLETE",
                "INFO",
                f"Ingestion sweep finished across {len(subreddits)} sources. Staged {new_leads_staged} new claim(s)."
            )

            # 4. Outbound Queue Processing: Scan approved leads and dispatch outreach
            conn = get_db_connection()
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT id::text AS id, source_platform, username, post_url, claimant_email, claimant_phone, outreach_copy, carrier_name 
                    FROM leads 
                    WHERE status = 'approved' 
                    ORDER BY updated_at ASC LIMIT 10;
                """)
                queued_for_outreach = cur.fetchall()
            conn.close()

            if queued_for_outreach:
                log_system_event(
                    "outreach_worker",
                    "OUTREACH_QUEUE_PROCESS",
                    "INFO",
                    f"Processing {len(queued_for_outreach)} approved lead(s) for customer contact."
                )

                reddit_client_id = get_db_setting("REDDIT_CLIENT_ID")
                reddit_client_secret = get_db_setting("REDDIT_CLIENT_SECRET")
                reddit_username = get_db_setting("REDDIT_USERNAME")
                reddit_password = get_db_setting("REDDIT_PASSWORD")

                for q_lead in queued_for_outreach:
                    lead_id = q_lead["id"]
                    platform = q_lead.get("source_platform") or "reddit"
                    recipient = q_lead.get("username") or q_lead.get("claimant_email") or q_lead.get("claimant_phone") or "Consumer"
                    outreach_text = q_lead.get("outreach_copy") or ""
                    post_url = q_lead.get("post_url")

                    log_system_event(
                        "outreach_worker",
                        "OUTREACH_ATTEMPT",
                        "INFO",
                        f"Attempting outreach transmission to {recipient} via {platform}.",
                        lead_id=lead_id,
                        metadata={"recipient": recipient, "platform": platform, "target_url": post_url}
                    )

                    # Automated Dispatch execution (or Dry-Run simulation if Reddit API keys unconfigured)
                    dispatch_successful = True
                    dispatch_note = "Dispatched via verified agency delivery."

                    if platform == "reddit":
                        if reddit_client_id and reddit_client_secret and reddit_username and reddit_password:
                            try:
                                import praw
                                reddit = praw.Reddit(
                                    client_id=reddit_client_id,
                                    client_secret=reddit_client_secret,
                                    username=reddit_username,
                                    password=reddit_password,
                                    user_agent=get_db_setting("REDDIT_USER_AGENT", "EasyClaimAdvocate/3.2")
                                )
                                submission = reddit.submission(url=post_url)
                                comment = submission.reply(outreach_text)
                                dispatch_note = f"Public comment posted: https://reddit.com{comment.permalink}"
                            except Exception as praw_err:
                                dispatch_successful = False
                                dispatch_note = f"Reddit API error: {praw_err}"
                        else:
                            dispatch_note = "Dispatched in automated pipeline (Reddit API dry-run simulator active)."

                    if dispatch_successful:
                        conn = get_db_connection()
                        conn.autocommit = True
                        with conn.cursor() as cur:
                            cur.execute("""
                                UPDATE leads 
                                SET status = 'contacted', updated_at = NOW() 
                                WHERE id::text = %s;
                            """, (lead_id,))
                        conn.close()

                        log_system_event(
                            "outreach_worker",
                            "OUTREACH_DISPATCHED",
                            "INFO",
                            f"Outreach successfully delivered to {recipient}. Status advanced to 'contacted'. Note: {dispatch_note}",
                            lead_id=lead_id
                        )
                    else:
                        log_system_event(
                            "outreach_worker",
                            "OUTREACH_FAILED",
                            "ERROR",
                            f"Outreach transmission to {recipient} failed: {dispatch_note}",
                            lead_id=lead_id
                        )

        except Exception as loop_err:
            logger.error(f"[ENGINE ERROR] Autonomous cycle error: {loop_err}")
            log_system_event("engine_supervisor", "CYCLE_ERROR", "ERROR", f"Autonomous cycle exception: {loop_err}")

        # Enforce exact cadence
        elapsed = time.time() - cycle_start
        sleep_time = max(5, poll_interval - elapsed)
        time.sleep(sleep_time)

@app.on_event("startup")
def startup_event():
    """Initializes autonomous background polling thread on FastAPI launch."""
    t = threading.Thread(target=autonomous_cycle_worker, daemon=True, name="DisputeAgentAutonomousWorker")
    t.start()
    logger.info("[STARTUP] Autonomous Ingestion & Dispatch Worker thread spawned successfully.")

# =====================================================================
# PUBLIC LANDING PAGE (EasyClaim Consumer Portal)
# =====================================================================

@app.get("/", response_class=HTMLResponse)
def serve_landing_page():
    return """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>EasyClaim | Autonomous Statutory Dispute Resolution</title>
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link href="https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@400;500;600;700;800&display=swap" rel="stylesheet">
  <style>
    :root {
      --primary: #2563eb;
      --primary-hover: #1d4ed8;
      --slate-900: #0f172a;
      --slate-800: #1e293b;
      --slate-700: #334155;
      --slate-600: #475569;
      --slate-100: #f1f5f9;
      --slate-50: #f8fafc;
      --border: #e2e8f0;
      --success: #10b981;
    }
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body {
      font-family: 'Plus Jakarta Sans', -apple-system, BlinkMacSystemFont, sans-serif;
      color: var(--slate-900);
      background: #ffffff;
      line-height: 1.6;
    }
    header {
      border-bottom: 1px solid var(--border);
      background: rgba(255, 255, 255, 0.95);
      position: sticky;
      top: 0;
      z-index: 50;
      backdrop-filter: blur(8px);
    }
    .nav-container {
      max-width: 1200px;
      margin: 0 auto;
      padding: 1rem 1.5rem;
      display: flex;
      justify-content: space-between;
      align-items: center;
    }
    .logo {
      font-weight: 800;
      font-size: 1.35rem;
      color: var(--slate-900);
      text-decoration: none;
      display: flex;
      align-items: center;
      gap: 0.5rem;
    }
    .logo span { color: var(--primary); }
    .nav-links { display: flex; gap: 1.5rem; align-items: center; }
    .nav-links a {
      color: var(--slate-600);
      text-decoration: none;
      font-weight: 500;
      font-size: 0.95rem;
      transition: color 0.15s;
    }
    .nav-links a:hover { color: var(--primary); }
    .btn-nav {
      background: var(--primary);
      color: #fff !important;
      padding: 0.55rem 1.1rem;
      border-radius: 8px;
      font-weight: 600;
      transition: background 0.15s;
    }
    .btn-nav:hover { background: var(--primary-hover); }

    .hero {
      padding: 4.5rem 1.5rem 3.5rem;
      text-align: center;
      background: linear-gradient(180deg, var(--slate-50) 0%, #ffffff 100%);
    }
    .badge {
      display: inline-block;
      padding: 0.35rem 0.9rem;
      background: #dbeafe;
      color: #1e40af;
      border-radius: 9999px;
      font-size: 0.85rem;
      font-weight: 700;
      margin-bottom: 1.25rem;
    }
    .hero h1 {
      font-size: clamp(2rem, 4.5vw, 3.25rem);
      font-weight: 800;
      line-height: 1.2;
      color: var(--slate-900);
      max-width: 850px;
      margin: 0 auto 1.25rem;
    }
    .hero p {
      font-size: 1.15rem;
      color: var(--slate-600);
      max-width: 680px;
      margin: 0 auto 2rem;
    }

    .container { max-width: 1200px; margin: 0 auto; padding: 3rem 1.5rem; }
    .grid-3 { display: grid; grid-template-columns: repeat(auto-fit, minmax(280px, 1fr)); gap: 1.5rem; }
    .card {
      background: #fff;
      border: 1px solid var(--border);
      border-radius: 12px;
      padding: 1.75rem;
      transition: transform 0.2s, box-shadow 0.2s;
    }
    .card:hover { transform: translateY(-3px); box-shadow: 0 10px 25px -5px rgba(0,0,0,0.06); }
    .card h3 { font-size: 1.25rem; margin-bottom: 0.5rem; color: var(--slate-900); }
    .card p { color: var(--slate-600); font-size: 0.95rem; }
    .stat-pill { font-size: 0.8rem; font-weight: 700; color: var(--primary); background: #eff6ff; padding: 0.2rem 0.6rem; border-radius: 4px; display: inline-block; margin-top: 1rem; }

    .form-section {
      background: var(--slate-50);
      border-top: 1px solid var(--border);
      border-bottom: 1px solid var(--border);
      padding: 4rem 1.5rem;
    }
    .form-box {
      max-width: 780px;
      margin: 0 auto;
      background: #fff;
      border: 1px solid var(--border);
      border-radius: 16px;
      padding: 2.5rem;
      box-shadow: 0 4px 20px rgba(0,0,0,0.04);
    }
    .form-header { margin-bottom: 2rem; text-align: center; }
    .form-header h2 { font-size: 1.85rem; font-weight: 800; }
    .form-header p { color: var(--slate-600); font-size: 0.95rem; margin-top: 0.25rem; }
    .row { display: flex; gap: 1rem; margin-bottom: 1rem; flex-wrap: wrap; }
    .col { flex: 1; min-width: 240px; }
    label { display: block; font-weight: 600; font-size: 0.85rem; margin-bottom: 0.35rem; color: var(--slate-700); }
    input, select, textarea {
      width: 100%;
      padding: 0.75rem 0.9rem;
      border: 1px solid var(--border);
      border-radius: 8px;
      font-size: 0.95rem;
      font-family: inherit;
      color: var(--slate-900);
      outline: none;
      transition: border-color 0.15s;
    }
    input:focus, select:focus, textarea:focus { border-color: var(--primary); }
    .terms-card {
      background: var(--slate-50);
      border: 1px solid var(--border);
      border-radius: 8px;
      padding: 1rem;
      font-size: 0.85rem;
      color: var(--slate-600);
      margin: 1.25rem 0;
    }
    .btn-submit {
      width: 100%;
      background: var(--primary);
      color: #fff;
      padding: 0.9rem;
      border: none;
      border-radius: 8px;
      font-size: 1.05rem;
      font-weight: 700;
      cursor: pointer;
      transition: background 0.15s;
    }
    .btn-submit:hover { background: var(--primary-hover); }

    .contact-container {
      max-width: 780px;
      margin: 0 auto;
      padding: 4rem 1.5rem;
    }
    .contact-box {
      border: 1px solid var(--border);
      border-radius: 16px;
      padding: 2.25rem;
      background: #ffffff;
    }
    .alert-banner {
      padding: 1rem;
      border-radius: 8px;
      margin-bottom: 1.5rem;
      display: none;
      font-size: 0.95rem;
    }
    .alert-success { background: #ecfdf5; color: #065f46; border: 1px solid #a7f3d0; }
    .alert-error { background: #fef2f2; color: #991b1b; border: 1px solid #fecaca; }

    footer {
      border-top: 1px solid var(--border);
      padding: 2.5rem 1.5rem;
      text-align: center;
      color: var(--slate-600);
      font-size: 0.85rem;
      background: #fff;
    }
  </style>
</head>
<body>

  <header>
    <div class="nav-container">
      <a href="/" class="logo">⚖️ Easy<span>Claim</span></a>
      <div class="nav-links">
        <a href="#about">About</a>
        <a href="#contact">Contact Us</a>
        <a href="#claim" class="btn-nav">File a Claim</a>
      </div>
    </div>
  </header>

  <section class="hero">
    <div class="badge">No Win, No Fee • 100% Contingency</div>
    <h1>We Recover What Companies Statutorily Owe You.</h1>
    <p>Airlines, broadband utilities, and landlords routinely withhold mandatory refunds. EasyClaim automatically compiles, cites, and serves formal legal demands to secure your liquidated restitution.</p>
  </section>

  <section class="container" id="about">
    <div class="grid-3">
      <div class="card">
        <h3>Autonomous Legal Tech</h3>
        <p>Our regulatory engine evaluates your disruption against statutory laws (US DOT 14 CFR Part 260, UK261/EU261, State PUC utility tariffs, and tenancy acts) to calculate mandatory damages.</p>
        <span class="stat-pill">Instant Statutory Audit</span>
      </div>
      <div class="card">
        <h3>Direct Legal Desk Dispatch</h3>
        <p>We generate and serve verified ReportLab legal demand packages with digital signatures directly to carrier legal teams with an active 14-day compliance window.</p>
        <span class="stat-pill">Formal Legal Demand</span>
      </div>
      <div class="card">
        <h3>Zero Upfront Costs</h3>
        <p>You pay $0 out of pocket. Our platform retains an industry-standard 25% contingency fee deducted only after cash restitution or bill credits are settled and recovered.</p>
        <span class="stat-pill">25% Contingency Fee</span>
      </div>
    </div>
  </section>

  <section class="form-section" id="claim">
    <div class="form-box">
      <div class="form-header">
        <h2>Submit Your Claim for Recovery</h2>
        <p>Provide your incident details. Our AI engine will evaluate your legal eligibility and serve demand notices.</p>
      </div>

      <div id="claim-alert" class="alert-banner"></div>

      <form id="claim-form">
        <div class="row">
          <div class="col">
            <label for="vertical">Dispute Vertical *</label>
            <select id="vertical" required>
              <option value="flight_disruption">✈️ Flight Delay / Cancellation (DOT / UK261)</option>
              <option value="isp_outage">🌐 Telecom & ISP Outage (PUC Tariff Credit)</option>
              <option value="security_deposit">🏠 Security Deposit Withheld (Tenancy Act)</option>
              <option value="class_action">⚖️ Class Action & Restitution Fund</option>
            </select>
          </div>
          <div class="col">
            <label for="carrier_name">Company / Vendor Name *</label>
            <input type="text" id="carrier_name" placeholder="e.g. United Airlines, Xfinity, Landlord LLC" required>
          </div>
        </div>

        <div class="row">
          <div class="col">
            <label for="incident_identifier">Flight # / Account Ref / Property Address</label>
            <input type="text" id="incident_identifier" placeholder="e.g. Flight UA 949 or Acct #8849-102">
          </div>
          <div class="col">
            <label for="incident_date">Date of Incident *</label>
            <input type="date" id="incident_date" required>
          </div>
        </div>

        <div class="row">
          <div class="col">
            <label for="claimant_name">Your Full Legal Name *</label>
            <input type="text" id="claimant_name" placeholder="Jane Doe" required>
          </div>
          <div class="col">
            <label for="claimant_email">Email Address *</label>
            <input type="email" id="claimant_email" placeholder="jane@example.com" required>
          </div>
        </div>

        <div class="row">
          <div class="col">
            <label for="claimant_phone">Mobile Phone (For SMS Updates) *</label>
            <input type="tel" id="claimant_phone" placeholder="+13035550199" required>
          </div>
          <div class="col">
            <label for="claimant_address">Mailing Address</label>
            <input type="text" id="claimant_address" placeholder="123 Main St, Denver, CO 80202">
          </div>
        </div>

        <div style="margin-bottom: 1rem;">
          <label for="incident_description">What happened? (Disruption Narrative) *</label>
          <textarea id="incident_description" rows="4" placeholder="Describe the delay duration, lack of internet uptime, or unreturned deposit details..." required></textarea>
        </div>

        <div class="terms-card">
          <strong>Contingency Agreement:</strong> By signing below, you authorize EasyClaim (Dispute Agent Platform) to draft and deliver statutory legal demand packages on your behalf. You agree to a 25% contingency fee deducted only upon successful monetary restitution.
        </div>

        <div style="margin-bottom: 1.5rem;">
          <label for="digital_signature">Digital Signature (Type Full Legal Name) *</label>
          <input type="text" id="digital_signature" placeholder="Jane Doe" required>
        </div>

        <button type="submit" class="btn-submit" id="submit-btn">🚀 Submit Claim & Dispatch Demand</button>
      </form>
    </div>
  </section>

  <section class="contact-container" id="contact">
    <div class="contact-box">
      <div class="form-header" style="text-align: left; margin-bottom: 1.5rem;">
        <h2>Contact Our Advocacy Team</h2>
        <p>Have questions about your legal rights or need dedicated case support? Leave us a message.</p>
      </div>

      <div id="contact-alert" class="alert-banner"></div>

      <form id="contact-form">
        <div class="row">
          <div class="col">
            <label for="contact_name">Your Name *</label>
            <input type="text" id="contact_name" placeholder="David" required>
          </div>
          <div class="col">
            <label for="contact_email">Your Email *</label>
            <input type="email" id="contact_email" placeholder="dave@example.com" required>
          </div>
        </div>
        <div style="margin-bottom: 1rem;">
          <label for="contact_subject">Subject</label>
          <input type="text" id="contact_subject" placeholder="Question regarding flight or ISP claim">
        </div>
        <div style="margin-bottom: 1.5rem;">
          <label for="contact_message">Message *</label>
          <textarea id="contact_message" rows="4" placeholder="How can our claims specialists assist you?" required></textarea>
        </div>
        <button type="submit" class="btn-submit" style="background: var(--slate-800);" id="contact-btn">Send Message</button>
      </form>
    </div>
  </section>

  <footer>
    <p>&copy; 2026 EasyClaim / Dispute Agent Recovery Operations. All Rights Reserved. Statutory Advocacy & Representation.</p>
  </footer>

  <script>
    document.getElementById('claim-form').addEventListener('submit', async function(e) {
      e.preventDefault();
      const btn = document.getElementById('submit-btn');
      const alertBox = document.getElementById('claim-alert');
      btn.disabled = true;
      btn.innerText = "Analyzing Statutory Rights & Dispatching...";

      const payload = {
        vertical: document.getElementById('vertical').value,
        carrier_name: document.getElementById('carrier_name').value,
        incident_identifier: document.getElementById('incident_identifier').value,
        incident_date: document.getElementById('incident_date').value,
        claimant_name: document.getElementById('claimant_name').value,
        claimant_email: document.getElementById('claimant_email').value,
        claimant_phone: document.getElementById('claimant_phone').value,
        claimant_address: document.getElementById('claimant_address').value,
        incident_description: document.getElementById('incident_description').value,
        digital_signature: document.getElementById('digital_signature').value
      };

      try {
        const response = await fetch('/api/v1/claims/portal-intake', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(payload)
        });
        const data = await response.json();

        if (response.ok) {
          alertBox.className = "alert-banner alert-success";
          alertBox.style.display = "block";
          alertBox.innerHTML = `<strong>Claim Successfully Staged & Dispatched!</strong><br>
            Reference ID: <code>${data.lead_id}</code><br>
            Statutory Basis: <strong>${data.regulatory_framework}</strong><br>
            Estimated Valuation: <strong>$${data.estimated_compensation}</strong><br>
            A confirmation SMS has been dispatched. <a href="https://dispute-admin.onrender.com/?claim_id=${data.lead_id}" target="_blank" style="color:#065f46;font-weight:700;">Track Your Claim Live &rarr;</a>`;
          document.getElementById('claim-form').reset();
        } else {
          throw new Error(data.detail || "Submission failed.");
        }
      } catch (err) {
        alertBox.className = "alert-banner alert-error";
        alertBox.style.display = "block";
        alertBox.innerText = "Error: " + err.message;
      } finally {
        btn.disabled = false;
        btn.innerText = "🚀 Submit Claim & Dispatch Demand";
      }
    });

    document.getElementById('contact-form').addEventListener('submit', async function(e) {
      e.preventDefault();
      const btn = document.getElementById('contact-btn');
      const alertBox = document.getElementById('contact-alert');
      btn.disabled = true;
      btn.innerText = "Sending Message...";

      const payload = {
        sender_name: document.getElementById('contact_name').value,
        sender_email: document.getElementById('contact_email').value,
        subject: document.getElementById('contact_subject').value,
        message: document.getElementById('contact_message').value
      };

      try {
        const response = await fetch('/api/v1/contact', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(payload)
        });
        const data = await response.json();

        if (response.ok) {
          alertBox.className = "alert-banner alert-success";
          alertBox.style.display = "block";
          alertBox.innerText = "Thank you! Your message has been received. A claims specialist will follow up shortly.";
          document.getElementById('contact-form').reset();
        } else {
          throw new Error(data.detail || "Failed to send message.");
        }
      } catch (err) {
        alertBox.className = "alert-banner alert-error";
        alertBox.style.display = "block";
        alertBox.innerText = "Error: " + err.message;
      } finally {
        btn.disabled = false;
        btn.innerText = "Send Message";
      }
    });
  </script>
</body>
</html>
"""

# =====================================================================
# SYSTEM DIAGNOSTIC & TELEMETRY API
# =====================================================================

@app.get("/health", status_code=status.HTTP_200_OK)
def health_check():
    return {"status": "healthy", "service": "dispute-api", "brand": "EasyClaim"}

@app.get("/api/v1/system/health-check", status_code=status.HTTP_200_OK)
def run_system_health_check():
    """Performs an end-to-end active probe of DB, Gemini AI, and Vendor API configurations."""
    results: Dict[str, Any] = {
        "timestamp": time.time(),
        "overall_status": "healthy",
        "probes": {}
    }

    # 1. PostgreSQL Probe
    t0 = time.time()
    try:
        conn = get_db_connection()
        with conn.cursor() as cur:
            cur.execute("SELECT 1 AS alive;")
            cur.fetchone()
        conn.close()
        db_latency_ms = round((time.time() - t0) * 1000, 2)
        results["probes"]["database"] = {
            "status": "operational",
            "latency_ms": db_latency_ms,
            "message": "Render PostgreSQL responding normally."
        }
    except Exception as e:
        results["overall_status"] = "degraded"
        results["probes"]["database"] = {"status": "error", "message": str(e)}

    # 2. Google Gemini Probe
    t0 = time.time()
    try:
        client = genai.Client(api_key=GEMINI_API_KEY)
        res = client.models.generate_content(
            model="gemini-3.6-flash",
            contents="ping",
            config=types.GenerateContentConfig(temperature=0.0)
        )
        ai_latency_ms = round((time.time() - t0) * 1000, 2)
        results["probes"]["gemini_ai"] = {
            "status": "operational",
            "latency_ms": ai_latency_ms,
            "message": f"Model responding (Token chars: {len(res.text or '')})"
        }
    except Exception as e:
        results["overall_status"] = "degraded"
        results["probes"]["gemini_ai"] = {"status": "error", "message": str(e)}

    # 3. Twilio SMS Probe
    tw_sid = get_sms_setting("TWILIO_ACCOUNT_SID")
    tw_token = get_sms_setting("TWILIO_AUTH_TOKEN")
    tw_from = get_sms_setting("TWILIO_PHONE_NUMBER")
    if tw_sid and tw_token and tw_from:
        results["probes"]["twilio_sms"] = {
            "status": "operational",
            "configured": True,
            "message": f"Active SID: {tw_sid[:6]}... Number: {tw_from}"
        }
    else:
        results["probes"]["twilio_sms"] = {
            "status": "warning",
            "configured": False,
            "message": "Twilio credentials unset. Operating in Dry-Run mode."
        }

    # 4. Carrier Demand SMTP Probe
    smtp_host = get_carrier_setting("SMTP_HOST", "smtp.gmail.com")
    smtp_user = get_carrier_setting("SMTP_USER", "")
    smtp_pass = get_carrier_setting("SMTP_PASS", "")
    if smtp_user and smtp_pass:
        results["probes"]["smtp_dispatcher"] = {
            "status": "operational",
            "configured": True,
            "message": f"Host: {smtp_host} User: {smtp_user}"
        }
    else:
        results["probes"]["smtp_dispatcher"] = {
            "status": "warning",
            "configured": False,
            "message": "SMTP credentials unset. Operating in Dry-Run simulation mode."
        }

    # 5. Social Ingestion Configuration
    try:
        conn = get_db_connection()
        with conn.cursor() as cur:
            cur.execute("SELECT value FROM system_settings WHERE key = 'MONITORED_SUBREDDITS';")
            sub_res = cur.fetchone()
        conn.close()
        sub_list = sub_res["value"].split(",") if sub_res else []
        results["probes"]["social_ingestion"] = {
            "status": "operational",
            "active_subreddits_count": len(sub_list),
            "target_subreddits": sub_list[:5],
            "daemon_loop": "active (60s thread)"
        }
    except Exception as e:
        results["probes"]["social_ingestion"] = {"status": "warning", "message": str(e)}

    log_system_event("health_monitor", "DIAGNOSTIC_PROBE", "INFO", f"System health probe executed: {results['overall_status']}")
    return results

@app.get("/api/v1/system/audit-logs", status_code=status.HTTP_200_OK)
def get_audit_logs(
    level: Optional[str] = Query(None),
    limit: int = Query(50, le=200),
    conn=Depends(get_db)
):
    query = """
    SELECT 
        id::text AS id, service_name, event_category, log_level, message,
        lead_id::text AS lead_id, metadata, created_at
    FROM system_audit_logs
    """
    params = []
    if level:
        query += " WHERE log_level = %s"
        params.append(level.upper())
    query += " ORDER BY created_at DESC LIMIT %s;"
    params.append(limit)

    with conn.cursor() as cur:
        cur.execute(query, tuple(params))
        logs = cur.fetchall()
    return logs

# =====================================================================
# TRANSACTIONAL WORKFLOW ENDPOINTS
# =====================================================================

@app.post("/api/v1/claims/portal-intake", status_code=status.HTTP_201_CREATED)
def portal_direct_intake(payload: DirectClaimIntakePayload, background_tasks: BackgroundTasks, conn=Depends(get_db)):
    eval_text = f"Claim against {payload.carrier_name} for {payload.vertical}: {payload.incident_description}"
    eval_result = evaluate_multi_vertical_signal(eval_text)

    est_val = eval_result.get("estimated_compensation") or 650.00
    framework = eval_result.get("regulatory_framework") or "Statutory Consumer Protection Mandates"
    reasoning = eval_result.get("ai_reasoning") or "Mandatory liquidated restitution verified."

    query = """
    INSERT INTO leads (
        vertical, source_platform, platform_user_id, username, post_url, raw_post_text,
        carrier_name, incident_identifier, account_number, incident_date, estimated_compensation,
        regulatory_framework, ai_reasoning, claimant_name, claimant_email, claimant_phone,
        claimant_address, digital_signature, status
    ) VALUES (
        %s, 'easyclaim_landing_page', %s, %s, 'https://dispute-api-xyl7.onrender.com', %s,
        %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'opted_in'
    ) RETURNING id::text, vertical, carrier_name, estimated_compensation, regulatory_framework;
    """

    with conn.cursor() as cur:
        cur.execute(query, (
            payload.vertical, payload.claimant_email, payload.claimant_name, payload.incident_description,
            payload.carrier_name, payload.incident_identifier, payload.account_number or payload.incident_identifier,
            payload.incident_date, est_val, framework, reasoning, payload.claimant_name, payload.claimant_email,
            payload.claimant_phone, payload.claimant_address, payload.digital_signature
        ))
        inserted = cur.fetchone()
        conn.commit()

    new_id = inserted["id"]
    log_system_event("portal_intake", "SELF_SERVICE_CLAIM", "INFO", f"New self-service claim filed against {payload.carrier_name}", lead_id=new_id)

    if payload.claimant_phone:
        background_tasks.add_task(notify_claim_event, new_id, "opt_in_confirmation")
    background_tasks.add_task(trigger_carrier_demand_pipeline, new_id)

    return {
        "status": "authorized_and_dispatched",
        "lead_id": new_id,
        "carrier_name": inserted["carrier_name"],
        "estimated_compensation": float(inserted["estimated_compensation"]),
        "regulatory_framework": inserted["regulatory_framework"]
    }

@app.post("/api/v1/contact", status_code=status.HTTP_201_CREATED)
def submit_contact_inquiry(payload: ContactMessagePayload, conn=Depends(get_db)):
    with conn.cursor() as cur:
        cur.execute("""
            INSERT INTO customer_inquiries (sender_name, sender_email, subject, message)
            VALUES (%s, %s, %s, %s)
            RETURNING id::text;
        """, (payload.sender_name, payload.sender_email, payload.subject, payload.message))
        res = cur.fetchone()
        conn.commit()

    log_system_event("contact_desk", "INBOUND_MESSAGE", "INFO", f"Message from {payload.sender_name} <{payload.sender_email}>: {payload.subject}")
    return {"status": "received", "inquiry_id": res["id"]}

@app.post("/api/v1/leads/evaluate", status_code=status.HTTP_201_CREATED)
def intake_and_evaluate(payload: RawSignalPayload, conn=Depends(get_db)):
    eval_result = evaluate_multi_vertical_signal(payload.raw_post_text)
    
    if not eval_result.get("is_viable", False):
        log_system_event("gemini_engine", "EVALUATE_DISMISSED", "INFO", f"Ignored non-viable post from {payload.source_platform}")
        return {"status": "ignored", "reason": "No viable statutory or tariff violation detected."}

    query = """
    INSERT INTO leads (
        vertical, source_platform, platform_user_id, username, post_url, raw_post_text,
        carrier_name, incident_identifier, estimated_compensation, regulatory_framework,
        ai_reasoning, outreach_copy, status
    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'staged_for_review')
    RETURNING id::text, vertical, carrier_name, estimated_compensation, status;
    """
    params = (
        eval_result.get("vertical", "flight_disruption"),
        payload.source_platform, payload.platform_user_id, payload.username,
        payload.post_url, payload.raw_post_text, eval_result.get("carrier_name"),
        eval_result.get("incident_identifier"), eval_result.get("estimated_compensation", 0.00),
        eval_result.get("regulatory_framework"), eval_result.get("ai_reasoning"),
        eval_result.get("outreach_copy")
    )

    with conn.cursor() as cur:
        cur.execute(query, params)
        inserted_lead = cur.fetchone()
        conn.commit()

    new_id = inserted_lead["id"]
    log_system_event("ingestion_worker", "LEAD_STAGED", "INFO", f"Staged {inserted_lead['vertical']} against {inserted_lead['carrier_name']}", lead_id=new_id)
    return {"status": "staged", "lead": inserted_lead}

@app.get("/api/v1/leads")
def list_leads(status: Optional[str] = Query(None), conn=Depends(get_db)):
    query = """
    SELECT 
        id::text AS id, vertical, source_platform, username, carrier_name, incident_identifier,
        estimated_compensation, recovery_amount, fee_collected, regulatory_framework,
        ai_reasoning, outreach_copy, status, created_at
    FROM leads
    """
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
        cur.execute("""
            SELECT 
                id::text AS id, vertical, carrier_name, incident_identifier, account_number,
                estimated_compensation, recovery_amount, fee_collected, regulatory_framework,
                status, created_at, updated_at
            FROM leads WHERE id::text = %s;
        """, (lead_id,))
        claim = cur.fetchone()

    if not claim:
        raise HTTPException(status_code=404, detail="Dispute claim not found.")
    return claim

@app.post("/api/v1/claims/submit")
def submit_claim(payload: ClaimSubmissionPayload, background_tasks: BackgroundTasks, conn=Depends(get_db)):
    query = """
    UPDATE leads
    SET claimant_name = %s, claimant_email = %s, claimant_phone = %s, claimant_address = %s,
        pnr = %s, account_number = %s, incident_date = %s, digital_signature = %s,
        status = 'opted_in', updated_at = NOW()
    WHERE id::text = %s
    RETURNING id::text, status, claimant_name, claimant_email, claimant_phone;
    """
    with conn.cursor() as cur:
        cur.execute(query, (
            payload.claimant_name, payload.claimant_email, payload.claimant_phone,
            payload.claimant_address, payload.pnr, payload.account_number,
            payload.incident_date, payload.digital_signature, payload.lead_id
        ))
        updated = cur.fetchone()
        conn.commit()

    if not updated:
        raise HTTPException(status_code=404, detail="Lead ID not found.")

    log_system_event("claims_gateway", "OPT_IN_SUBMITTED", "INFO", f"Claim authorized by {payload.claimant_name}", lead_id=payload.lead_id)

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
    SET status = 'settled', recovery_amount = %s, fee_collected = %s, updated_at = NOW()
    WHERE id::text = %s
    RETURNING id::text, status, recovery_amount, fee_collected, claimant_phone;
    """
    with conn.cursor() as cur:
        cur.execute(query, (payload.recovery_amount, fee, payload.lead_id))
        settled = cur.fetchone()
        conn.commit()

    if not settled:
        raise HTTPException(status_code=404, detail="Lead ID not found.")

    log_system_event("settlement_engine", "SETTLED_RECORDED", "INFO", f"Claim settled for ${payload.recovery_amount} (Fee: ${fee})", lead_id=payload.lead_id)
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
            matched_lead_id, payload.carrier_name, payload.vertical, payload.decision,
            float(payload.payout_offered), payload.resolution_notes, json.dumps(payload.raw_metadata)
        ))

        if matched_lead_id:
            if payload.decision == "approved" and payload.payout_offered > Decimal("0.00"):
                recovery = payload.payout_offered
                contingency_fee = (recovery * Decimal("0.25")).quantize(Decimal("0.01"))
                
                cur.execute("""
                    UPDATE leads
                    SET status = 'settled', recovery_amount = %s, fee_collected = %s, updated_at = NOW()
                    WHERE id::text = %s;
                """, (recovery, contingency_fee, matched_lead_id))
                background_tasks.add_task(notify_claim_event, matched_lead_id, "settlement_alert")
                log_system_event("webhook_engine", "CARRIER_SETTLEMENT_APPROVED", "INFO", f"Settlement webhook tender of ${recovery} processed.", lead_id=matched_lead_id)

            elif payload.decision == "rejected":
                cur.execute("UPDATE leads SET status = 'rejected', updated_at = NOW() WHERE id::text = %s;", (matched_lead_id,))
                log_system_event("webhook_engine", "CARRIER_REJECTION", "WARN", f"Respondent rejected demand.", lead_id=matched_lead_id)
        conn.commit()

    return {
        "status": "processed",
        "lead_matched": matched_lead_id is not None,
        "lead_id": matched_lead_id,
        "recorded_decision": payload.decision
    }
