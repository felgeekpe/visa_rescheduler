#!/usr/bin/env python3
"""Simple script to test Telegram bot notification with realistic messages."""

import configparser
import requests
import sys

# Example messages matching visa.py notifications
TEST_MESSAGES = [
    ("Dates available", "📅 <b>Dates Available</b>\n\n• 2025-03-15\n• 2025-03-22\n• 2025-04-01"),
    ("Rescheduling", "🔄 <b>Rescheduling</b>\n\nAttempting: <code>2025-03-15</code>"),
    ("Success", "✅ <b>Success!</b>\n\nNew appointment: <code>2025-03-15</code>"),
    ("Failed", "❌ <b>Failed</b>\n\nCould not book: <code>2025-03-15</code>"),
    ("Error", "⚠️ <b>Error</b>\n\nException occurred, retrying..."),
    ("Crashed", "🚨 <b>Crashed</b>\n\nMax retries exceeded, restarting..."),
    ("Login error", "⚠️ <b>Login Error</b>\n\nException during login, retrying..."),
]

def send_message(url, chat_id, text):
    """Send a single message and return success status."""
    data = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML"
    }
    response = requests.post(url, data=data, timeout=10)
    result = response.json()
    return result.get("ok"), result.get("description", "Unknown error")

def test_telegram():
    config = configparser.ConfigParser()
    config.read('config.ini')

    try:
        token = config['TELEGRAM']['TELEGRAM_BOT_TOKEN']
        chat_id = config['TELEGRAM']['TELEGRAM_CHAT_ID']
    except KeyError as e:
        print(f"Error: Missing config key {e}")
        print("Make sure [TELEGRAM] section exists in config.ini with TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID")
        sys.exit(1)

    if not token or not chat_id:
        print("Error: TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID is empty in config.ini")
        sys.exit(1)

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    print(f"Sending test messages to chat ID: {chat_id}\n")

    try:
        for label, msg in TEST_MESSAGES:
            print(f"  [{label}] {msg}")
            ok, error = send_message(url, chat_id, msg)
            if ok:
                print("    ✓ Sent\n")
            else:
                print(f"    ✗ Failed: {error}\n")
                sys.exit(1)

        print("All test messages sent successfully!")
    except requests.RequestException as e:
        print(f"✗ Request failed: {e}")
        sys.exit(1)

if __name__ == "__main__":
    test_telegram()
