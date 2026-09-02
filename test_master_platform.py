import requests
import json
import time

API_BASE = "https://dispute-api-xyl7.onrender.com"

def run_comprehensive_test():
    print("==================================================================")
    print("   DISPUTE AGENT PLATFORM: MASTER VERIFICATION RUN                ")
    print("==================================================================")

    # 1. API Health Check
    print("\n[1/5] Checking Production API Health...")
    try:
        h_res = requests.get(f"{API_BASE}/health", timeout=10)
        print(f"Health Response: {h_res.status_code} -> {h_res.json()}")
    except Exception as e:
        print(f"[-] API unavailable: {e}")
        return

    # 2. Multi-Vertical Ingestion & AI Evaluation Tests
    test_signals = [
        {
            "vertical_expected": "flight_disruption",
            "name": "Flight Disruption (US DOT 14 CFR Part 260 / UK261)",
            "payload": {
                "source_platform": "reddit",
                "platform_user_id": "u_denver_flyer",
                "username": "denver_flyer_01",
                "post_url": "https://reddit.com/r/unitedairlines/comments/sample_flight_test",
                "raw_post_text": "Flight UA 884 from Denver to Frankfurt was delayed 7 hours due to crew scheduling. United only gave me a $20 meal voucher."
            }
        },
        {
            "vertical_expected": "isp_outage",
            "name": "Regional Telecom Outage (State Utility Tariff)",
            "payload": {
                "source_platform": "reddit",
                "platform_user_id": "u_denver_broadband",
                "username": "highlands_ranch_user",
                "post_url": "https://reddit.com/r/comcast/comments/sample_isp_test",
                "raw_post_text": "Xfinity fiber has been down in Highlands Ranch for 32 hours straight. Support refused any bill credit for our gigabit line."
            }
        },
        {
            "vertical_expected": "security_deposit",
            "name": "Security Deposit Non-Compliance (Statutory Penalties)",
            "payload": {
                "source_platform": "reddit",
                "platform_user_id": "u_tenant_co",
                "username": "co_tenant_80126",
                "post_url": "https://reddit.com/r/Tenant/comments/sample_deposit_test",
                "raw_post_text": "Moved out 45 days ago in Colorado and landlord still has not sent my $2,000 security deposit or any itemized list of deductions."
            }
        }
    ]

    staged_claims = []
    print("\n[2/5] Testing Multi-Vertical AI Ingestion Gateway...")
    for item in test_signals:
        print(f"\n -> Testing: {item['name']}")
        res = requests.post(f"{API_BASE}/api/v1/leads/evaluate", json=item["payload"], timeout=25)
        print(f"    Status: {res.status_code}")
        data = res.json()
        if data.get("status") == "staged":
            lead = data["lead"]
            staged_claims.append(lead)
            print(f"    [✓] Staged: ID={lead['id']} | Vertical={lead['vertical']} | Target={lead['carrier_name']} | Est=${lead['estimated_compensation']}")
        else:
            print(f"    [!] Evaluation returned non-staged: {data}")

    if not staged_claims:
        print("[-] No claims staged. Exiting test.")
        return

    # 3. Digital Authorization, PDF Generation & Dispatch
    primary_claim = staged_claims[0]
    lead_id = primary_claim["id"]
    print(f"\n[3/5] Testing Claim Digital Submission for Lead ID: {lead_id}...")
    
    submission_payload = {
        "lead_id": lead_id,
        "claimant_name": "David Herron",
        "claimant_email": "dave@example.com",
        "claimant_phone": "+13035550199",
        "claimant_address": "Highlands Ranch, CO 80126",
        "pnr": "K82X9Q",
        "incident_date": "2026-09-01",
        "digital_signature": "David S. Herron"
    }

    sub_res = requests.post(f"{API_BASE}/api/v1/claims/submit", json=submission_payload, timeout=20)
    print(f"Submit Status: {sub_res.status_code} -> {sub_res.json()}")

    print(" -> Awaiting asynchronous background workers (PDF build + SMTP + SMS)...")
    time.sleep(4)

    # 4. Inbound Carrier Webhook Settlement
    print("\n[4/5] Testing Inbound Carrier Settlement Webhook & 25% Fee Calculation...")
    webhook_payload = {
        "carrier_name": primary_claim.get("carrier_name") or "United Airlines",
        "vertical": primary_claim.get("vertical") or "flight_disruption",
        "claim_id": lead_id,
        "decision": "approved",
        "payout_offered": 650.00,
        "resolution_notes": "Statutory disruption settlement authorized.",
        "raw_metadata": {"claim_ref": "AUTOTEST-994"}
    }
    wb_res = requests.post(f"{API_BASE}/api/v1/webhooks/carrier/inbound", json=webhook_payload, timeout=15)
    print(f"Webhook Status: {wb_res.status_code} -> {wb_res.json()}")

    # 5. Verify Public Tracking Portal
    print("\n[5/5] Verifying Final Claim Ledger State via Public Tracking API...")
    track_res = requests.get(f"{API_BASE}/api/v1/claims/track/{lead_id}", timeout=15)
    final_data = track_res.json()
    print(json.dumps(final_data, indent=2))

    rec = float(final_data.get("recovery_amount") or 0)
    fee = float(final_data.get("fee_collected") or 0)
    expected_fee = round(rec * 0.25, 2)
    status_val = final_data.get("status")

    print("\n==================================================================")
    print(f"Final Lifecycle Status: {status_val}")
    print(f"Settled Recovery:       ${rec:.2f}")
    print(f"Platform Fee (25%):     ${fee:.2f} (Expected: ${expected_fee:.2f})")
    
    if status_val == "settled" and abs(fee - expected_fee) < 0.01:
        print("[✓] ALL PLATFORM MODULES VERIFIED OPERATIONAL.")
    else:
        print("[!] Verification completed with warnings. Check logs.")
    print("==================================================================")

if __name__ == "__main__":
    run_comprehensive_test()
