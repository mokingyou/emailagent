import uuid
from datetime import datetime, time, timedelta
from typing import Optional, List, Dict, Tuple
import pytz
from googleapiclient.errors import HttpError
from schemas import EventStatus
from google_auth import get_service

PST_TZ = pytz.timezone("America/Los_Angeles")

# Operating hours: Mon-Sat (0-5), 6:00 AM - 9:00 PM PST. Sundays (6) blocked.
ALLOWED_DAYS = {0, 1, 2, 3, 4, 5}  # Monday=0, Sunday=6
START_HOUR = time(6, 0)
END_HOUR = time(21, 0)

class CalendarService:
    """
    Manages Google Calendar operations for the EmailAgent.
    Features:
    - Operating hours validation (Mon-Sat 6am-9pm PST, no Sundays)
    - Free/Busy conflict detection (checking busy blocks and existing holds)
    - 3-tier event lifecycle: [HOLD] -> [CONFIRMED] -> [CANCELLED]
    - Zero guest spam: holds do not send invites; confirmed sends invites
    - Auto-generated Google Meet video conference links
    - Idempotency keyed off Gmail message_id via private extended properties
    """

    def __init__(self, calendar_id: str = "primary", service=None):
        self.calendar_id = calendar_id
        self.service = service or get_service('calendar', 'v3')

    def validate_operating_hours(self, start_dt: datetime, end_dt: datetime) -> Tuple[bool, str]:
        """
        Validates that the meeting falls within:
        - Monday through Saturday (Sunday is blocked)
        - 6:00 AM to 9:00 PM PST
        """
        # Ensure timezone-aware in PST
        if start_dt.tzinfo is None:
            start_pst = PST_TZ.localize(start_dt)
        else:
            start_pst = start_dt.astimezone(PST_TZ)

        if end_dt.tzinfo is None:
            end_pst = PST_TZ.localize(end_dt)
        else:
            end_pst = end_dt.astimezone(PST_TZ)

        # 1. Day Check
        if start_pst.weekday() not in ALLOWED_DAYS:
            day_name = start_pst.strftime("%A")
            return False, f"Proposed meeting is on {day_name}; scheduling is only allowed Monday through Saturday (no Sundays)."

        # 2. Time Window Check (6:00 AM to 9:00 PM PST)
        start_t = start_pst.time()
        end_t = end_pst.time()

        if start_t < START_HOUR or end_t > END_HOUR:
            return False, f"Proposed time ({start_pst.strftime('%I:%M %p')} - {end_pst.strftime('%I:%M %p')} PST) is outside available operating hours (6:00 AM - 9:00 PM PST)."

        return True, "Within operating hours"

    def check_is_free(self, start_dt: datetime, end_dt: datetime) -> Tuple[bool, str]:
        """
        Verifies that:
        1. The slot is within operating hours.
        2. Google Calendar Free/Busy query returns zero busy overlapping blocks.
        """
        # 1. Operating hours check
        in_hours, reason = self.validate_operating_hours(start_dt, end_dt)
        if not in_hours:
            return False, reason

        # Ensure ISO strings with timezone
        if start_dt.tzinfo is None:
            start_iso = PST_TZ.localize(start_dt).isoformat()
        else:
            start_iso = start_dt.isoformat()

        if end_dt.tzinfo is None:
            end_iso = PST_TZ.localize(end_dt).isoformat()
        else:
            end_iso = end_dt.isoformat()

        # 2. Query Google Calendar Free/Busy API
        body = {
            "timeMin": start_iso,
            "timeMax": end_iso,
            "timeZone": "America/Los_Angeles",
            "items": [{"id": self.calendar_id}]
        }

        try:
            freebusy_result = self.service.freebusy().query(body=body).execute()
            calendars = freebusy_result.get("calendars", {})
            busy_slots = calendars.get(self.calendar_id, {}).get("busy", [])

            if busy_slots:
                return False, f"Time slot conflicts with an existing event ({len(busy_slots)} conflicting booking found)."

            return True, "Slot is free and available"
        except Exception as e:
            return False, f"Free/busy query failed: {e}"

    def find_event_by_message_id(self, message_id: str) -> Optional[Dict]:
        """Finds any calendar event originating from a specific Gmail message ID."""
        try:
            events_result = self.service.events().list(
                calendarId=self.calendar_id,
                privateExtendedProperty=f"source_message_id={message_id}",
                maxResults=1
            ).execute()
            items = events_result.get("items", [])
            return items[0] if items else None
        except Exception:
            return None

    def get_active_event_for_contact(self, contact_email: str) -> Optional[Dict]:
        """
        Finds the nearest active upcoming event (> now) for this contact.
        Used to feed current context back into the classifier.
        """
        clean_email = contact_email.strip().lower()
        now_iso = datetime.now(PST_TZ).isoformat()

        try:
            events_result = self.service.events().list(
                calendarId=self.calendar_id,
                timeMin=now_iso,
                q=clean_email,
                singleEvents=True,
                orderBy="startTime",
                maxResults=5
            ).execute()

            items = events_result.get("items", [])
            for event in items:
                # Check attendees or private extended property
                attendees = [a.get("email", "").lower() for a in event.get("attendees", [])]
                contact_prop = event.get("extendedProperties", {}).get("private", {}).get("contact_email", "").lower()

                if clean_email in attendees or clean_email == contact_prop:
                    start_str = event["start"].get("dateTime", event["start"].get("date"))
                    summary = event.get("summary", "Untitled Event")
                    is_hold = summary.startswith("[HOLD]")
                    return {
                        "id": event["id"],
                        "title": summary,
                        "start_time": start_str,
                        "status": "hold" if is_hold else "confirmed",
                        "html_link": event.get("htmlLink", "")
                    }
            return None
        except Exception as e:
            print(f"⚠️ Error querying active event for {contact_email}: {e}")
            return None

    def create_hold_event(
        self,
        title: str,
        start_dt: datetime,
        duration_minutes: int = 30,
        contact_name: str = "",
        contact_email: str = "",
        message_id: Optional[str] = None,
        description: str = "",
    ) -> Dict:
        """
        Creates a tentative [HOLD] calendar event.
        - Checks free/busy conflict first.
        - Does NOT send email invites to the guest (sendUpdates='none').
        - Auto-attaches a Google Meet link.
        - Stamped with private idempotency properties.
        """
        end_dt = start_dt + timedelta(minutes=duration_minutes)

        # 1. Idempotency Check
        if message_id:
            existing = self.find_event_by_message_id(message_id)
            if existing:
                print(f"⚠️ [IDEMPOTENT SKIP] Event for message '{message_id}' already exists: {existing.get('summary')}")
                return {"action": "SKIPPED_DUPLICATE", "event": existing}

        # 2. Free/Busy Check
        is_free, reason = self.check_is_free(start_dt, end_dt)
        if not is_free:
            return {"action": "CONFLICT", "reason": reason}

        # 3. Timezone formatting
        if start_dt.tzinfo is None:
            start_iso = PST_TZ.localize(start_dt).isoformat()
            end_iso = PST_TZ.localize(end_dt).isoformat()
        else:
            start_iso = start_dt.isoformat()
            end_iso = end_dt.isoformat()

        # 4. Construct Event Payload
        hold_title = f"[HOLD] {title}" if not title.startswith("[HOLD]") else title
        attendees = [{"email": contact_email, "displayName": contact_name}] if contact_email else []

        event_body = {
            "summary": hold_title,
            "description": f"{description}\n\n[Status: Tentative Hold awaiting confirmation]",
            "start": {"dateTime": start_iso, "timeZone": "America/Los_Angeles"},
            "end": {"dateTime": end_iso, "timeZone": "America/Los_Angeles"},
            "attendees": attendees,
            "conferenceData": {
                "createRequest": {
                    "requestId": str(uuid.uuid4()),
                    "conferenceSolutionKey": {"type": "hangoutsMeet"}
                }
            },
            "extendedProperties": {
                "private": {
                    "source_message_id": message_id or "",
                    "contact_email": contact_email.strip().lower(),
                    "event_status": EventStatus.HOLD.value,
                }
            }
        }

        # 5. Insert with sendUpdates='none' (No guest spam on holds)
        created_event = self.service.events().insert(
            calendarId=self.calendar_id,
            body=event_body,
            conferenceDataVersion=1,
            sendUpdates="none"
        ).execute()

        meet_link = created_event.get("hangoutLink", "Google Meet")
        print(f"📅 Created [HOLD] event: '{hold_title}' on {start_iso} (Video: {meet_link})")
        return {
            "action": "CREATED_HOLD",
            "event_id": created_event["id"],
            "summary": hold_title,
            "start_time": start_iso,
            "meet_link": meet_link,
            "html_link": created_event.get("htmlLink", "")
        }

    def confirm_event(self, event_id: str) -> Dict:
        """
        Upgrades a [HOLD] event to [CONFIRMED].
        - Strips [HOLD] prefix and adds [CONFIRMED].
        - Sends official calendar invites to all attendees (sendUpdates='all').
        """
        try:
            event = self.service.events().get(calendarId=self.calendar_id, eventId=event_id).execute()
            old_summary = event.get("summary", "")
            new_summary = old_summary.replace("[HOLD]", "[CONFIRMED]").strip()
            if not new_summary.startswith("[CONFIRMED]"):
                new_summary = f"[CONFIRMED] {new_summary}"

            event["summary"] = new_summary
            
            # Update private property
            if "extendedProperties" not in event:
                event["extendedProperties"] = {"private": {}}
            event["extendedProperties"]["private"]["event_status"] = EventStatus.CONFIRMED.value

            updated_event = self.service.events().patch(
                calendarId=self.calendar_id,
                eventId=event_id,
                body=event,
                sendUpdates="all"  # Send official calendar invite!
            ).execute()

            print(f"🎉 Upgraded event to [CONFIRMED]: '{new_summary}' (Invites dispatched to attendees)")
            return {
                "action": "CONFIRMED",
                "event_id": event_id,
                "summary": new_summary,
                "html_link": updated_event.get("htmlLink", "")
            }
        except HttpError as e:
            return {"action": "ERROR", "error": str(e)}

    def cancel_event(self, event_id: str, reason: str = "") -> Dict:
        """Deletes a calendar event and notifies guests."""
        try:
            self.service.events().delete(
                calendarId=self.calendar_id,
                eventId=event_id,
                sendUpdates="all"
            ).execute()
            print(f"🗑️ Cancelled and removed event '{event_id}' (Reason: {reason})")
            return {"action": "CANCELLED", "event_id": event_id}
        except HttpError as e:
            return {"action": "ERROR", "error": str(e)}

