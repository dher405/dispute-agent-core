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
    page_title="Dispute Agent | Operations & Claims Desk",
    page_icon="⚖️",
    layout="wide"
)

DATABASE_URL = os.getenv("DATABASE_URL")
API_BASE = os.getenv("API_BASE_URL", "https://dispute-api-xyl7.onrender.com")

def get_db():
    conn = psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)
    conn.autocommit = True
    return conn

# Auto-heal: Ensure admin_users table exists whenever Streamlit starts
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

# =========================================================
# PUBLIC CLAIMANT TRACKING VIEW (?claim_id=<UUID>)
# =========================================================
query_params = st.query_params
claim_id_param = query_params.get("claim_id")

if claim_id_param:
    st.title("🛡️ Dispute Claim Resolution Portal")
    st.caption(f"Tracking Case Reference: `{claim_id_param}`")

    try:
        res = requests.get(f"{API_BASE}/api/v1/claims/track/{claim_id_param}", timeout=10)
        if res.status_code == 200:
            claim = res.json()
            st.divider()
            col1, col2, col3, col4 = st.columns(4)
            col1.metric("Vertical", (claim.get("vertical") or "Dispute").replace("_", " ").title())
            col2.metric("Target Entity", claim.get("carrier_name") or "Entity")
            col3.metric("Current Status", (claim.get("status") or "Pending").upper())
            
            payout = float(claim.get("recovery_amount") or claim.get("estimated_compensation") or 0)
            col4.metric("Settlement Amount", f"${payout:.2f}")

            st.subheader("Statutory Legal Framework")
            st.info(claim.get("regulatory_framework") or "Statutory consumer protection laws apply.")

            status = claim.get("status")
            st.subheader("Dispute Timeline")
            steps = ["Staged", "Opted In", "Demand Dispatched", "Settled"]
            step_idx = 0
            if status == "opted_in":
                step_idx = 1
            elif status == "dispatched":
                step_idx = 2
            elif status == "settled":
                step_idx = 3

            st.progress((step_idx + 1) / len(steps))
            st.write(f"**Current Milestone:** {steps[step_idx]}")

            if status == "settled":
                fee = float(claim.get("fee_collected") or 0)
                client_net = payout - fee
                st.success(f"🎉 **Dispute Resolved!** Net disbursement: **${client_net:.2f}** (after statutory contingency fee: ${fee:.2f}).")
            elif status in ("staged_for_review", "approved"):
                st.warning("Action Required: Please complete authorization to proceed with formal recovery.")
        else:
            st.error("Dispute record not found. Please verify your claim reference URL.")
    except Exception as e:
        st.error(f"Error fetching tracking data: {e}")

    st.stop()

# =========================================================
# AUTHENTICATION & SESSION STATE MANAGEMENT
# =========================================================
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
                btn_verify = st.form_submit_button("Verify Code & Sign In")
                
                if btn_verify:
                    totp = pyotp.TOTP(user["totp_secret"])
                    if totp.verify(otp_code.strip()):
                        st.session_state.authenticated = True
                        st.session_state.user_info = user
                        st.session_state.pending_2fa_user = None
                        st.success("Authentication successful.")
                        st.rerun()
                    else:
                        st.error("Invalid or expired 2FA code.")
            
            if st.button("← Back to Username/Password"):
                st.session_state.pending_2fa_user = None
                st.rerun()
            return

        with st.form("form_login"):
            username_input = st.text_input("Username").strip().lower()
            password_input = st.text_input("Password", type="password")
            btn_login = st.form_submit_button("Sign In")

            if btn_login:
                if not username_input or not password_input:
                    st.error("Please enter both username and password.")
                    return

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
                            st.success("Login successful.")
                            st.rerun()
                    else:
                        st.error("Invalid username or password.")
                except Exception as e:
                    st.error(f"Database error: {e}")

if not st.session_state.authenticated:
    render_login_screen()
    st.stop()

# =========================================================
# AUTHENTICATED OPERATOR DESK
# =========================================================
current_user = st.session_state.user_info
user_role = current_user.get("role", "claims_agent")

