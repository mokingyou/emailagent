#!/usr/bin/env python3
"""
EmailAgent: Local Multi-Agent Email Triage & Scheduling System
CLI Entrypoint and Orchestrator.
"""
import os
import sys
import argparse
import time
from datetime import datetime
from dotenv import load_dotenv

from google_auth import get_service
from gmail_ingest import fetch_unread_primary_emails, get_or_create_triage_label, mark_email_triaged
from sheets_service import SheetsCRMService
from calendar_service import CalendarService
from agent_classifier import classify_email
from schemas import SchedulingIntent, ContactStatus

load_dotenv()
SPREADSHEET_ID = os.getenv("SPREADSHEET_ID", "1edZ1f3QZ4pUY4NVSJa2yrVj0n3uA8b5-mHFzOXPrDQI")

def process_single_email(
    email: dict,
    gmail_service,
    crm_service: SheetsCRMService,
    cal_service: CalendarService,
    label_id: str,
    dry_run: bool = False,
    interactive: bool = False
) -> dict:
    """Processes a single email dictionary through the full perception and decision pipeline."""
    sender_email = email['sender_email']
    sender_name = email['sender_name']
    subject = email['subject']
    message_id = email['id']
    date_received = email.get('date_received', datetime.now())
    body = email['body']

    print(f"\n📨 Email: '{subject}'")
    print(f"   From:    {sender_name} <{sender_email}>")
    print(f"   Msg ID:  {message_id}")

    # 1. Check CRM for existing contact state & idempotency
    contact = crm_service.find_contact(sender_email)
    current_status = contact['status'] if contact else ContactStatus.NEW.value
    processed_ids = contact['processed_msg_ids'] if contact else []

    if message_id in processed_ids:
        print(f"   ⚠️ [IDEMPOTENT SKIP] Message already logged in CRM.")
        if not dry_run and gmail_service and label_id:
            mark_email_triaged(gmail_service, message_id, label_id)
        return {"action": "SKIPPED_DUPLICATE", "sender": sender_email}

    # 2. Check Calendar for any active upcoming hold/event for this person
    active_event = cal_service.get_active_event_for_contact(sender_email)
    if active_event:
        print(f"   📅 Found Active Calendar Event: '{active_event['title']}' at {active_event['start_time']}")
    else:
        print("   📅 No active upcoming calendar hold found for this contact.")

    # 3. Cognitive Classifier (Qwen2.5-1.5B)
    decision = classify_email(
        subject=subject,
        body=body,
        contact_status=current_status,
        active_event=active_event,
        email_date=date_received
    )

    intent = decision['intent']
    summary = decision['summary']
    topic = decision['topic']
    parsed_time = decision['parsed_time']

    print(f"   🧠 Model Intent:    {intent.value}")
    print(f"   📝 Summary:         {summary}")
    print(f"   🏷️ Topic:           {topic}")
    if parsed_time:
        print(f"   ⏰ Extracted Time:  {parsed_time.strftime('%a, %b %d %Y at %I:%M %p PST')}")

    # Standardized Event Title Formula: {Topic}: Micah <> {Contact Name}
    meeting_title = f"{topic}: Micah <> {sender_name}"

    new_status = current_status
    event_summary = None
    note_entry = summary
    calendar_action_desc = "None"

    # 4. Deterministic FSM Execution Plan
    if intent == SchedulingIntent.PROPOSE_NEW_TIME:
        if parsed_time:
            calendar_action_desc = f"Create [HOLD] event for '{meeting_title}' on {parsed_time.strftime('%b %d at %I:%M %p PST')}"
            new_status = ContactStatus.HOLD_FOR_ME
        else:
            new_status = ContactStatus.NEEDS_ATTENTION
            note_entry = f"[Needs Attention]: Proposing a meeting but time phrase was unparseable: '{decision['time_phrase']}'"

    elif intent == SchedulingIntent.CONFIRM_HOLD:
        if active_event:
            calendar_action_desc = f"Upgrade hold '{active_event['title']}' to [CONFIRMED]"
            new_status = ContactStatus.SCHEDULED_CONFIRMED
        else:
            new_status = ContactStatus.NEEDS_ATTENTION
            note_entry = f"[Review]: Sender confirmed meeting, but no active hold was located on calendar."

    elif intent == SchedulingIntent.CANCEL_HOLD:
        if active_event:
            calendar_action_desc = f"Cancel and remove event '{active_event['title']}'"
        new_status = ContactStatus.FOLLOW_UP
        note_entry = f"Cancelled meeting: {summary}"

    elif intent == SchedulingIntent.NO_CALENDAR_ACTION:
        new_status = current_status if contact else ContactStatus.NEW
        note_entry = summary

    else:  # AMBIGUOUS
        new_status = ContactStatus.NEEDS_ATTENTION
        note_entry = f"[Needs Attention]: Ambiguous intent: {summary}"

    # Interactive Confirmation
    if interactive and not dry_run:
        print(f"\n   Proposed Plan:")
        print(f"     • Calendar Action: {calendar_action_desc}")
        print(f"     • CRM Status:      {new_status}")
        print(f"     • Note Entry:      {note_entry}")
        choice = input("   👉 Apply this action to Calendar and Sheets? [Y/n/skip]: ").strip().lower()
        if choice in ['n', 'no', 's', 'skip']:
            print("   ⏭️ Skipped by user.")
            return {"action": "SKIPPED_USER", "sender": sender_email}

    # 5. Apply Calendar Action
    if not dry_run:
        if intent == SchedulingIntent.PROPOSE_NEW_TIME and parsed_time:
            cal_res = cal_service.create_hold_event(
                title=meeting_title,
                start_dt=parsed_time,
                duration_minutes=30,
                contact_name=sender_name,
                contact_email=sender_email,
                message_id=message_id,
                description=f"Topic: {summary}\nEmail Subject: {subject}"
            )
            if cal_res['action'] == "CREATED_HOLD":
                event_summary = f"{parsed_time.strftime('%Y-%m-%d %H:%M PST')}: [HOLD] {meeting_title}"
                note_entry = f"Proposed meeting on {parsed_time.strftime('%a, %b %d at %I:%M %p PST')} (Hold placed; Meet: {cal_res.get('meet_link', 'Google Meet')})"
            elif cal_res['action'] == "CONFLICT":
                new_status = ContactStatus.NEEDS_ATTENTION
                note_entry = f"[Conflict]: Proposed time {parsed_time.strftime('%b %d @ %I:%M %p PST')} conflicts: {cal_res['reason']}"
                print(f"   🚨 Scheduling Conflict: {cal_res['reason']}")

        elif intent == SchedulingIntent.CONFIRM_HOLD and active_event:
            cal_service.confirm_event(active_event['id'])
            event_summary = f"{active_event.get('start_time', '')}: [CONFIRMED] {meeting_title}"
            note_entry = f"Confirmed meeting on {active_event.get('start_time', '')} (Official invites dispatched)"

        elif intent == SchedulingIntent.CANCEL_HOLD and active_event:
            cal_service.cancel_event(active_event['id'], reason="Cancelled by contact via email")
            event_summary = f"{active_event.get('start_time', '')}: [CANCELLED] {active_event.get('title', '')}"

    # 6. Apply CRM Touchpoint
    if not dry_run:
        crm_service.record_touchpoint(
            name=sender_name,
            email=sender_email,
            status=new_status,
            note=note_entry,
            event_summary=event_summary,
            message_id=message_id,
            touchpoint_date=date_received.strftime("%Y-%m-%d") if isinstance(date_received, datetime) else None
        )

    # 7. Apply Gmail Label & Mark Read
    if not dry_run and gmail_service and label_id:
        mark_email_triaged(gmail_service, message_id, label_id)
        print(f"   🏷️ Marked as AI-Triaged in Gmail.")

    return {
        "action": intent.value,
        "sender": sender_email,
        "status": new_status,
        "calendar_action": calendar_action_desc
    }

