# -*- coding: utf8 -*-
"""
US VISA Appointment Rescheduler for ais.usvisa-info.com

This script monitors the US visa appointment system for earlier available slots
and automatically reschedules when a date within the desired range becomes available.
It was originally designed for Colombian applicants but can be adapted for other countries.

Overview:
    The script uses Selenium WebDriver to automate browser interactions with the
    usvisa-info.com system. It performs the following workflow:

    1. Logs into the visa appointment portal
    2. Polls for available appointment dates using JavaScript-executed XHR requests
    3. Filters dates within the configured range (MY_SCHEDULE_DATE_START to MY_SCHEDULE_DATE)
    4. Automatically reschedules if an earlier date is found
    5. Sends notifications via Pushover, SendGrid email, and/or Slack

Prerequisites:
    - An existing US visa appointment scheduled on ais.usvisa-info.com
    - Google Chrome browser installed
    - Python 3.x with required packages (see requirements.txt)
    - Valid credentials for the visa appointment portal

Configuration:
    Create a config.ini file with the following sections:

    [USVISA]
        USERNAME: Login email for usvisa-info.com
        PASSWORD: Account password
        SCHEDULE_ID: Your appointment schedule ID (from the appointment URL)
        MY_SCHEDULE_DATE: Latest acceptable date (YYYY-MM-DD format)
        MY_SCHEDULE_DATE_START: Earliest acceptable date (YYYY-MM-DD format)
        RELATIVE_END_DATE_DAYS: Optional - days from today (e.g., 14 for 2 weeks)
            When set, overrides MY_SCHEDULE_DATE with a dynamic value
        COUNTRY_CODE: Country code for the portal (e.g., 'es-co' for Colombia)
        FACILITY_ID: Consulate/facility ID (e.g., 25 for Bogota)

    [CHROMEDRIVER]
        LOCAL_USE: True for local Chrome, False for remote WebDriver
        HUB_ADDRESS: Remote WebDriver URL (required if LOCAL_USE is False)

    [TELEGRAM] (recommended)
        TELEGRAM_BOT_TOKEN: Bot token from @BotFather
        TELEGRAM_CHAT_ID: Your chat or group ID

    [DISCORD] (optional)
        DISCORD_WEBHOOK: Discord channel webhook URL

    [PUSHOVER] (optional)
        PUSH_TOKEN: Pushover API token
        PUSH_USER: Pushover user key

    [SENDGRID] (optional)
        SENDGRID_API_KEY: SendGrid API key for email notifications

    [SLACK] (optional)
        SLACK_WEBHOOK: Slack webhook URL for notifications

Usage:
    $ python3 visa.py

    The script runs continuously until a successful reschedule or manual termination.

Note:
    - The script targets Spanish-language pages (Colombian locale)
    - Timing constants can be adjusted based on system responsiveness
    - A successful reschedule sets EXIT=True to stop execution
"""

import time
import json
import random
import platform
import configparser
import logging
from datetime import datetime, timedelta
from logging.handlers import RotatingFileHandler
from typing import Any, Optional, Union

import requests
from selenium import webdriver
from selenium.webdriver.chrome.service import Service as ChromeService
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait as Wait
from selenium.webdriver.common.by import By
from webdriver_manager.chrome import ChromeDriverManager

from sendgrid import SendGridAPIClient
from sendgrid.helpers.mail import Mail


# =============================================================================
# Logging Setup
# =============================================================================
# Configure logging to write to both console and file
LOG_FILE = "visa_rescheduler.log"
LOG_FORMAT = "%(asctime)s | %(levelname)-8s | %(message)s"
LOG_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

# Create logger
logger = logging.getLogger("visa_rescheduler")
logger.setLevel(logging.INFO)

# File handler with rotation (5MB max, keep 3 backups)
file_handler = RotatingFileHandler(
    LOG_FILE, maxBytes=5*1024*1024, backupCount=3, encoding='utf-8'
)
file_handler.setLevel(logging.INFO)
file_handler.setFormatter(logging.Formatter(LOG_FORMAT, LOG_DATE_FORMAT))

# Console handler
console_handler = logging.StreamHandler()
console_handler.setLevel(logging.INFO)
console_handler.setFormatter(logging.Formatter(LOG_FORMAT, LOG_DATE_FORMAT))

# Add handlers to logger
logger.addHandler(file_handler)
logger.addHandler(console_handler)

