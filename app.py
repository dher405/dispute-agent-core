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
    page_title="Dispute Agent | Claims & Recovery Portal",
    page_icon="⚖️",
    layout="wide"
)

DATABASE_URL = os.getenv("DATABASE_URL")
API_BASE = os.getenv("API_BASE_URL", "https://dispute-api-xyl7.onrender.com")

def get_db():
    conn = psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)
    conn.autocommit = True
    return conn

def ensure_auth_schema():
    try:
        conn = get_db()
        with conn.cursor() as cur:
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

ensure_auth_schema()

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

            # TOP METRICS BANNER
            st.divider()
            c1, c2, c3, c4 = st.columns(4)
            c1.metric("Dispute Vertical", vertical.replace("_", " ").title())
            c2.metric("Target Entity", carrier)
            c3.metric("Statutory Valuation", f"${est_comp:.2f}")
            c4.metric("Current Status", status.replace("_", " ").upper())

            # SCENARIO A: PENDING CONSUMER AUTHORIZATION
            if status in ("staged_for_review", "approved", "contacted"):
                st.subheader("📋 Complete Your Representation Authorization")
                st.info(
                    f"**Statutory Basis:** {claim.get('regulatory_framework', 'Consumer Protection Mandates')}\n\n"
                    "Dispute Agent operates on a **100% No-Win, No-Fee contingency basis**. "
                    "There are **$0 upfront fees**. If we recover compensation on your behalf, our standard platform contingency fee is **25%** of the settled amount."
                )

                with st.form("form_claimant_optin"):
                    st.markdown("#### 1. Claimant Identification & Contact")
                    col_u1, col_u2 = st.columns(2)
                    with col_u1:
                        c_name = st.text_input("Full Legal Name *", placeholder="Jane Doe")
                        c_email = st.text_input("Email Address (for demand copies) *", placeholder="jane@example.com")
                    with col_u2:
                        c_phone = st.text_input("Mobile Phone (for real-time SMS status alerts) *", placeholder="+13035550199")
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

            # SCENARIO B: ACTIVE TRACKING TIMELINE
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
# AUTHENTICATION & OPERATOR DESK (Standard Admin View)
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
# AUTHENTICATED OPERATOR TABS
# =====================================================================
current_user = st.session_state.user_info
user_role = current_user.get("role", "claims_agent")

with st.sidebar:
    st.markdown(f"### 👤 Logged In: `{current_user['username']}`")
    st.markdown(f"**Name:** {current_user['full_name']}")
    st.markdown(f"**Role:** {user_role.replace('_', ' ').title()}")
    st.divider()
    if st.button("🚪 Sign Out"):
        st.session_state.authenticated = False
        st.session_state.user_info = None
        st.rerun()

tab_titles = ["📥 Ingestion Queue", "💼 Active Claims", "📡 Webhook Audit", "⚠️ Dead-Letter Queue"]
if user_role == "super_admin":
    tab_titles.append("👥 User Administration")

tabs = st.tabs(tab_titles)

with tabs[0]:
    st.subheader("Staged Consumer Signals")
    conn = get_db()
    with conn.cursor() as cur:
        cur.execute("SELECT * FROM v_staged_leads_for_review LIMIT 50;")
        leads = cur.fetchall()
    conn.close()
    if leads:
        df = pd.DataFrame(leads)
        st.dataframe(df[["id", "vertical", "carrier_name", "estimated_compensation", "regulatory_framework", "created_at"]], use_container_width=True)
    else:
        st.info("No leads pending review.")

with tabs[1]:
    st.subheader("Active & Settled Claims Ledger")
    conn = get_db()
    with conn.cursor() as cur:
        cur.execute("SELECT id::text AS id, vertical, carrier_name, claimant_name, recovery_amount, fee_collected, status FROM leads WHERE status IN ('opted_in', 'dispatched', 'settled') ORDER BY updated_at DESC LIMIT 100;")
        claims_list = cur.fetchall()
    conn.close()
    if claims_list:
        st.dataframe(pd.DataFrame(claims_list), use_container_width=True)
    else:
        st.info("No active claims.")

with tabs[2]:
    st.subheader("Inbound Carrier Telemetry Events")
    conn = get_db()
    with conn.cursor() as cur:
        cur.execute("SELECT id::text, carrier_name, vertical, event_type, settlement_amount, parsed_notes, created_at FROM carrier_inbound_events ORDER BY created_at DESC LIMIT 50;")
        events = cur.fetchall()
    conn.close()
    if events:
        st.dataframe(pd.DataFrame(events), use_container_width=True)
    else:
        st.info("No webhook events logged.")

with tabs[3]:
    st.subheader("⚠️ Dead-Letter Queue (DLQ)")
    conn = get_db()
    with conn.cursor() as cur:
        cur.execute("SELECT id::text AS id, carrier_name, claimant_name, dispatch_attempts, last_dispatch_error, status FROM leads WHERE status='dispatch_failed' OR last_dispatch_error IS NOT NULL;")
        dlq = cur.fetchall()
    conn.close()
    if dlq:
        st.dataframe(pd.DataFrame(dlq), use_container_width=True)
    else:
        st.success("✅ Dead-Letter Queue is clear.")

if user_role == "super_admin" and len(tabs) > 4:
    with tabs[4]:
        st.subheader("👥 User Management & Role Provisioning")
        conn = get_db()
        with conn.cursor() as cur:
            cur.execute("SELECT id::text AS id, username, full_name, role, is_2fa_enabled, is_active FROM admin_users ORDER BY created_at ASC;")
            users = cur.fetchall()
        conn.close()
        st.dataframe(pd.DataFrame(users), use_container_width=True)
