#!/usr/bin/env python3
# sync_calendar_notion.py
# A Python script to continuously sync Google Calendar events to a Notion database,
# handling additions, updates, and deletions, with single authentication,
# including events from 3 days past to 7 days future, and minimal skip logging.

import json
import os.path
import time
import threading
import logging
import traceback
from datetime import datetime, timedelta
from typing import Optional, Dict, Any, Tuple, List, Set
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from googleapiclient.discovery import build, Resource
from googleapiclient.errors import HttpError
from notion_client import Client
from notion_client.errors import APIResponseError
from flask import Flask

# ------------------- CONFIGURATION -------------------
# NOTE: Storing secrets directly in code is not recommended for production.
# Consider using environment variables or a secret manager.

# Google Calendar API scopes
SCOPES: List[str] = ['https://www.googleapis.com/auth/calendar.readonly']

# Path to your Google OAuth 2.0 client credentials JSON file
CREDENTIALS_PATH: str = 'credentials.json'
# Path where the script will save/load your refresh token
TOKEN_PATH: str = 'token.json'
# Path to store the local cache of synced events
CACHE_PATH: str = 'event_cache.json'

# Replace with your actual Google Calendar ID (e.g., your email or calendar ID)
CALENDAR_ID: str = 'primary'
# Replace with your actual Notion Integration Token
NOTION_TOKEN: str = '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'
# Replace with your actual Notion Database ID
NOTION_DATABASE_ID: str = '6a90ca4647934efe9e8dbc63c6e6c4a5'

# Polling interval (seconds) - WARNING: 10 seconds is very frequent and may lead to rate limits.
# Consider increasing this to 60, 180, or 300 seconds for stability.
POLL_INTERVAL: int = 60 # Increased default polling interval
# Time range for fetching future events (days from now)
EVENT_TIME_RANGE_DAYS: int = 7 # Changed default to 7 days future
# Time range for fetching past events (days before now)
PAST_EVENT_RANGE_DAYS: int = 3

# Flask App Initialization
app = Flask(__name__)

# ------------------- HELPER TYPES -------------------
# Define a type for the cache structure
EventCache = Dict[str, Any] # Example: {'event_id': {'details': {...}, 'page_id': '...'}, ...}
CacheStructure = Dict[str, Any] # Example: {'events': EventCache, 'last_sync': '...'}

# ------------------- CACHE MANAGEMENT -------------------

def load_cache() -> CacheStructure:
    """Load the event cache from disk, ensuring basic structure."""
    cache: CacheStructure = {'events': {}, 'last_sync': None}
    if os.path.exists(CACHE_PATH):
        try:
            with open(CACHE_PATH, 'r') as f:
                loaded_data = json.load(f)
                # Basic validation
                if isinstance(loaded_data, dict) and 'events' in loaded_data:
                    cache = loaded_data
                else:
                    print(f"Warning: Cache file '{CACHE_PATH}' has unexpected format. Starting fresh.")
        except json.JSONDecodeError:
            print(f"Error: Could not parse cache file '{CACHE_PATH}'. Starting fresh.")
        except Exception as e:
            print(f"Error loading cache file '{CACHE_PATH}': {e}. Starting fresh.")
    # Ensure 'events' key exists
    if 'events' not in cache:
        cache['events'] = {}
    return cache

def save_cache(cache: CacheStructure) -> None:
    """Save the event cache to disk."""
    try:
        with open(CACHE_PATH, 'w') as f:
            json.dump(cache, f, indent=2)
        # Removed noisy save message: print(f"Cache saved to '{CACHE_PATH}'")
    except Exception as e:
        print(f"Error saving cache file '{CACHE_PATH}': {e}")

# ------------------- GOOGLE AUTHENTICATION -------------------

