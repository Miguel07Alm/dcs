import os
import requests
from dotenv import load_dotenv
from git import Repo, GitCommandError, Commit
from typing import List, Dict, Any
import time
from datetime import datetime, timedelta
import logging
from openai import OpenAI, OpenAIError
import smtplib
from email.mime.text import MIMEText
import traceback
import sys


LOG_DIR = "logs"
RUN_TIMESTAMP = datetime.now().strftime("%Y%m%d_%H%M%S")
LOG_FILE_PATH = os.path.join(LOG_DIR, f"dcs_run_{RUN_TIMESTAMP}.md")

def ensure_log_dir_exists():
    """Creates the log directory if it doesn't exist."""
    try:
        os.makedirs(LOG_DIR, exist_ok=True)
    except Exception as e:
        logging.error(f"Failed to create log directory '{LOG_DIR}': {e}")

def log_to_run_file(header, content):
    """Appends formatted content to the run-specific log file."""
    try:
        ensure_log_dir_exists()
        with open(LOG_FILE_PATH, "a", encoding="utf-8") as f:
            f.write(f"## {header} ({datetime.now().strftime('%Y-%m-%d %H:%M:%S')})\n\n")
            if isinstance(content, (list, dict)):
                import json
                f.write(f"```json\n{json.dumps(content, indent=2, default=str)}\n```\n\n")
            else:
                f.write(f"```\n{str(content)}\n```\n\n")
    except Exception as e:
        logging.error(f"Failed to write to log file '{LOG_FILE_PATH}': {e}")


SMTP_SERVER = os.getenv("SMTP_SERVER")
SMTP_PORT = os.getenv("SMTP_PORT", 587)
SMTP_USER = os.getenv("SMTP_USER")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD")
EMAIL_SENDER = os.getenv("EMAIL_SENDER", SMTP_USER)
EMAIL_RECEIVER = os.getenv("EMAIL_RECEIVER")
ENABLE_EMAIL_NOTIFICATION = os.getenv("ENABLE_EMAIL_NOTIFICATION", "false").lower() == "true"

def send_failure_email(subject, error_details):
    """Sends an email notification about a critical failure."""
    if not ENABLE_EMAIL_NOTIFICATION:
        logging.info("Email notifications are disabled. Skipping failure email.")
        return

    if not all([SMTP_SERVER, SMTP_PORT, SMTP_USER, SMTP_PASSWORD, EMAIL_SENDER, EMAIL_RECEIVER]):
        logging.error("Missing one or more required SMTP environment variables for email notification. Cannot send email.")
        missing_vars = [var for var, val in {
            "SMTP_SERVER": SMTP_SERVER, "SMTP_PORT": SMTP_PORT, "SMTP_USER": SMTP_USER,
            "SMTP_PASSWORD": "***", "EMAIL_SENDER": EMAIL_SENDER, "EMAIL_RECEIVER": EMAIL_RECEIVER
        }.items() if not val]
        logging.error(f"Missing email config variables: {', '.join(missing_vars)}")
        log_to_run_file("Email Sending Error", f"Missing config variables: {', '.join(missing_vars)}")
        return

    msg = MIMEText(f"The DCS script encountered a critical error and could not complete.\n\nError Details:\n{error_details}")
    msg['Subject'] = f"[DCS Failure] {subject}"
    msg['From'] = EMAIL_SENDER
    msg['To'] = EMAIL_RECEIVER

    try:
        logging.info(f"Attempting to send failure email to {EMAIL_RECEIVER} via {SMTP_SERVER}:{SMTP_PORT}")
        with smtplib.SMTP(SMTP_SERVER, int(SMTP_PORT)) as server:
            server.ehlo()
            server.starttls()
            server.ehlo()
            server.login(SMTP_USER, SMTP_PASSWORD)
            server.sendmail(EMAIL_SENDER, EMAIL_RECEIVER, msg.as_string())
            logging.info("Failure email sent successfully.")
            log_to_run_file("Failure Email Sent", f"Sent notification for error: {subject}")
    except smtplib.SMTPAuthenticationError as e:
        logging.error(f"SMTP Authentication failed: {e}. Check SMTP_USER and SMTP_PASSWORD.")
        log_to_run_file("Email Sending Error", f"SMTP Authentication failed: {e}")
    except smtplib.SMTPServerDisconnected as e:
         logging.error(f"SMTP server disconnected unexpectedly: {e}. Check server/port and network.")
         log_to_run_file("Email Sending Error", f"SMTP server disconnected: {e}")
    except smtplib.SMTPException as e:
        logging.error(f"Failed to send failure email due to SMTP error: {e}")
        log_to_run_file("Email Sending Error", f"SMTP Error: {e}")
    except Exception as e:
        logging.error(f"An unexpected error occurred while sending the failure email: {e}")
        log_to_run_file("Email Sending Error", f"Unexpected Error: {e}")