with st.sidebar:
    st.markdown(f"### 👤 Logged In: `{current_user['username']}`")
    st.markdown(f"**Name:** {current_user['full_name']}")
    
    role_badge = {
        "super_admin": "🔴 Super Admin",
        "claims_manager": "🟠 Claims Manager",
        "claims_agent": "🔵 Claims Agent",
        "auditor": "🟢 Auditor"
    }.get(user_role, user_role)
    st.markdown(f"**Role:** {role_badge}")
    
    st.divider()
    st.subheader("🔐 Two-Factor Security")

    conn = get_db()
    with conn.cursor() as cur:
        cur.execute("SELECT is_2fa_enabled, totp_secret FROM admin_users WHERE id = %s;", (current_user["id"],))
        latest_auth = cur.fetchone()
    conn.close()

    is_2fa_active = latest_auth["is_2fa_enabled"] if latest_auth else False
    totp_secret = latest_auth["totp_secret"] if latest_auth else pyotp.random_base32()

    if not is_2fa_active:
        st.warning("2FA is **Disabled**.")
        with st.expander("Enable 2FA Authenticator"):
            totp_uri = pyotp.totp.TOTP(totp_secret).provisioning_uri(
                name=current_user['username'],
                issuer_name="DisputeAgent"
            )
            qr = qrcode.QRCode(box_size=4, border=2)
            qr.add_data(totp_uri)
            qr.make(fit=True)
            img = qr.make_image(fill_color="black", back_color="white")
            img_buf = io.BytesIO()
            img.save(img_buf, format="PNG")
            
            st.image(img_buf.getvalue(), caption="Scan with Authenticator App")
            st.code(totp_secret, language="text")
            
            verify_token = st.text_input("Enter 6-Digit Code to Activate", max_chars=6, key="act_2fa")
            if st.button("Activate 2FA"):
                if pyotp.TOTP(totp_secret).verify(verify_token.strip()):
                    conn = get_db()
                    with conn.cursor() as cur:
                        cur.execute("UPDATE admin_users SET is_2fa_enabled = TRUE, totp_secret = %s WHERE id = %s;", (totp_secret, current_user["id"]))
                    conn.close()
                    st.success("2FA successfully enabled!")
                    st.rerun()
                else:
                    st.error("Invalid token. 2FA not enabled.")
    else:
        st.success("2FA is **Active**.")
        if st.button("Disable 2FA"):
            conn = get_db()
            with conn.cursor() as cur:
                cur.execute("UPDATE admin_users SET is_2fa_enabled = FALSE WHERE id = %s;", (current_user["id"],))
            conn.close()
            st.warning("2FA has been disabled.")
            st.rerun()

    st.divider()
    if st.button("🚪 Sign Out"):
        st.session_state.authenticated = False
        st.session_state.user_info = None
        st.rerun()

tab_titles = ["📥 Ingestion Queue", "💼 Active Claims", "📡 Webhook Audit", "⚠️ Dead-Letter Queue"]
if user_role == "super_admin":
    tab_titles.append("👥 User Administration")

tabs = st.tabs(tab_titles)
tab_review = tabs[0]
tab_active = tabs[1]
tab_webhooks = tabs[2]
tab_dlq = tabs[3]
tab_users = tabs[4] if user_role == "super_admin" else None

# --- TAB 1: INGESTION QUEUE ---
with tab_review:
    st.subheader("Staged Consumer Signals")
    vertical_filter = st.selectbox("Filter Vertical", ["All Verticals", "flight_disruption", "isp_outage", "security_deposit", "class_action"])
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
            st.dataframe(df[["id", "vertical", "carrier_name", "estimated_compensation", "regulatory_framework", "created_at"]], use_container_width=True)
            
            if user_role != "auditor":
                st.divider()
                st.subheader("Action Selected Dispute Lead")
                sel_id = st.selectbox("Inspect Lead ID", [l["id"] for l in leads])
                selected_lead = next(l for l in leads if l["id"] == sel_id)

                col_a, col_b = st.columns(2)
                with col_a:
                    st.markdown(f"**Entity:** `{selected_lead['carrier_name']}` | **Valuation:** ${float(selected_lead['estimated_compensation'] or 0):.2f}")
                    st.markdown(f"**Framework:** {selected_lead['regulatory_framework']}")
                    st.info(f"**AI Reasoning:**\n{selected_lead['ai_reasoning']}")

                with col_b:
                    outreach_text = st.text_area("Outreach Copy", value=selected_lead["outreach_copy"] or "", height=120)
                    c_btn1, c_btn2 = st.columns(2)
                    if c_btn1.button("✅ Approve Outreach", key=f"app_{sel_id}"):
                        conn = get_db()
                        with conn.cursor() as cur:
                            cur.execute("UPDATE leads SET status='approved', outreach_copy=%s, updated_at=NOW() WHERE id::text=%s", (outreach_text, sel_id))
                        conn.close()
                        st.success("Lead approved.")
                        st.rerun()

                    if c_btn2.button("❌ Dismiss / Reject", key=f"rej_{sel_id}"):
                        conn = get_db()
                        with conn.cursor() as cur:
                            cur.execute("UPDATE leads SET status='rejected', updated_at=NOW() WHERE id::text=%s", (sel_id,))
                        conn.close()
                        st.warning("Lead rejected.")
                        st.rerun()
        else:
            st.info("No leads currently pending review.")
    except Exception as e:
        st.error(f"Error: {e}")

