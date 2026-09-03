import os
import io
import time
import streamlit as st
import requests
import pandas as pd
import psycopg2
from psycopg2.extras import RealDictCursor
import bcrypt
import pyotp
import qrcode
from dotenv import load_dotenv

load_dotenv()

st.set_page_config(
    page_title="Dispute Agent | Operations & Claims Desk",
    page_icon="⚖️",
    layout="wide"
)

DATABASE_URL = os.getenv("DATABASE_URL")
API_BASE = os.getenv("API_BASE_URL", "https://dispute-api-xyl7.onrender.com")

def get_connection():
    return get_db()

def get_db():
    conn = psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)
    conn.autocommit = True
    return conn

def ensure_database_schema():
    """Auto-heals missing tables and columns on startup."""
    try:
        conn = get_db()
        with conn.cursor() as cur:
            cur.execute("""
                ALTER TABLE leads
                ADD COLUMN IF NOT EXISTS vertical VARCHAR(50) DEFAULT 'flight_disruption',
                ADD COLUMN IF NOT EXISTS account_number VARCHAR(100),
                ADD COLUMN IF NOT EXISTS outage_duration_hours NUMERIC(6,2),
                ADD COLUMN IF NOT EXISTS tier_speed_tier VARCHAR(100),
                ADD COLUMN IF NOT EXISTS dispatch_attempts INT DEFAULT 0,
                ADD COLUMN IF NOT EXISTS last_dispatch_error TEXT,
                ADD COLUMN IF NOT EXISTS last_dispatch_attempt_at TIMESTAMPTZ,
                ADD COLUMN IF NOT EXISTS next_dispatch_retry_at TIMESTAMPTZ DEFAULT NOW();
            """)
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
            cur.execute("""
                CREATE TABLE IF NOT EXISTS system_settings (
                    key VARCHAR(100) PRIMARY KEY,
                    value TEXT NOT NULL,
                    category VARCHAR(50) NOT NULL,
                    description TEXT,
                    updated_at TIMESTAMPTZ DEFAULT NOW()
                );
            """)
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
            cur.execute("SELECT COUNT(*) FROM admin_users;")
            if cur.fetchone()["count"] == 0:
                default_pw = "DisputeAdmin2026!"
                hashed_pw = bcrypt.hashpw(default_pw.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')
                totp_seed = pyotp.random_base32()
                cur.execute("""
                    INSERT INTO admin_users (username, full_name, password_hash, role, is_2fa_enabled, totp_secret)
                    VALUES ('admin', 'Master Administrator', %s, 'super_admin', FALSE, %s);
                """, (hashed_pw, totp_seed))
        conn.close()
    except Exception:
        pass

ensure_database_schema()

# =====================================================================
# PUBLIC CLAIMANT INTAKE & TRACKING PORTAL (?claim_id=<UUID>)
# =====================================================================
query_params = st.query_params
claim_id_param = query_params.get("claim_id")

if claim_id_param:
    st.title("🛡️ Statutory Dispute Recovery Portal")
    st.caption(f"Secure Case Reference: `{claim_id_param}`")

    try:
        res = requests.get(f"{API_BASE}/api/v1/claims/track/{claim_id_param}", timeout=10)
        if res.status_code == 200:
            claim = res.json()
            status = claim.get("status", "staged_for_review")
            vertical = claim.get("vertical", "flight_disruption")
            carrier = claim.get("carrier_name") or "Service Provider"
            est_comp = float(claim.get("estimated_compensation") or 0.0)

            st.divider()
            c1, c2, c3, c4 = st.columns(4)
            c1.metric("Dispute Vertical", vertical.replace("_", " ").title())
            c2.metric("Target Entity", carrier)
            c3.metric("Statutory Valuation", f"${est_comp:.2f}")
            c4.metric("Current Status", status.replace("_", " ").upper())

            # SCENARIO A: INTAKE & DIGITAL OPT-IN FORM
            if status in ("staged_for_review", "approved", "contacted"):
                st.subheader("📋 Complete Your Representation Authorization")
                st.info(
                    f"**Statutory Legal Basis:** {claim.get('regulatory_framework', 'Consumer Protection Mandates')}\n\n"
                    "Dispute Agent operates on a **100% No-Win, No-Fee contingency basis**. "
                    "There are **$0 upfront costs**. Upon successful settlement, our platform contingency fee is **25%** of the recovered amount."
                )

                with st.form("form_claimant_optin"):
                    st.markdown("#### 1. Claimant Identification & Contact")
                    col_u1, col_u2 = st.columns(2)
                    with col_u1:
                        c_name = st.text_input("Full Legal Name *", value=claim.get("claimant_name") or "", placeholder="Jane Doe")
                        c_email = st.text_input("Email Address (for demand copies) *", value=claim.get("claimant_email") or "", placeholder="jane@example.com")
                    with col_u2:
                        c_phone = st.text_input("Mobile Phone (for instant SMS status alerts) *", value=claim.get("claimant_phone") or "", placeholder="+13035550199")
                        c_address = st.text_input("Mailing Address *", value=claim.get("claimant_address") or "", placeholder="123 Main St, Denver, CO 80202")

                    st.markdown("#### 2. Incident & Account Verification")
                    col_i1, col_i2 = st.columns(2)
                    with col_i1:
                        if vertical == "flight_disruption":
                            c_pnr = st.text_input("6-Character Booking Ref (PNR) *", value=claim.get("pnr") or "", placeholder="e.g., K82X9Q").upper()
                            c_acct = None
                        elif vertical == "isp_outage":
                            c_acct = st.text_input("ISP / Utility Account Number *", value=claim.get("account_number") or "", placeholder="e.g., 8497-10-9281920")
                            c_pnr = None
                        else:
                            c_pnr = None
                            c_acct = st.text_input("Lease / Account Reference", value=claim.get("account_number") or "", placeholder="Account or Property Ref")
                    with col_i2:
                        c_date = st.text_input("Date of Incident / Outage (YYYY-MM-DD) *", value=claim.get("incident_date") or "", placeholder="2026-09-01")

                    st.markdown("#### 3. Representation Agreement & E-Signature")
                    st.caption(
                        "By signing below, you authorize Dispute Agent to serve formal statutory demand packages, "
                        "communicate with the respondent's legal department on your behalf, and agree to the 25% contingency fee deducted upon successful recovery."
                    )
                    c_signature = st.text_input("Type Your Full Legal Name to E-Sign *", placeholder="Jane Doe")
                    terms_agreed = st.checkbox("I agree to the Representation Terms, Statutory Demand Filing, and SMS updates.")

                    btn_submit = st.form_submit_button("🚀 Authorize & Dispatch Statutory Demand Package")

                    if btn_submit:
                        if not (c_name and c_email and c_phone and c_signature and terms_agreed):
                            st.error("Please complete all required fields and accept the representation terms.")
                        else:
                            payload = {
                                "lead_id": claim_id_param,
                                "claimant_name": c_name.strip(),
                                "claimant_email": c_email.strip(),
                                "claimant_phone": c_phone.strip(),
                                "claimant_address": c_address.strip() if c_address else "",
                                "pnr": c_pnr,
                                "account_number": c_acct,
                                "incident_date": c_date.strip() if c_date else "",
                                "digital_signature": c_signature.strip()
                            }
                            with st.spinner("Compiling formal PDF demand and dispatching..."):
                                sub_res = requests.post(f"{API_BASE}/api/v1/claims/submit", json=payload, timeout=20)
                                if sub_res.status_code == 200:
                                    st.success("✅ Claim Authorized! Demand letter compiled and served. SMS updates activated.")
                                    st.rerun()
                                else:
                                    st.error(f"Error submitting authorization: {sub_res.text}")

            # SCENARIO B: ACTIVE TRACKING VIEW
            else:
                st.subheader("Dispute Resolution Progress")
                steps = ["Authorized", "Demand Dispatched to Legal", "Settlement Reconciled"]
                step_idx = 0
                if status == "dispatched":
                    step_idx = 1
                elif status == "settled":
                    step_idx = 2

                st.progress((step_idx + 1) / len(steps))
                st.write(f"**Current Milestone:** {steps[step_idx]}")

                if status == "dispatched":
                    st.info("📨 **Formal Demand Served:** Legal package served to respondent compliance desk. Mandatory 14-day response window active.")
                elif status == "settled":
                    recovery = float(claim.get("recovery_amount") or est_comp)
                    fee = float(claim.get("fee_collected") or (recovery * 0.25))
                    net = recovery - fee
                    st.success(
                        f"🎉 **Claim Settled!**\n\n"
                        f"- **Gross Recovered:** ${recovery:.2f}\n"
                        f"- **Platform Fee (25%):** ${fee:.2f}\n"
                        f"- **Net Payout to You:** **${net:.2f}**"
                    )
        else:
            st.error("Dispute record not found. Please check your tracking link.")
    except Exception as e:
        st.error(f"Error loading claim portal: {e}")

    st.stop()

# =====================================================================
# AUTHENTICATION & OPERATOR DESK
# =====================================================================
if "authenticated" not in st.session_state:
    st.session_state.authenticated = False
if "user_info" not in st.session_state:
    st.session_state.user_info = None
if "pending_2fa_user" not in st.session_state:
    st.session_state.pending_2fa_user = None

def render_login_screen():
    col_l, col_center, col_r = st.columns([1, 1.5, 1])
    with col_center:
        st.title("⚖️ Dispute Agent Operations Desk")
        st.caption("Secure Operator Authentication & Verification Portal")

        if st.session_state.pending_2fa_user:
            user = st.session_state.pending_2fa_user
            st.info(f"Two-Factor Authentication required for **{user['username']}**.")
            with st.form("form_2fa"):
                otp_code = st.text_input("Enter 6-Digit Authenticator Code", max_chars=6, type="password")
                if st.form_submit_button("Verify Code & Sign In"):
                    if pyotp.TOTP(user["totp_secret"]).verify(otp_code.strip()):
                        st.session_state.authenticated = True
                        st.session_state.user_info = user
                        st.session_state.pending_2fa_user = None
                        st.rerun()
                    else:
                        st.error("Invalid or expired 2FA code.")
            if st.button("← Back to Sign In"):
                st.session_state.pending_2fa_user = None
                st.rerun()
            return

        with st.form("form_login"):
            username_input = st.text_input("Username").strip().lower()
            password_input = st.text_input("Password", type="password")
            if st.form_submit_button("Sign In"):
                if not username_input or not password_input:
                    st.error("Please enter both username and password.")
                else:
                    try:
                        conn = get_db()
                        with conn.cursor() as cur:
                            cur.execute("SELECT * FROM admin_users WHERE username = %s AND is_active = TRUE;", (username_input,))
                            user_record = cur.fetchone()
                        conn.close()

                        if user_record and bcrypt.checkpw(password_input.encode('utf-8'), user_record["password_hash"].encode('utf-8')):
                            if user_record.get("is_2fa_enabled") and user_record.get("totp_secret"):
                                st.session_state.pending_2fa_user = user_record
                                st.rerun()
                            else:
                                st.session_state.authenticated = True
                                st.session_state.user_info = user_record
                                st.rerun()
                        else:
                            st.error("Invalid username or password.")
                    except Exception as e:
                        st.error(f"Database error: {e}")

if not st.session_state.authenticated:
    render_login_screen()
    st.stop()

# =====================================================================
# AUTHENTICATED OPERATOR DESK
# =====================================================================
current_user = st.session_state.user_info
user_role = current_user.get("role", "claims_agent")

with st.sidebar:
    st.markdown(f"### 👤 Logged In: `{current_user['username']}`")
    st.markdown(f"**Name:** {current_user['full_name']}")
    st.markdown(f"**Role:** {user_role.replace('_', ' ').title()}")
    st.divider()

    # Dynamic Vendor Integrations Panel (Super Admin Only)
    if user_role == "super_admin":
        st.subheader("⚙️ Vendor & Service Integrations")
        with st.expander("Configure 3rd Party APIs"):
            conn = get_db()
            with conn.cursor() as cur:
                cur.execute("SELECT key, value, category, description FROM system_settings ORDER BY category, key;")
                settings_rows = cur.fetchall()
            conn.close()

            settings_dict = {row["key"]: row["value"] for row in settings_rows}

            st.markdown("**Twilio SMS Gateway**")
            new_tw_sid = st.text_input("Twilio Account SID", value=settings_dict.get("TWILIO_ACCOUNT_SID", ""))
            new_tw_token = st.text_input("Twilio Auth Token", value=settings_dict.get("TWILIO_AUTH_TOKEN", ""), type="password")
            new_tw_phone = st.text_input("Twilio Phone Number", value=settings_dict.get("TWILIO_PHONE_NUMBER", ""))

            st.markdown("**Stripe Payouts & Settlement**")
            new_st_sec = st.text_input("Stripe Secret Key", value=settings_dict.get("STRIPE_SECRET_KEY", ""), type="password")
            new_st_pub = st.text_input("Stripe Publishable Key", value=settings_dict.get("STRIPE_PUBLISHABLE_KEY", ""))

            st.markdown("**Carrier Demand SMTP Email**")
            new_smtp_host = st.text_input("SMTP Host", value=settings_dict.get("SMTP_HOST", "smtp.gmail.com"))
            new_smtp_port = st.text_input("SMTP Port", value=settings_dict.get("SMTP_PORT", "587"))
            new_smtp_user = st.text_input("SMTP User / Email", value=settings_dict.get("SMTP_USER", ""))
            new_smtp_pass = st.text_input("SMTP Password", value=settings_dict.get("SMTP_PASS", ""), type="password")

            st.markdown("**Social Ingestion Monitoring**")
            new_subs = st.text_area("Monitored Subreddits (comma separated)", value=settings_dict.get("MONITORED_SUBREDDITS", ""))
            new_poll = st.text_input("Poll Cadence (seconds)", value=settings_dict.get("POLL_INTERVAL_SECONDS", "60"))

            if st.button("💾 Save Integration Settings"):
                conn = get_db()
                with conn.cursor() as cur:
                    updates = [
                        ("TWILIO_ACCOUNT_SID", new_tw_sid, "twilio"),
                        ("TWILIO_AUTH_TOKEN", new_tw_token, "twilio"),
                        ("TWILIO_PHONE_NUMBER", new_tw_phone, "twilio"),
                        ("STRIPE_SECRET_KEY", new_st_sec, "stripe"),
                        ("STRIPE_PUBLISHABLE_KEY", new_st_pub, "stripe"),
                        ("SMTP_HOST", new_smtp_host, "smtp"),
                        ("SMTP_PORT", new_smtp_port, "smtp"),
                        ("SMTP_USER", new_smtp_user, "smtp"),
                        ("SMTP_PASS", new_smtp_pass, "smtp"),
                        ("MONITORED_SUBREDDITS", new_subs, "monitoring"),
                        ("POLL_INTERVAL_SECONDS", new_poll, "monitoring")
                    ]
                    for k, val, cat in updates:
                        cur.execute("""
                            INSERT INTO system_settings (key, value, category, updated_at)
                            VALUES (%s, %s, %s, NOW())
                            ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = NOW();
                        """, (k, val, cat))
                conn.close()
                st.success("Integration settings saved to database!")
                st.rerun()

        st.divider()

    if st.button("🚪 Sign Out"):
        st.session_state.authenticated = False
        st.session_state.user_info = None
        st.rerun()

    tab_titles = [
        "📥 Ingestion Queue",
        "📝 Direct Intake",
        "💬 Inquiries",
        "💼 Active Claims",
        "👤 Customer Interactions",
        "🏢 Vendor Communications",
        "📥 Webhook Audit",
        "⚠️ Dead-Letter Queue",
        "📊 System Telemetry",
        "📖 Operations Manual",
        "⚙️ Super Admin"
    ]
tabs = st.tabs(tab_titles)

# --- TAB 0: INGESTION QUEUE ---
with tabs[0]:
    st.subheader("Staged Consumer Signals")
    vertical_filter = st.selectbox(
        "Filter by Vertical",
        ["All Verticals", "flight_disruption", "isp_outage", "security_deposit", "class_action"]
    )
    
    try:
        conn = get_db()
        with conn.cursor() as cur:
            query = "SELECT * FROM v_staged_leads_for_review"
            params = []
            if vertical_filter != "All Verticals":
                query += " WHERE vertical = %s"
                params.append(vertical_filter)
            query += " LIMIT 50;"
            cur.execute(query, tuple(params))
            leads = cur.fetchall()
        conn.close()

        if leads:
            df = pd.DataFrame(leads)
            st.dataframe(df[["id", "vertical", "carrier_name", "estimated_compensation", "regulatory_framework", "created_at"]])
            
            st.divider()
            st.subheader("Action Selected Dispute Lead")
            
            lead_options = [l["id"] for l in leads]
            selected_lead_id = st.selectbox("Inspect Lead ID", lead_options, key="sel_review_lead")
            selected_lead = next(l for l in leads if l["id"] == selected_lead_id)

            col_a, col_b = st.columns(2)
            with col_a:
                st.markdown(f"**Platform / User:** `{(selected_lead or {}).get('source_platform')}` / `{(selected_lead or {}).get('username')}`")
                st.markdown(f"**Respondent Entity:** `{(selected_lead or {}).get('carrier_name') or 'N/A'}`")
                st.markdown(f"**Statutory Basis:** `{(selected_lead or {}).get('regulatory_framework') or 'Consumer Protection Laws'}`")
                st.markdown(f"**Estimated Valuation:** **${float((selected_lead or {}).get('estimated_compensation') or 0):.2f}**")
                st.info(f"**AI Reasoning & Disruption Breakdown:**\n{(selected_lead or {}).get('ai_reasoning') or 'Disruption confirmed by operational rules.'}")

            with col_b:
                claim_auth_link = f"https://dispute-admin.onrender.com/?claim_id={selected_lead_id}"
                raw_copy = (selected_lead or {}).get("outreach_copy") or ""
                
                if any(p in raw_copy.lower() for p in ["dear ", "customer care", "i am writing to"]):
                    c_name = (selected_lead or {}).get("carrier_name") or "the provider"
                    c_amt = float((selected_lead or {}).get("estimated_compensation") or 0.0)
                    c_law = (selected_lead or {}).get("regulatory_framework") or "statutory protections"
                    full_outreach_proposal = (
                        f"Under {c_law}, you are entitled to claim up to ${c_amt:.2f} from {c_name} for your disruption. "
                        f"Authorize our legal desk to serve your formal demand letter here: {claim_auth_link}"
                    )
                else:
                    if claim_auth_link not in raw_copy:
                        full_outreach_proposal = f"{raw_copy} Authorize claim recovery here: {claim_auth_link}".strip()
                    else:
                        full_outreach_proposal = raw_copy

                outreach_text = st.text_area(
                    "Consumer Outreach Message (Sends to social platform / claimant)",
                    value=full_outreach_proposal,
                    height=130
                )
                
                col_btn1, col_btn2 = st.columns(2)
                
                if col_btn1.button("✅ Approve & Stage Outreach", key=f"app_{selected_lead_id}"):
                    conn = get_db()
                    with conn.cursor() as cur:
                        cur.execute("""
                            UPDATE leads 
                            SET status = 'approved', outreach_copy = %s, updated_at = NOW() 
                            WHERE id::text = %s;
                        """, (outreach_text, selected_lead_id))
                    conn.close()
                    st.success("Lead approved and queued for outreach.")
                    st.rerun()

                if col_btn2.button("❌ Dismiss / Reject", key=f"rej_{selected_lead_id}"):
                    conn = get_db()
                    with conn.cursor() as cur:
                        cur.execute("UPDATE leads SET status = 'rejected', updated_at = NOW() WHERE id::text = %s;", (selected_lead_id,))
                    conn.close()
                    st.warning("Lead dismissed and marked as rejected.")
                    st.rerun()
        else:
            st.info("No leads currently pending review in this vertical.")
    except Exception as e:
        st.error(f"Error loading review queue: {e}")

# --- TAB 1: DIRECT MANUAL INTAKE ---
with tabs[1]:
    st.subheader("✍️ Direct Manual Claim Ingestion & Scenario Builder")
    st.caption("Create a dispute scenario when a consumer contacts you directly via email, phone, or referral.")

    intake_mode = st.radio(
        "Select Ingestion Method",
        ["🤖 AI-Assisted Evaluation (Paste Client Grievance)", "📝 Explicit Field Configuration (Manual Entry)"],
        horizontal=True
    )

    if intake_mode == "🤖 AI-Assisted Evaluation (Paste Client Grievance)":
        with st.form("form_ai_manual_intake"):
            st.markdown("#### Paste Consumer Grievance Details")
            c_name_in = st.text_input("Customer Name", placeholder="e.g. David Herron")
            c_contact_in = st.text_input("Customer Contact (Email or Phone)", placeholder="e.g. dave@example.com or +13035550199")
            client_narrative = st.text_area(
                "Incident Narrative / Dispute Details *",
                placeholder="Example: My flight UA 949 from London to Denver was delayed 5 hours due to mechanical issues. United refused to pay cash compensation.",
                height=160
            )
            submit_ai_intake = st.form_submit_button("⚡ Analyze Statutory Viability & Generate Case")

            if submit_ai_intake:
                if not client_narrative or len(client_narrative.strip()) < 20:
                    st.error("Please provide sufficient narrative details regarding the disruption.")
                else:
                    with st.spinner("Analyzing statutory framework with Google Gemini..."):
                        payload = {
                            "source_platform": "direct_inbound",
                            "platform_user_id": c_contact_in or "direct_client",
                            "username": c_name_in or "Direct Client",
                            "post_url": "https://disputeagent.internal/direct-intake",
                            "raw_post_text": client_narrative.strip()
                        }
                        try:
                            eval_res = requests.post(f"{API_BASE}/api/v1/leads/evaluate", json=payload, timeout=25)
                            if eval_res.status_code == 201:
                                res_data = eval_res.json()
                                if res_data.get("status") == "staged":
                                    new_lead = res_data["lead"]
                                    lead_uuid = new_lead["id"]
                                    direct_link = f"https://dispute-admin.onrender.com/?claim_id={lead_uuid}"
                                    
                                    if c_name_in or c_contact_in:
                                        conn = get_db()
                                        with conn.cursor() as cur:
                                            cur.execute("""
                                                UPDATE leads 
                                                SET claimant_name = COALESCE(NULLIF(%s, ''), claimant_name),
                                                    claimant_email = CASE WHEN %s LIKE '%%@%%' THEN %s ELSE claimant_email END,
                                                    claimant_phone = CASE WHEN %s NOT LIKE '%%@%%' THEN %s ELSE claimant_phone END,
                                                    updated_at = NOW()
                                                WHERE id::text = %s;
                                            """, (c_name_in, c_contact_in, c_contact_in, c_contact_in, c_contact_in, lead_uuid))
                                        conn.close()

                                    st.success("✅ Case successfully evaluated and staged in database!")
                                    st.markdown(f"### Customer Authorization Link:\n`{direct_link}`")
                                    
                                    email_pitch = (
                                        f"Hi {c_name_in or 'there'},\n\n"
                                        f"We analyzed your disruption involving {new_lead.get('carrier_name')}. "
                                        f"You are entitled to statutory compensation estimated at ${float(new_lead.get('estimated_compensation') or 0):.2f}.\n\n"
                                        f"To have our legal desk compile and serve your formal statutory demand package, "
                                        f"please authorize representation using our secure form here:\n{direct_link}\n\n"
                                        f"Dispute Agent operates on a strict 100% No-Win, No-Fee contingency basis (0 upfront fees, 25% fee only upon recovery)."
                                    )
                                    st.text_area("Pre-formatted Client Outreach Message (Copy & Send)", value=email_pitch, height=180)
                                else:
                                    st.warning(f"Ineligible: {res_data.get('reason')}")
                            else:
                                st.error(f"API Error {eval_res.status_code}: {eval_res.text}")
                        except Exception as e:
                            st.error(f"Failed to communicate with API: {e}")

    else:
        with st.form("form_explicit_manual_intake"):
            st.markdown("#### Explicit Dispute Parameters")
            col_m1, col_m2 = st.columns(2)
            with col_m1:
                v_choice = st.selectbox("Dispute Vertical *", [
                    ("flight_disruption", "✈️ Air Travel Disruption (US DOT Part 260 / UK261)"),
                    ("isp_outage", "🌐 Telecom & ISP Outage (PUC Tariff SLA)"),
                    ("security_deposit", "🏠 Security Deposit Non-Compliance (Statutory Penalties)"),
                    ("class_action", "⚖️ Class Action & FTC Restitution")
                ], format_func=lambda x: x[1])[0]
                target_carrier = st.text_input("Target Entity / Vendor *", placeholder="e.g. United Airlines, Comcast, Landlord LLC")
                incident_ref = st.text_input("Incident Reference (Flight #, Account #, or Lease Property)", placeholder="e.g. UA 949 or TKT-994812")
                est_payout = st.number_input("Estimated Statutory Compensation ($) *", min_value=1.0, value=650.0, step=25.0)

            with col_m2:
                reg_citation = st.text_input("Statutory Regulatory Basis *", value="US DOT 14 CFR Part 260 / UK261")
                client_full_name = st.text_input("Customer Name", placeholder="Jane Doe")
                client_email = st.text_input("Customer Email", placeholder="jane@example.com")
                client_phone = st.text_input("Customer Phone", placeholder="+13035550199")

            dispute_reasoning = st.text_area(
                "Legal & Factual Disruption Justification *",
                value="Technical operational delay exceeding mandatory statutory thresholds without extraordinary excludable circumstances.",
                height=100
            )

            submit_explicit = st.form_submit_button("🚀 Create Dispute Case & Generate Client Link")

            if submit_explicit:
                if not target_carrier or not reg_citation:
                    st.error("Target Entity and Statutory Legal Basis are required.")
                else:
                    try:
                        conn = get_db()
                        with conn.cursor() as cur:
                            cur.execute("""
                                INSERT INTO leads (
                                    vertical, source_platform, platform_user_id, username, post_url,
                                    raw_post_text, carrier_name, incident_identifier, estimated_compensation,
                                    regulatory_framework, ai_reasoning, claimant_name, claimant_email, claimant_phone, status
                                ) VALUES (
                                    %s, 'direct_inbound', %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'staged_for_review'
                                ) RETURNING id::text;
                            """, (
                                v_choice, client_email or client_phone or "direct_intake",
                                client_full_name or "Direct Client", "https://disputeagent.internal/manual-intake",
                                dispute_reasoning, target_carrier, incident_ref, est_payout,
                                reg_citation, dispute_reasoning, client_full_name, client_email, client_phone
                            ))
                            new_case_id = cur.fetchone()["id"]
                        conn.close()

                        client_url = f"https://dispute-admin.onrender.com/?claim_id={new_case_id}"
                        st.success("✅ Dispute scenario created successfully!")
                        st.markdown(f"### Client Onboarding & Authorization URL:\n`{client_url}`")
                        
                        pitch = (
                            f"Hi {client_full_name or 'there'},\n\n"
                            f"We have staged your formal recovery claim against {target_carrier} under {reg_citation} "
                            f"(Valuation: ${est_payout:.2f}).\n\n"
                            f"To sign your representation authorization and trigger the formal legal demand letter, "
                            f"please verify your details here:\n{client_url}\n\n"
                            f"Platform terms: 100% contingency basis (0 upfront, 25% fee upon liquidated payout)."
                        )
                        st.text_area("Client Message (Ready to Email or SMS)", value=pitch, height=180)
                    except Exception as e:
                        st.error(f"Database insertion failed: {e}")

# --- TAB 2: CUSTOMER INQUIRIES ---
with tabs[2]:
    st.subheader("💬 Inbound Contact Messages & Inquiries")
    st.caption("Messages submitted through the public EasyClaim contact form.")
    try:
        conn = get_db()
        with conn.cursor() as cur:
            cur.execute("""
                SELECT id::text AS id, sender_name, sender_email, subject, message, status, created_at 
                FROM customer_inquiries ORDER BY created_at DESC LIMIT 50;
            """)
            inquiries = cur.fetchall()
        conn.close()

        if inquiries:
            df_inq = pd.DataFrame(inquiries)
            st.dataframe(df_inq[["created_at", "sender_name", "sender_email", "subject", "status"]])
            
            sel_inq_id = st.selectbox("Inspect Message", [i["id"] for i in inquiries], key="sel_inq")
            sel_inq = next(i for i in inquiries if i["id"] == sel_inq_id)
            
            st.markdown(f"**From:** {sel_inq['sender_name']} (`{sel_inq['sender_email']}`)")
            st.markdown(f"**Subject:** {sel_inq['subject']}")
            st.text_area("Message Body", value=sel_inq['message'], height=120, disabled=True)
            
            if st.button("Mark as Read / Processed", key=f"read_{sel_inq_id}"):
                conn = get_db()
                with conn.cursor() as cur:
                    cur.execute("UPDATE customer_inquiries SET status = 'processed' WHERE id::text = %s;", (sel_inq_id,))
                conn.close()
                st.success("Marked as processed.")
                st.rerun()
        else:
            st.info("No customer inquiries recorded.")
    except Exception as e:
        st.error(f"Error loading customer inquiries: {e}")

# --- TAB 3: ACTIVE CLAIMS (ENHANCED QUEUE & ARCHIVE) ---
with tabs[3]:
    st.subheader("💼 Active Claims & Outreach Tracking Ledger")
    st.caption("Track pipeline progress from queue and contact through formal dispatch and final settlement. Archive settled or unresponded claims to declutter this view.")

    status_filter = st.selectbox(
        "Filter by Lifecycle Stage",
        ["All Active Stages", "approved (Queued for Outreach)", "contacted (Outreach Dispatched)", "opted_in (Authorized by Client)", "dispatched (Served to Legal)", "settled (Recovered)"],
        key="filter_active_claims_status"
    )

    try:
        conn = get_db()
        with conn.cursor() as cur:
            query = """
                SELECT 
                    id::text AS id, vertical, carrier_name, status, source_platform,
                    username, claimant_name, claimant_email, claimant_phone, post_url,
                    outreach_copy, estimated_compensation, recovery_amount, fee_collected,
                    created_at, updated_at
                FROM leads
                WHERE status IN ('approved', 'contacted', 'opted_in', 'dispatched', 'settled')
                  AND status != 'archived'
            """
            params = []
            if status_filter != "All Active Stages":
                st_code = status_filter.split(" ")[0]
                query += " AND status = %s"
                params.append(st_code)
            query += " ORDER BY updated_at DESC LIMIT 150;"

            cur.execute(query, tuple(params))
            claims_list = cur.fetchall()
        conn.close()

        if claims_list:
            df_claims = pd.DataFrame(claims_list)

            def compute_channel(row):
                src = row.get("source_platform") or "unknown"
                if src == "reddit":
                    return f"Reddit (u/{row.get('username') or 'anonymous'})"
                elif src == "direct_inbound":
                    return f"Direct Client ({row.get('claimant_email') or row.get('claimant_phone') or 'Direct'})"
                elif src == "easyclaim_landing_page":
                    return f"Landing Page ({row.get('claimant_email') or 'Portal'})"
                return src

            df_claims["dispatch_channel"] = df_claims.apply(compute_channel, axis=1)

            m1, m2, m3, m4 = st.columns(4)
            tot_rec = df_claims["recovery_amount"].astype(float).sum()
            tot_fee = df_claims["fee_collected"].astype(float).sum()
            queued_count = len(df_claims[df_claims['status'] == 'approved'])
            contacted_count = len(df_claims[df_claims['status'] == 'contacted'])

            m1.metric("Displaying Claims", len(df_claims))
            m2.metric("Queued for Outreach", queued_count)
            m3.metric("Contacted (Pending Intake)", contacted_count)
            m4.metric("Platform Fees Recovered (25%)", f"${tot_fee:.2f}")

            display_cols = ["id", "status", "vertical", "carrier_name", "dispatch_channel", "estimated_compensation", "recovery_amount", "updated_at"]
            st.dataframe(df_claims[display_cols], use_container_width=True)

            st.markdown("### 🗂️ Declutter & Bulk Archive Management")
            active_claim_ids = [c["id"] for c in claims_list]
            claims_to_archive = st.multiselect(
                "Select Claims to Move to Archive",
                options=active_claim_ids,
                format_func=lambda x: next((f"{c['carrier_name'] or 'Provider'} ({c['vertical']}) - Status: {c['status']} - ID: {x[:8]}..." for c in claims_list if c["id"] == x), x),
                key="multiselect_archive_claims"
            )
            if st.button("📦 Archive Selected Claims", type="primary", key="btn_bulk_archive"):
                if claims_to_archive:
                    conn = get_db()
                    with conn.cursor() as cur:
                        for cid in claims_to_archive:
                            cur.execute("UPDATE leads SET status = 'archived', updated_at = NOW() WHERE id::text = %s;", (cid,))
                    conn.close()
                    st.success(f"Successfully archived {len(claims_to_archive)} claim(s).")
                    st.rerun()
                else:
                    st.warning("Please select at least one claim to archive.")
        else:
            st.info("No active claims found matching this lifecycle stage.")

        st.divider()
        st.subheader("📦 Archived Claims Vault & Restoration")
        conn = get_db()
        with conn.cursor() as cur:
            cur.execute("SELECT id::text AS id, vertical, carrier_name, status, claimant_name, claimant_email, estimated_compensation, recovery_amount, updated_at FROM leads WHERE status = 'archived' ORDER BY updated_at DESC;")
            archived_list = cur.fetchall()
        conn.close()

        if archived_list:
            df_archived = pd.DataFrame(archived_list)
            st.dataframe(df_archived[["id", "vertical", "carrier_name", "claimant_name", "estimated_compensation", "recovery_amount", "updated_at"]], use_container_width=True)

            archived_ids = df_archived["id"].tolist()
            claims_to_restore = st.multiselect("Select Archived Claims to Restore Back to Active Queue", options=archived_ids, key="multiselect_restore_claims")
            if st.button("🔄 Restore Selected to Active", type="primary", key="btn_bulk_restore"):
                if claims_to_restore:
                    conn = get_db()
                    with conn.cursor() as cur:
                        for cid in claims_to_restore:
                            cur.execute("UPDATE leads SET status = 'approved', updated_at = NOW() WHERE id::text = %s;", (cid,))
                    conn.close()
                    st.success(f"Restored {len(claims_to_restore)} claim(s) to active queue.")
                    st.rerun()
        else:
            st.info("The archive vault is currently empty.")
    except Exception as e:
        st.error(f"Error loading active claims ledger: {e}")

# --- TAB 4: CUSTOMER INTERACTIONS BY CLAIM ID ---
with tabs[4]:
    st.subheader("👤 Customer Correspondence & Verification Audit")
    st.caption("Inspect passenger onboarding data, signed authorizations, PNR references, and consumer outreach by Claim ID.")

    try:
        conn = get_db()
        with conn.cursor() as cur:
            cur.execute("SELECT id::text AS id, carrier_name, vertical, claimant_name, claimant_email, status FROM leads ORDER BY updated_at DESC LIMIT 200;")
            leads_for_cust = cur.fetchall()
        conn.close()

        if leads_for_cust:
            sel_lead_id_cust = st.selectbox(
                "Select Claim ID to Audit Customer Communications",
                options=[l["id"] for l in leads_for_cust],
                format_func=lambda x: next((f"Claim ID: {x[:8]}... | {l['carrier_name'] or 'N/A'} | Customer: {l['claimant_name'] or 'Unclaimed'} ({l['status']})" for l in leads_for_cust if l["id"] == x), x),
                key="sel_claim_cust_audit"
            )

            conn = get_db()
            with conn.cursor() as cur:
                cur.execute("SELECT * FROM leads WHERE id::text = %s;", (sel_lead_id_cust,))
                c_lead = cur.fetchone()
            conn.close()

            if c_lead:
                c1, c2 = st.columns(2)
                with c1:
                    st.markdown("#### 📋 Passenger Verification Card")
                    st.markdown(f"**Claimant Full Name:** `{c_lead.get('claimant_name') or 'Pending Intake'}`")
                    st.markdown(f"**Email Address:** `{c_lead.get('claimant_email') or 'N/A'}`")
                    st.markdown(f"**Phone Number:** `{c_lead.get('claimant_phone') or 'N/A'}`")
                    st.markdown(f"**Physical Address:** `{c_lead.get('claimant_address') or 'N/A'}`")
                    st.markdown(f"**PNR / Booking Reference:** `{c_lead.get('pnr') or 'N/A'}`")
                    st.markdown(f"**Incident Date:** `{c_lead.get('incident_date') or 'N/A'}`")
                with c2:
                    st.markdown("#### ✍️ Legal Authorization & Terms")
                    st.markdown(f"**Digital Signature:** `{c_lead.get('digital_signature') or 'Not Signed Yet'}`")
                    st.markdown(f"**Authorization Status:** `{str(c_lead.get('status')).upper()}`")
                    st.markdown(f"**Contingency Terms:** `Flat 25% Deducted Upon Recovery ($0 Upfront)`")
                    portal_url = f"https://dispute-admin.onrender.com/?claim_id={sel_lead_id_cust}"
                    st.markdown(f"**Client Portal URL:** [{portal_url}]({portal_url})")

                st.divider()
                st.markdown("#### 💬 Consumer Outreach Notice & Channel Delivery")
                st.markdown(f"**Source Ingestion Platform:** `{c_lead.get('source_platform') or 'Direct Inbound'}`")
                st.markdown(f"**Target User Handle:** `u/{c_lead.get('username') or 'N/A'}`")
                st.markdown(f"**Target Discussion URL:** [{c_lead.get('post_url') or 'Portal'}]({c_lead.get('post_url') or '#'})")
                st.text_area("Outreach Message Delivered to Passenger", value=c_lead.get('outreach_copy') or "Standard notification.", height=110, disabled=True)
        else:
            st.info("No claims found in database.")
    except Exception as e:
        st.error(f"Error loading customer interactions: {e}")

# --- TAB 5: VENDOR COMMUNICATIONS & DEMAND LETTERS ---
with tabs[5]:
    st.subheader("🏢 Vendor Demand Letters & Carrier Interaction Ledger")
    st.caption("Audit where and how statutory legal demands were served to carriers and inspect inbound vendor responses.")

    try:
        conn = get_db()
        with conn.cursor() as cur:
            cur.execute("SELECT id::text AS id, carrier_name, vertical, claimant_name, status FROM leads WHERE status IN ('dispatched', 'settled', 'opted_in', 'approved') ORDER BY updated_at DESC LIMIT 200;")
            leads_for_vendor = cur.fetchall()
        conn.close()

        if leads_for_vendor:
            sel_lead_id_ven = st.selectbox(
                "Select Claim ID to Audit Vendor Communications",
                options=[l["id"] for l in leads_for_vendor],
                format_func=lambda x: next((f"Claim ID: {x[:8]}... | Target: {l['carrier_name'] or 'Carrier'} ({l['status']})" for l in leads_for_vendor if l["id"] == x), x),
                key="sel_claim_vendor_audit"
            )

            conn = get_db()
            with conn.cursor() as cur:
                cur.execute("SELECT * FROM leads WHERE id::text = %s;", (sel_lead_id_ven,))
                v_lead = cur.fetchone()

                cur.execute("SELECT * FROM carrier_inbound_events WHERE lead_id::text = %s ORDER BY created_at DESC;", (sel_lead_id_ven,))
                vendor_events = cur.fetchall()
            conn.close()

            if v_lead:
                v1, v2 = st.columns(2)
                with v1:
                    st.markdown("#### 🎯 Target Carrier & Service Details")
                    st.markdown(f"**Respondent Carrier / Vendor:** `{v_lead.get('carrier_name') or 'N/A'}`")
                    st.markdown(f"**Vertical & Statute:** `{v_lead.get('vertical')} ({v_lead.get('regulatory_framework') or 'US DOT 14 CFR Part 260'})`")
                    st.markdown(f"**Delivery Protocol:** `Formal PDF Legal Demand served via SMTP to Legal Intake Desk`")
                    st.markdown(f"**Dispatch Lifecycle State:** `{str(v_lead.get('status')).upper()}`")
                with v2:
                    st.markdown("#### 💰 Financial Ledger & Fee Tracking")
                    r_amt = float(v_lead.get('recovery_amount') or 0.0)
                    e_amt = float(v_lead.get('estimated_compensation') or 0.0)
                    f_amt = float(v_lead.get('fee_collected') or (r_amt * 0.25))
                    st.markdown(f"**Statutory Demand Amount:** `${e_amt:.2f}`")
                    st.markdown(f"**Actual Carrier Tender:** `${r_amt:.2f}`")
                    st.markdown(f"**EasyClaim 25% Contingency Fee:** `${f_amt:.2f}`")
                    st.markdown(f"**Net Disbursed to Consumer:** `${max(0.0, r_amt - f_amt):.2f}`")

                st.divider()
                st.markdown("#### ⚖️ Formal Statutory Demand Dispatched to Carrier Legal Desk")
                carrier_n = v_lead.get('carrier_name') or 'Carrier Legal Department'
                flt_no = v_lead.get('incident_identifier') or 'Disrupted Service'
                client_n = v_lead.get('claimant_name') or 'Authorized Claimant'
                pnr_code = v_lead.get('pnr') or 'N/A'
                framework = v_lead.get('regulatory_framework') or 'US DOT 14 CFR Part 260'
                
                demand_text = (
                    f"FORMAL LEGAL DEMAND NOTICE & STATUTORY RESTITUTION FILING\n\n"
                    f"TO: Legal & Regulatory Affairs, {carrier_n}\n"
                    f"RE: Demand for Immediate Restitution under {framework}\n"
                    f"CLAIMANT: {client_n} (Booking Reference / PNR: {pnr_code})\n"
                    f"DISRUPTED SERVICE: {flt_no}\n\n"
                    f"Notice is hereby served that claimant was subjected to a qualifying disruption on flight {flt_no}. "
                    f"Pursuant to {framework}, passenger is legally entitled to full non-excludable cash restitution. "
                    f"Demand is made for immediate disbursement of tender to the designated escrow account."
                )
                st.text_area("Statutory Demand Content Served to Legal Desk", value=demand_text, height=130, disabled=True)

                st.markdown("#### 📥 Inbound Carrier Responses & Webhook Tenders")
                if vendor_events:
                    df_v_ev = pd.DataFrame(vendor_events)
                    st.dataframe(df_v_ev[["created_at", "carrier_name", "event_type", "settlement_amount", "parsed_notes"]], use_container_width=True)
                else:
                    st.info("No inbound vendor responses or settlement webhooks recorded yet for this claim.")
        else:
            st.info("No dispatched claims available for vendor review.")
    except Exception as e:
        st.error(f"Error loading vendor communications: {e}")

# --- TAB 6: WEBHOOK AUDIT ---
with tabs[6]:
    st.subheader("📥 Inbound Carrier Webhook Audit")
    st.caption("Inspect raw webhook payloads received from carriers regarding settlements and dispute updates.")

    try:
        conn = get_db()
        with conn.cursor() as cur:
            cur.execute("SELECT id::text AS id, lead_id::text AS lead_id, carrier_name, vertical, event_type, settlement_amount, parsed_notes, raw_payload, created_at FROM carrier_inbound_events ORDER BY created_at DESC LIMIT 100;")
            events = cur.fetchall()
        conn.close()

        if events:
            df_events = pd.DataFrame(events)
            st.dataframe(df_events[["id", "carrier_name", "vertical", "event_type", "settlement_amount", "created_at"]], use_container_width=True)
        else:
            st.info("No inbound webhook events recorded yet.")
    except Exception as e:
        st.error(f"Error loading webhook audit: {e}")

# --- TAB 7: DEAD-LETTER QUEUE (DLQ) ---
with tabs[7]:
    st.subheader("⚠️ Dead-Letter Queue (DLQ) & Dispatch Failures")
    st.caption("Review leads where outbound dispatch failed repeatedly and trigger manual retries.")

    try:
        conn = get_db()
        with conn.cursor() as cur:
            cur.execute("SELECT id::text AS id, vertical, carrier_name, claimant_email, dispatch_attempts, last_dispatch_error, last_dispatch_attempt_at FROM leads WHERE status = 'dispatch_failed' ORDER BY last_dispatch_attempt_at DESC;")
            dlq_leads = cur.fetchall()
        conn.close()

        if dlq_leads:
            df_dlq = pd.DataFrame(dlq_leads)
            st.dataframe(df_dlq, use_container_width=True)
        else:
            st.info("Dead-letter queue is clear. No failed dispatches found.")
    except Exception as e:
        st.error(f"Error loading DLQ: {e}")

# --- TAB 8: SYSTEM TELEMETRY & HISTORICAL AUDIT LOGS (FULL SEARCH) ---
with tabs[8]:
    st.subheader("📊 System Telemetry & Historical Audit Logs")
    st.caption("Search across the entire history of system telemetry, worker executions, email dispatches, and errors.")

    col_s1, col_s2, col_s3 = st.columns(3)
    with col_s1:
        log_level_filter = st.selectbox("Filter Log Level", ["All Levels", "INFO", "WARNING", "ERROR", "CRITICAL"], key="telemetry_level_filter")
    with col_s2:
        date_start = st.date_input("Start Date", value=None, key="telemetry_start_date")
    with col_s3:
        date_end = st.date_input("End Date", value=None, key="telemetry_end_date")

    search_keyword = st.text_input("🔍 Full-Text Search Logs (Message, Service, Lead ID, or Keyword)", key="telemetry_keyword_search")

    try:
        conn = get_db()
        with conn.cursor() as cur:
            sql = "SELECT id::text AS id, service_name, event_category, log_level, message, lead_id::text AS lead_id, metadata, created_at FROM system_audit_logs WHERE 1=1"
            sql_params = []
            if log_level_filter != "All Levels":
                sql += " AND log_level = %s"
                sql_params.append(log_level_filter)
            if date_start:
                sql += " AND created_at::date >= %s"
                sql_params.append(date_start)
            if date_end:
                sql += " AND created_at::date <= %s"
                sql_params.append(date_end)
            if search_keyword:
                sql += " AND (message ILIKE %s OR service_name ILIKE %s OR lead_id::text ILIKE %s OR metadata::text ILIKE %s)"
                kw = f"%{search_keyword}%"
                sql_params.extend([kw, kw, kw, kw])

            sql += " ORDER BY created_at DESC LIMIT 5000;"
            cur.execute(sql, tuple(sql_params))
            found_logs = cur.fetchall()
        conn.close()

        if found_logs:
            df_fl = pd.DataFrame(found_logs)
            st.metric("Total Logs Matching Query", len(df_fl))
            st.dataframe(df_fl[["created_at", "service_name", "event_category", "log_level", "message", "lead_id"]], use_container_width=True)

            sel_lid = st.selectbox("Inspect Full Metadata for Log ID", df_fl["id"].tolist(), key="sel_log_meta_id")
            selected_l = df_fl[df_fl["id"] == sel_lid].iloc[0]
            st.markdown(f"**Service:** `{selected_l['service_name']}` | **Category:** `{selected_l['event_category']}` | **Level:** `{selected_l['log_level']}`")
            st.markdown(f"**Message:** {selected_l['message']}")
            st.json(selected_l["metadata"] or {})
        else:
            st.info("No logs found matching search criteria.")
    except Exception as e:
        st.error(f"Error querying telemetry logs: {e}")


# --- TAB 7: CLAIM-SPECIFIC INTERACTION & AUDIT VIEWER (NEW) ---
with tabs[7]:
    st.subheader("🕵️ Claim-Specific Interaction & Communications Audit")
    st.caption("Decipher and inspect all customer correspondence, vendor demand letters, transmission receipts, and billing/recovery logs by Claim ID.")

    try:
        conn = get_db()
        with conn.cursor() as cur:
            cur.execute("SELECT id::text AS id, carrier_name, vertical, claimant_name, claimant_email, status FROM leads ORDER BY updated_at DESC LIMIT 200;")
            all_leads_dropdown = cur.fetchall()
        conn.close()

        if all_leads_dropdown:
            selected_lead_id = st.selectbox(
                "Select Claim ID to Audit All Interactions",
                options=[l["id"] for l in all_leads_dropdown],
                format_func=lambda x: next((f"ID: {x[:8]}... | Carrier: {l['carrier_name'] or 'N/A'} | Claimant: {l['claimant_name'] or 'Unclaimed'} ({l['status']})" for l in all_leads_dropdown if l["id"] == x), x),
                key="select_audit_claim_id"
            )

            # Fetch full lead details
            conn = get_db()
            with conn.cursor() as cur:
                cur.execute("SELECT * FROM leads WHERE id::text = %s;", (selected_lead_id,))
                lead_row = cur.fetchone()

                # Fetch associated carrier inbound events
                cur.execute("SELECT * FROM carrier_inbound_events WHERE lead_id::text = %s ORDER BY created_at DESC;", (selected_lead_id,))
                inbound_events = cur.fetchall()

                # Fetch associated audit logs
                cur.execute("SELECT * FROM system_audit_logs WHERE lead_id::text = %s ORDER BY created_at DESC;", (selected_lead_id,))
                lead_audit_logs = cur.fetchall()
            conn.close()

            if lead_row:
                c_col1, c_col2 = st.columns(2)
                with c_col1:
                    st.markdown("#### 👤 Customer & Onboarding Information")
                    st.markdown(f"**Claimant Name:** `{lead_row.get('claimant_name') or 'Pending Onboarding'}`")
                    st.markdown(f"**Email:** `{lead_row.get('claimant_email') or 'N/A'}`")
                    st.markdown(f"**Phone:** `{lead_row.get('claimant_phone') or 'N/A'}`")
                    st.markdown(f"**Address:** `{lead_row.get('claimant_address') or 'N/A'}`")
                    st.markdown(f"**PNR / Account Number:** `{lead_row.get('pnr') or lead_row.get('account_number') or 'N/A'}`")
                    st.markdown(f"**Digital Signature:** `{lead_row.get('digital_signature') or 'Not Signed Yet'}`")

                with c_col2:
                    st.markdown("#### 💰 Financials, Recovery & Billing")
                    rec_amt = float(lead_row.get('recovery_amount') or 0.0)
                    est_amt = float(lead_row.get('estimated_compensation') or 0.0)
                    fee_amt = float(lead_row.get('fee_collected') or (rec_amt * 0.25))
                    st.markdown(f"**Estimated Compensation:** `${est_amt:.2f}`")
                    st.markdown(f"**Actual Recovery Amount:** `${rec_amt:.2f}`")
                    st.markdown(f"**Platform 25% Contingency Fee:** `${fee_amt:.2f}`")
                    st.markdown(f"**Current Pipeline Status:** `{str(lead_row.get('status')).upper()}`")
                    st.markdown(f"**Regulatory Framework:** `{lead_row.get('regulatory_framework') or 'N/A'}`")

                st.divider()
                st.markdown("#### ✉️ Vendor Demand Letter & Delivery Audit")
                st.markdown(f"**Target Carrier / Vendor:** `{lead_row.get('carrier_name') or 'N/A'}`")
                st.markdown(f"**Source Post URL:** `{lead_row.get('post_url') or 'Direct Inbound'}`")
                
                st.markdown("**Outreach & Demand Verbiage:**")
                st.text_area("Recorded Outreach Copy / Statutory Notice", value=lead_row.get('outreach_copy') or "No outreach copy recorded.", height=120, disabled=True, key="audit_outreach_view")

                st.markdown("#### 📥 Vendor Responses & Inbound Webhook Events")
                if inbound_events:
                    df_in_ev = pd.DataFrame(inbound_events)
                    st.dataframe(df_in_ev[["created_at", "event_type", "carrier_name", "settlement_amount", "parsed_notes"]], use_container_width=True)
                else:
                    st.info("No inbound responses or settlement webhooks recorded yet for this Claim ID.")

                st.markdown("#### 📋 Associated System Audit Trails & Dispatch Logs")
                if lead_audit_logs:
                    df_l_logs = pd.DataFrame(lead_audit_logs)
                    st.dataframe(df_l_logs[["created_at", "service_name", "event_category", "log_level", "message"]], use_container_width=True)
                else:
                    st.info("No system audit logs recorded specifically for this Claim ID.")
            else:
                st.warning("Selected claim record could not be loaded from database.")
        else:
            st.info("No claims available in database.")
    except Exception as e:
        st.error(f"Error loading claim interaction viewer: {e}")


# --- TAB 7: OPERATIONS MANUAL ---
with tabs[7]:
    st.header("📖 Dispute Agent Platform: Complete Operations Manual")
    st.caption("Standard Operating Procedures, Claim Lifecycle Guidelines, and Troubleshooting Reference.")

    with st.expander("1. Platform Overview & Plain-English Purpose", expanded=True):
        st.markdown("""
        **What Dispute Agent / EasyClaim Does:**
        Corporate providers (airlines, broadband providers, and landlords) frequently fail to fulfill statutory obligations when disruptions occur. State, federal, and international consumer protection statutes mandate monetary compensation, statutory interest, and liquidated penalties for these failures.

        Dispute Agent operates as an autonomous recovery engine that:
        1. **Detects** consumer disruptions across social platforms or direct intake channels.
        2. **Quantifies** statutory restitution using Google Gemini AI and authoritative legal citations.
        3. **Onboards** claimants via an automated contingency fee authorization workflow (0 upfront fees, 25% fee only upon recovery).
        4. **Generates & Dispatches** verified ReportLab PDF demand letters to carrier legal desks.
        5. **Reconciles** carrier settlements and executes automated contingency fee accounting.
        """)

    with st.expander("2. End-to-End Claim Lifecycle & Status Flow", expanded=True):
        st.markdown("""
        Every claim in the database moves through a strict lifecycle:

        ```
        [staged_for_review] ──▶ [approved] ──▶ [contacted] ──▶ [opted_in] ──▶ [dispatched] ──▶ [settled]
                 │                                                │
                 └──▶ [rejected]                                  └──▶ [dispatch_failed (DLQ)]
        ```

        * **`staged_for_review`**: 
          * *What it means:* A new disruption has been detected or submitted. The AI has calculated the legal basis and estimated valuation, but no outreach has been sent yet.
          * *Action required:* Review the lead in **Tab 0 (`Ingestion Queue`)**, ensure the outreach copy is consumer-directed, and click **Approve** or **Dismiss**.
        * **`approved`**: 
          * *What it means:* An operator has approved the outreach text. The claim is queued for notification.
        * **`contacted`**: 
          * *What it means:* The claimant has been sent their unique authorization URL (`/?claim_id=<UUID>`).
        * **`opted_in`**: 
          * *What it means:* The consumer opened the link, entered their contact information, signed the contingency agreement digitally, and authorized EasyClaim to represent them.
        * **`dispatched`**: 
          * *What it means:* The system automatically compiled the ReportLab statutory PDF demand letter, served it to the respondent's legal department via email/SMTP, and sent a confirmation SMS to the claimant's phone. A 14-day statutory compliance countdown is now active.
        * **`settled`**: 
          * *What it means:* The airline, utility, or landlord approved the claim. The gross settlement and 25% platform contingency fee have been recorded, and an alert SMS has been dispatched to the consumer.
        * **`rejected`**: 
          * *What it means:* The claim was dismissed as non-viable or outside the statutory scope.
        * **`dispatch_failed` (DLQ)**: 
          * *What it means:* An email transmission error occurred 5 times consecutively. The claim is held safely in the Dead-Letter Queue for operator remediation.
        """)

    with st.expander("3. Step-by-Step Operator Instructions (From Ingest to Payout)", expanded=True):
        st.markdown("""
        ### Step 1: Handling Inbound Consumer Complaints
        * **Option A: Social Media Ingestion (Tab 0)**
          * Open **Tab 0 (`Ingestion Queue`)**.
          * Select any staged claim from the dropdown.
          * Review the **Respondent Entity**, **Estimated Valuation**, and the **AI Statutory Reasoning**.
          * Verify that the outreach text explains what the company owes them. Click **Approve & Stage Outreach**.
        * **Option B: Direct Client Inbound (Tab 1)**
          * If a customer contacts you directly via email or phone, open **Tab 1 (`Direct Manual Intake`)**.
          * Paste their complaint into the **AI-Assisted** box and click **Analyze Statutory Viability**.
          * Copy the generated client link (`/?claim_id=<UUID>`) and send it directly to the consumer.
        * **Option C: Public Website Submissions (EasyClaim Landing Page)**
          * Consumers who visit `https://dispute-api-xyl7.onrender.com/#claim` can submit their details and digital signature directly.
          * These claims are automatically created with status `opted_in`, their PDF demand is dispatched to the carrier legal desk immediately, and a confirmation SMS is sent to their phone without requiring manual intervention.

        ### Step 2: Customer Authorization & E-Signature
        * The consumer opens their personalized URL.
        * They see the target entity, estimated payout, and statutory protection laws.
        * They confirm their full legal name, email, phone number, and incident reference (Flight PNR or Account Number).
        * They review the 25% contingency agreement ($0 upfront costs) and type their name to digitally e-sign.
        * Once submitted, the system triggers the ReportLab PDF compilation and SMTP dispatch.

        ### Step 3: Carrier Demand Dispatch & Compliance Tracking
        * Once the status reaches `dispatched`, open **Tab 3 (`Active Claims`)** to monitor the case portfolio.
        * The demand letter is on file with the respondent company's legal department with a formal 14 business day response notice.

        ### Step 4: Settlement Reconciliation & 25% Fee Collection
        * When a carrier approves compensation, they send a webhook notice or payout tender.
        * If automated, the webhook updates the status to `settled`, computes $\\text{recovery} \\times 0.25$, and logs the contingency fee.
        * For manual settlement checks, enter the gross amount in the settlement endpoint to update the case and dispatch a settlement notification SMS to the claimant.
        """)

    with st.expander("4. The Four Supported Dispute Verticals & Legal Rules", expanded=False):
        st.markdown("""
        | Vertical | Regulatory Framework | Statutory Mandate / Consumer Entitlement |
        |---|---|---|
        | **✈️ Flight Disruptions** (`flight_disruption`) | **US DOT 14 CFR Part 260**<br>**UK261 / EU261** | • Cash refund for domestic flights delayed >3 hrs or canceled where alternative flight is refused.<br>• Flat cash compensation of **£220 to £520 / €250 to €600** ($300–$650 USD) for delays >3 hrs departing the UK/EU due to carrier fault. |
        | **🌐 Telecom & ISP Outages** (`isp_outage`) | **State PUC Utility Tariffs**<br>**FCC SLA Regulations** | Mandatory prorated bill credits and liquidated statutory service outage compensation when broadband or phone service drops for >4 to 24 continuous hours. |
        | **🏠 Security Deposits** (`security_deposit`) | **State Residential Tenancy Acts**<br>(e.g., C.R.S. § 38-12-103) | Landlords must return deposits or provide an itemized deduction statement within 30 to 60 days of lease termination. Failure forfeits all deductions and incurs **2x to 3x liquidated statutory damages**. |
        | **⚖️ Class Actions** (`class_action`) | **Court Settlement Orders**<br>**FTC Restitution Mandates** | Liquidated restitution payments from court-approved common settlement funds for qualifying consumer claims. |
        """)

    with st.expander("5. Managing Website Messages & Direct Inquiries", expanded=False):
        st.markdown("""
        * Visitors who submit messages through the contact form at `https://dispute-api-xyl7.onrender.com/#contact` are routed directly to **Tab 2 (`Customer Inquiries`)**.
        * Open **Tab 2** to inspect the sender's name, email, subject line, and full message text.
        * After responding to the customer, click **Mark as Read / Processed** to keep the queue organized.
        """)

    with st.expander("6. Troubleshooting & Dead-Letter Queue (DLQ) Remediation", expanded=False):
        st.markdown("""
        * **When does a claim enter the DLQ?**
          * If an airline legal email address rejects the demand package or the SMTP server experiences a timeout, the background worker will retry automatically up to 5 times using exponential backoff ($2^1, 2^2, 2^3, 2^4, 2^5$ minutes).
          * If all 5 attempts fail, the claim enters **Tab 5 (`Dead-Letter Queue`)** as `dispatch_failed`.
        * **How to remediate a DLQ record:**
          1. Open **Tab 5 (`Dead-Letter Queue`)**.
          2. Inspect the **Last Dispatch Error** column to see why the email failed (e.g., invalid carrier address or connection timeout).
          3. Select the claim from the dropdown.
          4. Click **🔄 Force Immediate Re-Dispatch** to clear the attempt counter and trigger an immediate re-send.
        """)

    with st.expander("7. System Administration, API Keys & 2FA Security", expanded=False):
        st.markdown("""
        * **Vendor Integration Settings (Super Admin Only):**
          * In the left sidebar, Super Admins can expand **⚙️ Vendor & Service Integrations**.
          * You can update your **Twilio SMS keys**, **Stripe API secrets**, **SMTP Email credentials**, and **Monitored Subreddits** dynamically.
          * Clicking **Save Integration Settings** updates the database immediately with no code deployment required.
        * **Two-Factor Authentication (2FA):**
          * Every operator can secure their account in the sidebar under **🔐 Two-Factor Security**.
          * Click **Enable 2FA Authenticator**, scan the QR code using Google Authenticator, 1Password, or Authy, enter the 6-digit verification code, and click **Activate 2FA**.
        * **Provisioning New Team Logins (Super Admin Only):**
          * Super Admins have access to **Tab 8 (`User Administration`)** to create accounts for team members.
          * Assign roles based on access needs: `claims_agent` (review only), `claims_manager` (review + DLQ actions), `auditor` (read-only), or `super_admin` (full system access).
        """)

# --- TAB 8: USER ADMINISTRATION (SUPER ADMIN ONLY) ---
if user_role == "super_admin" and len(tabs) > 8:
    with tabs[8]:
        st.subheader("👥 User Management & Role Provisioning")
        col_new_user, col_user_list = st.columns([1, 1.4])

        with col_new_user:
            st.markdown("#### Provision New User")
            with st.form("form_create_user"):
                new_username = st.text_input("Username").strip().lower()
                new_full_name = st.text_input("Full Name")
                new_password = st.text_input("Temporary Password", type="password")
                new_role = st.selectbox("Assign Role", [
                    ("claims_agent", "Claims Agent (Queue Review & Outreach)"),
                    ("claims_manager", "Claims Manager (Queue & DLQ Control)"),
                    ("auditor", "Auditor (Read-Only Telemetry)"),
                    ("super_admin", "Super Admin (Full Access & User Admin)")
                ], format_func=lambda x: x[1])[0]

                if st.form_submit_button("Create User Account"):
                    if not new_username or not new_password or not new_full_name:
                        st.error("All fields are required.")
                    else:
                        try:
                            hashed = bcrypt.hashpw(new_password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')
                            seed = pyotp.random_base32()
                            conn = get_db()
                            with conn.cursor() as cur:
                                cur.execute("""
                                    INSERT INTO admin_users (username, full_name, password_hash, role, is_2fa_enabled, totp_secret)
                                    VALUES (%s, %s, %s, %s, FALSE, %s);
                                """, (new_username, new_full_name, hashed, new_role, seed))
                            conn.close()
                            st.success(f"User `{new_username}` created.")
                            st.rerun()
                        except psycopg2.IntegrityError:
                            st.error(f"Username `{new_username}` already exists.")
                        except Exception as e:
                            st.error(f"Error: {e}")

        with col_user_list:
            st.markdown("#### Active System Users")
            try:
                conn = get_db()
                with conn.cursor() as cur:
                    cur.execute("SELECT id::text AS id, username, full_name, role, is_2fa_enabled, is_active, created_at FROM admin_users ORDER BY created_at ASC;")
                    all_users = cur.fetchall()
                conn.close()
                if all_users:
                    st.dataframe(pd.DataFrame(all_users)[["username", "full_name", "role", "is_2fa_enabled", "is_active"]])
            except Exception as e:
                st.error(f"Error: {e}")
