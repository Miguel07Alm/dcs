# DCS - Discord Commit Summarizer

A tool that monitors a local Git repository, generates AI-powered summaries of recent commits (daily, weekly, or monthly), and sends them to a Discord channel. Includes robust error handling and optional email notifications for critical failures.

## Motivation

Keeping a community updated with the latest project developments often involves manually reading through Git commits and crafting announcements. This tool was born out of the desire to automate this process, making it effortless to generate clear, engaging updates from commit history and share them directly where the community gathers, like Discord.

## Features

*   Monitors a specified local Git repository.
*   Fetches commits based on a configurable frequency (`daily`, `weekly`, `monthly`).
*   Uses the Gemini API (via the OpenAI library interface) to generate user-friendly summaries focused on user impact and new capabilities, styled for Discord announcements.
*   Includes project context (reads the first 1000 characters of the repository's `README.md`) in the AI prompt for more relevant summaries.
*   Provides a basic commit list fallback if the AI summarization fails.
*   Sends summaries to a specified Discord webhook URL.
*   Automatically splits long messages to comply with Discord character limits.
*   Logs detailed run information to timestamped markdown files in the `logs/` directory.
*   Robust error handling for configuration issues, Git operations, AI API calls, and Discord sending.
*   Optional email notifications via SMTP (configurable for Gmail or other providers) for critical script failures, making it suitable for monitoring automated runs (e.g., via cron).

## Setup

1.  **Clone the repository:**
    ```bash
    git clone <your-repo-url>
    cd dcs
    ```
2.  **Create and activate a virtual environment:**
    ```bash
    # Using venv (standard library)
    python -m venv .venv
    source .venv/bin/activate # On Windows use `.venv\Scripts\activate`

    # Or using uv (if installed)
    uv venv
    source .venv/bin/activate
    ```
3.  **Install dependencies:**
    ```bash
    # Using pip
    pip install -r requirements.txt 
    # Or if you have configured pyproject.toml for the project:
    # pip install .

    # Or using uv
    uv pip install -r requirements.txt
    # Or uv pip install .
    ```
4.  **Configure environment variables:**
    *   Copy the example environment file:
        ```bash
        cp .env.example .env
        ```
    *   Edit the `.env` file and fill in the required values:
        *   `DISCORD_WEBHOOK_URL`: Your Discord channel webhook URL.
        *   `GIT_REPO_PATH`: The absolute path to the local Git repository you want to monitor.
        *   `GEMINI_API_KEY`: Your API key for the Google Gemini model.
        *   (Optional) `SUMMARY_FREQUENCY`: Set to `daily`, `weekly` (default), or `monthly`.
        *   (Optional) Email Notification Settings: If you want email alerts on failure:
            *   Set `ENABLE_EMAIL_NOTIFICATION="true"`.
            *   Configure `SMTP_SERVER`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASSWORD`, `EMAIL_SENDER`, `EMAIL_RECEIVER`.
            *   **Important for Gmail:** Use `smtp.gmail.com` and port `587`. If you have 2-Factor Authentication enabled on your Google account, you **must** generate an "App Password" and use that for `SMTP_PASSWORD`. Do not use your regular Google account password.

## Usage

*   **Manual Run:**
    Execute the script directly from the project root:
    ```bash
    python src/dcs/main.py
    
    # Or using directly
    dcs
    ```
*   **Automated Run (Cron Job):**
    Set up a cron job to run the script automatically. Edit your crontab (`crontab -e`) and add an entry like this (example runs daily at 3:00 AM):
    ```cron
    0 3 * * * /usr/bin/python /path/to/your/dcs/src/dcs/main.py >> /path/to/your/dcs/logs/cron.log 2>&1
    ```
    *   **Important:**
        *   Replace `/usr/bin/python` with the actual path to the python executable *within your virtual environment* (e.g., `/path/to/your/dcs/.venv/bin/python`) or ensure the script runs with the correct environment activated.
        *   Replace `/path/to/your/dcs/` with the absolute path to the project directory.
        *   Ensure the user running the cron job has the necessary permissions and environment variables set (cron jobs often run with a minimal environment; you might need to source variables or use absolute paths).

## Logging

Check the `logs/` directory within the project folder. Each script run creates a detailed markdown file (e.g., `dcs_run_YYYYMMDD_HHMMSS.md`) containing information about the execution steps, fetched commits, AI prompts/responses, and any errors encountered.

## GitHub Action

You can also use DCS as a GitHub Action to automatically generate summaries from your GitHub repository and post them to Discord.

### Setting up the GitHub Action

1. **Add required secrets to your GitHub repository:**
   - Go to your repository's Settings > Secrets and variables > Actions
   - Add the following secrets:
     - `DISCORD_WEBHOOK_URL`: Your Discord channel webhook URL
     - `GEMINI_API_KEY`: Your API key for the Google Gemini model

2. **Use as a workflow in your repository:**

   Create a file at `.github/workflows/discord-summary.yml` with the following content:

   ```yaml
   name: Discord Commit Summary

   on:
     schedule:
       - cron: '0 12 * * 5'  # Run every Friday at 12:00 UTC
     workflow_dispatch:  

   jobs:
     summarize:
       uses: miguel07alm/dcs/.github/workflows/dcs-action.yml@main
       with:
         frequency: weekly  # Options: daily, weekly, monthly
         send_empty_summary: false  # Whether to send a summary when no commits are found
       secrets:
         DISCORD_WEBHOOK_URL: ${{ secrets.DISCORD_WEBHOOK_URL }}
         GEMINI_API_KEY: ${{ secrets.GEMINI_API_KEY }}
   ```

3. **Customize as needed:**
   - Adjust the schedule to your preferred frequency
   - Change the `frequency` parameter to match your needs
   - Replace `your-username` with your actual GitHub username

### Manual Trigger

You can also manually trigger the workflow by:
1. Going to your repository's "Actions" tab
2. Selecting the "Discord Commit Summary" workflow
3. Clicking "Run workflow"
4. Optionally adjusting the parameters before running

## License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.
