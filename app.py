import streamlit as st
import db

st.set_page_config(page_title="Autonomous Dispute Admin Desk", layout="wide")

st.title("Autonomous Dispute & Monetization Desk")
st.caption("Inspect inbound AI signals, approve outreach, monitor active claims, and review fee settlements.")

status_filter = st.sidebar.selectbox(
    "Filter by Status",
    ["staged_for_review", "approved", "contacted", "opted_in", "won", "rejected"],
    index=0
)

df_leads = db.fetch_staged_leads(status_filter)

c1, c2, c3, c4 = st.columns(4)
c1.metric("Leads in View", len(df_leads))
c2.metric("Total Estimated Recovery", f"${df_leads['estimated_recovery_amount'].sum():,.2f}" if not df_leads.empty else "$0.00")
c3.metric("Total Fees Collected", f"${df_leads['fee_charged_amount'].sum():,.2f}" if not df_leads.empty and 'fee_charged_amount' in df_leads else "$0.00")
c4.metric("Avg AI Confidence", f"{df_leads['confidence_score'].mean():.2f}" if not df_leads.empty and 'confidence_score' in df_leads else "N/A")

st.divider()

if df_leads.empty:
    st.info(f"No leads currently found with status '{status_filter}'.")
else:
    selected_lead_id = st.selectbox(
        "Select Lead to Review / Action",
        df_leads["lead_id"].tolist(),
        format_func=lambda x: f"@{df_leads.loc[df_leads['lead_id'] == x, 'platform_username'].values[0]} | ${df_leads.loc[df_leads['lead_id'] == x, 'estimated_recovery_amount'].values[0]} | {df_leads.loc[df_leads['lead_id'] == x, 'incident_identifier'].values[0]}"
    )

    lead = df_leads[df_leads["lead_id"] == selected_lead_id].iloc[0]

    left, right = st.columns([1, 1])
    with left:
        st.markdown("### 1. Inbound Public Signal & Evidence")
        st.write(f"**Platform:** `{lead['source_platform']}` | **User:** `@{lead['platform_username']}`")
        st.link_button("View Original Public Post", lead["post_url"])
        st.text_area("Post Content", value=lead["raw_post_text"], height=100, disabled=True)
        st.info(f"**Statute Basis:** {lead['governing_statute']}\n\n**AI Reasoning:** {lead['ai_reasoning']}")

        if lead['consent_obtained']:
            st.success(f"Customer Authorized: {lead['full_name']} ({lead['email']}) | Stripe: {lead['stripe_customer_id']}")

    with right:
        st.markdown("### 2. Actions & Outreach Control")
        with st.form("admin_action_form"):
            recovery_amt = st.number_input("Estimated Recovery ($)", value=float(lead["estimated_recovery_amount"] or 0.0), step=25.0)
            copy_draft = st.text_area("Outreach Reply Copy", value=lead["outreach_copy_draft"] or "", height=120)

            col_a, col_b, col_c = st.columns(3)
            approve = col_a.form_submit_button("Approve for Auto-Dispatch", type="primary")
            reject = col_b.form_submit_button("Reject Lead")
            save = col_c.form_submit_button("Save Edits")

            if approve:
                db.update_lead_review_status(selected_lead_id, "approved", copy_draft, recovery_amt)
                st.success("Lead approved.")
                st.rerun()
            elif reject:
                db.update_lead_review_status(selected_lead_id, "rejected")
                st.warning("Lead rejected.")
                st.rerun()
            elif save:
                db.update_lead_review_status(selected_lead_id, "staged_for_review", copy_draft, recovery_amt)
                st.info("Draft edits saved.")
                st.rerun()