def log(msg: str, level: str = "info") -> None:
    """Log a message to both console and file."""
    getattr(logger, level)(msg)

# =============================================================================
# Configuration Loading
# =============================================================================
# Load settings from config.ini file. See config.ini.example for template.
config = configparser.ConfigParser()
config.read('config.ini')

# -----------------------------------------------------------------------------
# Account & Appointment Settings (from [USVISA] section)
# -----------------------------------------------------------------------------
USERNAME = config['USVISA']['USERNAME']           # Login email for usvisa-info.com
PASSWORD = config['USVISA']['PASSWORD']           # Account password
SCHEDULE_ID = config['USVISA']['SCHEDULE_ID']     # Appointment ID from URL
MY_SCHEDULE_DATE_START = config.get('USVISA', 'MY_SCHEDULE_DATE_START', fallback='').strip()
if not MY_SCHEDULE_DATE_START:
    MY_SCHEDULE_DATE_START = datetime.today().strftime("%Y-%m-%d")
    log(f"No MY_SCHEDULE_DATE_START set, defaulting to today: {MY_SCHEDULE_DATE_START}")
MY_SCHEDULE_DATE = config['USVISA']['MY_SCHEDULE_DATE']              # Latest acceptable date (deadline)

# Optional: Override MY_SCHEDULE_DATE with a relative date (days from today)
# When set, filters appointments to only those within N days from now
RELATIVE_END_DATE_DAYS = config['USVISA'].get('RELATIVE_END_DATE_DAYS', '').strip()
if RELATIVE_END_DATE_DAYS:
    days = int(RELATIVE_END_DATE_DAYS)
    MY_SCHEDULE_DATE = (datetime.today() + timedelta(days=days)).strftime("%Y-%m-%d")
    log(f"Using relative end date: {MY_SCHEDULE_DATE} ({days} days from today)")
COUNTRY_CODE = config['USVISA']['COUNTRY_CODE']   # Portal locale (e.g., 'es-co' for Colombia)
FACILITY_ID = config['USVISA']['FACILITY_ID']     # Consulate ID (e.g., 25 for Bogota)

# Dry-run mode: goes through reschedule flow but stops before final confirmation
DRY_RUN = config['USVISA'].getboolean('DRY_RUN', fallback=False)
if DRY_RUN:
    log("⚠️  DRY_RUN mode enabled - will NOT actually reschedule")

# -----------------------------------------------------------------------------
# Notification Services (all optional)
# -----------------------------------------------------------------------------
SENDGRID_API_KEY = config.get('SENDGRID', 'SENDGRID_API_KEY', fallback='')
PUSH_TOKEN = config.get('PUSHOVER', 'PUSH_TOKEN', fallback='')
PUSH_USER = config.get('PUSHOVER', 'PUSH_USER', fallback='')
SLACK_WEBHOOK = config.get('SLACK', 'SLACK_WEBHOOK', fallback='')
TELEGRAM_BOT_TOKEN = config.get('TELEGRAM', 'TELEGRAM_BOT_TOKEN', fallback='')
TELEGRAM_CHAT_ID = config.get('TELEGRAM', 'TELEGRAM_CHAT_ID', fallback='')
DISCORD_WEBHOOK = config.get('DISCORD', 'DISCORD_WEBHOOK', fallback='')

# -----------------------------------------------------------------------------
# WebDriver Configuration (from [CHROMEDRIVER] section)
# -----------------------------------------------------------------------------
LOCAL_USE = config['CHROMEDRIVER'].getboolean('LOCAL_USE')  # True=local Chrome, False=remote
HUB_ADDRESS = config['CHROMEDRIVER']['HUB_ADDRESS']         # Remote WebDriver URL (if LOCAL_USE=False)

# -----------------------------------------------------------------------------
# UI Element Locators
# -----------------------------------------------------------------------------
# XPath for the "Continue" button (Spanish: "Continuar") used in navigation
REGEX_CONTINUE = "//a[contains(text(),'Continuar')]"


# -----------------------------------------------------------------------------
# Custom Date Filter Function
# -----------------------------------------------------------------------------
# MY_CONDITION allows additional filtering beyond the date range.
# Return True to accept the date, False to reject it.
# Example: Only accept dates in November on or after the 5th:
#   def MY_CONDITION(month, day): return int(month) == 11 and int(day) >= 5
def MY_CONDITION(month: int, day: int) -> bool: return True  # Accept all dates within range

