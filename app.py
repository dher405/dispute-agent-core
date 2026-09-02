import os
import io
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
    page_title="Dispute Agent | Claims & Operations Desk",
    page_icon="⚖️",
    layout="wide"
)

DATABASE_URL = os.getenv("DATABASE_URL")
API_BASE = os.getenv("API_BASE_URL", "https://dispute-api-xyl7.onrender.com")

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
                        c_name = st.text_input("Full Legal Name *", placeholder="Jane Doe")
                        c_email = st.text_input("Email Address (for demand copies) *", placeholder="jane@example.com")
                    with col_u2:
                        c_phone = st.text_input("Mobile Phone (for instant SMS status alerts) *", placeholder="+13035550199")
                        c_address = st.text_input("Mailing Address *", placeholder="123 Main St, Denver, CO 80202")

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
                            c_acct = st.text_input("Lease / Account Reference", placeholder="Account or Property Ref")
                    with col_i2:
                        c_date = st.text_input("Date of Incident / Outage (YYYY-MM-DD) *", placeholder="2026-09-01")

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

tab_titles = ["📥 Ingestion Queue", "💼 Active Claims", "📡 Webhook Audit", "⚠️ Dead-Letter Queue", "📖 Operations Manual"]
if user_role == "super_admin":
    tab_titles.append("👥 User Administration")

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
                st.markdown(f"**Platform / User:** `{selected_lead.get('source_platform')}` / `{selected_lead.get('username')}`")
                st.markdown(f"**Respondent Entity:** `{selected_lead.get('carrier_name') or 'N/A'}`")
                st.markdown(f"**Statutory Basis:** `{selected_lead.get('regulatory_framework') or 'Consumer Protection Laws'}`")
                st.markdown(f"**Estimated Valuation:** **${float(selected_lead.get('estimated_compensation') or 0):.2f}**")
                st.info(f"**AI Reasoning & Disruption Breakdown:**\n{selected_lead.get('ai_reasoning') or 'Disruption confirmed by operational rules.'}")

            with col_b:
                claim_auth_link = f"https://dispute-admin.onrender.com/?claim_id={selected_lead_id}"
                raw_copy = selected_lead.get("outreach_copy") or ""
                
                # Defensive check: if copy starts like a vendor letter, reconstruct consumer copy
                if any(p in raw_copy.lower() for p in ["dear ", "customer care", "i am writing to"]):
                    c_name = selected_lead.get("carrier_name") or "the provider"
                    c_amt = float(selected_lead.get("estimated_compensation") or 0.0)
                    c_law = selected_lead.get("regulatory_framework") or "statutory protections"
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

# --- TAB 1: ACTIVE CLAIMS ---
with tabs[1]:
    st.subheader("Active & Settled Claims Ledger")
    try:
        conn = get_db()
        with conn.cursor() as cur:
            cur.execute("""
                SELECT id::text AS id, vertical, carrier_name, claimant_name, recovery_amount, fee_collected, status 
                FROM leads WHERE status IN ('opted_in', 'dispatched', 'settled') ORDER BY updated_at DESC LIMIT 100;
            """)
            claims_list = cur.fetchall()
        conn.close()
        if claims_list:
            df_claims = pd.DataFrame(claims_list)
            m1, m2, m3 = st.columns(3)
            tot_rec = df_claims["recovery_amount"].astype(float).sum()
            tot_fee = df_claims["fee_collected"].astype(float).sum()
            m1.metric("Active Claims", len(df_claims))
            m2.metric("Total Recovered", f"${tot_rec:.2f}")
            m3.metric("Platform Fees (25%)", f"${tot_fee:.2f}")
            st.dataframe(df_claims)
        else:
            st.info("No active claims.")
    except Exception as e:
        st.error(f"Error: {e}")

