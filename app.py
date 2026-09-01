import os
import streamlit as st
import requests
import pandas as pd
import psycopg2
from psycopg2.extras import RealDictCursor
from dotenv import load_dotenv

load_dotenv()
st.set_page_config(page_title="Dispute Agent | Claims Desk", page_icon="⚖️", layout="wide")

DATABASE_URL = os.getenv("DATABASE_URL")
API_BASE = os.getenv("API_BASE_URL", "https://dispute-api-xyl7.onrender.com")

def get_db():
    return psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)

claim_id_param = st.query_params.get("claim_id")

if claim_id_param:
    st.title("🛡️ Dispute Claim Resolution Portal")
    st.caption(f"Tracking ID: `{claim_id_param}`")
    try:
        res = requests.get(f"{API_BASE}/api/v1/claims/track/{claim_id_param}", timeout=10)
        if res.status_code == 200:
            claim = res.json()
            st.divider()
            c1, c2, c3, c4 = st.columns(4)
            c1.metric("Vertical", (claim.get("vertical") or "Dispute").replace("_", " ").title())
            c2.metric("Target Entity", claim.get("carrier_name") or "Entity")
            c3.metric("Status", (claim.get("status") or "Pending").upper())
            payout = float(claim.get("recovery_amount") or claim.get("estimated_compensation") or 0)
            c4.metric("Valuation", f"${payout:.2f}")

            st.subheader("Statutory Basis")
            st.info(claim.get("regulatory_framework") or "Statutory consumer protection laws apply.")
            if claim.get("status") == "settled":
                fee = float(claim.get("fee_collected") or 0)
                st.success(f"🎉 **Resolved!** Net payout: **${payout - fee:.2f}** (after 25% fee: ${fee:.2f}).")
        else:
            st.error("Claim not found.")
    except Exception as e:
        st.error(f"Error loading claim: {e}")
    st.stop()

st.title("⚖️ Dispute Agent: Multi-Vertical Operations Desk")
tab_review, tab_active, tab_webhooks, tab_dlq = st.tabs(["📥 Ingestion Queue", "💼 Active Claims", "📡 Webhook Audit", "⚠️ Dead-Letter Queue"])

with tab_review:
    st.subheader("Staged Consumer Signals")
    try:
        conn = get_db()
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM v_staged_leads_for_review LIMIT 50;")
            leads = cur.fetchall()
        conn.close()
        if leads:
            df = pd.DataFrame(leads)
            st.dataframe(df[["id", "vertical", "carrier_name", "estimated_compensation", "regulatory_framework", "created_at"]], use_container_width=True)
            sel_id = st.selectbox("Inspect Lead", [l["id"] for l in leads])
            sel = next(l for l in leads if l["id"] == sel_id)
            c_a, c_b = st.columns(2)
            with c_a:
                st.markdown(f"**Entity:** `{sel['carrier_name']}` | **Valuation:** ${float(sel['estimated_compensation'] or 0):.2f}")
                st.info(f"**AI Reasoning:**\n{sel['ai_reasoning']}")
            with c_b:
                outreach = st.text_area("Outreach Text", value=sel["outreach_copy"] or "", height=120)
                if st.button("✅ Approve Outreach", key=f"app_{sel_id}"):
                    conn = get_db()
                    with conn.cursor() as cur:
                        cur.execute("UPDATE leads SET status='approved', outreach_copy=%s, updated_at=NOW() WHERE id::text=%s", (outreach, sel_id))
                        conn.commit()
                    conn.close()
                    st.rerun()
        else:
            st.info("Queue is clear.")
    except Exception as e:
        st.error(f"Database error: {e}")

with tab_active:
    st.subheader("Active Dispute Ledger")
    try:
        conn = get_db()
        with conn.cursor() as cur:
            cur.execute("SELECT id::text AS id, vertical, carrier_name, claimant_name, recovery_amount, fee_collected, status FROM leads WHERE status IN ('opted_in', 'dispatched', 'settled') ORDER BY updated_at DESC LIMIT 100;")
            claims = cur.fetchall()
        conn.close()
        if claims:
            st.dataframe(pd.DataFrame(claims), use_container_width=True)
        else:
            st.info("No active claims.")
    except Exception as e:
        st.error(f"Error: {e}")

with tab_webhooks:
    st.subheader("Carrier Telemetry Events")
    try:
        conn = get_db()
        with conn.cursor() as cur:
            cur.execute("SELECT id::text, carrier_name, vertical, event_type, settlement_amount, parsed_notes, created_at FROM carrier_inbound_events ORDER BY created_at DESC LIMIT 50;")
            events = cur.fetchall()
        conn.close()
        if events:
            st.dataframe(pd.DataFrame(events), use_container_width=True)
        else:
            st.info("No webhook logs.")
    except Exception as e:
        st.error(f"Error: {e}")

with tab_dlq:
    st.subheader("⚠️ Failed Transmissions & DLQ")
    try:
        conn = get_db()
        with conn.cursor() as cur:
            cur.execute("SELECT id::text AS id, carrier_name, claimant_name, dispatch_attempts, last_dispatch_error, status FROM leads WHERE status='dispatch_failed' OR last_dispatch_error IS NOT NULL;")
            dlq = cur.fetchall()
        conn.close()
        if dlq:
            st.dataframe(pd.DataFrame(dlq), use_container_width=True)
            sel_dlq = st.selectbox("Select Stalled Claim", [d["id"] for d in dlq])
            if st.button("🔄 Force Immediate Re-Dispatch", key=f"dlq_retry_{sel_dlq}"):
                conn = get_db()
                with conn.cursor() as cur:
                    cur.execute("UPDATE leads SET status='opted_in', dispatch_attempts=0, last_dispatch_error=NULL, next_dispatch_retry_at=NOW(), updated_at=NOW() WHERE id::text=%s;", (sel_dlq,))
                    conn.commit()
                conn.close()
                st.success("Claim requeued for dispatch.")
                st.rerun()
        else:
            st.success("DLQ is clear.")
    except Exception as e:
        st.error(f"Error: {e}")
