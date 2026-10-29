import os
import base64
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

# If modifying these scopes, delete the file token.json.
# We need modify rights to remove the "UNREAD" label later.
SCOPES = ['https://www.googleapis.com/auth/gmail.modify']

def authenticate_gmail():
    """Handles the OAuth2 flow and returns the Gmail service."""
    creds = None
    # token.json stores the user's access and refresh tokens
    if os.path.exists('token.json'):
        creds = Credentials.from_authorized_user_file('token.json', SCOPES)
    
    # If there are no (valid) credentials available, let the user log in.
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file('credentials.json', SCOPES)
            creds = flow.run_local_server(port=0)
        # Save the credentials for the next run
        with open('token.json', 'w') as token:
            token.write(creds.to_json())

    return build('gmail', 'v1', credentials=creds)

def get_email_body(payload):
    """Recursively parses the email payload to find the plain text body."""
    if 'data' in payload.get('body', {}):
        # Base64url decode the text
        data = payload['body']['data']
        return base64.urlsafe_b64decode(data).decode('utf-8')
    
    # If it's a multipart email, dig into the parts
    if 'parts' in payload:
        for part in payload['parts']:
            if part['mimeType'] == 'text/plain' or part['mimeType'] == 'multipart/alternative':
                return get_email_body(part)
    return "[No Plain Text Found]"

def fetch_unread_emails(service, max_results=5, query='is:unread in:inbox category:primary'):
    """Fetches the latest unread emails in the Primary inbox and extracts their text."""
    print(f"📥 Checking for up to {max_results} unread emails matching: '{query}'...\n")
    
    # Search for unread emails in the Primary inbox
    results = service.users().messages().list(userId='me', q=query, maxResults=max_results).execute()
    messages = results.get('messages', [])

    if not messages:
        print("No unread messages found.")
        return []

    extracted_emails = []
    
    for msg in messages:
        # Fetch the full email payload
        msg_data = service.users().messages().get(userId='me', id=msg['id'], format='full').execute()
        payload = msg_data['payload']
        headers = payload.get('headers', [])
        
        # Extract metadata from headers
        subject = next((header['value'] for header in headers if header['name'] == 'Subject'), 'No Subject')
        sender = next((header['value'] for header in headers if header['name'] == 'From'), 'Unknown Sender')
        date_received = next((header['value'] for header in headers if header['name'] == 'Date'), 'Unknown Date')
        
        # Extract clean text body
        body_text = get_email_body(payload)
        
        extracted_emails.append({
            "id": msg['id'],
            "sender": sender,
            "subject": subject,
            "date": date_received,
            "body": body_text.strip()
        })
        
        print(f"Found Email From: {sender} | Subject: {subject}")

    return extracted_emails

if __name__ == '__main__':
    gmail_service = authenticate_gmail()
    emails = fetch_unread_emails(gmail_service)
    
    # Print the first extracted email body to verify it worked
    if emails:
        print("\n--- Raw Text of First Email ---")
        print(emails[0]['body'][:500] + "...\n")