# --- TAB 2: ACTIVE & SETTLED CLAIMS ---
with tab_active:
    st.subheader("Active Dispute Portfolio & Ledger")
    try:
        conn = get_db()
        with conn.cursor() as cur:
            cur.execute("""
                SELECT id::text AS id, vertical, carrier_name, claimant_name, recovery_amount, fee_collected, status, created_at 
                FROM leads WHERE status IN ('opted_in', 'dispatched', 'settled') ORDER BY updated_at DESC LIMIT 100;
            """)
            claims = cur.fetchall()
        conn.close()

        if claims:
            df_claims = pd.DataFrame(claims)
            tot_rec = df_claims["recovery_amount"].astype(float).sum()
            tot_fee = df_claims["fee_collected"].astype(float).sum()

            m1, m2, m3 = st.columns(3)
            m1.metric("Active / Resolved Portfolio", len(df_claims))
            m2.metric("Total Recovered Payouts", f"${tot_rec:.2f}")
            m3.metric("Platform Fees Collected (25%)", f"${tot_fee:.2f}")
            st.dataframe(df_claims, use_container_width=True)
        else:
            st.info("No active claims in portfolio.")
    except Exception as e:
        st.error(f"Database error: {e}")

# --- TAB 3: INBOUND CARRIER WEBHOOK AUDIT ---
with tab_webhooks:
    st.subheader("Carrier & Utility Telemetry Events")
    try:
        conn = get_db()
        with conn.cursor() as cur:
            cur.execute("SELECT id::text AS event_id, lead_id::text AS matched_lead_id, carrier_name, vertical, event_type, settlement_amount, parsed_notes, created_at FROM carrier_inbound_events ORDER BY created_at DESC LIMIT 50;")
            events = cur.fetchall()
        conn.close()
        if events:
            st.dataframe(pd.DataFrame(events), use_container_width=True)
        else:
            st.info("No inbound carrier events recorded.")
    except Exception as e:
        st.error(f"Error fetching webhooks: {e}")

# --- TAB 4: DEAD-LETTER QUEUE (DLQ) ---
with tab_dlq:
    st.subheader("⚠️ Dead-Letter Queue & Transmission Overrides")
    try:
        conn = get_db()
        with conn.cursor() as cur:
            cur.execute("SELECT id::text AS id, vertical, carrier_name, claimant_name, claimant_email, dispatch_attempts, last_dispatch_error, status FROM leads WHERE status='dispatch_failed' OR last_dispatch_error IS NOT NULL;")
            dlq_records = cur.fetchall()
        conn.close()

        if dlq_records:
            st.dataframe(pd.DataFrame(dlq_records), use_container_width=True)
            if user_role in ("super_admin", "claims_manager"):
                sel_dlq = st.selectbox("Select Stalled Claim", [d["id"] for d in dlq_records])
                if st.button("🔄 Force Immediate Re-Dispatch", key=f"force_{sel_dlq}"):
                    conn = get_db()
                    with conn.cursor() as cur:
                        cur.execute("UPDATE leads SET status='opted_in', dispatch_attempts=0, last_dispatch_error=NULL, next_dispatch_retry_at=NOW(), updated_at=NOW() WHERE id::text=%s;", (sel_dlq,))
                    conn.close()
                    st.success("Claim requeued for carrier dispatch.")
                    st.rerun()
        else:
            st.success("✅ Dead-Letter Queue is clear.")
    except Exception as e:
        st.error(f"Error: {e}")

# --- TAB 5: USER ADMINISTRATION (SUPER ADMIN ONLY) ---
if tab_users and user_role == "super_admin":
    with tab_users:
        st.subheader("👥 User Management & Role-Based Access Control")
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

                btn_create = st.form_submit_button("Create User Account")
                if btn_create:
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
                    st.dataframe(pd.DataFrame(all_users)[["username", "full_name", "role", "is_2fa_enabled", "is_active"]], use_container_width=True)
            except Exception as e:
                st.error(f"Error: {e}")
