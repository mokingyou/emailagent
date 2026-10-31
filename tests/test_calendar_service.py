import unittest
from unittest.mock import MagicMock
from datetime import datetime
import pytz
from calendar_service import CalendarService, PST_TZ

class TestCalendarService(unittest.TestCase):

    def setUp(self):
        self.mock_service = MagicMock()
        self.cal_service = CalendarService(calendar_id="primary", service=self.mock_service)

    def test_operating_hours_valid_weekday(self):
        """Tuesday 2:00 PM PST should be valid."""
        start = PST_TZ.localize(datetime(2026, 11, 3, 14, 0))  # Tuesday 2pm
        end = PST_TZ.localize(datetime(2026, 11, 3, 14, 30))
        valid, msg = self.cal_service.validate_operating_hours(start, end)
        self.assertTrue(valid)

    def test_operating_hours_valid_saturday(self):
        """Saturday 10:00 AM PST should be valid."""
        start = PST_TZ.localize(datetime(2026, 11, 7, 10, 0))  # Saturday 10am
        end = PST_TZ.localize(datetime(2026, 11, 7, 10, 30))
        valid, msg = self.cal_service.validate_operating_hours(start, end)
        self.assertTrue(valid)

    def test_operating_hours_sunday_blocked(self):
        """Sunday should be strictly blocked."""
        start = PST_TZ.localize(datetime(2026, 11, 8, 14, 0))  # Sunday
        end = PST_TZ.localize(datetime(2026, 11, 8, 14, 30))
        valid, msg = self.cal_service.validate_operating_hours(start, end)
        self.assertFalse(valid)
        self.assertIn("Sunday", msg)

    def test_operating_hours_too_early(self):
        """5:00 AM PST is before 6:00 AM operating window."""
        start = PST_TZ.localize(datetime(2026, 11, 3, 5, 0))
        end = PST_TZ.localize(datetime(2026, 11, 3, 5, 30))
        valid, msg = self.cal_service.validate_operating_hours(start, end)
        self.assertFalse(valid)
        self.assertIn("outside available operating hours", msg)

    def test_operating_hours_too_late(self):
        """9:30 PM PST is after 9:00 PM operating window."""
        start = PST_TZ.localize(datetime(2026, 11, 3, 21, 30))
        end = PST_TZ.localize(datetime(2026, 11, 3, 22, 0))
        valid, msg = self.cal_service.validate_operating_hours(start, end)
        self.assertFalse(valid)
        self.assertIn("outside available operating hours", msg)

    def test_check_is_free_with_conflict(self):
        """If freebusy returns busy block, should report conflict."""
        self.mock_service.freebusy().query().execute.return_value = {
            "calendars": {
                "primary": {
                    "busy": [{"start": "2026-11-03T14:00:00Z", "end": "2026-11-03T14:30:00Z"}]
                }
            }
        }
        start = PST_TZ.localize(datetime(2026, 11, 3, 14, 0))
        end = PST_TZ.localize(datetime(2026, 11, 3, 14, 30))

        is_free, reason = self.cal_service.check_is_free(start, end)
        self.assertFalse(is_free)
        self.assertIn("conflicts with an existing event", reason)

    def test_create_hold_event_success(self):
        """Tests successful hold creation with no invites and meet link request."""
        # 1. No existing event for message
        self.mock_service.events().list().execute.return_value = {"items": []}
        # 2. Free slot
        self.mock_service.freebusy().query().execute.return_value = {
            "calendars": {"primary": {"busy": []}}
        }
        # 3. Insert mock response
        self.mock_service.events().insert().execute.return_value = {
            "id": "evt_123",
            "summary": "[HOLD] Intro Sync: Micah <> Sarah Jenkins",
            "hangoutLink": "https://meet.google.com/abc-defg-hij",
            "htmlLink": "https://calendar.google.com/event?eid=123"
        }

        start = PST_TZ.localize(datetime(2026, 11, 3, 14, 0))
        res = self.cal_service.create_hold_event(
            title="Intro Sync: Micah <> Sarah Jenkins",
            start_dt=start,
            duration_minutes=30,
            contact_name="Sarah Jenkins",
            contact_email="sarah@acmepartners.com",
            message_id="msg_001"
        )

        self.assertEqual(res["action"], "CREATED_HOLD")
        self.assertEqual(res["event_id"], "evt_123")
        self.assertEqual(res["meet_link"], "https://meet.google.com/abc-defg-hij")

        # Verify sendUpdates was "none"
        insert_kwargs = self.mock_service.events().insert.call_args[1]
        self.assertEqual(insert_kwargs["sendUpdates"], "none")
        self.assertEqual(insert_kwargs["conferenceDataVersion"], 1)

    def test_create_hold_idempotent_skip(self):
        """If event already exists for message ID, skips creation."""
        existing_event = {"id": "evt_existing", "summary": "[HOLD] Prior Sync"}
        self.mock_service.events().list().execute.return_value = {"items": [existing_event]}

        start = PST_TZ.localize(datetime(2026, 11, 3, 14, 0))
        res = self.cal_service.create_hold_event(
            title="Intro Sync",
            start_dt=start,
            message_id="msg_001"
        )

        self.assertEqual(res["action"], "SKIPPED_DUPLICATE")
        self.mock_service.events().insert.assert_not_called()

    def test_confirm_event_upgrades_and_sends_invites(self):
        """Confirming an event changes title to [CONFIRMED] and sends invites to guests."""
        self.mock_service.events().get().execute.return_value = {
            "id": "evt_123",
            "summary": "[HOLD] Intro Sync",
            "extendedProperties": {"private": {"event_status": "hold"}}
        }
        self.mock_service.events().patch().execute.return_value = {
            "id": "evt_123",
            "summary": "[CONFIRMED] Intro Sync",
            "htmlLink": "https://calendar.google.com/event?eid=123"
        }

        res = self.cal_service.confirm_event("evt_123")
        self.assertEqual(res["action"], "CONFIRMED")
        self.assertEqual(res["summary"], "[CONFIRMED] Intro Sync")

        patch_kwargs = self.mock_service.events().patch.call_args[1]
        self.assertEqual(patch_kwargs["sendUpdates"], "all")

    def test_cancel_event_deletes_with_updates(self):
        """Cancelling an event deletes it and notifies attendees."""
        res = self.cal_service.cancel_event("evt_123", reason="Client cancelled")
        self.assertEqual(res["action"], "CANCELLED")
        self.mock_service.events().delete.assert_called_once_with(
            calendarId="primary",
            eventId="evt_123",
            sendUpdates="all"
        )

if __name__ == "__main__":
    unittest.main()

