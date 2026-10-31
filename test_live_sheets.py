import os
from dotenv import load_dotenv
from schemas import ContactStatus
from sheets_service import SheetsCRMService

load_dotenv()

SPREADSHEET_ID = os.getenv("SPREADSHEET_ID", "1edZ1f3QZ4pUY4NVSJa2yrVj0n3uA8b5-mHFzOXPrDQI")

def run_live_test():
    print(f"\n========================================================")
    print(f"🔗 CONNECTING TO LIVE GOOGLE SHEET:")
    print(f"   https://docs.google.com/spreadsheets/d/{SPREADSHEET_ID}/edit")
    print(f"========================================================\n")

    crm = SheetsCRMService(spreadsheet_id=SPREADSHEET_ID)

    # 1. Initialize Sheet & Headers
    print("Step 1: Initializing CRM headers...")
    crm.ensure_sheet_initialized()

    # 2. Record First Touchpoint: Inbound Meeting Proposal (Sarah Jenkins)
    print("\nStep 2: Recording Inbound Proposal from Sarah Jenkins...")
    crm.record_touchpoint(
        name="Sarah Jenkins",
        email="sarah.jenkins@acmepartners.com",
        status=ContactStatus.HOLD_FOR_ME,
        note="Inbound partnership inquiry; proposed 30m intro sync next Tuesday at 2pm",
        event_summary="2026-11-03 14:00 PST: [HOLD] Intro Sync",
        message_id="mock_msg_001",
        touchpoint_date="2026-10-28"
    )

    # 3. Record Second Touchpoint: Urgent Billing Complaint (David Miller)
    print("\nStep 3: Recording Inbound Billing Complaint from David Miller...")
    crm.record_touchpoint(
        name="David Miller",
        email="dmiller@cloudscale.io",
        status=ContactStatus.NEEDS_ATTENTION,
        note="Urgent refund request for double billing on invoice #INV-9281 ($1,499.00)",
        event_summary=None,
        message_id="mock_msg_005",
        touchpoint_date="2026-10-28"
    )

    # 4. Record Third Touchpoint: Sarah Jenkins confirms the meeting
    # Notice: New note and [CONFIRMED] event will appear at the TOP of her cells!
    print("\nStep 4: Recording Confirmation from Sarah Jenkins (Testing in-line top prepend)...")
    crm.record_touchpoint(
        name="Sarah Jenkins",
        email="sarah.jenkins@acmepartners.com",
        status=ContactStatus.SCHEDULED_CONFIRMED,
        note="Confirmed Tuesday 2pm works; intro sync locked in",
        event_summary="2026-11-03 14:00 PST: [CONFIRMED] Intro Sync",
        message_id="mock_msg_002",
        touchpoint_date="2026-10-29"
    )

    # 5. Test Idempotency: Re-submitting mock_msg_002
    print("\nStep 5: Testing Idempotency guard (re-running mock_msg_002)...")
    crm.record_touchpoint(
        name="Sarah Jenkins",
        email="sarah.jenkins@acmepartners.com",
        status=ContactStatus.SCHEDULED_CONFIRMED,
        note="This duplicate note should never appear",
        message_id="mock_msg_002"
    )

    print("\n========================================================")
    print("✅ LIVE TEST COMPLETE!")
    print(f"👉 Check your spreadsheet now at:")
    print(f"   https://docs.google.com/spreadsheets/d/{SPREADSHEET_ID}/edit")
    print("========================================================\n")

if __name__ == "__main__":
    run_live_test()