def run_simulation(crm_service, cal_service):
    """Runs a simulated email interaction directly from the CLI without needing a real email."""
    print("\n" + "=" * 70)
    print("🧪 EMAILAGENT CLI SIMULATOR")
    print("=" * 70)
    print("Choose a test scenario to inject into your live Calendar & Sheets:")
    print("  1. Inbound Meeting Proposal (Propose Tuesday Nov 3 @ 2pm PST)")
    print("  2. Inbound Meeting Confirmation (Confirm Tuesday Nov 3 @ 2pm PST)")
    print("  3. Out-of-Hours Conflict Test (Propose Sunday meeting)")
    print("  4. Custom Interactive Email")
    print("  q. Quit")

    choice = input("\nEnter choice [1-4, q]: ").strip()
    if choice == '1':
        sim_email = {
            "id": f"sim_msg_{int(time.time())}",
            "sender_name": "Sarah Jenkins",
            "sender_email": "sarah.jenkins@acmepartners.com",
            "subject": "Intro call & partnership sync",
            "body": "Hi Micah, would love to schedule a quick 30-minute intro call next Tuesday, November 3rd at 2:00 PM PST. Let me know if that works!",
            "date_received": datetime.now()
        }
    elif choice == '2':
        sim_email = {
            "id": f"sim_msg_{int(time.time())}",
            "sender_name": "Sarah Jenkins",
            "sender_email": "sarah.jenkins@acmepartners.com",
            "subject": "Re: Intro call & partnership sync",
            "body": "Hi Micah, Tuesday at 2:00 PM PST works perfectly for me. Looking forward to our sync!",
            "date_received": datetime.now()
        }
    elif choice == '3':
        sim_email = {
            "id": f"sim_msg_{int(time.time())}",
            "sender_name": "Alex Chen",
            "sender_email": "alex.chen@innovate.co",
            "subject": "Weekend sync",
            "body": "Hi Micah, are you free this Sunday at 2pm PST for a quick call?",
            "date_received": datetime.now()
        }
    elif choice == '4':
        name = input("Sender Name: ").strip() or "Test Contact"
        email_addr = input("Sender Email: ").strip() or "test@example.com"
        subject = input("Subject: ").strip() or "Quick sync"
        print("Enter Body (end with an empty line):")
        lines = []
        while True:
            line = input()
            if not line:
                break
            lines.append(line)
        body = "\n".join(lines)
        sim_email = {
            "id": f"sim_msg_{int(time.time())}",
            "sender_name": name,
            "sender_email": email_addr,
            "subject": subject,
            "body": body,
            "date_received": datetime.now()
        }
    else:
        print("Simulation cancelled.")
        return

    process_single_email(
        email=sim_email,
        gmail_service=None,
        crm_service=crm_service,
        cal_service=cal_service,
        label_id="",
        dry_run=False,
        interactive=True
    )