# --- TAB 2: WEBHOOK AUDIT ---
with tabs[2]:
    st.subheader("Inbound Carrier Telemetry Events")
    try:
        conn = get_db()
        with conn.cursor() as cur:
            cur.execute("SELECT id::text, carrier_name, vertical, event_type, settlement_amount, parsed_notes, created_at FROM carrier_inbound_events ORDER BY created_at DESC LIMIT 50;")
            events = cur.fetchall()
        conn.close()
        if events:
            st.dataframe(pd.DataFrame(events))
        else:
            st.info("No webhook events logged.")
    except Exception as e:
        st.error(f"Error: {e}")

# --- TAB 3: DLQ ---
with tabs[3]:
    st.subheader("⚠️ Dead-Letter Queue (DLQ)")
    try:
        conn = get_db()
        with conn.cursor() as cur:
            cur.execute("""
                SELECT id::text AS id, carrier_name, claimant_name, 
                       COALESCE(dispatch_attempts, 0) AS dispatch_attempts, 
                       last_dispatch_error, status 
                FROM leads 
                WHERE status='dispatch_failed' OR last_dispatch_error IS NOT NULL;
            """)
            dlq = cur.fetchall()
        conn.close()
        if dlq:
            st.dataframe(pd.DataFrame(dlq))
            if user_role in ("super_admin", "claims_manager"):
                sel_dlq = st.selectbox("Select Stalled Claim to Re-Queue", [d["id"] for d in dlq])
                if st.button("🔄 Force Immediate Re-Dispatch", key=f"dlq_retry_{sel_dlq}"):
                    conn = get_db()
                    with conn.cursor() as cur:
                        cur.execute("""
                            UPDATE leads 
                            SET status='opted_in', dispatch_attempts=0, 
                                last_dispatch_error=NULL, next_dispatch_retry_at=NOW(), 
                                updated_at=NOW() 
                            WHERE id::text=%s;
                        """, (sel_dlq,))
                    conn.close()
                    st.success("Claim requeued for carrier dispatch.")
                    st.rerun()
        else:
            st.success("✅ Dead-Letter Queue is clear.")
    except Exception as e:
        st.error(f"Error: {e}")

# --- TAB 4: OPERATIONS MANUAL ---
with tabs[4]:
    st.header("📖 Dispute Agent Platform: Field Manual & RBAC Guide")
    with st.expander("1. System Purpose & Plain-English Overview", expanded=True):
        st.markdown("""
        **What Dispute Agent Does:**
        When corporations cause non-excludable consumer disruptions (e.g., flight delays, multi-day internet outages, withheld security deposits), statutory regulations mandate liquidated cash compensation or bill credits.
        Dispute Agent automates detection, evaluation, formal PDF demand compilation, carrier dispatch, and 25% fee reconciliation.
        """)
    with st.expander("2. Supported Dispute Verticals & Laws", expanded=False):
        st.markdown("""
        * **✈️ Flight Disruptions (`flight_disruption`)**: US DOT 14 CFR Part 260 & UK261/EU261.
        * **🌐 Telecom & ISP Outages (`isp_outage`)**: State PUC Tariffs & FCC Mandates.
        * **🏠 Security Deposit Non-Compliance (`security_deposit`)**: State Tenancy Codes (e.g., CRS 38-12-103) with 2x to 3x liquidated penalties.
        * **⚖️ Class Action Restitution (`class_action`)**: Active court-approved restitution pools.
        """)
    with st.expander("3. Role Permissions (RBAC)", expanded=False):
        st.markdown("""
        * `super_admin`: Full authority, claim actions, DLQ overrides, Vendor configuration, and User Administration.
        * `claims_manager`: Review queue, approvals, and DLQ re-dispatch actions.
        * `claims_agent`: Review and approve/reject social signals.
        * `auditor`: Read-only access to portfolio ledgers and webhook telemetry.
        """)

# --- TAB 5: USER ADMINISTRATION (SUPER ADMIN ONLY) ---
if user_role == "super_admin" and len(tabs) > 5:
    with tabs[5]:
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
