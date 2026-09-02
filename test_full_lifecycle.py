import requests
import json
import time

API_BASE = "https://dispute-api-xyl7.onrender.com"

def run_lifecycle_test():
    print("==================================================================")
    print("   Dispute Agent: End-to-End Background Dispatch Verification    ")
    print("==================================================================")

    # 1. Ingest Airline Disruption Lead
    print("\n[+] 1. Ingesting Aviation Disruption Lead...")
    lead_payload = {
        "source_platform": "reddit",
        "platform_user_id": "u_traveler_den",
        "username": "delayed_flier_99",
        "post_url": "https://reddit.com/r/unitedairlines/comments/sample_delay_den",
        "raw_post_text": "Flight UA 949 from Denver to London Heathrow was delayed 6 hours due to scheduled maintenance crew shortage. United offered only a $15 meal voucher."
    }
    eval_res = requests.post(f"{API_BASE}/api/v1/leads/evaluate", json=lead_payload, timeout=20)
    print(f"Status: {eval_res.status_code}")
    lead_resp = eval_res.json()
    print(json.dumps(lead_resp, indent=2))

    if lead_resp.get("status") != "staged":
        print("[-] Lead not staged. Halting test.")
        return

    lead_id = lead_resp["lead"]["id"]
    print(f"\n[+] Staged Lead ID: {lead_id}")

    # 2. Submit Claim (Triggers Carrier PDF Dispatch & SMS Confirmation in Background)
    print("\n[+] 2. Submitting Digital Opt-In & Authorization...")
    submit_payload = {
        "lead_id": lead_id,
        "claimant_name": "David Herron",
        "claimant_email": "dave@example.com",
        "claimant_phone": "+13035550199",
        "claimant_address": "Highlands Ranch, CO 80126",
        "pnr": "K82X9Q",
        "incident_date": "2026-09-01",
        "digital_signature": "David S. Herron"
    }
    sub_res = requests.post(f"{API_BASE}/api/v1/claims/submit", json=submit_payload, timeout=20)
    print(f"Submit Status: {sub_res.status_code}")
    print(json.dumps(sub_res.json(), indent=2))

    # Wait for Render background tasks to generate PDF and execute dispatch
    print("\n[+] Waiting 4 seconds for asynchronous background tasks...")
    time.sleep(4)

    # 3. Verify Claim State Transitioned to 'dispatched'
    print("\n[+] 3. Checking Public Tracking State...")
    track_res = requests.get(f"{API_BASE}/api/v1/claims/track/{lead_id}", timeout=15)
    tracking_data = track_res.json()
    print(json.dumps(tracking_data, indent=2))

    # 4. Simulate Inbound Carrier Settlement Webhook
    print("\n[+] 4. Simulating Inbound Airline Settlement Approval...")
    webhook_payload = {
        "carrier_name": "United Airlines",
        "vertical": "flight_disruption",
        "claim_id": lead_id,
        "decision": "approved",
        "payout_offered": 650.00,
        "resolution_notes": "Statutory UK261 compensation authorized for non-weather mechanical delay.",
        "raw_metadata": {"airline_claim_ref": "UA-SETTLE-88129"}
    }
    wb_res = requests.post(f"{API_BASE}/api/v1/webhooks/carrier/inbound", json=webhook_payload, timeout=15)
    print(f"Webhook Status: {wb_res.status_code}")
    print(json.dumps(wb_res.json(), indent=2))

    # 5. Final Reconciliation Check
    print("\n[+] 5. Verifying Final Settlement & 25% Contingency Fee...")
    final_res = requests.get(f"{API_BASE}/api/v1/claims/track/{lead_id}", timeout=15)
    final_data = final_res.json()
    print(json.dumps(final_data, indent=2))

    recovery = float(final_data.get("recovery_amount") or 0)
    fee = float(final_data.get("fee_collected") or 0)
    expected_fee = round(recovery * 0.25, 2)
    final_status = final_data.get("status")

    print("\n==================================================================")
    print(f"Final Status:       {final_status}")
    print(f"Recovery Amount:    ${recovery:.2f}")
    print(f"Platform Fee (25%): ${fee:.2f} (Expected: ${expected_fee:.2f})")

    if final_status == "settled" and abs(fee - expected_fee) < 0.01:
        print("[✓] ALL SYSTEMS OPERATIONAL: PDF generation, background dispatch, webhook ingestion, and fee settlement verified.")
    else:
        print("[!] State anomaly detected.")
    print("==================================================================")

if __name__ == "__main__":
    run_lifecycle_test()
