import os
import streamlit as st
import requests
import pandas as pd
import psycopg2
from psycopg2.extras import RealDictCursor

st.set_page_config(
    page_title="Dispute Agent | Operations & Claims Desk",
    page_icon="⚖️",
    layout="wide"
)

DATABASE_URL = os.getenv("DATABASE_URL")
API_BASE = os.getenv("API_BASE_URL", "https://dispute-api-xyl7.onrender.com")

def get_db():
    return psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)

query_params = st.query_params
claim_id_param = query_params.get("claim_id")

# =========================================================
# PUBLIC CLAIMANT TRACKING VIEW (?claim_id=<UUID>)
# =========================================================
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
            elif status == "staged_for_review":
                st.warning("Action Required: Please complete authorization to proceed with formal recovery.")
        else:
            st.error("Dispute record not found. Please verify your claim reference URL.")
    except Exception as e:
        st.error(f"Error fetching tracking data: {e}")

    st.stop()

# =========================================================
# OPERATOR INTERNAL AUDIT & DESK
# =========================================================
st.title("⚖️ Dispute Agent: Multi-Vertical Operations Desk")
st.caption("Autonomous Statutory Enforcement Engine & Carrier Settlement Gateway")

tab_review, tab_active, tab_webhooks = st.tabs([
    "📥 Ingestion & Review Queue",
    "💼 Active & Settled Claims",
    "📡 Inbound Carrier Webhooks"
])

# --- TAB 1: REVIEW QUEUE ---
with tab_review:
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
            st.dataframe(
                df[["id", "vertical", "carrier_name", "estimated_compensation", "regulatory_framework", "created_at"]],
                use_container_width=True
            )

            st.divider()
            st.subheader("Action Selected Dispute Lead")
            lead_ids = [l["id"] for l in leads]
            selected_id = st.selectbox("Select Lead ID to Inspect", lead_ids)
            selected_lead = next(l for l in leads if l["id"] == selected_id)

            col_a, col_b = st.columns(2)
            with col_a:
                st.markdown(f"**Platform / User:** {selected_lead['source_platform']} / `{selected_lead['username']}`")
                st.markdown(f"**Entity / Target:** `{selected_lead['carrier_name']}`")
                st.markdown(f"**Statutory Basis:** {selected_lead['regulatory_framework']}")
                st.markdown(f"**Estimated Valuation:** ${float(selected_lead['estimated_compensation'] or 0):.2f}")
                st.info(f"**AI Reasoning:**\n{selected_lead['ai_reasoning']}")

            with col_b:
                outreach_text = st.text_area("Outreach Copy", value=selected_lead["outreach_copy"] or "", height=140)
                col_btn1, col_btn2 = st.columns(2)
                
                if col_btn1.button("✅ Approve & Stage Outreach", key=f"app_{selected_id}"):
                    conn = get_db()
                    with conn.cursor() as cur:
                        cur.execute("UPDATE leads SET status = 'approved', outreach_copy = %s WHERE id::text = %s", (outreach_text, selected_id))
                        conn.commit()
                    conn.close()
                    st.success("Lead marked as Approved.")
                    st.rerun()

                if col_btn2.button("❌ Dismiss / Reject", key=f"rej_{selected_id}"):
                    conn = get_db()
                    with conn.cursor() as cur:
                        cur.execute("UPDATE leads SET status = 'rejected' WHERE id::text = %s", (selected_id,))
                        conn.commit()
                    conn.close()
                    st.warning("Lead dismissed.")
                    st.rerun()
        else:
            st.info("No leads currently pending review in this category.")
    except Exception as e:
        st.error(f"Error connecting to database: {e}")

# --- TAB 2: ACTIVE & SETTLED CLAIMS ---
with tab_active:
    st.subheader("Dispute Portfolio & Ledger")
    try:
        conn = get_db()
        with conn.cursor() as cur:
            cur.execute("""
                SELECT 
                    id::text AS id,
                    vertical,
                    carrier_name,
                    claimant_name,
                    incident_identifier,
                    estimated_compensation,
                    recovery_amount,
                    fee_collected,
                    status,
                    created_at
                FROM leads
                WHERE status IN ('opted_in', 'dispatched', 'settled')
                ORDER BY updated_at DESC
                LIMIT 100;
            """)
            claims = cur.fetchall()
        conn.close()

        if claims:
            df_claims = pd.DataFrame(claims)
            
            # Metrics
            total_recovered = df_claims["recovery_amount"].astype(float).sum()
            total_fees = df_claims["fee_collected"].astype(float).sum()
            
            m1, m2, m3 = st.columns(3)
            m1.metric("Active / Resolved Portfolio", len(df_claims))
            m2.metric("Total Recovered Payouts", f"${total_recovered:.2f}")
            m3.metric("Platform Fees Collected (25%)", f"${total_fees:.2f}")

            st.dataframe(df_claims, use_container_width=True)
        else:
            st.info("No active claims in portfolio.")
    except Exception as e:
        st.error(f"Database error: {e}")

# --- TAB 3: INBOUND CARRIER WEBHOOK AUDIT ---
with tab_webhooks:
    st.subheader("Carrier & Utility Inbound Telemetry")
    try:
        conn = get_db()
        with conn.cursor() as cur:
            cur.execute("""
                SELECT 
                    id::text AS event_id,
                    lead_id::text AS matched_lead_id,
                    carrier_name,
                    vertical,
                    event_type,
                    settlement_amount,
                    parsed_notes,
                    created_at
                FROM carrier_inbound_events
                ORDER BY created_at DESC
                LIMIT 50;
            """)
            events = cur.fetchall()
        conn.close()

        if events:
            df_events = pd.DataFrame(events)
            st.dataframe(df_events, use_container_width=True)
        else:
            st.info("No inbound carrier events recorded.")
    except Exception as e:
        st.error(f"Error fetching inbound webhook logs: {e}")