def get_calendar_service() -> Tuple[Optional[Resource], Optional[Credentials]]:
    """Authenticates with Google Calendar API, handles token refresh/creation."""
    creds: Optional[Credentials] = None
    # ... (rest of the Google Auth logic remains largely the same, adding type hints)
    if os.path.exists(TOKEN_PATH):
        try:
            creds = Credentials.from_authorized_user_file(TOKEN_PATH, SCOPES)
        except Exception as e:
            print(f"Error loading token file '{TOKEN_PATH}': {e}", flush=True)
            try:
                os.remove(TOKEN_PATH)
                print(f"Deleted corrupted token file '{TOKEN_PATH}'. Re-authorization required.", flush=True)
            except OSError as remove_err:
                print(f"Error deleting corrupted token file '{TOKEN_PATH}': {remove_err}", flush=True)
            creds = None

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            print("Refreshing access token...", flush=True)
            try:
                creds.refresh(Request())
                print("Token refreshed successfully.", flush=True)
                # Save the refreshed token
                try:
                    with open(TOKEN_PATH, 'w') as token:
                        token.write(creds.to_json())
                    print(f"Refreshed credentials saved to '{TOKEN_PATH}'", flush=True)
                except Exception as save_err:
                    print(f"Warning: Could not save refreshed token file '{TOKEN_PATH}': {save_err}", flush=True)
            except Exception as e:
                print(f"Error refreshing token: {e}", flush=True)
                print("Likely the refresh token is invalid or expired. Re-authorization required.", flush=True)
                # Attempt re-authorization by clearing creds
                creds = None

        if not creds:
            print("Performing initial browser authorization...", flush=True)
            if not os.path.exists(CREDENTIALS_PATH):
                print(f"Error: Google credentials file not found at '{CREDENTIALS_PATH}'. Exiting.", flush=True)
                return None, None # Return None instead of exiting

            try:
                flow = InstalledAppFlow.from_client_secrets_file(CREDENTIALS_PATH, SCOPES)
                # Specify redirect_uri for better control if needed, but default usually works
                creds = flow.run_local_server(port=0) # Server closes automatically
                print("Authorization successful.", flush=True)
                # Save the new credentials immediately
                try:
                    with open(TOKEN_PATH, 'w') as token:
                        token.write(creds.to_json())
                    print(f"Credentials saved to '{TOKEN_PATH}'", flush=True)
                except Exception as save_err:
                    print(f"Warning: Could not save new token file '{TOKEN_PATH}': {save_err}", flush=True)

            except FileNotFoundError:
                print(f"Error: '{CREDENTIALS_PATH}' not found. Please check the path. Exiting.", flush=True)
                return None, None
            except json.JSONDecodeError:
                print(f"Error: Could not parse '{CREDENTIALS_PATH}'. Ensure it's valid JSON. Exiting.", flush=True)
                return None, None
            except Exception as e:
                print(f"Error during authorization flow: {e}", flush=True)
                traceback.print_exc()
                return None, None

    # Build the service
    try:
        service: Resource = build('calendar', 'v3', credentials=creds)
        print("Google Calendar service built successfully.", flush=True)
        return service, creds
    except Exception as e:
        print(f"Error building Calendar service: {e}", flush=True)
        traceback.print_exc()
        return None, None

def refresh_credentials_if_needed(service: Optional[Resource], creds: Optional[Credentials]) -> Tuple[Optional[Resource], Optional[Credentials]]:
    """Refresh credentials if expired and rebuild the service."""
    if creds and creds.valid and not creds.expired:
        return service, creds # Still valid, no action needed

    print("Credentials invalid or expired. Attempting refresh or re-auth...", flush=True)
    # Attempt to get fresh service/creds
    new_service, new_creds = get_calendar_service()
    return new_service, new_creds

# ------------------- GOOGLE CALENDAR API -------------------