# -----------------------------------------------------------------------------
# Timing Constants (in seconds)
# -----------------------------------------------------------------------------
# Adjust these values based on network latency and server responsiveness
STEP_TIME = 0.5         # Delay between form interactions (clicks, inputs)
RETRY_TIME = 60*3       # Interval between availability checks (3 minutes)
EXCEPTION_TIME = 60*30  # Wait time after errors before retrying (30 minutes)
COOLDOWN_TIME = 60*10   # Wait time when no appointments available (10 minutes)

# -----------------------------------------------------------------------------
# API Endpoints
# -----------------------------------------------------------------------------
# These URLs are constructed using the country code, schedule ID, and facility ID
DATE_URL = f"https://ais.usvisa-info.com/{COUNTRY_CODE}/niv/schedule/{SCHEDULE_ID}/appointment/days/{FACILITY_ID}.json?appointments[expedite]=false"
TIME_URL = f"https://ais.usvisa-info.com/{COUNTRY_CODE}/niv/schedule/{SCHEDULE_ID}/appointment/times/{FACILITY_ID}.json?date=%s&appointments[expedite]=false"
APPOINTMENT_URL = f"https://ais.usvisa-info.com/{COUNTRY_CODE}/niv/schedule/{SCHEDULE_ID}/appointment?confirmed_limit_message=1"

# -----------------------------------------------------------------------------
# JavaScript for Async XHR Requests
# -----------------------------------------------------------------------------
# This script is executed in the browser to make authenticated API calls.
# Uses async XHR instead of sync XHR (which Chrome now blocks).
# Cookies are sent automatically since we're on the same domain.
# %s = URL
JS_SCRIPT = """
var callback = arguments[arguments.length - 1];
var req = new XMLHttpRequest();
req.open('GET', '%s', true);
req.setRequestHeader('Accept', 'application/json, text/javascript, */*; q=0.01');
req.setRequestHeader('X-Requested-With', 'XMLHttpRequest');
req.withCredentials = true;
req.onreadystatechange = function() {
    if (req.readyState === 4) {
        if (req.status === 200) {
            callback(req.responseText);
        } else {
            callback(JSON.stringify({error: 'HTTP ' + req.status + ': ' + req.statusText, body: req.responseText}));
        }
    }
};
req.onerror = function() {
    callback(JSON.stringify({error: 'XHR network error'}));
};
req.send(null);
"""

# -----------------------------------------------------------------------------
# Global State
# -----------------------------------------------------------------------------
# Set to True when a successful reschedule occurs to stop the main loop
EXIT = False


def send_notification(msg: str) -> None:
    """Send notification message through all configured channels.

    Attempts to send the notification via each configured service. If a service
    is not configured (API key or webhook is empty/missing), it is skipped.
    Errors in one channel do not prevent attempts to other channels.

    Args:
        msg: The notification message to send. Used as both subject and body
            for email notifications.

    Configured channels:
        - Telegram: Sends message via Telegram bot (recommended - fast & reliable)
        - Discord: Posts message to Discord channel webhook (recommended)
        - SendGrid: Sends email to USERNAME (the account email)
        - Pushover: Sends push notification to mobile device
        - Slack: Posts message to configured webhook channel
    """
    log(f"Sending notification: {msg}")

    # Telegram - fast, reliable, and free
    if TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID:
        log("  -> Sending via Telegram...")
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        data = {
            "chat_id": TELEGRAM_CHAT_ID,
            "text": msg,
            "parse_mode": "HTML"
        }
        try:
            resp = requests.post(url, data=data)
            if resp.ok:
                log("  -> Telegram: OK")
            else:
                log(f"  -> Telegram: Failed ({resp.status_code}: {resp.text})", "warning")
        except Exception as e:
            log(f"  -> Telegram: Exception - {e}", "error")

    # Discord - popular for gaming/tech communities
    if DISCORD_WEBHOOK:
        log("  -> Sending via Discord...")
        headers = {'Content-type': 'application/json'}
        payload = {'content': msg}
        try:
            resp = requests.post(DISCORD_WEBHOOK, data=json.dumps(payload), headers=headers)
            if resp.ok:
                log("  -> Discord: OK")
            else:
                log(f"  -> Discord: Failed ({resp.status_code}: {resp.text})", "warning")
        except Exception as e:
            log(f"  -> Discord: Exception - {e}", "error")

    if SENDGRID_API_KEY:
        log("  -> Sending via SendGrid...")
        message = Mail(
            from_email=USERNAME,
            to_emails=USERNAME,
            subject=msg,
            html_content=msg)
        try:
            sg = SendGridAPIClient(SENDGRID_API_KEY)
            response = sg.send(message)
            log(f"  -> SendGrid: OK ({response.status_code})")
        except Exception as e:
            log(f"  -> SendGrid: Failed - {e}", "error")

    if PUSH_TOKEN:
        log("  -> Sending via Pushover...")
        url = "https://api.pushover.net/1/messages.json"
        data = {
            "token": PUSH_TOKEN,
            "user": PUSH_USER,
            "message": msg
        }
        try:
            resp = requests.post(url, data=data)
            if resp.ok:
                log("  -> Pushover: OK")
            else:
                log(f"  -> Pushover: Failed ({resp.status_code}: {resp.text})", "warning")
        except Exception as e:
            log(f"  -> Pushover: Exception - {e}", "error")

    if SLACK_WEBHOOK:
        log("  -> Sending via Slack...")
        headers = {'Content-type': 'application/json'}
        payload = {'text': msg}
        try:
            resp = requests.post(SLACK_WEBHOOK, data=json.dumps(payload), headers=headers)
            if resp.ok:
                log("  -> Slack: OK")
            else:
                log(f"  -> Slack: Failed ({resp.status_code}: {resp.text})", "warning")
        except Exception as e:
            log(f"  -> Slack: Exception - {e}", "error")


