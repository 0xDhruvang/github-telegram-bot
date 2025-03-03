import os
import time
import re
import logging
import requests
import asyncio
from textblob import TextBlob
from telegram import Bot
from telegram.constants import ParseMode
from transformers import pipeline
from dotenv import load_dotenv

# Load API Keys & Config
load_dotenv()
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
REPOS = os.getenv("REPOS").split(",")

HEADERS = {"Authorization": f"token {GITHUB_TOKEN}"}
bot = Bot(token=TELEGRAM_BOT_TOKEN)
logging.basicConfig(level=logging.INFO)

# Load AI Models
summarizer = pipeline("summarization", model="facebook/bart-large-cnn", framework="pt")

# Store last processed commits
last_commits = {}

def get_latest_commit(repo):
    """Fetch the latest commit from a repository."""
    url = f"https://api.github.com/repos/{repo}/commits"
    try:
        response = requests.get(url, headers=HEADERS, timeout=10)
        response.raise_for_status()
        return response.json()[0]  
    except requests.RequestException as e:
        logging.error(f"Error fetching commits for {repo}: {e}")
        return None

def summarize_text(text):
    """Summarizes commit messages dynamically handling length constraints."""
    input_length = len(text.split())
    max_length = min(50, max(10, input_length - 1))
    min_length = max(5, max_length // 2)

    try:
        summary = summarizer(text, max_length=max_length, min_length=min_length, do_sample=False)
        return summary[0]["summary_text"]
    except Exception as e:
        logging.warning(f"Summarization failed, using original text: {e}")
        return text[:200]  # Fallback to truncated message

def analyze_commit(commit_message, commit_files):
    """Analyze commit changes to determine if it's bullish or bearish."""
    summary = summarize_text(commit_message)
    sentiment = TextBlob(commit_message).sentiment.polarity

    # Define categories based on commit content
    bullish_terms = ["scaling", "upgrade", "performance", "optimization", "security", "merge", "enhancement", "feature"]
    bearish_terms = ["bug", "deprecated", "reverted", "removed", "issue", "vulnerability", "rollback", "fix"]

    # Analyze file changes for deeper insights
    added_files = [f["filename"] for f in commit_files if f["status"] == "added"]
    removed_files = [f["filename"] for f in commit_files if f["status"] == "removed"]

    explanation = "No strong indicators found."
    sentiment_label = "Neutral ⚖️"

    # Determine sentiment based on both commit message and file changes
    if any(word in commit_message.lower() for word in bullish_terms) or added_files:
        explanation = "New features or optimizations detected."
        sentiment_label = "Bullish 📈"
    elif any(word in commit_message.lower() for word in bearish_terms) or removed_files:
        explanation = "Bug fixes, deprecations, or rollbacks detected."
        sentiment_label = "Bearish 📉"

    return summary, sentiment_label, explanation

def escape_markdown(text):
    """Escapes Markdown characters for Telegram messages."""
    escape_chars = r"[_*[\]()~`>#\+\-=|{}.!]"
    return re.sub(escape_chars, r"\\\g<0>", text)

async def send_telegram_message(message):
    """Sends a message to Telegram, handling length limits."""
    safe_message = escape_markdown(message)

    # Telegram message limit is 4096 characters, split if needed
    for chunk in [safe_message[i:i+4000] for i in range(0, len(safe_message), 4000)]:
        try:
            await bot.send_message(chat_id=TELEGRAM_CHAT_ID, text=chunk, parse_mode=ParseMode.MARKDOWN_V2)
            time.sleep(1)  # Prevent rate-limiting
        except Exception as e:
            logging.error(f"Telegram API Error: {e}")

async def track_repos():
    """Monitors multiple GitHub repositories and sends updates to Telegram."""
    while True:
        messages = []
        
        for repo in REPOS:
            commit = get_latest_commit(repo)

            if commit:
                commit_message = commit["commit"]["message"]
                commit_url = commit["html_url"]
                commit_sha = commit["sha"]
                
                # Get commit file changes
                commit_details_url = commit["url"]
                commit_details = requests.get(commit_details_url, headers=HEADERS).json()
                commit_files = commit_details.get("files", [])

                if last_commits.get(repo) != commit_sha:
                    last_commits[repo] = commit_sha
                    summary, sentiment, explanation = analyze_commit(commit_message, commit_files)

                    messages.append(
                        f"🔔 **{repo} Update**\n"
                        f"📝 {summary}\n"
                        f"📊 Sentiment: {sentiment}\n"
                        f"🧐 Reason: {explanation}\n"
                        f"🔗 [View Commit]({commit_url})"
                    )

        if messages:
            await send_telegram_message("\n\n".join(messages))

        await asyncio.sleep(300)  # Wait 5 minutes before checking again

if __name__ == "__main__":
    while True:
        try:
            asyncio.run(track_repos())
        except Exception as e:
            logging.error(f"Error: {e}, restarting in 10 seconds...")
            time.sleep(10)
