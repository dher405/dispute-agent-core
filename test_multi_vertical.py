import requests
import json
import time

API_BASE = "https://dispute-api-xyl7.onrender.com"

def test_pipeline():
    print("==================================================================")
    print("   Dispute Agent: Multi-Vertical Ingestion & Settlement Test      ")
    print("==================================================================")

    # 1. Health Check
    print("\n[+] Step 1: Verifying API Health...")
    try:
        health_res = requests.get(f"{API_BASE}/health", timeout=15)
        print(f"Health Status: {health_res.status_code} -> {health_res.json()}")
    except Exception as e:
        print(f"[-] API Health Check failed: {e}")
        return

    # 2. Ingest Regional ISP Outage Signal
    print("\n[+] Step 2: Testing Regional ISP Outage Ingestion & Statutory Evaluation...")
    outage_payload = {
        "source_platform": "reddit",
        "platform_user_id": "u_frontrange_net",
        "username": "denver_fiber_user",
        "post_url": "https://reddit.com/r/comcast/comments/denver_outage_36hr",
        "raw_post_text": "Xfinity fiber has been completely down across Littleton and Highlands Ranch for 38 straight hours. Support refused a credit on my gigabit bill."
    }
    
    ingest_res = requests.post(f"{API_BASE}/api/v1/leads/evaluate", json=outage_payload, timeout=30)
    print(f"Ingest Status: {ingest_res.status_code}")
    lead_data = ingest_res.json()
    print(json.dumps(lead_data, indent=2))

    if lead_data.get("status") != "staged":
        print("[-] Lead evaluation did not stage a viable claim. Exiting test.")
        return

    lead_id = lead_data["lead"]["id"]
    print(f"\n[+] Successfully staged Lead ID: {lead_id}")

    # 3. Simulate Inbound Carrier Settlement Webhook
    print("\n[+] Step 3: Simulating Inbound Carrier/ISP Settlement Webhook...")
    webhook_payload = {
        "carrier_name": "Xfinity / Comcast",
        "vertical": "isp_outage",
        "claim_id": lead_id,
        "decision": "approved",
        "payout_offered": 85.50,
        "resolution_notes": "State PUC Tariff disruption credit approved for continuous downtime >24hrs.",
        "raw_metadata": {"billing_cycle": "2026-09", "ticket": "CR-DEN-4491"}
    }
    
    wb_res = requests.post(f"{API_BASE}/api/v1/webhooks/carrier/inbound", json=webhook_payload, timeout=15)
    print(f"Webhook Status: {wb_res.status_code}")
    print(json.dumps(wb_res.json(), indent=2))

    # 4. Verify Claim Tracking Status and Fee Calculation
    print("\n[+] Step 4: Verifying Public Claim Tracking & Fee Reconciliation...")
    track_res = requests.get(f"{API_BASE}/api/v1/claims/track/{lead_id}", timeout=15)
    print(f"Tracking Status: {track_res.status_code}")
    tracking_data = track_res.json()
    print(json.dumps(tracking_data, indent=2))

    # Assertions
    recovery = float(tracking_data.get("recovery_amount") or 0)
    fee = float(tracking_data.get("fee_collected") or 0)
    status_val = tracking_data.get("status")

    print("\n==================================================================")
    print(f"Final Status:      {status_val}")
    print(f"Recovery Amount:   ${recovery:.2f}")
    print(f"Fee Collected:     ${fee:.2f} (Expected: ${recovery * 0.25:.2f})")
    
    if status_val == "settled" and abs(fee - (recovery * 0.25)) < 0.01:
        print("[✓] TEST PASSED: Ingestion, statutory evaluation, webhook handling, and 25% fee calculation verified.")
    else:
        print("[!] TEST ANOMALY: Verify database state or settlement logic.")
    print("==================================================================")

if __name__ == "__main__":
    test_pipeline()