def get_driver() -> Union[webdriver.Chrome, webdriver.Remote]:
    """Initialize and return a Selenium WebDriver instance.

    Creates either a local Chrome browser instance or connects to a remote
    WebDriver hub based on the LOCAL_USE configuration setting.

    Returns:
        WebDriver instance (Chrome for local, Remote for hub connection).

    Configuration:
        LOCAL_USE=True: Creates a local Chrome browser using webdriver-manager
            for automatic ChromeDriver version handling.
        LOCAL_USE=False: Connects to a remote Selenium hub at HUB_ADDRESS,
            useful for running in Docker or on a remote server.
    """
    if LOCAL_USE:
        dr = webdriver.Chrome()
    else:
        dr = webdriver.Remote(command_executor=HUB_ADDRESS, options=webdriver.ChromeOptions())
    return dr

driver = get_driver()


def login() -> None:
    """Navigate to the visa portal and complete the login process.

    Performs the full login flow including:
    1. Navigating to the visa portal landing page
    2. Clicking through initial UI elements to bypass reCAPTCHA triggers
    3. Navigating to the login form
    4. Delegating credential entry to do_login_action()

    The function uses deliberate timing delays (STEP_TIME) between interactions
    to mimic human behavior and avoid triggering bot detection mechanisms.

    Raises:
        TimeoutException: If the login form or navigation elements don't appear
            within 60 seconds.
    """
    # Bypass reCAPTCHA
    driver.get(f"https://ais.usvisa-info.com/{COUNTRY_CODE}/niv")
    time.sleep(STEP_TIME)
    a = driver.find_element(By.XPATH, '//a[@class="down-arrow bounce"]')
    a.click()
    time.sleep(STEP_TIME)

    log("Login start...")
    href = driver.find_element(By.XPATH, '//*[@id="header"]/nav/div[1]/div[1]/div[2]/div[1]/ul/li[3]/a')

    href.click()
    time.sleep(STEP_TIME)
    Wait(driver, 60).until(EC.presence_of_element_located((By.NAME, "commit")))

    log("  click bounce")
    a = driver.find_element(By.XPATH, '//a[@class="down-arrow bounce"]')
    a.click()
    time.sleep(STEP_TIME)

    do_login_action()