def get_calendar_events(service: Resource) -> List[Dict[str, Any]]:
    """Fetch events within the configured time range."""
    now = datetime.utcnow()
    time_min = (now - timedelta(days=PAST_EVENT_RANGE_DAYS)).isoformat() + 'Z'
    time_max = (now + timedelta(days=EVENT_TIME_RANGE_DAYS)).isoformat() + 'Z'

    print(f"Fetching Google Calendar events from {time_min} to {time_max}", flush=True)
    try:
        events_result = service.events().list(
            calendarId=CALENDAR_ID,
            timeMin=time_min,
            timeMax=time_max,
            singleEvents=True,
            orderBy='startTime'
        ).execute()
        items = events_result.get('items', [])
        print(f"Fetched {len(items)} events from Google Calendar.", flush=True)
        return items
    except HttpError as e:
        print(f"Google API HTTP Error fetching events: {e}", flush=True)
        # Check for specific errors like 401/403 which might indicate auth issues
        if e.resp.status in [401, 403]:
            print("Authorization error encountered. Credentials might be invalid or revoked.", flush=True)
            # Consider triggering re-authentication or stopping the sync
        return [] # Return empty list on error
    except Exception as e:
        print(f"Unexpected error fetching calendar events: {e}", flush=True)
        traceback.print_exc()
        return []

# ------------------- NOTION API HELPERS -------------------

def _create_notion_properties(event: Dict[str, Any]) -> Dict[str, Any]:
    """Helper function to build the Notion properties dictionary from a Google event."""
    start_dt = event.get('start', {}).get('dateTime')
    end_dt = event.get('end', {}).get('dateTime')
    start_date = event.get('start', {}).get('date')
    end_date = event.get('end', {}).get('date')

    notion_date_prop = {}
    if start_dt:
        notion_date_prop = {"start": start_dt}
        if end_dt:
            notion_date_prop["end"] = end_dt
    elif start_date:
        notion_date_prop = {"start": start_date}
        # Handle multi-day all-day events correctly for Notion
        if end_date:
            try:
                sd = datetime.strptime(start_date, '%Y-%m-%d').date()
                ed = datetime.strptime(end_date, '%Y-%m-%d').date()
                # Notion's end date for all-day is exclusive, Google's is inclusive.
                # If the end date is exactly one day after the start date, it's a single all-day event in Notion.
                if (ed - sd).days > 1:
                    # For multi-day, Notion expects the day *after* the last day.
                    # But Google uses the *last day*. To be safe, let's just not set the end date
                    # if Notion doesn't require it for multi-day spans starting on start_date.
                    # Let's test setting it to the day *before* Google's end_date for clarity.
                    end_inclusive = ed - timedelta(days=1)
                    if end_inclusive >= sd:
                       notion_date_prop["end"] = end_inclusive.strftime('%Y-%m-%d')
                    # If unsure, omitting end_date for multi-day might be safer depending on Notion behavior.
            except ValueError:
                 print(f"Warning: Could not parse date format for event {event.get('id')}", flush=True)
            except Exception as date_err:
                 print(f"Warning: Error processing date range for event {event.get('id')}: {date_err}", flush=True)

    # Truncate descriptions/locations safely
    description = (event.get('description', '') or '')[:2000]
    location = (event.get('location', '') or '')[:2000]
    html_link = event.get('htmlLink', '')

    props = {
        "Name": {"title": [{"text": {"content": event.get('summary', 'No Title')}}]} if event.get('summary') else None,
        "Event ID": {"rich_text": [{"text": {"content": event['id']}}]},
        "Date": {"date": notion_date_prop} if notion_date_prop else None,
        "Description": {"rich_text": [{"text": {"content": description}}]} if description else None,
        "Location": {"rich_text": [{"text": {"content": location}}]} if location else None,
        "Link": {"url": html_link} if html_link else None,
         # Example of a Status property (if you have one in Notion)
         # "Status": {"status": {"name": "Synced"}}
    }
    # Remove None properties to avoid sending empty values
    return {k: v for k, v in props.items() if v is not None}

