import base64
from email.utils import parseaddr, parsedate_to_datetime
from datetime import datetime
from typing import List, Dict, Optional, Tuple
from googleapiclient.errors import HttpError
from google_auth import get_service

TRIAGE_LABEL_NAME = "AI-Triaged"

def get_or_create_triage_label(service, label_name: str = TRIAGE_LABEL_NAME) -> str:
    """
    Checks if the triage label exists in Gmail; creates it if not.
    Returns the label ID.
    """
    try:
        response = service.users().labels().list(userId='me').execute()
        labels = response.get('labels', [])
        for label in labels:
            if label['name'].lower() == label_name.lower():
                return label['id']

        # Label does not exist, create it
        new_label_body = {
            "name": label_name,
            "labelListVisibility": "labelShow",
            "messageListVisibility": "show",
        }
        created = service.users().labels().create(userId='me', body=new_label_body).execute()
        print(f"🏷️ Created new Gmail label: '{label_name}' (ID: {created['id']})")
        return created['id']
    except HttpError as e:
        print(f"⚠️ Error with Gmail labels: {e}")
        return ""

def get_email_body(payload: Dict) -> str:
    """Recursively parses the email payload to find the plain text body."""
    if 'data' in payload.get('body', {}):
        data = payload['body']['data']
        return base64.urlsafe_b64decode(data).decode('utf-8', errors='replace')

    if 'parts' in payload:
        for part in payload['parts']:
            if part.get('mimeType') == 'text/plain':
                return get_email_body(part)
        for part in payload['parts']:
            if part.get('mimeType') == 'multipart/alternative':
                return get_email_body(part)
    return "[No Plain Text Found]"

def clean_email_text(raw_text: str) -> str:
    """Strips excessive newlines and basic email noise."""
    lines = raw_text.splitlines()
    cleaned_lines = []
    for line in lines:
        cleaned_lines.append(line.rstrip())
    return "\n".join(cleaned_lines).strip()

def fetch_unread_primary_emails(service, max_results: int = 10) -> List[Dict]:
    """
    Fetches unread emails in the Primary inbox that haven't been triaged yet:
    Query: 'is:unread in:inbox category:primary -label:AI-Triaged'
    """
    query = f"is:unread in:inbox category:primary -label:{TRIAGE_LABEL_NAME}"
    try:
        results = service.users().messages().list(userId='me', q=query, maxResults=max_results).execute()
        messages = results.get('messages', [])
    except HttpError as e:
        print(f"⚠️ Error querying Gmail messages: {e}")
        return []

    if not messages:
        return []

    extracted_emails = []

    for msg in messages:
        try:
            msg_data = service.users().messages().get(userId='me', id=msg['id'], format='full').execute()
            payload = msg_data.get('payload', {})
            headers = payload.get('headers', [])

            subject = next((h['value'] for h in headers if h['name'].lower() == 'subject'), 'No Subject')
            from_header = next((h['value'] for h in headers if h['name'].lower() == 'from'), 'Unknown Sender')
            date_header = next((h['value'] for h in headers if h['name'].lower() == 'date'), '')

            sender_name, sender_email = parseaddr(from_header)
            if not sender_name:
                sender_name = sender_email.split('@')[0].capitalize()

            # Parse date header into datetime
            try:
                dt_received = parsedate_to_datetime(date_header) if date_header else datetime.now()
            except Exception:
                dt_received = datetime.now()

            body_text = clean_email_text(get_email_body(payload))

            extracted_emails.append({
                "id": msg['id'],
                "thread_id": msg.get('threadId', ''),
                "sender_raw": from_header,
                "sender_name": sender_name,
                "sender_email": sender_email.strip().lower(),
                "subject": subject,
                "date_received": dt_received,
                "date_str": date_header,
                "body": body_text
            })
        except Exception as e:
            print(f"⚠️ Failed to extract message {msg['id']}: {e}")

    return extracted_emails

def mark_email_triaged(service, message_id: str, label_id: str) -> bool:
    """Removes 'UNREAD' and adds 'AI-Triaged' label to message."""
    try:
        body = {
            "removeLabelIds": ["UNREAD"],
            "addLabelIds": [label_id] if label_id else []
        }
        service.users().messages().modify(userId='me', id=message_id, body=body).execute()
        return True
    except HttpError as e:
        print(f"⚠️ Failed to mark message {message_id} as triaged: {e}")
        return False
