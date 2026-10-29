from datetime import datetime
from enum import Enum
from typing import List, Optional
from pydantic import BaseModel, Field, EmailStr


class TriageCategory(str, Enum):
    """Refined classification categories for inbound primary emails."""
    MEETING_REQUEST = "meeting request"
    FOLLOW_UP = "follow-up"
    SOCIAL = "social"
    OTHER = "other"


class ContactStatus(str, Enum):
    """Lifecycle status of a person in the mini-CRM."""
    NEW = "new"
    HOLD_FOR_ME = "hold for me"
    WAITING_ON_THEM = "wait for them"
    SCHEDULED_CONFIRMED = "scheduled an appointment"
    CALLED = "called"
    TEXTED = "texted"
    FOLLOW_UP = "follow up"
    NEEDS_ATTENTION = "needs attention"
    OTHER = "other"


class EventStatus(str, Enum):
    """Status of a calendar event (tentative hold vs confirmed)."""
    HOLD = "hold"
    CONFIRMED = "confirmed"


class SchedulingIntent(str, Enum):
    """5-way intent classification emitted by the LLM reasoning step."""
    CONFIRM_HOLD = "CONFIRM_HOLD"
    PROPOSE_NEW_TIME = "PROPOSE_NEW_TIME"
    CANCEL_HOLD = "CANCEL_HOLD"
    NO_CALENDAR_ACTION = "NO_CALENDAR_ACTION"
    AMBIGUOUS = "AMBIGUOUS"


class InboundEmail(BaseModel):
    """Cleaned representation of an inbound Gmail message."""
    id: str = Field(description="Unique Gmail message ID")
    thread_id: Optional[str] = None
    sender_name: Optional[str] = Field(default=None, description="Extracted full name of sender")
    sender_email: str = Field(description="Normalized sender email address")
    subject: str = Field(description="Email subject line")
    date_received: datetime = Field(description="Timestamp when email was sent/received")
    body: str = Field(description="Cleaned email body text")


class TriageDecision(BaseModel):
    """Decision output produced by the LLM reasoning step."""
    category: TriageCategory
    summary: str = Field(description="1-2 sentence core intent or summary")
    suggested_status: ContactStatus = Field(description="Proposed status update for this contact")
    requires_calendar_event: bool = Field(default=False, description="Whether this email requires scheduling")


class SchedulingDecision(BaseModel):
    """Structured decision output from the cognitive scheduling classifier."""
    intent: SchedulingIntent
    summary: str = Field(description="1-2 sentence summary of message intent")
    proposed_start_time: Optional[datetime] = Field(default=None, description="Candidate start time (PST) if proposing a time")
    duration_minutes: int = Field(default=30, description="Meeting duration in minutes")


class CalendarAction(BaseModel):
    """Validated specification for scheduling a Google Calendar event."""
    title: str = Field(description="Event title, e.g. 'Intro Sync: Micah <> Sarah Jenkins'")
    start_time: datetime = Field(description="Timezone-aware start time")
    duration_minutes: int = Field(default=30, description="Meeting duration in minutes")
    status: EventStatus = Field(default=EventStatus.HOLD, description="Whether event is a tentative hold or confirmed")
    guest_emails: List[str] = Field(default_factory=list, description="List of participant email addresses")
    description: str = Field(default="", description="Agenda or context from the email")
    location: Optional[str] = Field(default="Google Meet", description="Meeting URL or physical location")


class PersonCRMRecord(BaseModel):
    """Contact-centric row model for the Google Sheets mini-CRM."""
    name: str = Field(description="Contact person's full name")
    emails: List[str] = Field(description="List of associated email addresses")
    status: ContactStatus = Field(default=ContactStatus.NEW, description="Current contact status")
    notes: List[str] = Field(default_factory=list, description="Chronological log of notes/touchpoints")
    events_summary: List[str] = Field(default_factory=list, description="Formatted summary of past/upcoming events")
    processed_message_ids: List[str] = Field(default_factory=list, description="Message IDs processed for idempotency")
    last_contacted: Optional[datetime] = None

    def format_notes_cell(self) -> str:
        """Renders notes into a multiline cell value."""
        return "\n".join(self.notes)

    def format_events_cell(self) -> str:
        """Renders event summaries into an in-line multiline cell value."""
        return "\n".join(self.events_summary)

