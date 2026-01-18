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
        COUNTRY_CODE: Country code for the portal (e.g., 'es-co' for Colombia)
        FACILITY_ID: Consulate/facility ID (e.g., 25 for Bogota)

    [CHROMEDRIVER]
        LOCAL_USE: True for local Chrome, False for remote WebDriver
        HUB_ADDRESS: Remote WebDriver URL (required if LOCAL_USE is False)

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
from datetime import datetime
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
MY_SCHEDULE_DATE_START = config['USVISA']['MY_SCHEDULE_DATE_START']  # Earliest acceptable date
MY_SCHEDULE_DATE = config['USVISA']['MY_SCHEDULE_DATE']              # Latest acceptable date (deadline)
COUNTRY_CODE = config['USVISA']['COUNTRY_CODE']   # Portal locale (e.g., 'es-co' for Colombia)
FACILITY_ID = config['USVISA']['FACILITY_ID']     # Consulate ID (e.g., 25 for Bogota)

# -----------------------------------------------------------------------------
# Notification Services (all optional)
# -----------------------------------------------------------------------------
SENDGRID_API_KEY = config['SENDGRID']['SENDGRID_API_KEY']  # Email notifications via SendGrid
PUSH_TOKEN = config['PUSHOVER']['PUSH_TOKEN']              # Pushover API token
PUSH_USER = config['PUSHOVER']['PUSH_USER']                # Pushover user key
SLACK_WEBHOOK = config['SLACK']['SLACK_WEBHOOK']           # Slack webhook URL

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
# JavaScript for XHR Requests
# -----------------------------------------------------------------------------
# This script is executed in the browser to make authenticated API calls.
# It bypasses CORS restrictions by running within the page context and
# uses the session cookie for authentication.
JS_SCRIPT = ("var req = new XMLHttpRequest();"
                f"req.open('GET', '%s', false);"
                "req.setRequestHeader('Accept', 'application/json, text/javascript, /; q=0.01');"
                "req.setRequestHeader('X-Requested-With', 'XMLHttpRequest');"
                f"req.setRequestHeader('Cookie', '_yatri_session=%s');"
                "req.send(null);"
                "return req.responseText;")

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
        - SendGrid: Sends email to USERNAME (the account email)
        - Pushover: Sends push notification to mobile device
        - Slack: Posts message to configured webhook channel
    """
    print(f"Sending notification: {msg}")

    if SENDGRID_API_KEY:
        message = Mail(
            from_email=USERNAME,
            to_emails=USERNAME,
            subject=msg,
            html_content=msg)
        try:
            sg = SendGridAPIClient(SENDGRID_API_KEY)
            response = sg.send(message)
            print(response.status_code)
            print(response.body)
            print(response.headers)
        except Exception as e:
            print(e.message)

    if PUSH_TOKEN:
        url = "https://api.pushover.net/1/messages.json"
        data = {
            "token": PUSH_TOKEN,
            "user": PUSH_USER,
            "message": msg
        }
        requests.post(url, data)

    if SLACK_WEBHOOK:
        headers = {'Content-type': 'application/json'}
        payload = {'text': msg}
        requests.post(SLACK_WEBHOOK, data=json.dumps(payload), headers=headers)


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

    print("Login start...")
    href = driver.find_element(By.XPATH, '//*[@id="header"]/nav/div[1]/div[1]/div[2]/div[1]/ul/li[3]/a')
   
    href.click()
    time.sleep(STEP_TIME)
    Wait(driver, 60).until(EC.presence_of_element_located((By.NAME, "commit")))

    print("\tclick bounce")
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
    print("\tinput email")
    user = driver.find_element(By.ID, 'user_email')
    user.send_keys(USERNAME)
    time.sleep(random.randint(1, 3))

    print("\tinput pwd")
    pw = driver.find_element(By.ID, 'user_password')
    pw.send_keys(PASSWORD)
    time.sleep(random.randint(1, 3))

    print("\tclick privacy")
    box = driver.find_element(By.CLASS_NAME, 'icheckbox')
    box .click()
    time.sleep(random.randint(1, 3))

    print("\tcommit")
    btn = driver.find_element(By.NAME, 'commit')
    btn.click()
    time.sleep(random.randint(1, 3))

    Wait(driver, 60).until(
        EC.presence_of_element_located((By.XPATH, REGEX_CONTINUE)))
    print("\tlogin successful!")


def get_date() -> list[dict[str, Any]]:
    driver.get(APPOINTMENT_URL)
    session = driver.get_cookie("_yatri_session")["value"]
    script = "var req = new XMLHttpRequest();req.open('GET', '" + str(DATE_URL) + "', false);req.setRequestHeader('Accept', 'application/json, text/javascript, /; q=0.01');req.setRequestHeader('X-Requested-With', 'XMLHttpRequest'); req.setRequestHeader('Cookie', '_yatri_session=" + session + "'); req.send(null);return req.responseText;"
    NEW_GET = driver.execute_script(script)
    return json.loads(NEW_GET)

def get_time(date: str) -> str:
    time_url = TIME_URL % date
    session = driver.get_cookie("_yatri_session")["value"]
    script = JS_SCRIPT % (str(time_url), session)
    content = driver.execute_script(script)
    data = json.loads(content)
    print(f"Got time successfully! {data}")
    time = data.get("available_times")[-1]
    print(f"Got time successfully! {date} {time}")
    return time


def reschedule(date: str) -> None:
    global EXIT
    print(f"Starting Reschedule ({date})")
    send_notification(f"Starting Reschedule ({date})")

    print("\tinput date")
    date_input = driver.find_element(By.ID, 'appointments_consulate_appointment_date')
    driver.execute_script("arguments[0].removeAttribute('readonly')", date_input)
    date_input.send_keys(date)
    time.sleep(random.randint(1, 2))
    print("\tselect day")
    current_day = driver.find_element(By.CLASS_NAME, 'ui-datepicker-current-day')
    current_day.find_element(By.XPATH, './a').click()
    time.sleep(random.randint(1, 2))

    print("\tselect time")
    select = driver.find_element(By.ID, 'appointments_consulate_appointment_time')
    # select first available option
    select.find_element(By.XPATH, './option[2]').click()
    
    time.sleep(random.randint(1, 2))

    print("\taccept appointment")
    accept = driver.find_element(By.ID, 'appointments_submit')
    accept.click()
    time.sleep(random.randint(1, 2))

    # Confirmar
    # Get a tag with text "Confirmar"
    confirm_button = driver.find_element(By.XPATH, '//*[contains(text(), "Confirmar")]')
    confirm_button.click()
    time.sleep(3)

    # Check if the following text is present "La programación de su cita se ha realizado correctamente"

    if(driver.page_source.find('La programación de su cita se ha realizado correctamente') != -1):
        msg = f"Rescheduled Successfully! {date}"
        send_notification(msg)
        EXIT = True
    else:
        msg = f"Reschedule Failed. {date}"
        send_notification(msg)


def is_logged_in() -> bool:
    content = driver.page_source
    if(content.find("error") != -1):
        return False
    return True


def print_dates(dates: list[dict[str, Any]]) -> None:
    print("Available dates:")
    for d in dates:
        print("%s \t business_day: %s" % (d.get('date'), d.get('business_day')))
    print()


def get_available_date(dates: list[dict[str, Any]]) -> Optional[str]:

    def is_in_period(date: str, PSD: datetime, PED: datetime) -> bool:
        new_date = datetime.strptime(date, "%Y-%m-%d")
        result = ( PED > new_date and new_date > PSD )
        return result

    print("Checking for an earlier date:")
    PED = datetime.strptime(MY_SCHEDULE_DATE, "%Y-%m-%d")
    PSD = datetime.strptime(MY_SCHEDULE_DATE_START, "%Y-%m-%d")
    for d in dates:
        date = d.get('date')
        if is_in_period(date, PSD, PED):
            return date
    print(f"\n\nNo available dates between ({PSD.date()}) and ({PED.date()})!")


def push_notification(dates: list[dict[str, Any]]) -> None:
    msg = "date: "
    for d in dates:
        msg = msg + d.get('date') + '; '
    send_notification(msg)


if __name__ == "__main__":
    while not EXIT:
        try:
            login()
            retry_count = 0
            while retry_count <= 6:
                try:
                    print("------------------")
                    print(datetime.today())
                    print(f"Retry count: {retry_count}")
                    print()

                    dates = get_date()[:5]
                    print_dates(dates)
                    date = get_available_date(dates)
                    print()
                    print(f"New date: {date}")
                    if date:
                        reschedule(date)
                        push_notification(dates)

                    if(EXIT):
                        print("------------------exit")
                        break

                    if not dates:
                        msg = "List is empty"
                        print(msg)
                        time.sleep(COOLDOWN_TIME)
                    else:
                        time.sleep(RETRY_TIME)

                except:
                    retry_count += 1
                    send_notification("Exception occurred!")
                    time.sleep(RETRY_TIME)
            if not EXIT:
                send_notification("HELP! Crashed.")
        except Exception as e:
            print(f"Login failed with exception: {e}")
            send_notification("Exception occurred during login!")
            time.sleep(EXCEPTION_TIME)
            driver.quit()
            driver = get_driver()  # Reinitialize the driver