def do_login_action() -> None:
    """Fill in and submit the login form with configured credentials.

    Interacts with the login form elements to:
    1. Enter the username (email) from config
    2. Enter the password from config
    3. Accept the privacy policy checkbox
    4. Submit the form

    Random delays (1-3 seconds) are added between each interaction to simulate
    human typing speed and reduce the chance of bot detection. The function
    waits up to 60 seconds for the "Continue" button to appear, indicating
    successful authentication.

    Raises:
        TimeoutException: If login fails or the continue button doesn't appear
            within 60 seconds.
    """
    log("  input email")
    user = driver.find_element(By.ID, 'user_email')
    user.send_keys(USERNAME)
    time.sleep(random.randint(1, 3))

    log("  input pwd")
    pw = driver.find_element(By.ID, 'user_password')
    pw.send_keys(PASSWORD)
    time.sleep(random.randint(1, 3))

    log("  click privacy")
    box = driver.find_element(By.CLASS_NAME, 'icheckbox')
    box .click()
    time.sleep(random.randint(1, 3))

    log("  commit")
    btn = driver.find_element(By.NAME, 'commit')
    btn.click()
    time.sleep(random.randint(1, 3))

    Wait(driver, 60).until(
        EC.presence_of_element_located((By.XPATH, REGEX_CONTINUE)))
    log("  login successful!")


def get_date() -> list[dict[str, Any]]:
    """Fetch available appointment dates from the visa portal API.

    Uses Python requests with the session cookie extracted from the browser.
    This avoids Chrome's restrictions on synchronous XHR.

    Returns:
        List of date dictionaries, each containing:
            - 'date': Date string in YYYY-MM-DD format
            - 'business_day': Boolean indicating if it's a business day
    """
    driver.get(APPOINTMENT_URL)
    time.sleep(STEP_TIME)  # Wait for page to load

    # Extract session cookie from browser
    session_cookie = driver.get_cookie("_yatri_session")["value"]

    headers = {
        "Accept": "application/json, text/javascript, */*; q=0.01",
        "X-Requested-With": "XMLHttpRequest",
        "User-Agent": driver.execute_script("return navigator.userAgent;"),
    }
    cookies = {"_yatri_session": session_cookie}

    response = requests.get(DATE_URL, headers=headers, cookies=cookies)
    log(f"API response: {response.status_code} OK ({len(response.text)} bytes)")

    if response.status_code != 200:
        raise Exception(f"API error: HTTP {response.status_code}")

    data = response.json()
    if isinstance(data, dict) and 'error' in data:
        raise Exception(f"API error: {data['error']}")
    return data

def get_time(date: str) -> str:
    """Fetch available time slots for a specific appointment date.

    Queries the visa portal API for available appointment times on the
    given date. Uses Python requests with the session cookie from the browser.

    Args:
        date: The appointment date in YYYY-MM-DD format.

    Returns:
        The last available time slot string (e.g., "10:30").
        Returns the last slot as it's typically less contested.
    """
    time_url = TIME_URL % date

    # Extract session cookie from browser
    session_cookie = driver.get_cookie("_yatri_session")["value"]

    headers = {
        "Accept": "application/json, text/javascript, */*; q=0.01",
        "X-Requested-With": "XMLHttpRequest",
        "User-Agent": driver.execute_script("return navigator.userAgent;"),
    }
    cookies = {"_yatri_session": session_cookie}

    response = requests.get(time_url, headers=headers, cookies=cookies)
    data = response.json()
    log(f"Got time successfully! {data}")
    time = data.get("available_times")[-1]
    log(f"Got time successfully! {date} {time}")
    return time


