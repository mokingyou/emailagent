import unittest
from unittest.mock import MagicMock
from schemas import ContactStatus
from sheets_service import SheetsCRMService, CRM_HEADERS

class TestSheetsCRMService(unittest.TestCase):

    def setUp(self):
        self.mock_service = MagicMock()
        self.spreadsheet_id = "test_spreadsheet_123"
        self.crm = SheetsCRMService(
            spreadsheet_id=self.spreadsheet_id,
            sheet_name="CRM",
            service=self.mock_service
        )

    def test_ensure_sheet_initialized_empty(self):
        """Test writing headers when sheet is blank."""
        # Mock get returning empty rows
        self.mock_service.spreadsheets().values().get().execute.return_value = {"values": []}

        self.crm.ensure_sheet_initialized()

        # Should call update with CRM_HEADERS
        self.mock_service.spreadsheets().values().update.assert_called_once()
        call_args = self.mock_service.spreadsheets().values().update.call_args[1]
        self.assertEqual(call_args["range"], "CRM!A1:H1")
        self.assertEqual(call_args["body"]["values"], [CRM_HEADERS])

    def test_create_new_contact(self):
        """Test inserting a new contact when email is not found."""
        # Sheet currently has only headers, no matching email
        self.mock_service.spreadsheets().values().get().execute.return_value = {"values": []}

        res = self.crm.record_touchpoint(
            name="Sarah Jenkins",
            email="sarah@acmepartners.com",
            status=ContactStatus.HOLD_FOR_ME,
            note="Proposed intro sync for 11/3",
            event_summary="2026-11-03 14:00 PST: [HOLD] Intro Sync",
            message_id="msg_001",
            touchpoint_date="2026-10-29"
        )

        self.assertEqual(res["action"], "CREATED")
        self.assertEqual(res["email"], "sarah@acmepartners.com")
        self.mock_service.spreadsheets().values().append.assert_called_once()
        append_body = self.mock_service.spreadsheets().values().append.call_args[1]["body"]["values"][0]
        self.assertEqual(append_body[0], "Sarah Jenkins")
        self.assertEqual(append_body[1], "sarah@acmepartners.com")
        self.assertEqual(append_body[3], "hold for me")
        self.assertIn("Intro Sync", append_body[5])
        self.assertIn("[2026-10-29]: Proposed intro sync for 11/3", append_body[6])
        self.assertEqual(append_body[7], "msg_001")

    def test_update_existing_contact(self):
        """Test updating an existing contact's notes, status, and events."""
        existing_row = [
            "Sarah Jenkins",
            "sarah@acmepartners.com",
            "",
            "hold for me",
            "2026-10-28",
            "• 2026-11-03 14:00 PST: [HOLD] Intro Sync",
            "[2026-10-28]: Initial outreach",
            "msg_001"
        ]
        # Mock get returning existing row at index 0 (which is row 2)
        self.mock_service.spreadsheets().values().get().execute.return_value = {"values": [existing_row]}

        res = self.crm.record_touchpoint(
            name="Sarah Jenkins",
            email="sarah@acmepartners.com",
            status=ContactStatus.SCHEDULED_CONFIRMED,
            note="Confirmed Tuesday works",
            event_summary="2026-11-03 14:00 PST: [CONFIRMED] Intro Sync",
            message_id="msg_002",
            touchpoint_date="2026-10-29"
        )

        self.assertEqual(res["action"], "UPDATED")
        self.assertEqual(res["row_number"], 2)

        update_call = self.mock_service.spreadsheets().values().update.call_args[1]
        updated_row = update_call["body"]["values"][0]
        self.assertEqual(updated_row[3], "scheduled an appointment")
        self.assertIn("[CONFIRMED]", updated_row[5])
        self.assertIn("[2026-10-29]: Confirmed Tuesday works", updated_row[6])
        self.assertEqual(updated_row[7], "msg_001, msg_002")

    def test_idempotent_skip_on_duplicate_message_id(self):
        """Test that re-processing the same message_id is a NO-OP."""
        existing_row = [
            "Sarah Jenkins",
            "sarah@acmepartners.com",
            "",
            "hold for me",
            "2026-10-28",
            "• [HOLD] Intro Sync",
            "[2026-10-28]: Initial outreach",
            "msg_001, msg_002"
        ]
        self.mock_service.spreadsheets().values().get().execute.return_value = {"values": [existing_row]}

        res = self.crm.record_touchpoint(
            name="Sarah Jenkins",
            email="sarah@acmepartners.com",
            status=ContactStatus.SCHEDULED_CONFIRMED,
            note="Duplicate attempt",
            message_id="msg_002"
        )

        self.assertEqual(res["action"], "SKIPPED_DUPLICATE")
        # Ensure NO update or append calls were made
        self.mock_service.spreadsheets().values().update.assert_not_called()
        self.mock_service.spreadsheets().values().append.assert_not_called()

    def test_get_monitored_emails(self):
        """Test gathering all primary and secondary emails for outbound filtering."""
        # Range B2:C returns column B (Primary Email) and C (Secondary Emails)
        rows = [
            ["sarah@acme.com", "sjenkins@gmail.com"],
            ["alex@innovate.co", ""],
        ]
        self.mock_service.spreadsheets().values().get().execute.return_value = {"values": rows}

        monitored = self.crm.get_monitored_emails()
        self.assertEqual(monitored, {"sarah@acme.com", "sjenkins@gmail.com", "alex@innovate.co"})

if __name__ == "__main__":
    unittest.main()