load_dotenv()

GIT_REPO_PATH = os.getenv("GIT_REPO_PATH")
DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
SUMMARY_FREQUENCY = os.getenv("SUMMARY_FREQUENCY", "weekly").lower()

DISCORD_CHAR_LIMIT = 2000

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

def get_commits_since(repo_path, since_date) -> List[Dict[str, Any]]:
    """Fetches commits from the repository since a given date, including diff info."""
    commits_data = []
    try:
        repo = Repo(repo_path)
        commits = list(repo.iter_commits(rev='main', since=since_date.isoformat()))
        logging.info(f"Found {len(commits)} commits since {since_date.strftime('%Y-%m-%d')}")

        for commit in commits:
            diff_output = "Diff not available."
            try:
                if commit.parents:
                    parent_sha = commit.parents[0].hexsha
                    diff_output = repo.git.diff(parent_sha, commit.hexsha, '--shortstat')
                else:
                    diff_output = repo.git.show(commit.hexsha, '--shortstat', '--pretty=format:')
            except Exception as diff_err:
                logging.warning(f"Could not get diff for commit {commit.hexsha[:7]}: {diff_err}")

            commits_data.append({
                "commit": commit,
                "diff_summary": diff_output.strip()
            })

        return commits_data
    except GitCommandError as e:
        logging.error(f"Error accessing Git repository at {repo_path}: {e}")
        return []
    except Exception as e:
        logging.error(f"An unexpected error occurred while fetching commits: {e}")
        return []

def format_commits_for_prompt(commits_data: List[Dict[str, Any]]) -> str:
    """Formats commit messages and diff summaries into a single string for the AI prompt."""
    commit_texts = []
    for data in commits_data:
        commit = data["commit"]
        diff_summary = data["diff_summary"]
        commit_texts.append(
            f"- Commit: {commit.hexsha[:7]} by {commit.author.name}\n"
            f"  Message: {commit.message.strip()}\n"
            f"  Changes: {diff_summary if diff_summary else 'No changes summary available.'}"
        )
    return "\n\n".join(commit_texts)

