#!/usr/bin/env python3
# sync_calendar_notion.py
# A Python script to continuously sync Google Calendar events to a Notion database,
# handling additions, updates, and deletions, with single authentication,
# including events from 3 days past to 7 days future, and minimal skip logging.

import json
import os.path
import time
from datetime import datetime, timedelta
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from notion_client import Client
import threading
from flask import Flask
import logging

# ------------------- CONFIGURATION -------------------

# Google Calendar API scopes
SCOPES = ['https://www.googleapis.com/auth/calendar.readonly']

# Path to your Google OAuth 2.0 client credentials JSON file
CREDENTIALS_PATH = 'credentials.json'
# Path where the script will save/load your refresh token
TOKEN_PATH = 'token.json'
# Path to store the local cache of synced events
CACHE_PATH = 'event_cache.json'

# Replace with your actual Google Calendar ID (e.g., your email or calendar ID)
CALENDAR_ID = 'primary'
# Replace with your actual Notion Integration Token
NOTION_TOKEN = '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'
# Replace with your actual Notion Database ID
NOTION_DATABASE_ID = '6a90ca4647934efe9e8dbc63c6e6c4a5'

# Polling interval (seconds)
POLL_INTERVAL = 10  # 5 minutes
# Time range for fetching future events (days from now)
EVENT_TIME_RANGE_DAYS = 2  # Sync events up to 7 days in the future
# Time range for fetching past events (days before now)
PAST_EVENT_RANGE_DAYS = 3  # Sync events from 3 days in the past

# -------------------------------------------------------------------

# Flask app setup
app = Flask(__name__)

@app.route('/')
def index():
    return "hey Yash"

def load_cache():
    """Load the event cache from disk."""
    if os.path.exists(CACHE_PATH):
        try:
            with open(CACHE_PATH, 'r') as f:
                return json.load(f)
        except Exception as e:
            print(f"Error loading cache file '{CACHE_PATH}': {e}")
    return {'events': {}, 'last_sync': None}

def save_cache(cache):
    """Save the event cache to disk."""
    try:
        with open(CACHE_PATH, 'w') as f:
            json.dump(cache, f, indent=2)
        print(f"Cache saved to '{CACHE_PATH}'")
    except Exception as e:
        print(f"Error saving cache file '{CACHE_PATH}': {e}")

def get_calendar_service():
    """
    Authenticates with Google Calendar API using OAuth 2.0 flow.
    Handles loading/saving tokens and browser-based authorization if needed.
    """
    creds = None

    if os.path.exists(TOKEN_PATH):
        try:
            creds = Credentials.from_authorized_user_file(TOKEN_PATH, SCOPES)
        except Exception as e:
            print(f"Error loading token file '{TOKEN_PATH}': {e}")
            os.remove(TOKEN_PATH)
            print(f"Deleted corrupted token file '{TOKEN_PATH}'. Re-authorization required.")
            creds = None

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            print("Refreshing access token...")
            try:
                creds.refresh(Request())
                print("Token refreshed successfully.")
            except Exception as e:
                print(f"Error refreshing token: {e}")
                print("Likely the refresh token is invalid or expired. Re-authorization required.")
                creds = None

        if not creds:
            print("Performing initial browser authorization...")
            if not os.path.exists(CREDENTIALS_PATH):
                print(f"Error: Google credentials file not found at '{CREDENTIALS_PATH}'")
                exit(1)

            try:
                flow = InstalledAppFlow.from_client_secrets_file(CREDENTIALS_PATH, SCOPES)
                creds = flow.run_local_server(port=0)
                print("Authorization successful.")
            except FileNotFoundError:
                print(f"Error: '{CREDENTIALS_PATH}' not found. Please check the file path.")
                exit(1)
            except json.JSONDecodeError:
                print(f"Error: Could not parse '{CREDENTIALS_PATH}'. Ensure it's valid JSON.")
                exit(1)
            except Exception as e:
                print(f"Error during authorization flow: {e}")
                exit(1)

        try:
            with open(TOKEN_PATH, 'w') as token:
                token.write(creds.to_json())
            print(f"Credentials saved to '{TOKEN_PATH}'")
        except Exception as e:
            print(f"Warning: Could not save token file '{TOKEN_PATH}': {e}")

    try:
        service = build('calendar', 'v3', credentials=creds)
        return service, creds
    except Exception as e:
        print(f"Error building Calendar service: {e}")
        return None, None

