from datetime import datetime
from typing import Optional
import pytz
from dateutil import parser

PST_TZ = pytz.timezone("America/Los_Angeles")

def parse_meeting_time(time_phrase: str, anchor_date: Optional[datetime] = None) -> Optional[datetime]:
    """
    Parses a natural-language time phrase (e.g. 'November 3rd at 2:00 PM PST' or 'next Tuesday at 2pm')
    into a timezone-aware datetime in PST.
    Uses anchor_date (defaulting to current time or email received date) as the temporal base.
    """
    if not time_phrase or time_phrase.lower() == "none":
        return None

    clean_phrase = time_phrase.strip()
    base_anchor = anchor_date or datetime.now(PST_TZ)

    # Ensure base anchor has a timezone
    if base_anchor.tzinfo is None:
        base_anchor = PST_TZ.localize(base_anchor)

    # Convert base anchor to naive for dateutil default template
    naive_anchor = base_anchor.astimezone(PST_TZ).replace(tzinfo=None)

    try:
        parsed_dt = parser.parse(clean_phrase, fuzzy=True, default=naive_anchor)

        # If parsed_dt has no timezone info, attach PST
        if parsed_dt.tzinfo is None:
            parsed_dt = PST_TZ.localize(parsed_dt)
        else:
            parsed_dt = parsed_dt.astimezone(PST_TZ)

        return parsed_dt
    except (parser.ParserError, ValueError, OverflowError) as e:
        print(f"⚠️ Could not parse date from phrase '{time_phrase}': {e}")
        return None