def summarize_commits_with_ai(commits_data: List[Dict[str, Any]], project_context: str | None = None) -> str:
    """Generates a summary of commits using Gemini via OpenAI interface, including diff info and project context."""
    if not commits_data:
        log_to_run_file("AI Summarization Skipped", "No new commits found.")
        return "No new commits found in the specified period."

    raw_commits = [data["commit"] for data in commits_data]

    log_to_run_file("Project Context Provided to AI", project_context if project_context else "None")

    if not GEMINI_API_KEY:
        logging.warning("GEMINI_API_KEY not found. Falling back to basic formatting.")
        log_to_run_file("AI Summarization Skipped", "GEMINI_API_KEY not found. Falling back to basic formatting.")
        return format_commits_basic(raw_commits)

    try:
        client = OpenAI(
            api_key=GEMINI_API_KEY,
            base_url="https://generativelanguage.googleapis.com/v1beta/",
        )

        commit_details_with_diffs = format_commits_for_prompt(commits_data)
        log_to_run_file("Formatted Commits & Diffs for User Prompt", commit_details_with_diffs)

        context_str = "No project description available."
        if project_context:
            context_str = ' '.join(project_context.split())[:500]

        system_prompt = (
                "You are a helpful assistant mimicking the style of a lead developer announcing updates on Discord. "
            "Your goal is to generate an engaging update message for end-users (non-technical audience) based on recent Git commits and project context, focusing on the *transformation* users experience. "
            "**VERY Strict Style Guidelines:**"
            "1.  **Start:** Begin the entire response with *TWO emojis (one in the start and the other in the end of the header)* that best represents the update, followed *EXACTLY* by ` @everyone Major Update!`. Example: `🚀 @everyone Major Update!`. **DO NOT multiple headers.**"
            "2.  **Tone:** Enthusiastic, direct, and personal (use 'I\'ve been working on...', 'You can now...'). Focus on excitement about **new capabilities** and **what users can achieve**."
            "3.  **Formatting:** Use Discord markdown (`**bold**` for headings/key features). Group related changes under **bold headings** that hint at the **new capability or outcome**. Follow headings with a newline. **DO NOT use the em-dash character (—).** Use standard hyphens (-) or rephrase if necessary."
            "4.  **Emojis:** Use ONLY the single chosen emoji at the very start. **NO OTHER EMOJIS** in the message body."
            "5.  **Focus & Language:** **Sell the transformation!** Explain *what new ability or outcome the user gains*. Instead of just listing a feature ('Added X'), explain the result ('You can now achieve Y because I've added X' or 'Doing Z is now much easier/faster'). Use simple, non-technical language. **ABSOLUTELY NO mentioning internal code names, function names, component names, file names, or technical jargon.**"
            "6.  **Conciseness:** Keep the entire message well under 2000 characters."
            "7.  **Bug Fixes:** Only mention specific bug fixes if they resolved a very noticeable problem for users. Otherwise, group them generally under a heading like '**Smoother Experience**' and say something like 'I\'ve also fixed several smaller issues to make things run better.'."
            "8.  **Example Style Reference (DO NOT COPY CONTENT, ONLY MIMIC STYLE, STRUCTURE & TRANSFORMATION FOCUS):**\n"
            "   ```\n"
            "   🤖 @everyone Major Overhaul Update! 🤖\n\n"
            "   Over the past two months, I've been working on a complete overhaul of the editor, optimizing the code (boring stuff) and adding exciting new features to enhance your experience. Here's what's new:\n\n"
            "   **Edit Feature**\n\n"
            "   You can now add new elements to your images by simply drawing on them and providing a prompt describing what you want to create.\n\n"
            "   **Agent Mode**\n\n"
            "   Two new modes have been added to the chat:\n\n"
            "   *   **Manual Mode:** Ideal for quick edits using commands. It's fast and perfect for implementing ideas on the fly.\n"
            "   *   **Agent Mode:** Allows you to create a list of edits for the agent to execute. While this prototype can't yet create full videos, it's a step toward smarter video editing.\n\n"
            "   **Mentions (#) and Command Suggestions (@)**\n\n"
            "   *   **Mentions (#):** Easily select specific elements in the editor or groups of elements.\n"
            "   *   **Command Suggestions (@):** Quickly execute specific commands or combinations of commands to streamline your workflow.\n\n"
            "   **Overlay Design**\n\n"
            "   I redesigned the toolbar actions, such as crop, add blur region, background remover and AI upscaler into overlays. These overlays allow you to see changes directly on the canvas in real time, without needing to open a separate panel.\n\n"
            "   **Feedback**\n\n"
            "   Try out the new features and let me know what you think using the feedback dialog! I'm also open to suggestions for new features, feel free to submit them in ⁠feature-request.\n\n"
            "   https://editfa.st/\n"
            "   ```\n\n"
            f"**Project Context:** {context_str}\n"
        )

        log_to_run_file("System Prompt Sent to AI", system_prompt)

        user_prompt = f"Generate the Discord update message based on these commits and changes from the last {SUMMARY_FREQUENCY}:\n\n{commit_details_with_diffs}"
        log_to_run_file("User Prompt Sent to AI", user_prompt)

        logging.info(f"Sending {len(commits_data)} commits with diff summaries and project context to Gemini for summarization (refined style)...")

        response = client.chat.completions.create(
            model="gemini-2.0-flash",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}
            ],
            temperature=0.65,
            max_tokens=550,
            n=1,
        )
        log_to_run_file("Raw AI Response Object", response.model_dump_json(indent=2))

        if response.choices and len(response.choices) > 0 and response.choices[0].message and response.choices[0].message.content:
            summary = response.choices[0].message.content.strip()
            log_to_run_file("Extracted AI Summary (Raw)", summary)
            logging.info("Successfully received summary from Gemini.")
            return summary
        else:
            logging.error(f"Unexpected response structure from Gemini API: {response}")
            log_to_run_file("Error: Unexpected AI Response Structure", response.model_dump_json(indent=2))
            logging.warning("Falling back to basic formatting due to unexpected API response structure.")
            return format_commits_basic(raw_commits)

    except OpenAIError as e:
        logging.error(f"Error calling Gemini API: {e}")
        log_to_run_file("Error: OpenAI API Error", str(e))
        logging.warning("Falling back to basic formatting due to API error.")
        return format_commits_basic(raw_commits)
    except Exception as e:
        logging.error(f"An unexpected error occurred during AI summarization: {e}")
        log_to_run_file("Error: Unexpected Summarization Error", str(e))
        logging.warning("Falling back to basic formatting due to unexpected error.")
        return format_commits_basic(raw_commits)