def refresh_credentials_if_needed(service, creds):
    """Refresh credentials if expired and rebuild the service."""
    if creds and creds.expired and creds.refresh_token:
        print("Access token expired. Refreshing...")
        try:
            creds.refresh(Request())
            print("Token refreshed successfully.")
            with open(TOKEN_PATH, 'w') as token:
                token.write(creds.to_json())
            print(f"Updated credentials saved to '{TOKEN_PATH}'")
            return build('calendar', 'v3', credentials=creds), creds
        except Exception as e:
            print(f"Error refreshing token: {e}")
            return None, None
    return service, creds

def get_calendar_events(service, time_min=None, time_max=None):
    """
    Fetch events between time_min and time_max.
    Defaults to PAST_EVENT_RANGE_DAYS before now -> EVENT_TIME_RANGE_DAYS ahead.
    """
    if service is None:
        print("Calendar service not available. Cannot fetch events.")
        return []

    time_min = (datetime.utcnow() - timedelta(days=PAST_EVENT_RANGE_DAYS)).isoformat() + 'Z'
    time_max = (datetime.utcnow() + timedelta(days=EVENT_TIME_RANGE_DAYS)).isoformat() + 'Z'

    print(f"Fetching events from {time_min} to {time_max}")

    try:
        events_result = service.events().list(
            calendarId=CALENDAR_ID,
            timeMin=time_min,
            timeMax=time_max,
            singleEvents=True,
            orderBy='startTime'
        ).execute()
        return events_result.get('items', [])
    except Exception as e:
        print(f"Error fetching calendar events: {e}")
        return []

def get_notion_page_id(notion, event_id):
    """Retrieve the Notion page ID for a given Google event ID."""
    try:
        resp = notion.databases.query(
            database_id=NOTION_DATABASE_ID,
            filter={
                "property": "Event ID",
                "rich_text": {"equals": event_id}
            }
        )
        results = resp.get('results', [])
        return results[0]['id'] if results else None
    except Exception as e:
        print(f"Error querying Notion for event ID {event_id}: {e}")
        return None

def event_has_changed(event, cached_event):
    """Check if the event has changed compared to the cached version."""
    if not cached_event:
        return True
    keys_to_check = ['summary', 'start', 'end', 'description', 'location', 'htmlLink']
    for key in keys_to_check:
        if event.get(key) != cached_event.get(key):
            return True
    return False

def create_or_update_notion_page(notion, event, existing_page_id=None):
    """
    Create or update a Notion page for the Google Calendar event.
    """
    # Determine timed vs all-day
    start_dt = event['start'].get('dateTime')
    end_dt = event['end'].get('dateTime')
    start_date = event['start'].get('date')
    end_date = event['end'].get('date')

    notion_date_prop = {}
    if start_dt:
        notion_date_prop = {"start": start_dt, "end": end_dt}
    elif start_date:
        notion_date_prop = {"start": start_date}
        if end_date:
            try:
                sd = datetime.strptime(start_date, '%Y-%m-%d').date()
                ed = datetime.strptime(end_date, '%Y-%m-%d').date()
                if (ed - sd).days > 0:
                    end_inclusive = ed - timedelta(days=1)
                    notion_date_prop["end"] = end_inclusive.strftime('%Y-%m-%d')
            except Exception:
                pass

    props = {
        "Name": {"title": [{"text": {"content": event.get('summary', 'No Title')}}]},
        "Event ID": {"rich_text": [{"text": {"content": event['id']}}]},
        "Date": {"date": notion_date_prop},
        "Description": {"rich_text": [{"text": {"content": event.get('description', '')[:2000]}}]}
    }

    if event.get('location'):
        props["Location"] = {"rich_text": [{"text": {"content": event['location'][:2000]}}]}

    if event.get('htmlLink'):
        props["Link"] = {
            "rich_text": [
                {"text": {"content": event['htmlLink'], "link": {"url": event['htmlLink']}}}
            ]
        }

    try:
        if existing_page_id:
            notion.pages.update(page_id=existing_page_id, properties=props)
            action = "Updated"
        else:
            notion.pages.create(parent={"database_id": NOTION_DATABASE_ID}, properties=props)
            action = "Created"
        print(f"{action} Notion page for event '{event.get('summary', 'No Title')}'")
        return True
    except Exception as e:
        print(f"Error {'updating' if existing_page_id else 'creating'} Notion page for event '{event.get('summary', '')}': {e}")
        return False