def event_details_changed(event: Dict[str, Any], cached_details: Optional[Dict[str, Any]]) -> bool:
    """Check if relevant event details have changed compared to the cached version."""
    if not cached_details:
        return True # Treat as changed if no cache entry exists

    # Compare specific fields that map to Notion properties
    keys_to_check = ['summary', 'start', 'end', 'description', 'location', 'htmlLink']
    for key in keys_to_check:
        # Handle nested dicts like 'start', 'end'
        current_val = event.get(key)
        cached_val = cached_details.get(key)
        if isinstance(current_val, dict) or isinstance(cached_val, dict):
            # Simple comparison for dicts like {'dateTime': ...} or {'date': ...}
            if json.dumps(current_val, sort_keys=True) != json.dumps(cached_val, sort_keys=True):
                 print(f"Change detected in '{key}' for event {event.get('id')}", flush=True)
                 return True
        elif current_val != cached_val:
            print(f"Change detected in '{key}' for event {event.get('id')}", flush=True)
            return True
    return False

def create_or_update_notion_page(notion: Client, event: Dict[str, Any], existing_page_id: Optional[str]) -> Optional[str]:
    """Create or update a Notion page for the event. Returns the Notion Page ID on success."""
    properties = _create_notion_properties(event)
    event_summary = event.get('summary', 'No Title')
    event_id = event['id']

    try:
        if existing_page_id:
            # Update existing page
            response = notion.pages.update(page_id=existing_page_id, properties=properties)
            action = "Updated"
            page_id = existing_page_id
        else:
            # Create new page
            response = notion.pages.create(parent={"database_id": NOTION_DATABASE_ID}, properties=properties)
            action = "Created"
            page_id = response.get('id') # Get ID from creation response

        if page_id:
             print(f"{action} Notion page for event '{event_summary}' (ID: {event_id}, Page: {page_id})", flush=True)
             return page_id
        else:
             print(f"Warning: {action} action for event '{event_summary}' (ID: {event_id}) did not return a page ID.", flush=True)
             return None

    except APIResponseError as e:
        print(f"Notion API Error ({e.status}) while {'updating' if existing_page_id else 'creating'} page for event '{event_summary}' (ID: {event_id}): {e.code} - {e.message}", flush=True)
        # Optional: print e.body for full error details
        # print(f"Error Body: {e.body}")
        return None
    except Exception as e:
        print(f"Unexpected error {'updating' if existing_page_id else 'creating'} Notion page for event '{event_summary}' (ID: {event_id}): {e}", flush=True)
        traceback.print_exc()
        return None

def delete_notion_page(notion: Client, page_id: str, event_id_for_log: str) -> bool:
    """Archive a Notion page by its ID. Returns True on success."""
    try:
        notion.pages.update(page_id=page_id, archived=True)
        print(f"Archived Notion page {page_id} for event ID {event_id_for_log}", flush=True)
        return True
    except APIResponseError as e:
        print(f"Notion API Error ({e.status}) archiving page {page_id} for event ID {event_id_for_log}: {e.code} - {e.message}", flush=True)
        # If it's a 404, the page might already be deleted/archived
        if e.status == 404:
            print("Page not found, might have been deleted already.", flush=True)
            return True # Treat as success if already gone
        return False
    except Exception as e:
        print(f"Unexpected error archiving Notion page {page_id} for event ID {event_id_for_log}: {e}", flush=True)
        traceback.print_exc()
        return False

# ------------------- CORE SYNC LOGIC -------------------