def format_commits_basic(commits: List[Commit]) -> str:
    """Formats commit messages into a basic string for fallback."""
    if not commits:
        return "No new commits found in the specified period."

    summary = f"**Commit Summary ({datetime.now().strftime('%Y-%m-%d')})**\n\n"
    summary += f"Found {len(commits)} commits:\n"
    for commit in commits:
        short_hash = commit.hexsha[:7]
        message_first_line = commit.message.split('\n')[0]
        summary += f"- `{short_hash}`: {message_first_line} (by {commit.author.name})\n"
    return summary

def split_message(message, limit=DISCORD_CHAR_LIMIT):
    """Splits a long message into chunks respecting Discord's limit, trying to split at newlines."""
    if len(message) <= limit:
        return [message]

    chunks = []
    current_chunk = ""
    lines = message.splitlines(keepends=True)

    for line in lines:
        if len(current_chunk) + len(line) > limit:
            if current_chunk:
                chunks.append(current_chunk)
            current_chunk = line
        else:
            current_chunk += line

        while len(current_chunk) > limit:
            split_at = current_chunk[:limit].rfind(" ")
            if split_at == -1 or split_at < limit // 2:
                split_at = limit

            chunks.append(current_chunk[:split_at])
            current_chunk = current_chunk[split_at:].lstrip()

    if current_chunk:
        chunks.append(current_chunk)

    return [chunk.strip() for chunk in chunks if chunk.strip()]


def send_to_discord(webhook_url, message):
    """Sends a message to the specified Discord webhook URL, splitting if necessary."""
    if not webhook_url:
        logging.error("Discord webhook URL is not set. Cannot send message.")
        log_to_run_file("Discord Sending Skipped", "Webhook URL not set.")
        return False

    message_chunks = split_message(message, DISCORD_CHAR_LIMIT - 20)
    total_chunks = len(message_chunks)
    log_to_run_file(f"Message Splitting (Into {total_chunks} Chunks)", message_chunks)

    if total_chunks == 0:
        logging.warning("Attempted to send an empty or whitespace-only message.")
        log_to_run_file("Discord Sending Skipped", "Attempted to send empty message.")
        return False

    success = True
    for i, chunk in enumerate(message_chunks):
        part_indicator = f" (Part {i+1}/{total_chunks})" if total_chunks > 1 else ""

        if len(chunk) + len(part_indicator) > DISCORD_CHAR_LIMIT:
             chunk = chunk[:DISCORD_CHAR_LIMIT - len(part_indicator) - 3] + "..."

        final_chunk = chunk + part_indicator
        log_to_run_file(f"Sending Chunk {i+1}/{total_chunks} to Discord", final_chunk)

        data = {"content": final_chunk}
        try:
            response = requests.post(webhook_url, json=data)
            response.raise_for_status()
            logging.info(f"Successfully sent part {i+1}/{total_chunks} to Discord.")
            if total_chunks > 1 and i < total_chunks - 1:
                time.sleep(1.5)
        except requests.exceptions.RequestException as e:
            logging.error(f"Failed to send part {i+1}/{total_chunks} to Discord: {e}")
            log_to_run_file(f"Error Sending Chunk {i+1}/{total_chunks} to Discord", str(e))
            success = False
            break

    return success

def get_start_date(frequency):
    """Calculates the start date based on the frequency."""
    now = datetime.now()
    if frequency == "daily":
        return now - timedelta(days=1)
    elif frequency == "weekly":
        return now - timedelta(weeks=1)
    elif frequency == "monthly":
        return now - timedelta(days=30)
    else:
        logging.warning(f"Unknown frequency '{frequency}', defaulting to weekly.")
        return now - timedelta(weeks=1)

