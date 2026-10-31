import os
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

SCOPES = [
    'https://www.googleapis.com/auth/gmail.modify',
    'https://www.googleapis.com/auth/spreadsheets',
    'https://www.googleapis.com/auth/calendar',
]

TOKEN_FILE = 'token.json'
CREDENTIALS_FILE = 'credentials.json'

def get_credentials(interactive: bool = True) -> Credentials:
    """
    Returns valid Google OAuth2 credentials covering Gmail, Sheets, and Calendar.
    If scopes changed or token is missing/expired, refreshes or requests login.
    """
    creds = None

    if os.path.exists(TOKEN_FILE):
        creds = Credentials.from_authorized_user_file(TOKEN_FILE, SCOPES)

    # Check if creds are valid and have all required scopes
    has_all_scopes = creds and creds.has_scopes(SCOPES)

    if not creds or not creds.valid or not has_all_scopes:
        if creds and creds.expired and creds.refresh_token and has_all_scopes:
            try:
                creds.refresh(Request())
            except Exception as e:
                print(f"Token refresh failed: {e}. Re-authenticating...")
                creds = None

        if not creds or not has_all_scopes:
            if not interactive:
                raise RuntimeError(
                    f"Authentication required for scopes: {SCOPES}. Run an interactive session to authenticate."
                )
            if not os.path.exists(CREDENTIALS_FILE):
                raise FileNotFoundError(
                    f"Missing {CREDENTIALS_FILE}. Please download client secrets from Google Cloud Console."
                )
            
            print("\n🔑 Launching browser for Google Workspace authentication (Gmail + Sheets + Calendar)...")
            flow = InstalledAppFlow.from_client_secrets_file(CREDENTIALS_FILE, SCOPES)
            creds = flow.run_local_server(port=0)

            # Persist updated token
            with open(TOKEN_FILE, 'w') as token_out:
                token_out.write(creds.to_json())
            print(f"✅ Credentials successfully saved to {TOKEN_FILE}.\n")

    return creds

def get_service(service_name: str, version: str, interactive: bool = True):
    """Factory to build an authenticated Google API service client."""
    creds = get_credentials(interactive=interactive)
    return build(service_name, version, credentials=creds)