def sync_events(service: Resource, creds: Credentials, notion: Client, cache: CacheStructure) -> Tuple[bool, Optional[Resource], Optional[Credentials]]:
    """Sync Google Calendar events to Notion using the improved cache."""
    print(f"\nStarting sync cycle at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}...", flush=True)

    # 1. Refresh Google credentials if needed
    service, creds = refresh_credentials_if_needed(service, creds)
    if not service or not creds:
        print("Cannot proceed: Google Calendar service/credentials unavailable.", flush=True)
        return False, service, creds # Indicate failure but pass potentially updated creds back

    # 2. Fetch current Google Calendar events
    google_events = get_calendar_events(service)
    if google_events is None: # Check if fetch failed
        print("Cannot proceed: Failed to fetch Google Calendar events.", flush=True)
        return False, service, creds

    # Create a map for quick lookup: {event_id: event_data}
    current_events_map: Dict[str, Dict[str, Any]] = {event['id']: event for event in google_events if event.get('id')}
    current_event_ids: Set[str] = set(current_events_map.keys())

    # 3. Load cache (ensure 'events' key exists)
    cached_events: EventCache = cache.setdefault('events', {})
    cached_event_ids: Set[str] = set(cached_events.keys())

    added_count = updated_count = deleted_count = skipped_count = failed_count = 0

    # 4. Process current events (Additions/Updates)
    print("Processing current Google events for additions/updates...", flush=True)
    for event_id, event_data in current_events_map.items():
        cached_entry = cached_events.get(event_id)
        cached_details = cached_entry.get('details') if cached_entry else None
        cached_page_id = cached_entry.get('page_id') if cached_entry else None

        if event_details_changed(event_data, cached_details):
            print(f"Event '{event_data.get('summary','[No Title]')}' (ID: {event_id}) needs sync.", flush=True)
            # Pass cached_page_id if available
            new_page_id = create_or_update_notion_page(notion, event_data, cached_page_id)

            if new_page_id:
                # Update cache with new details and page_id
                cached_events[event_id] = {
                    'details': {
                        'summary': event_data.get('summary'),
                        'start': event_data.get('start'),
                        'end': event_data.get('end'),
                        'description': event_data.get('description'),
                        'location': event_data.get('location'),
                        'htmlLink': event_data.get('htmlLink')
                    },
                    'page_id': new_page_id
                }
                if cached_page_id:
                    updated_count += 1
                else:
                    added_count += 1
            else:
                # Failed to create/update Notion page
                failed_count += 1
                # Optional: Consider removing from cache if creation failed repeatedly?
                # Or keep trying next cycle.
        else:
            # Event exists and hasn't changed
            # Ensure page_id is in cache if we have it (migration from old cache format)
            if cached_entry and not cached_page_id:
                 # If we skipped because it didn't change, but we *don't* have a page_id,
                 # try to query for it once to populate the cache.
                 # This helps migrate old caches without page_ids.
                 print(f"Attempting to find page_id for unchanged cached event {event_id}", flush=True)
                 page_id_from_query = get_notion_page_id(notion, event_id) # Reuse the old query func here
                 if page_id_from_query:
                     cached_events[event_id]['page_id'] = page_id_from_query
                     print(f"Found and cached page_id {page_id_from_query} for {event_id}", flush=True)
                 else:
                     print(f"Could not find page_id for {event_id}. Will retry update next cycle if event changes.", flush=True)

            skipped_count += 1

    # 5. Process Deletions (Events in cache but not in current Google events)
    print("Checking for deleted events (in cache, not in Google fetch)...", flush=True)
    deleted_event_ids = cached_event_ids - current_event_ids
    for event_id in deleted_event_ids:
        cached_entry = cached_events.get(event_id)
        page_id_to_delete = cached_entry.get('page_id') if cached_entry else None

        if page_id_to_delete:
            print(f"Event {event_id} seems deleted. Attempting to archive Notion page {page_id_to_delete}.", flush=True)
            if delete_notion_page(notion, page_id_to_delete, event_id):
                # Successfully archived, remove from cache
                del cached_events[event_id]
                deleted_count += 1
            else:
                # Failed to archive Notion page
                failed_count += 1
                print(f"Failed to archive Notion page for deleted event {event_id}. Will retry next cycle.", flush=True)
        else:
            # Event is not in current Google list, but we don't have a page_id in cache.
            # This could be an old cache entry before page_ids were stored, or creation failed previously.
            # It's safest to just remove it from the cache now.
            print(f"Removing event {event_id} from cache (likely deleted from Google, no Notion page ID found/stored).", flush=True)
            del cached_events[event_id]
            # We don't count this as a successful deletion as we didn't archive anything.

    # 6. Save cache and report
    cache['last_sync'] = datetime.utcnow().isoformat()
    save_cache(cache)

    print(f"Sync cycle complete. Added: {added_count}, Updated: {updated_count}, Archived: {deleted_count}, Skipped: {skipped_count}, Failed: {failed_count}", flush=True)
    return True, service, creds