def main():
    try:
        ensure_log_dir_exists()
    except Exception as e:
        print(f"CRITICAL: Failed to create log directory '{LOG_DIR}': {e}", file=sys.stderr)
        error_details = f"Failed to create log directory '{LOG_DIR}'.\n{traceback.format_exc()}"
        send_failure_email("Log Directory Creation Failed", error_details)
        sys.exit(1)

    log_to_run_file("Script Execution Started", f"Frequency: {SUMMARY_FREQUENCY}, Repo: {GIT_REPO_PATH}")
    logging.info("Starting DCS - Discord Commit Summarizer...")

    try:
        critical_error = False
        error_messages = []

        if not GIT_REPO_PATH:
            error_messages.append("GIT_REPO_PATH environment variable is not set.")
            critical_error = True
        elif not os.path.isdir(GIT_REPO_PATH):
             error_messages.append(f"GIT_REPO_PATH '{GIT_REPO_PATH}' does not exist or is not a directory.")
             critical_error = True

        if not GEMINI_API_KEY:
             error_messages.append("GEMINI_API_KEY environment variable is not set. AI summarization will fail.")
             critical_error = True

        if not DISCORD_WEBHOOK_URL:
            logging.warning("DISCORD_WEBHOOK_URL environment variable is not set. Summary will only be logged.")
            log_to_run_file("Configuration Warning", "DISCORD_WEBHOOK_URL not set.")

        if critical_error:
            full_error_msg = "Critical configuration error(s):\n- " + "\n- ".join(error_messages)
            logging.error(full_error_msg)
            log_to_run_file("Script Execution Error", full_error_msg)
            send_failure_email("Configuration Error", full_error_msg)
            sys.exit(1)


        start_date = get_start_date(SUMMARY_FREQUENCY)
        log_to_run_file("Calculated Start Date", start_date.isoformat())
        logging.info(f"Fetching commits since {start_date.strftime('%Y-%m-%d')} based on '{SUMMARY_FREQUENCY}' frequency.")

        commits_data = get_commits_since(GIT_REPO_PATH, start_date)
        commits_summary_for_log = [
            {
                "hexsha": d["commit"].hexsha,
                "message": d["commit"].message.strip(),
                "author": d["commit"].author.name,
                "date": d["commit"].committed_datetime.isoformat(),
                "diff_summary": d["diff_summary"]
            } for d in commits_data
        ]
        log_to_run_file("Fetched Commits Data (Summary)", commits_summary_for_log)

        readme_content = None
        try:
            readme_path = os.path.join(GIT_REPO_PATH, 'README.md')
            if os.path.exists(readme_path):
                with open(readme_path, 'r', encoding='utf-8', errors='ignore') as f:
                    readme_content = f.read(1000)
                logging.info("Read start of project README.md for context.")
                log_to_run_file("Read README Context (First 1000 chars)", readme_content if readme_content else "Empty")
            else:
                logging.info("Project README.md not found in GIT_REPO_PATH. Proceeding without project context.")
                log_to_run_file("README Context", "README.md not found.")
        except Exception as e:
            logging.error(f"Error reading project README.md: {e}")
            log_to_run_file("Error Reading README", str(e))

        summary_message = summarize_commits_with_ai(commits_data, project_context=readme_content)
        log_to_run_file("Final Summary Message (Before Sending)", summary_message)

        if DISCORD_WEBHOOK_URL:
            success = send_to_discord(DISCORD_WEBHOOK_URL, summary_message)
            if not success:
                logging.error("Failed to send one or more message parts to Discord.")
                log_to_run_file("Discord Sending Issue", "Failed to send one or more message parts.")
        else:
            logging.info("Discord webhook URL not provided. Logging summary instead:")
            print("--- Summary Start ---")
            print(summary_message)
            print("--- Summary End ---")
            log_to_run_file("Discord Sending Skipped", "Webhook URL not provided. Logged to console.")

        log_to_run_file("Script Execution Finished Successfully", "------")
        logging.info("DCS script finished successfully.")

    except Exception as e:
        error_timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        error_type = type(e).__name__
        error_traceback = traceback.format_exc()

        logging.critical(f"CRITICAL ERROR encountered at {error_timestamp}: {error_type} - {e}")
        logging.critical(f"Traceback:\n{error_traceback}")

        error_details_for_log = f"Timestamp: {error_timestamp}\nError Type: {error_type}\nMessage: {e}\n\nTraceback:\n{error_traceback}"
        log_to_run_file(f"CRITICAL SCRIPT FAILURE: {error_type}", error_details_for_log)

        email_subject = f"Critical Error: {error_type}"
        send_failure_email(email_subject, error_details_for_log)

        sys.exit(1)


if __name__ == "__main__":
    main()
