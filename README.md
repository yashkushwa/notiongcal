# Google Calendar to Notion Sync

This Python script continuously syncs events from a specified Google Calendar to a Notion database. It handles adding new events, updating existing ones, and removing events that are deleted from the calendar.

## Setup

1.  **Clone the repository:**
    ```bash
    git clone <your-repo-url>
    cd <your-repo-name>
    ```
2.  **Install dependencies:**
    ```bash
    pip install -r requirements.txt
    ```
3.  **Configure `app.py`:**
    *   Open `app.py`.
    *   Replace the placeholder values for `CREDENTIALS_PATH`, `CALENDAR_ID`, `NOTION_TOKEN`, and `NOTION_DATABASE_ID` with your actual credentials and IDs.
    *   Ensure you have a `credentials.json` file downloaded from Google Cloud Console at the path specified by `CREDENTIALS_PATH`. **Do not commit `credentials.json` to Git.**
4.  **Run the script:**
    ```bash
    python app.py
    ```
    The first time you run it, you will likely need to go through the Google OAuth2 authentication flow in your browser. A `token.json` file will be created to store your refresh token.

## Deployment (e.g., on Render)

1.  Push your code to a GitHub repository (ensure your `.gitignore` prevents sensitive files like `credentials.json` and `token.json` from being committed).
2.  Connect your GitHub repository to Render.
3.  Create a new "Background Worker" service on Render.
4.  Set the build command to `pip install -r requirements.txt`.
5.  Set the start command to `python app.py`.
6.  **Important:** For Render deployment, you will need to handle the initial OAuth flow differently, as it requires browser interaction. You might need to:
    *   Run the script locally once to generate `token.json`.
    *   Securely add the *contents* of `token.json` as an environment variable (e.g., `GOOGLE_TOKEN_JSON`) in Render.
    *   Modify `app.py` to load the token from this environment variable instead of the file if it exists.
    *   Similarly, securely provide `credentials.json` content via environment variables or a secret file mechanism in Render, and adjust the script to read from there. Directly placing `credentials.json` in the repo is not recommended.

**Note:** The current script keeps credentials directly within `app.py` as requested, but for better security practice, especially in public repositories or production deployments, using environment variables or a secrets management system is strongly advised. The `README.md` provides guidance on how you might adapt this for a platform like Render. 