def reschedule(date: str) -> None:
    """Book a new appointment for the specified date.

    Automates the appointment rescheduling form by:
    1. Entering the new date in the date picker
    2. Selecting an available time slot
    3. Submitting the appointment form
    4. Confirming the booking

    On success, sets the global EXIT flag to True to stop the main loop.
    Sends notifications for both success and failure outcomes.

    Args:
        date: The new appointment date in YYYY-MM-DD format.

    Side effects:
        Sets EXIT=True on successful reschedule.
        Sends notification with result status.
    """
    global EXIT
    log(f"Starting Reschedule ({date})")
    send_notification(f"🔄 <b>Rescheduling</b>\n\nAttempting: <code>{date}</code>")

    log("  input date")
    date_input = driver.find_element(By.ID, 'appointments_consulate_appointment_date')
    driver.execute_script("arguments[0].removeAttribute('readonly')", date_input)
    date_input.send_keys(date)
    time.sleep(random.randint(1, 2))
    log("  select day")
    current_day = driver.find_element(By.CLASS_NAME, 'ui-datepicker-current-day')
    current_day.find_element(By.XPATH, './a').click()
    time.sleep(random.randint(1, 2))

    log("  select time")
    select = driver.find_element(By.ID, 'appointments_consulate_appointment_time')
    # select first available option
    select.find_element(By.XPATH, './option[2]').click()

    time.sleep(random.randint(1, 2))

    log("  accept appointment")
    accept = driver.find_element(By.ID, 'appointments_submit')
    accept.click()
    time.sleep(random.randint(1, 2))

    # Confirmar
    # Get a tag with text "Confirmar"
    confirm_button = driver.find_element(By.XPATH, '//*[contains(text(), "Confirmar")]')

    if DRY_RUN:
        # Dry-run mode: validate flow but don't actually confirm
        log("  🧪 DRY_RUN: Found 'Confirmar' button - would click here")
        log("  🧪 DRY_RUN: All form elements validated successfully!")
        msg = f"🧪 <b>Dry-Run Complete</b>\n\nValidated reschedule flow for: <code>{date}</code>\n\n✅ Date input\n✅ Time selection\n✅ Submit button\n✅ Confirm button found\n\n<i>No actual changes made</i>"
        send_notification(msg)
        log("  🧪 DRY_RUN: Reschedule validation successful - no changes made")
        return

    confirm_button.click()
    time.sleep(3)

    # Check if the following text is present "La programación de su cita se ha realizado correctamente"

    if(driver.page_source.find('La programación de su cita se ha realizado correctamente') != -1):
        msg = f"✅ <b>Success!</b>\n\nNew appointment: <code>{date}</code>"
        send_notification(msg)
        EXIT = True
    else:
        msg = f"❌ <b>Failed</b>\n\nCould not book: <code>{date}</code>"
        send_notification(msg)


def is_logged_in() -> bool:
    """Check if the current browser session is authenticated.

    Performs a simple check by looking for error messages in the page source.
    This is a basic validation and may not catch all session expiry cases.

    Returns:
        True if no error text is found in the page, False otherwise.
    """
    content = driver.page_source
    if(content.find("error") != -1):
        return False
    return True


def print_dates(dates: list[dict[str, Any]]) -> None:
    """Print available appointment dates to console for debugging.

    Args:
        dates: List of date dictionaries from get_date(), each containing
            'date' and 'business_day' keys.
    """
    log(f"Found {len(dates)} available dates (showing first 5):")
    for d in dates:
        log(f"  • {d.get('date')}")


def get_available_date(dates: list[dict[str, Any]]) -> Optional[str]:
    """Find the first available date within the configured date range.

    Iterates through the provided dates and returns the first one that falls
    within the acceptable range defined by MY_SCHEDULE_DATE_START and
    MY_SCHEDULE_DATE configuration values.

    Args:
        dates: List of date dictionaries from get_date(), each containing
            a 'date' key with YYYY-MM-DD format string.

    Returns:
        The first matching date string in YYYY-MM-DD format, or None if
        no dates fall within the acceptable range.
    """

    def is_in_period(date: str, PSD: datetime, PED: datetime) -> bool:
        new_date = datetime.strptime(date, "%Y-%m-%d")
        result = ( PED > new_date and new_date > PSD )
        return result

    PED = datetime.strptime(MY_SCHEDULE_DATE, "%Y-%m-%d")
    PSD = datetime.strptime(MY_SCHEDULE_DATE_START, "%Y-%m-%d")
    log(f"Filtering for dates between {PSD.date()} and {PED.date()}...")
    for d in dates:
        date = d.get('date')
        if is_in_period(date, PSD, PED):
            log(f"✅ Found date in range: {date}")
            return date
    log(f"❌ No dates in target range")


def push_notification(dates: list[dict[str, Any]]) -> None:
    """Send a notification with all available dates.

    Formats the dates into a semicolon-separated string and sends
    via all configured notification channels.

    Args:
        dates: List of date dictionaries to include in the notification.
    """
    date_list = "\n".join(f"• {d.get('date')}" for d in dates)
    msg = f"📅 <b>Dates Available</b>\n\n{date_list}"
    send_notification(msg)