def delete_notion_page(notion, page_id, event_id):
    """Delete a Notion page by its page ID."""
    try:
        notion.pages.update(page_id=page_id, archived=True)
        print(f"Deleted Notion page for event ID {event_id}")
        return True
    except Exception as e:
        print(f"Error deleting Notion page for event ID {event_id}: {e}")
        return False

def sync_events(service, creds):
    """Sync Google Calendar events to Notion, handling additions, updates, and deletions."""
    print(f"\nStarting sync at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}...")
    
    # Refresh credentials if needed
    service, creds = refresh_credentials_if_needed(service, creds)
    if not service:
        print("Cannot proceed without calendar service.")
        return False, service, creds

    notion = Client(auth=NOTION_TOKEN)
    cache = load_cache()
    events = get_calendar_events(service)
    current_event_ids = set()

    added = updated = deleted = skipped = 0

    for event in events:
        event_id = event.get('id')
        if not event_id:
            continue
        current_event_ids.add(event_id)
        title = event.get('summary', 'No Title')
        cached_event = cache['events'].get(event_id)

        page_id = get_notion_page_id(notion, event_id)
        if page_id and not event_has_changed(event, cached_event):
            skipped += 1
            continue

        if create_or_update_notion_page(notion, event, page_id):
            cache['events'][event_id] = {
                'summary': event.get('summary'),
                'start': event.get('start'),
                'end': event.get('end'),
                'description': event.get('description'),
                'location': event.get('location'),
                'htmlLink': event.get('htmlLink')
            }
            if page_id:
                updated += 1
            else:
                added += 1
        else:
            print(f"Failed to process: {title}")

    # Check for deleted events
    for event_id in list(cache['events'].keys()):
        if event_id not in current_event_ids:
            page_id = get_notion_page_id(notion, event_id)
            if page_id and delete_notion_page(notion, page_id, event_id):
                del cache['events'][event_id]
                deleted += 1
            else:
                print(f"Failed to delete Notion page for event ID {event_id}")

    cache['last_sync'] = datetime.utcnow().isoformat()
    save_cache(cache)

    print(f"Sync complete. Added: {added}, Updated: {updated}, Deleted: {deleted}, Skipped: {skipped}")
    return True, service, creds

def run_flask_app():
    """Runs the Flask app."""
    # Silence Werkzeug's default logger for cleaner output
    log = logging.getLogger('werkzeug')
    log.setLevel(logging.ERROR)

    print("Starting Flask server on http://0.0.0.0:8000")
    # Use '0.0.0.0' to make it accessible from the network
    # Set debug=False for production/concurrent use
    app.run(host='0.0.0.0', port=8000, debug=False)

def main():
    """Run the sync continuously with single authentication."""
    print("Starting continuous calendar sync...")

    # Start Flask app in a background thread
    flask_thread = threading.Thread(target=run_flask_app, daemon=True)
    flask_thread.start()

    service, creds = get_calendar_service()
    if not service:
        print("Failed to initialize calendar service. Exiting...")
        exit(1)

    while True:
        try:
            success, service, creds = sync_events(service, creds)
            if not success:
                print(f"Sync failed. Retrying in {POLL_INTERVAL} seconds...")
            time.sleep(POLL_INTERVAL)
        except KeyboardInterrupt:
            print("\nStopping sync...")
            break
        except Exception as e:
            print(f"Unexpected error during sync: {e}")
            print(f"Retrying in {POLL_INTERVAL} seconds...")
            time.sleep(POLL_INTERVAL)

if __name__ == '__main__':
    main()
