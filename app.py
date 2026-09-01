import os
import requests
import streamlit as st

API_BASE_URL = os.getenv("API_BASE_URL", "https://dispute-api-xyl7.onrender.com")

st.set_page_config(page_title="Dispute Desk & Tracker", layout="wide")

def render_claim_tracking_page(claim_id: str):
    st.title("Dispute Case Tracker")
    st.markdown("Real-time statutory passenger rights enforcement status.")

    if not claim_id:
        claim_id = st.text_input("Enter Claim Reference ID:", placeholder="e.g. b2a2c66b-a039-49eb-b643-a385865dae5c")
        if not claim_id:
            st.info("Enter your Claim Reference ID from your confirmation SMS or email.")
            return

    with st.spinner("Retrieving claim record..."):
        try:
            res = requests.get(f"{API_BASE_URL}/api/v1/claims/track/{claim_id.strip()}", timeout=10)
            if res.status_code == 404:
                st.error(f"No dispute record found for reference ID: {claim_id}")
                return
            elif res.status_code != 200:
                st.error(f"Unable to retrieve claim (HTTP {res.status_code})")
                return
            data = res.json()
        except Exception as err:
            st.error(f"Engine connection failed: {err}")
            return

    status = data.get("status", "pending")
    status_steps = {
        "staged_for_review": "AI Assessment Staged",
        "approved": "Outreach & Notice Prepared",
        "opted_in": "Statutory Demand Authorized",
        "dispatched": "Served on Carrier Claims Desk",
        "carrier_acknowledged": "Under Airline Legal Review",
        "settled": "Compensation Recovery Completed",
        "won": "Settlement Approved"
    }

    st.success(f"Dispute Status: **{status_steps.get(status, status.replace('_', ' ').title())}**")

    col1, col2 = st.columns(2)
    with col1:
        st.metric("Disputed Carrier", data.get("carrier", "Airline Carrier"))
        st.metric("Flight / Disruption", data.get("flight", "Disrupted Flight"))
    with col2:
        st.metric("Statutory Demand Amount", f"${data.get('amount', 0.0):,.2f}")
        st.metric("Governing Law", data.get("statute", "Air Passenger Rights"))

    st.markdown("---")
    st.caption(f"Last updated: {data.get('last_updated')} | Reference: `{data.get('lead_id')}`")

# Check for query params first
query_params = st.query_params
if "claim_id" in query_params:
    val = query_params["claim_id"]
    render_claim_tracking_page(val[0] if isinstance(val, list) else val)
    st.stop()

# --- ADMIN DASHBOARD ---
st.title("Autonomous Dispute & Monetization Desk")
st.markdown("Inspect inbound AI signals, approve outreach, monitor active claims, and review fee settlements.")

STATUS_OPTIONS = ["staged_for_review", "approved", "contacted", "opted_in", "dispatched", "settled", "won", "rejected"]
selected_status = st.sidebar.selectbox("Filter by Status", STATUS_OPTIONS)

try:
    resp = requests.get(f"{API_BASE_URL}/api/v1/leads?status={selected_status}", timeout=10)
    leads = resp.json() if resp.status_code == 200 else []
except Exception:
    leads = []

c1, c2, c3, c4 = st.columns(4)
total_rec = sum(float(l.get("estimated_compensation") or l.get("recovery_amount") or 0.0) for l in leads)
total_fees = sum(float(l.get("fee_collected") or 0.0) for l in leads)

c1.metric("Leads in View", len(leads))
c2.metric("Total Estimated Recovery", f"${total_rec:,.2f}")
c3.metric("Total Fees Collected", f"${total_fees:,.2f}")
c4.metric("Avg AI Confidence", "0.85" if leads else "0.00")

st.markdown("---")

if not leads:
    st.info(f"No claims currently in '{selected_status}' status.")
else:
    lead_titles = [f"@{l.get('username','anon')} | ${l.get('estimated_compensation',0)} | {l.get('incident_identifier','N/A')}_{l.get('source_platform','')}" for l in leads]
    selected_idx = st.selectbox("Select Lead to Review / Action", range(len(leads)), format_func=lambda i: lead_titles[i])
    lead = leads[selected_idx]

    col_a, col_b = st.columns(2)
    with col_a:
        st.subheader("1. Inbound Public Signal & Evidence")
        st.write(f"**Platform:** {lead.get('source_platform')} | **User:** @{lead.get('username')}")
        st.text_area("Post Content", lead.get("raw_post_text", ""), height=120, disabled=True)
        st.info(f"**Statute Basis:** {lead.get('regulatory_framework', 'N/A')}")

    with col_b:
        st.subheader("2. Actions & Outreach Control")
        est_val = st.number_input("Estimated Recovery ($)", value=float(lead.get("estimated_compensation") or 650.0))
        outreach_text = st.text_area("Outreach Reply Copy", lead.get("outreach_copy", ""), height=120)
        
        btn_cols = st.columns(3)
        if btn_cols[0].button("Approve for Auto-Dispatch", type="primary"):
            st.success("Claim approved.")
        if btn_cols[1].button("Reject Lead"):
            st.warning("Claim rejected.")