# =============================================================================
# Main Execution Loop
# =============================================================================
if __name__ == "__main__":
    # Display startup configuration summary
    log("=" * 60)
    log("US VISA APPOINTMENT RESCHEDULER")
    log("=" * 60)
    log(f"Target date range: {MY_SCHEDULE_DATE_START} to {MY_SCHEDULE_DATE}")
    log(f"Facility ID: {FACILITY_ID}")
    log(f"Schedule ID: {SCHEDULE_ID}")
    log(f"Log file: {LOG_FILE}")
    log("-" * 60)
    log("Timing configuration:")
    log(f"  Retry interval:    {RETRY_TIME}s ({RETRY_TIME // 60}m)")
    log(f"  Cooldown interval: {COOLDOWN_TIME}s ({COOLDOWN_TIME // 60}m)")
    log(f"  Exception wait:    {EXCEPTION_TIME}s ({EXCEPTION_TIME // 60}m)")
    log("-" * 60)
    notifications = []
    if TELEGRAM_BOT_TOKEN: notifications.append("Telegram")
    if DISCORD_WEBHOOK: notifications.append("Discord")
    if SENDGRID_API_KEY: notifications.append("SendGrid")
    if PUSH_TOKEN: notifications.append("Pushover")
    if SLACK_WEBHOOK: notifications.append("Slack")
    log(f"Notifications: {', '.join(notifications) if notifications else 'None configured'}")
    log("=" * 60)

    # Outer loop: keeps running until successful reschedule (EXIT=True)
    while not EXIT:
        try:
            login()
            retry_count = 0
            # Inner loop: allows up to 6 retries before forcing re-login
            # This handles transient errors without requiring full re-authentication
            while retry_count <= 6:
                try:
                    log("=" * 60)
                    log(f"📡 CHECK #{retry_count + 1}")
                    log("=" * 60)

                    # Fetch only top 5 dates to reduce processing time
                    dates = get_date()[:5]
                    print_dates(dates)
                    date = get_available_date(dates)
                    if date:
                        # Found a date in range - attempt to book it
                        reschedule(date)
                        push_notification(dates)

                    if(EXIT):
                        log("Exiting - appointment successfully rescheduled")
                        break

                    if not dates:
                        # Empty list may indicate rate limiting or temporary ban
                        # Use longer cooldown to avoid further restrictions
                        next_check = datetime.now() + timedelta(seconds=COOLDOWN_TIME)
                        log(f"⏸️  COOLDOWN: No dates returned (possible rate limit)")
                        log(f"   Next action: Check for available dates")
                        log(f"   Next check at: {next_check.strftime('%H:%M:%S')}")
                        time.sleep(COOLDOWN_TIME)
                    else:
                        # Normal polling interval between availability checks
                        next_check = datetime.now() + timedelta(seconds=RETRY_TIME)
                        log(f"⏳ WAITING: No dates in target range")
                        log(f"   Next action: Check for available dates")
                        log(f"   Next check at: {next_check.strftime('%H:%M:%S')}")
                        time.sleep(RETRY_TIME)

                except Exception as e:
                    # Increment retry counter and continue - may be transient error
                    retry_count += 1
                    import traceback
                    error_details = traceback.format_exc()
                    log(f"Exception in main loop: {e}", "error")
                    log(error_details, "error")
                    send_notification(f"⚠️ <b>Error</b>\n\n{type(e).__name__}: {e}")
                    next_check = datetime.now() + timedelta(seconds=RETRY_TIME)
                    log(f"🔄 RETRY: Error occurred (attempt {retry_count}/6)", "warning")
                    log(f"   Next action: Retry date check")
                    log(f"   Next check at: {next_check.strftime('%H:%M:%S')}")
                    time.sleep(RETRY_TIME)
            # Exhausted all retries without success - likely session expired
            if not EXIT:
                log(f"🚨 MAX RETRIES: Session likely expired", "error")
                log(f"   Next action: Re-login and restart monitoring")
                send_notification("🚨 <b>Crashed</b>\n\nMax retries exceeded, restarting...")
        except Exception as e:
            # Login failure - wait longer before retrying with fresh browser
            log(f"Login failed with exception: {e}", "error")
            send_notification("⚠️ <b>Login Error</b>\n\nException during login, retrying...")
            next_retry = datetime.now() + timedelta(seconds=EXCEPTION_TIME)
            log(f"💥 LOGIN FAILED: {type(e).__name__}", "error")
            log(f"   Next action: Reinitialize browser and retry login")
            log(f"   Next retry at: {next_retry.strftime('%H:%M:%S')}")
            time.sleep(EXCEPTION_TIME)
            driver.quit()
            driver = get_driver()  # Reinitialize the driver
