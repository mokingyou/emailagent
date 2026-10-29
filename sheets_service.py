from datetime import datetime
from typing import List, Optional, Set, Dict, Tuple
from schemas import ContactStatus, PersonCRMRecord
from google_auth import get_service

CRM_HEADERS = [
    "Name",
    "Primary Email",
    "Secondary Emails",
    "Status",
    "Last Contacted",
    "Events (In-line)",
    "Notes / History",
    "Processed Msg IDs",
]

class SheetsCRMService:
    """
    Manages the Google Sheets Person-Centric Mini-CRM dashboard.
    Implements 1-row-per-person semantics, in-line history, and strict idempotency.
    """

    def __init__(self, spreadsheet_id: str, sheet_name: str = "CRM", service=None):
        self.spreadsheet_id = spreadsheet_id
        self.sheet_name = sheet_name
        self.service = service or get_service('sheets', 'v4')

    def ensure_sheet_initialized(self) -> None:
        """Ensures the sheet exists and has the canonical headers in Row 1."""
        range_name = f"{self.sheet_name}!A1:H1"
        try:
            result = self.service.spreadsheets().values().get(
                spreadsheetId=self.spreadsheet_id,
                range=range_name
            ).execute()
            rows = result.get('values', [])
            if not rows or rows[0] != CRM_HEADERS:
                # Write / repair headers
                self.service.spreadsheets().values().update(
                    spreadsheetId=self.spreadsheet_id,
                    range=range_name,
                    valueInputOption="RAW",
                    body={"values": [CRM_HEADERS]}
                ).execute()
                print(f"📋 Initialized CRM headers in '{self.sheet_name}'.")
        except Exception as e:
            raise RuntimeError(f"Failed to access Google Sheet {self.spreadsheet_id}: {e}")

    def get_monitored_emails(self) -> Set[str]:
        """
        Extracts all known email addresses (primary and secondary) from the CRM.
        Used to filter outbound (sent) emails.
        """
        range_name = f"{self.sheet_name}!B2:C"
        result = self.service.spreadsheets().values().get(
            spreadsheetId=self.spreadsheet_id,
            range=range_name
        ).execute()
        rows = result.get('values', [])
        
        monitored = set()
        for row in rows:
            if len(row) > 0 and row[0].strip():
                monitored.add(row[0].strip().lower())
            if len(row) > 1 and row[1].strip():
                # Secondary emails are comma-separated
                for alias in row[1].split(','):
                    if alias.strip():
                        monitored.add(alias.strip().lower())
        return monitored

    def find_contact(self, email: str) -> Optional[Dict]:
        """
        Looks up a contact by primary or secondary email address.
        Returns a dict with row number and existing values, or None if not found.
        """
        target_email = email.strip().lower()
        range_name = f"{self.sheet_name}!A2:H"
        result = self.service.spreadsheets().values().get(
            spreadsheetId=self.spreadsheet_id,
            range=range_name
        ).execute()
        rows = result.get('values', [])

        for idx, row in enumerate(rows, start=2):  # 1-based index, row 1 is headers
            primary_email = row[1].strip().lower() if len(row) > 1 else ""
            secondary_emails_raw = row[2].strip().lower() if len(row) > 2 else ""
            secondary_emails = [e.strip() for e in secondary_emails_raw.split(',') if e.strip()]

            if target_email == primary_email or target_email in secondary_emails:
                processed_ids_raw = row[7].strip() if len(row) > 7 else ""
                processed_ids = [m.strip() for m in processed_ids_raw.split(',') if m.strip()]

                return {
                    "row_number": idx,
                    "name": row[0] if len(row) > 0 else "",
                    "primary_email": primary_email,
                    "secondary_emails": secondary_emails,
                    "status": row[3] if len(row) > 3 else ContactStatus.NEW.value,
                    "last_contacted": row[4] if len(row) > 4 else "",
                    "events": row[5] if len(row) > 5 else "",
                    "notes": row[6] if len(row) > 6 else "",
                    "processed_msg_ids": processed_ids,
                }
        return None

    def record_touchpoint(
        self,
        name: str,
        email: str,
        status: ContactStatus,
        note: Optional[str] = None,
        event_summary: Optional[str] = None,
        message_id: Optional[str] = None,
        touchpoint_date: Optional[str] = None,
    ) -> Dict:
        """
        Idempotently records an email touchpoint into the person-centric CRM.
        - If contact exists and message_id was already processed, skips to prevent duplicates.
        - If contact exists, updates status, appends to Notes and Events, and stamps message_id.
        - If contact is new, inserts a new row.
        """
        today_str = touchpoint_date or datetime.now().strftime("%Y-%m-%d")
        clean_email = email.strip().lower()
        contact = self.find_contact(clean_email)

        # 1. Existing Contact Case
        if contact:
            row_num = contact["row_number"]
            processed_ids = contact["processed_msg_ids"]

            # Idempotency check
            if message_id and message_id in processed_ids:
                print(f"⚠️ [IDEMPOTENT SKIP] Message '{message_id}' already processed for {clean_email}.")
                return {"action": "SKIPPED_DUPLICATE", "contact": contact}

            # Prepare new notes cell
            existing_notes = contact["notes"]
            new_note_entry = f"[{today_str}]: {note}" if note else ""
            if existing_notes and new_note_entry:
                updated_notes = f"{existing_notes}\n{new_note_entry}"
            elif new_note_entry:
                updated_notes = new_note_entry
            else:
                updated_notes = existing_notes

            # Prepare new events cell
            existing_events = contact["events"]
            if event_summary:
                new_event_entry = f"• {event_summary}" if not event_summary.startswith("•") else event_summary
                updated_events = f"{existing_events}\n{new_event_entry}" if existing_events else new_event_entry
            else:
                updated_events = existing_events

            # Update processed message IDs
            if message_id:
                processed_ids.append(message_id)
            updated_ids_str = ", ".join(processed_ids)

            # Build row update payload
            updated_row = [
                name or contact["name"],
                contact["primary_email"],
                ", ".join(contact["secondary_emails"]),
                status.value if isinstance(status, ContactStatus) else str(status),
                today_str,
                updated_events,
                updated_notes,
                updated_ids_str,
            ]

            update_range = f"{self.sheet_name}!A{row_num}:H{row_num}"
            self.service.spreadsheets().values().update(
                spreadsheetId=self.spreadsheet_id,
                range=update_range,
                valueInputOption="USER_ENTERED",
                body={"values": [updated_row]}
            ).execute()

            print(f"✅ Updated existing contact '{contact['name']}' at row {row_num} (Status: {status}).")
            return {"action": "UPDATED", "row_number": row_num, "email": clean_email}

        # 2. New Contact Case
        else:
            initial_note = f"[{today_str}]: {note}" if note else ""
            initial_event = f"• {event_summary}" if event_summary and not event_summary.startswith("•") else (event_summary or "")
            initial_ids = message_id or ""

            new_row = [
                name or "Unknown",
                clean_email,
                "",  # Secondary emails empty initially
                status.value if isinstance(status, ContactStatus) else str(status),
                today_str,
                initial_event,
                initial_note,
                initial_ids,
            ]

            append_range = f"{self.sheet_name}!A:H"
            self.service.spreadsheets().values().append(
                spreadsheetId=self.spreadsheet_id,
                range=append_range,
                valueInputOption="USER_ENTERED",
                insertDataOption="INSERT_ROWS",
                body={"values": [new_row]}
            ).execute()

            print(f"✨ Created new CRM contact row for '{name}' <{clean_email}> (Status: {status}).")
            return {"action": "CREATED", "email": clean_email}

