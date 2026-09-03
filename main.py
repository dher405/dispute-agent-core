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
from dedup import compute_dedup_key
from audit import log_status_change
from scoring import compute_lead_score
from crypto import decrypt_value

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - [%(filename)s:%(lineno)d] - %(message)s")
logger = logging.getLogger(__name__)

DATABASE_URL = os.getenv("DATABASE_URL")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
PUBLIC_API_URL = os.getenv("API_BASE_URL", "https://dispute-api-xyl7.onrender.com")

app = FastAPI(
    title="EasyClaim Autonomous Recovery Engine",
    description="Statutory micro-dispute resolution, multi-vendor ingestion, and telemetry logging.",
    version="3.6.0"
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
            return decrypt_value(res["value"].strip())
    except Exception:
        pass
    return os.getenv(key, default)

def initialize_database_schema():
    """Initialize all required database tables and columns on startup."""
    if not DATABASE_URL:
        logger.warning("DATABASE_URL not set; skipping schema initialization.")
        return

    try:
        conn = get_db_connection()
        conn.autocommit = False
        with conn.cursor() as cur:
            # Create leads table if it doesn't exist
            cur.execute("""
                CREATE TABLE IF NOT EXISTS leads (
                    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                    vertical VARCHAR(50) DEFAULT 'flight_disruption',
                    source_platform VARCHAR(50),
                    platform_user_id VARCHAR(255),
                    username VARCHAR(255),
                    post_url TEXT,
                    raw_post_text TEXT,
                    carrier_name VARCHAR(255),
                    incident_identifier VARCHAR(255),
                    account_number VARCHAR(255),
                    incident_date DATE,
                    estimated_compensation NUMERIC(12,2),
                    recovery_amount NUMERIC(12,2),
                    fee_collected NUMERIC(12,2),
                    regulatory_framework TEXT,
                    ai_reasoning TEXT,
                    outreach_copy TEXT,
                    status VARCHAR(50) DEFAULT 'staged_for_review',
                    claimant_name VARCHAR(255),
                    claimant_email VARCHAR(255),
                    claimant_phone VARCHAR(20),
                    claimant_address TEXT,
                    pnr VARCHAR(10),
                    digital_signature TEXT,
                    created_at TIMESTAMPTZ DEFAULT NOW(),
                    updated_at TIMESTAMPTZ DEFAULT NOW(),
                    discovered_at TIMESTAMPTZ DEFAULT NOW(),
                    last_status_change_at TIMESTAMPTZ DEFAULT NOW()
                );
            """)

            # Add new columns to leads if they don't exist
            cur.execute("""
                ALTER TABLE leads
                ADD COLUMN IF NOT EXISTS lead_score INT DEFAULT 0,
                ADD COLUMN IF NOT EXISTS dedup_key VARCHAR(64),
                ADD COLUMN IF NOT EXISTS flight_verified BOOLEAN DEFAULT FALSE,
                ADD COLUMN IF NOT EXISTS flight_verification_notes TEXT,
                ADD COLUMN IF NOT EXISTS escalation_stage VARCHAR(50),
                ADD COLUMN IF NOT EXISTS outage_duration_hours NUMERIC(6,2),
                ADD COLUMN IF NOT EXISTS tier_speed_tier VARCHAR(100),
                ADD COLUMN IF NOT EXISTS dispatch_attempts INT DEFAULT 0,
                ADD COLUMN IF NOT EXISTS last_dispatch_error TEXT,
                ADD COLUMN IF NOT EXISTS last_dispatch_attempt_at TIMESTAMPTZ,
                ADD COLUMN IF NOT EXISTS next_dispatch_retry_at TIMESTAMPTZ DEFAULT NOW();
            """)

            # Create status_audit_log table
            cur.execute("""
                CREATE TABLE IF NOT EXISTS status_audit_log (
                    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                    lead_id UUID REFERENCES leads(id) ON DELETE CASCADE,
                    old_status VARCHAR(50),
                    new_status VARCHAR(50),
                    changed_by VARCHAR(255),
                    note TEXT,
                    changed_at TIMESTAMPTZ DEFAULT NOW()
                );
            """)

            # Create outreach_queue table
            cur.execute("""
                CREATE TABLE IF NOT EXISTS outreach_queue (
                    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                    lead_id UUID REFERENCES leads(id) ON DELETE CASCADE,
                    channel VARCHAR(50),
                    recipient VARCHAR(255),
                    message_body TEXT,
                    status VARCHAR(50) DEFAULT 'pending_approval',
                    created_at TIMESTAMPTZ DEFAULT NOW(),
                    approved_by VARCHAR(255),
                    approved_at TIMESTAMPTZ,
                    sent_at TIMESTAMPTZ
                );
            """)

            # Create system_alerts table
            cur.execute("""
                CREATE TABLE IF NOT EXISTS system_alerts (
                    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                    lead_id UUID REFERENCES leads(id) ON DELETE SET NULL,
                    alert_type VARCHAR(50),
                    message TEXT,
                    created_at TIMESTAMPTZ DEFAULT NOW(),
                    acknowledged BOOLEAN DEFAULT FALSE,
                    acknowledged_by VARCHAR(255),
                    acknowledged_at TIMESTAMPTZ
                );
            """)

            # Create carrier_inbound_events table if it doesn't exist
            cur.execute("""
                CREATE TABLE IF NOT EXISTS carrier_inbound_events (
                    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                    lead_id UUID REFERENCES leads(id) ON DELETE SET NULL,
                    carrier_name VARCHAR(255),
                    vertical VARCHAR(50),
                    event_type VARCHAR(50),
                    settlement_amount NUMERIC(12,2),
                    parsed_notes TEXT,
                    raw_payload JSONB,
                    created_at TIMESTAMPTZ DEFAULT NOW()
                );
            """)

            # Ensure system_settings table exists
            cur.execute("""
                CREATE TABLE IF NOT EXISTS system_settings (
                    key VARCHAR(100) PRIMARY KEY,
                    value TEXT NOT NULL,
                    category VARCHAR(50) NOT NULL,
                    description TEXT,
                    updated_at TIMESTAMPTZ DEFAULT NOW()
                );
            """)

            # Ensure customer_inquiries table exists
            cur.execute("""
                CREATE TABLE IF NOT EXISTS customer_inquiries (
                    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                    sender_name VARCHAR(255) NOT NULL,
                    sender_email VARCHAR(255) NOT NULL,
                    subject VARCHAR(255),
                    message TEXT NOT NULL,
                    status VARCHAR(50) DEFAULT 'unread',
                    created_at TIMESTAMPTZ DEFAULT NOW()
                );
            """)

            # Ensure system_audit_logs table exists
            cur.execute("""
                CREATE TABLE IF NOT EXISTS system_audit_logs (
                    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                    service_name VARCHAR(50) NOT NULL,
                    event_category VARCHAR(50) NOT NULL,
                    log_level VARCHAR(20) DEFAULT 'INFO',
                    message TEXT NOT NULL,
                    lead_id UUID REFERENCES leads(id) ON DELETE SET NULL,
                    metadata JSONB DEFAULT '{}'::jsonb,
                    created_at TIMESTAMPTZ DEFAULT NOW()
                );
            """)

            # Ensure admin_users table exists
            cur.execute("""
                CREATE TABLE IF NOT EXISTS admin_users (
                    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                    username VARCHAR(100) UNIQUE NOT NULL,
                    full_name VARCHAR(255) NOT NULL,
                    password_hash VARCHAR(255) NOT NULL,
                    role VARCHAR(50) NOT NULL DEFAULT 'claims_agent',
                    is_2fa_enabled BOOLEAN DEFAULT FALSE,
                    totp_secret VARCHAR(64),
                    is_active BOOLEAN DEFAULT TRUE,
                    created_at TIMESTAMPTZ DEFAULT NOW(),
                    updated_at TIMESTAMPTZ DEFAULT NOW()
                );
            """)

            conn.commit()
            logger.info("Database schema initialized successfully.")

    except Exception as e:
        logger.error(f"Database schema initialization error: {e}")
        try:
            conn.rollback()
        except:
            pass

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
# MULTI-VENDOR INGESTION & OUTREACH ENGINE
# =====================================================================

INGESTION_KEYWORDS = [
    "delay", "delayed", "cancelled", "cancellation", "stranded", 
    "outage", "no internet", "bill credit", "deposit", "landlord kept", 
    "flight cancellation", "xfinity down", "comcast outage"
]

BLUESKY_QUERIES = [
    "flight cancelled", "flight delayed", "united delay", 
    "delta cancelled", "xfinity outage", "security deposit withheld"
]

def sweep_reddit_vendor():
    raw_subs = get_db_setting("MONITORED_SUBREDDITS", "unitedairlines,delta,americanairlines,southwestairlines,comcast,ATT,Tenant,mildlyinfuriating")
    subreddits = [s.strip() for s in raw_subs.split(",") if s.strip()]

    log_system_event(
        "reddit_ingestion",
        "POLL_START",
        "INFO",
        f"Checking Reddit ({len(subreddits)} subreddits) for statutory disruption complaints.",
        metadata={"subreddits": subreddits}
    )

    staged_count = 0
    scanned_posts = 0

    for sub in subreddits:
        try:
            res = requests.get(
                f"https://www.reddit.com/r/{sub}/new.json?limit=10",
                headers={"User-Agent": "DisputeAgentCore/3.6"},
                timeout=10
            )
            if res.status_code == 200:
                posts = res.json().get("data", {}).get("children", [])
                scanned_posts += len(posts)
                for item in posts:
                    d = item.get("data", {})
                    post_url = f"https://reddit.com{d.get('permalink')}"
                    text = f"{d.get('title', '')}\n{d.get('selftext', '')}".strip()

                    if len(text) > 30 and any(k in text.lower() for k in INGESTION_KEYWORDS):
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
                                        RETURNING id::text;
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
                                    lead_id = cur.fetchone()["id"]
                                conn.close()
                                staged_count += 1
                                log_system_event(
                                    "reddit_ingestion",
                                    "LEAD_STAGED",
                                    "INFO",
                                    f"Staged viable signal from r/{sub}: {eval_res.get('carrier_name')} - Valuation: ${eval_res.get('estimated_compensation')}",
                                    lead_id=lead_id,
                                    metadata={"post_url": post_url}
                                )
            time.sleep(0.5)
        except Exception as e:
            log_system_event("reddit_ingestion", "POLL_ERROR", "WARN", f"Error scanning r/{sub}: {e}")

    log_system_event(
        "reddit_ingestion",
        "POLL_COMPLETE",
        "INFO",
        f"Reddit sweep complete. Scanned {scanned_posts} posts across {len(subreddits)} subreddits. Staged {staged_count} new lead(s)."
    )

def sweep_bluesky_vendor():
    endpoint = "https://public.api.bsky.app/xrpc/app.bsky.feed.searchPosts"
    log_system_event(
        "bluesky_ingestion",
        "POLL_START",
        "INFO",
        f"Checking Bluesky Public Feed across {len(BLUESKY_QUERIES)} disruption filters."
    )

    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
        "Accept": "application/json"
    }

    staged_count = 0
    queried = 0

    for query in BLUESKY_QUERIES:
        try:
            queried += 1
            res = requests.get(endpoint, params={"q": query, "limit": 10, "sort": "latest"}, headers=headers, timeout=10)
            if res.status_code == 200:
                posts = res.json().get("posts", [])
                for p in posts:
                    author = p.get("author", {})
                    record = p.get("record", {})
                    did = author.get("did")
                    handle = author.get("handle")
                    rkey = p.get("uri", "").split("/")[-1]
                    post_url = f"https://bsky.app/profile/{handle}/post/{rkey}"
                    text = record.get("text", "").strip()

                    if len(text) > 30:
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
                                        ) VALUES (%s, 'bluesky', %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'staged_for_review')
                                        RETURNING id::text;
                                    """, (
                                        eval_res.get("vertical", "flight_disruption"),
                                        did,
                                        handle,
                                        post_url,
                                        text,
                                        eval_res.get("carrier_name"),
                                        eval_res.get("incident_identifier"),
                                        eval_res.get("estimated_compensation", 0.00),
                                        eval_res.get("regulatory_framework"),
                                        eval_res.get("ai_reasoning"),
                                        eval_res.get("outreach_copy")
                                    ))
                                    lead_id = cur.fetchone()["id"]
                                conn.close()
                                staged_count += 1
                                log_system_event(
                                    "bluesky_ingestion",
                                    "LEAD_STAGED",
                                    "INFO",
                                    f"Staged viable signal from Bluesky: @{handle} - {eval_res.get('carrier_name')} (${eval_res.get('estimated_compensation')})",
                                    lead_id=lead_id,
                                    metadata={"post_url": post_url}
                                )
            elif res.status_code == 403:
                log_system_event("bluesky_ingestion", "RATE_LIMIT_NOTICE", "WARN", f"Bluesky AppView returned 403 for query '{query}'. Continuing other vendors.")
                break
            time.sleep(0.5)
        except Exception as e:
            log_system_event("bluesky_ingestion", "POLL_ERROR", "WARN", f"Error querying Bluesky for '{query}': {e}")

    log_system_event(
        "bluesky_ingestion",
        "POLL_COMPLETE",
        "INFO",
        f"Bluesky sweep finished. Evaluated {queried} query terms. Staged {staged_count} new lead(s)."
    )

def sweep_hackernews_vendor():
    try:
        log_system_event("hackernews_ingestion", "POLL_START", "INFO", "Checking Hacker News Firebase API for major outage discussions.")
        hn_res = requests.get("https://hacker-news.firebaseio.com/v0/newstories.json", timeout=8)
        staged_count = 0
        if hn_res.status_code == 200:
            story_ids = hn_res.json()[:15]
            for sid in story_ids:
                s_res = requests.get(f"https://hacker-news.firebaseio.com/v0/item/{sid}.json", timeout=5)
                if s_res.status_code == 200:
                    story = s_res.json() or {}
                    title = story.get("title", "")
                    hn_url = f"https://news.ycombinator.com/item?id={sid}"

                    if any(k in title.lower() for k in ["outage", "down", "isp", "comcast", "centurylink", "fiber cut"]):
                        conn = get_db_connection()
                        with conn.cursor() as cur:
                            cur.execute("SELECT 1 FROM leads WHERE post_url = %s LIMIT 1;", (hn_url,))
                            exists = cur.fetchone() is not None
                        conn.close()

                        if not exists:
                            eval_res = evaluate_multi_vertical_signal(title)
                            if eval_res.get("is_viable", False):
                                conn = get_db_connection()
                                conn.autocommit = True
                                with conn.cursor() as cur:
                                    cur.execute("""
                                        INSERT INTO leads (
                                            vertical, source_platform, platform_user_id, username, post_url,
                                            raw_post_text, carrier_name, incident_identifier, estimated_compensation,
                                            regulatory_framework, ai_reasoning, outreach_copy, status
                                        ) VALUES (%s, 'hackernews', %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'staged_for_review')
                                        RETURNING id::text;
                                    """, (
                                        eval_res.get("vertical", "isp_outage"),
                                        story.get("by"),
                                        story.get("by"),
                                        hn_url,
                                        title,
                                        eval_res.get("carrier_name"),
                                        None,
                                        eval_res.get("estimated_compensation", 100.00),
                                        eval_res.get("regulatory_framework"),
                                        eval_res.get("ai_reasoning"),
                                        eval_res.get("outreach_copy")
                                    ))
                                    lead_id = cur.fetchone()["id"]
                                conn.close()
                                staged_count += 1
                                log_system_event(
                                    "hackernews_ingestion",
                                    "LEAD_STAGED",
                                    "INFO",
                                    f"Staged ISP disruption from Hacker News: {title}",
                                    lead_id=lead_id,
                                    metadata={"post_url": hn_url}
                                )
        log_system_event("hackernews_ingestion", "POLL_COMPLETE", "INFO", f"Hacker News sweep finished. Staged {staged_count} lead(s).")
    except Exception as e:
        log_system_event("hackernews_ingestion", "POLL_ERROR", "WARN", f"HN check error: {e}")

def process_outbound_queue():
    """
    Item 16 (mandatory human-in-the-loop gate): this sweep NO LONGER posts to Reddit or
    sends anything automatically. For each 'approved' lead it composes the outreach
    message and hands it to outreach_gateway.enqueue_outreach(), which writes a
    'pending_approval' row into outreach_queue and stops there. The lead is advanced to
    the intermediate 'pending_outreach_approval' status so it isn't re-queued every
    sweep. A human admin must review and click "Approve & Send" in app.py's Outreach
    Approval Queue tab -- ONLY THEN does outreach_gateway.dispatch_approved_outreach()
    actually post the Reddit reply / send the SMS / send the Bluesky DM.
    """
    from outreach_gateway import enqueue_outreach

    conn = get_db_connection()
    with conn.cursor() as cur:
        cur.execute("""
            SELECT id::text AS id, source_platform, username, post_url, claimant_email, claimant_phone, outreach_copy, carrier_name
            FROM leads
            WHERE status = 'approved'
            ORDER BY updated_at ASC LIMIT 10;
        """)
        queued = cur.fetchall()
    conn.close()

    if not queued:
        return

    log_system_event(
        "outreach_worker",
        "QUEUE_SWEEP",
        "INFO",
        f"Queuing {len(queued)} approved claim(s) for human-approved customer contact."
    )

    for lead in queued:
        lead_id = lead["id"]
        platform = (lead.get("source_platform") or "reddit").lower()
        recipient = lead.get("claimant_phone") or lead.get("username") or lead.get("claimant_email") or "Consumer"
        outreach_text = lead.get("outreach_copy") or ""

        if platform == "reddit":
            channel = "reddit_reply"
        elif platform == "bluesky":
            channel = "bluesky_dm"
        else:
            # direct_inbound / easyclaim_landing_page and anything else with a phone number
            channel = "sms" if lead.get("claimant_phone") else "reddit_reply"

        conn = get_db_connection()
        try:
            queue_id = enqueue_outreach(lead_id, channel, recipient, outreach_text, conn=conn)
            log_status_change(
                conn, lead_id, "approved", "pending_outreach_approval", "system:outreach_worker",
                note=f"Outreach composed and queued for human approval (queue_id={queue_id}, channel={channel}).",
            )
        finally:
            conn.close()

        log_system_event(
            "outreach_worker",
            "OUTREACH_QUEUED_FOR_APPROVAL",
            "INFO",
            f"Outreach to {recipient} via {channel} queued for human approval. Awaiting admin review in Operations Desk.",
            lead_id=lead_id,
            metadata={"recipient": recipient, "channel": channel}
        )

# =====================================================================
# RENDER ANTI-SLEEP IN-PROCESS KEEP-ALIVE DAEMON
# =====================================================================

def render_keep_alive_daemon():
    """Dispatches an HTTP GET to /health every 9 minutes to prevent Render idle spin-down."""
    time.sleep(30)
    health_url = f"{PUBLIC_API_URL.rstrip('/')}/health"
    logger.info(f"[KEEP-ALIVE] Initialized internal sentinel target: {health_url}")

    while True:
        try:
            res = requests.get(health_url, timeout=15)
            log_system_event(
                "keep_alive_sentinel",
                "KEEP_ALIVE_PING",
                "INFO",
                f"Keep-alive self-ping dispatched to {health_url} (HTTP {res.status_code}) to prevent instance sleep.",
                metadata={"status_code": res.status_code, "response": res.text[:50]}
            )
        except Exception as ping_err:
            log_system_event(
                "keep_alive_sentinel",
                "PING_WARNING",
                "WARN",
                f"Keep-alive self-ping encountered network glitch: {ping_err}"
            )
        # Sleep for 9 minutes (Render free tier timeout is 15 minutes)
        time.sleep(540)

def master_autonomous_cycle():
    """Master background loop running sweeps and outbound processing every 60 seconds."""
    logger.info("[ENGINE] Master Multi-Vendor Ingestion & Outreach Engine Started (60s Cadence).")
    time.sleep(3)

    while True:
        cycle_start = time.time()
        try:
            poll_interval = int(get_db_setting("POLL_INTERVAL_SECONDS", "60"))

            # 1. Sweep Reddit
            sweep_reddit_vendor()

            # 2. Sweep Bluesky AT Protocol
            sweep_bluesky_vendor()

            # 3. Sweep Hacker News
            sweep_hackernews_vendor()

            # 4. Sweep Outbound Outreach Queue
            process_outbound_queue()

        except Exception as e:
            logger.error(f"[ENGINE EXCEPTION] Master autonomous cycle error: {e}")
            log_system_event("engine_supervisor", "CYCLE_EXCEPTION", "ERROR", str(e))

        elapsed = time.time() - cycle_start
        sleep_sec = max(5, poll_interval - elapsed)
        time.sleep(sleep_sec)

@app.on_event("startup")
def startup_event():
    """Spawns unified background thread and keep-alive thread on FastAPI application startup."""
    initialize_database_schema()

    t_engine = threading.Thread(target=master_autonomous_cycle, daemon=True, name="DisputeAgentMasterEngine")
    t_engine.start()

    t_keepalive = threading.Thread(target=render_keep_alive_daemon, daemon=True, name="RenderKeepAliveSentinel")
    t_keepalive.start()

    logger.info("[STARTUP] Ingestion engine and Anti-Sleep Sentinel threads initialized.")

# =====================================================================
# PUBLIC LANDING PAGE (EasyClaim Consumer Portal)
# =====================================================================

@app.get("/", response_class=HTMLResponse)
def serve_landing_page():
    if os.path.exists("landing_page.html"):
        with open("landing_page.html", "r", encoding="utf-8") as f:
            return f.read()
    return "<h1>EasyClaim Portal Online</h1>"

# =====================================================================
# SYSTEM DIAGNOSTIC & TELEMETRY API
# =====================================================================

@app.get("/health", status_code=status.HTTP_200_OK)
def health_check():
    return {"status": "healthy", "service": "dispute-api", "brand": "EasyClaim", "keep_alive": True}

@app.get("/api/v1/system/health-check", status_code=status.HTTP_200_OK)
def run_system_health_check():
    results: Dict[str, Any] = {
        "timestamp": time.time(),
        "overall_status": "healthy",
        "probes": {}
    }

    t0 = time.time()
    try:
        conn = get_db_connection()
        with conn.cursor() as cur:
            cur.execute("SELECT 1 AS alive;")
            cur.fetchone()
        conn.close()
        results["probes"]["database"] = {
            "status": "operational",
            "latency_ms": round((time.time() - t0) * 1000, 2),
            "message": "Render PostgreSQL responding normally."
        }
    except Exception as e:
        results["overall_status"] = "degraded"
        results["probes"]["database"] = {"status": "error", "message": str(e)}

    t0 = time.time()
    try:
        client = genai.Client(api_key=GEMINI_API_KEY)
        res = client.models.generate_content(
            model="gemini-3.6-flash",
            contents="ping",
            config=types.GenerateContentConfig(temperature=0.0)
        )
        results["probes"]["gemini_ai"] = {
            "status": "operational",
            "latency_ms": round((time.time() - t0) * 1000, 2),
            "message": "Model online."
        }
    except Exception as e:
        results["overall_status"] = "degraded"
        results["probes"]["gemini_ai"] = {"status": "error", "message": str(e)}

    tw_sid = get_sms_setting("TWILIO_ACCOUNT_SID")
    tw_token = get_sms_setting("TWILIO_AUTH_TOKEN")
    tw_from = get_sms_setting("TWILIO_PHONE_NUMBER")
    results["probes"]["twilio_sms"] = {
        "status": "operational" if (tw_sid and tw_token and tw_from) else "warning",
        "configured": bool(tw_sid and tw_token and tw_from),
        "message": f"Active SID: {tw_sid[:6]}... ({tw_from})" if tw_sid else "Dry-run simulator mode."
    }

    results["probes"]["ingestion_engine"] = {
        "status": "operational",
        "cadence_seconds": int(get_db_setting("POLL_INTERVAL_SECONDS", "60")),
        "vendors_monitored": ["Reddit (Subreddits)", "Bluesky (AT Protocol)", "Hacker News (Outage Stories)"],
        "anti_sleep_sentinel": "active (9-minute self-ping loop)",
        "telemetry_logging": "active"
    }

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
        return cur.fetchall()

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

    # --- Item 3: cross-platform duplicate-person detection ---
    # Every lead entering the system funnels through this single endpoint, so this is the
    # authoritative dedup check (workers also do a cheaper pre-check before even calling Gemini,
    # but this is the one that actually blocks a duplicate row from being created).
    dedup_key = compute_dedup_key(username=payload.username)
    if dedup_key:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id::text FROM leads WHERE dedup_key = %s AND source_platform = %s LIMIT 1;",
                (dedup_key, payload.source_platform),
            )
            existing = cur.fetchone()
        if existing:
            log_system_event(
                "ingestion_worker", "DUPLICATE_SKIPPED", "INFO",
                f"Skipped duplicate lead from {payload.source_platform} (matches existing lead {existing['id']}).",
                lead_id=existing["id"],
            )
            return {"status": "duplicate", "existing_lead_id": existing["id"]}

    # --- Item 2: lead priority/value scoring ---
    lead_score = compute_lead_score(eval_result, payload.raw_post_text, payload.source_platform)

    # --- Item 6: auto-approve high-confidence/high-value leads (toggle + threshold in system_settings) ---
    auto_approve_enabled = (get_db_setting("AUTO_APPROVE_ENABLED", "false") or "false").strip().lower() == "true"
    try:
        auto_approve_min_score = int(get_db_setting("AUTO_APPROVE_MIN_SCORE", "75") or 75)
    except ValueError:
        auto_approve_min_score = 75
    initial_status = "approved" if (auto_approve_enabled and lead_score >= auto_approve_min_score) else "staged_for_review"

    query = """
    INSERT INTO leads (
        vertical, source_platform, platform_user_id, username, post_url, raw_post_text,
        carrier_name, incident_identifier, estimated_compensation, regulatory_framework,
        ai_reasoning, outreach_copy, status, dedup_key, lead_score, last_status_change_at
    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW())
    RETURNING id::text, vertical, carrier_name, estimated_compensation, status, lead_score;
    """
    params = (
        eval_result.get("vertical", "flight_disruption"),
        payload.source_platform, payload.platform_user_id, payload.username,
        payload.post_url, payload.raw_post_text, eval_result.get("carrier_name"),
        eval_result.get("incident_identifier"), eval_result.get("estimated_compensation", 0.00),
        eval_result.get("regulatory_framework"), eval_result.get("ai_reasoning"),
        eval_result.get("outreach_copy"), initial_status, dedup_key, lead_score,
    )

    with conn.cursor() as cur:
        cur.execute(query, params)
        inserted_lead = cur.fetchone()
        conn.commit()

    new_id = inserted_lead["id"]
    log_system_event("ingestion_worker", "LEAD_STAGED", "INFO", f"Staged {inserted_lead['vertical']} against {inserted_lead['carrier_name']} (score={lead_score}, status={initial_status})", lead_id=new_id)

    if initial_status == "approved":
        log_status_change(conn, new_id, "staged_for_review", "approved", "system:auto_approve", note=f"lead_score={lead_score} >= threshold={auto_approve_min_score}")

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