def main():
    parser = argparse.ArgumentParser(description="EmailAgent: Local Multi-Agent Email Triage & Scheduling")
    parser.add_argument("--dry-run", action="store_true", help="Evaluate emails without writing to Calendar, Sheets, or Gmail")
    parser.add_argument("-i", "--interactive", action="store_true", help="Interactively confirm each calendar/CRM action before applying")
    parser.add_argument("--max-emails", type=int, default=5, help="Maximum number of unread emails to process per run (default: 5)")
    parser.add_argument("--simulate", action="store_true", help="Launch interactive CLI email simulator")
    parser.add_argument("--poll-interval", type=int, default=0, help="If > 0, polls inbox every N seconds continuously")
    args = parser.parse_args()

    print("\n" + "=" * 70)
    print("🚀 EMAILAGENT CLI COMMAND CENTER")
    print(f"   Spreadsheet ID: {SPREADSHEET_ID}")
    print(f"   Dry Run Mode:   {args.dry_run}")
    print(f"   Interactive:    {args.interactive}")
    print("=" * 70)

    # 1. Initialize API Services
    gmail_service = get_service('gmail', 'v1')
    crm_service = SheetsCRMService(spreadsheet_id=SPREADSHEET_ID)
    cal_service = CalendarService(calendar_id="primary")

    if not args.dry_run:
        crm_service.ensure_sheet_initialized()

    # Simulation mode
    if args.simulate:
        run_simulation(crm_service, cal_service)
        return

    label_id = get_or_create_triage_label(gmail_service)

    # 2. Ingestion & Processing
    print(f"\n📬 Fetching up to {args.max_emails} unread primary emails...")
    emails = fetch_unread_primary_emails(gmail_service, max_results=args.max_emails)

    if not emails:
        print("✨ No unread primary emails pending triage. Inbox zero!")
        return

    print(f"📥 Found {len(emails)} email(s) to process.\n")
    results = []

    for email in emails:
        res = process_single_email(
            email=email,
            gmail_service=gmail_service,
            crm_service=crm_service,
            cal_service=cal_service,
            label_id=label_id,
            dry_run=args.dry_run,
            interactive=args.interactive
        )
        results.append(res)
        print("  " + "-" * 60)

    # Executive Summary Card
    print("\n" + "=" * 70)
    print("📊 TRIAGE RUN SUMMARY")
    print("=" * 70)
    print(f"  • Total Emails Scanned: {len(emails)}")
    print(f"  • Actions Executed:     {len([r for r in results if not r.get('action', '').startswith('SKIPPED')])}")
    print(f"  • Google Sheet:         https://docs.google.com/spreadsheets/d/{SPREADSHEET_ID}/edit")
    print("=" * 70 + "\n")

if __name__ == "__main__":
    main()