# ------------------- BACKGROUND SYNC THREAD -------------------

def run_sync_loop():
    """Run the sync continuously in a background thread."""
    print("Initializing background sync thread...", flush=True)

    # Initial setup: Get Google Service/Creds and Notion Client
    service, creds = get_calendar_service()
    if not service or not creds:
        print("Sync Thread: Failed initial Google auth. Stopping thread.", flush=True)
        return

    try:
        notion = Client(auth=NOTION_TOKEN)
        print("Sync Thread: Notion client initialized.", flush=True)
    except Exception as e:
        print(f"Sync Thread: Failed to initialize Notion client: {e}. Stopping thread.", flush=True)
        traceback.print_exc()
        return

    print("Starting continuous calendar sync loop...", flush=True)
    while True:
        print(f"\n{datetime.now()}: Sync thread starting cycle.", flush=True)
        current_cache = load_cache() # Load fresh cache each cycle

        try:
            success, service, creds = sync_events(service, creds, notion, current_cache)
            if not success:
                print(f"Sync cycle reported failure. Check logs above.", flush=True)
                # If creds became None, try to re-auth immediately before sleep
                if not creds:
                    print("Attempting Google re-authentication before next cycle...", flush=True)
                    service, creds = get_calendar_service()
                    if not service or not creds:
                        print("Re-authentication failed. Will retry after interval.", flush=True)

        except Exception as cycle_err:
            print(f"!!! UNEXPECTED CRITICAL ERROR during sync cycle: {cycle_err}", flush=True)
            traceback.print_exc()
            # Consider if service/creds need reset here too

        print(f"Sync thread finished cycle. Waiting {POLL_INTERVAL} seconds...", flush=True)
        time.sleep(POLL_INTERVAL)

# ------------------- FLASK APP -------------------

@app.route('/')
def home():
    """Basic route for the Flask app."""
    return "<h1>Hey Yash - Calendar Sync is Running</h1>"

# ------------------- MAIN EXECUTION -------------------

def main():
    """Check config, start the sync thread, and run the Flask app."""
    print("Application starting...", flush=True)

    # Configuration Check
    if not NOTION_TOKEN or not NOTION_TOKEN.startswith("secret_"):
         print("Error: NOTION_TOKEN is missing or looks invalid in app.py", flush=True)
         # exit(1) # Decide if you want to exit or just warn
    if not NOTION_DATABASE_ID:
         print("Error: NOTION_DATABASE_ID is missing in app.py", flush=True)
         # exit(1)
    if not os.path.exists(CREDENTIALS_PATH):
         print(f"Warning: CREDENTIALS_PATH ('{CREDENTIALS_PATH}') not found. Google Auth will require browser flow.", flush=True)

    # Silence Werkzeug's request logger
    log = logging.getLogger('werkzeug')
    log.setLevel(logging.ERROR) # Or logging.WARNING to see server errors but not requests

    # Start the background sync thread
    print("Starting background sync thread...", flush=True)
    sync_thread = threading.Thread(target=run_sync_loop, daemon=True)
    sync_thread.start()

    # Run the Flask web server
    print(f"Starting Flask server on http://0.0.0.0:8000", flush=True)
    try:
        # Use waitress or gunicorn for production instead of app.run()
        # For simplicity here, we still use app.run()
        app.run(host='0.0.0.0', port=8000) # Removed debug=True if it was there
    except Exception as flask_err:
        print(f"Flask server failed to start: {flask_err}", flush=True)
        traceback.print_exc()

if __name__ == '__main__':
    main()
