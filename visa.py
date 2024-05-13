# -*- coding: utf8 -*-

import time
import json
import random
import platform
import configparser
from datetime import datetime

import requests
from selenium import webdriver
from selenium.webdriver.chrome.service import Service as ChromeService
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait as Wait
from selenium.webdriver.common.by import By
from webdriver_manager.chrome import ChromeDriverManager

from sendgrid import SendGridAPIClient
from sendgrid.helpers.mail import Mail


config = configparser.ConfigParser()
config.read('config.ini')

USERNAME = config['USVISA']['USERNAME']
PASSWORD = config['USVISA']['PASSWORD']
SCHEDULE_ID = config['USVISA']['SCHEDULE_ID']
MY_SCHEDULE_DATE_START = config['USVISA']['MY_SCHEDULE_DATE_START']
MY_SCHEDULE_DATE = config['USVISA']['MY_SCHEDULE_DATE']
COUNTRY_CODE = config['USVISA']['COUNTRY_CODE'] 
FACILITY_ID = config['USVISA']['FACILITY_ID']

SENDGRID_API_KEY = config['SENDGRID']['SENDGRID_API_KEY']
PUSH_TOKEN = config['PUSHOVER']['PUSH_TOKEN']
PUSH_USER = config['PUSHOVER']['PUSH_USER']

LOCAL_USE = config['CHROMEDRIVER'].getboolean('LOCAL_USE')
HUB_ADDRESS = config['CHROMEDRIVER']['HUB_ADDRESS']

REGEX_CONTINUE = "//a[contains(text(),'Continuar')]"

SLACK_WEBHOOK = config['SLACK']['SLACK_WEBHOOK']


# def MY_CONDITION(month, day): return int(month) == 11 and int(day) >= 5
def MY_CONDITION(month, day): return True # No custom condition wanted for the new scheduled date

STEP_TIME = 0.5  # time between steps (interactions with forms): 0.5 seconds
RETRY_TIME = 60*3  # wait time between retries/checks for available dates: 10 minutes
EXCEPTION_TIME = 60*30  # wait time when an exception occurs: 30 minutes
COOLDOWN_TIME = 60*60  # wait time when temporary banned (empty list): 60 minutes

DATE_URL = f"https://ais.usvisa-info.com/{COUNTRY_CODE}/niv/schedule/{SCHEDULE_ID}/appointment/days/{FACILITY_ID}.json?appointments[expedite]=false"
TIME_URL = f"https://ais.usvisa-info.com/{COUNTRY_CODE}/niv/schedule/{SCHEDULE_ID}/appointment/times/{FACILITY_ID}.json?date=%s&appointments[expedite]=false"
APPOINTMENT_URL = f"https://ais.usvisa-info.com/{COUNTRY_CODE}/niv/schedule/{SCHEDULE_ID}/appointment"
JS_SCRIPT = ("var req = new XMLHttpRequest();"
                f"req.open('GET', '%s', false);"
                "req.setRequestHeader('Accept', 'application/json, text/javascript, /; q=0.01');"
                "req.setRequestHeader('X-Requested-With', 'XMLHttpRequest');"
                f"req.setRequestHeader('Cookie', '_yatri_session=%s');"
                "req.send(null);"
                "return req.responseText;")
EXIT = False


def send_notification(msg):
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


def get_driver():
    if LOCAL_USE:
        dr = webdriver.Chrome()
    else:
        dr = webdriver.Remote(command_executor=HUB_ADDRESS, options=webdriver.ChromeOptions())
    return dr

driver = get_driver()


def login():
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


def do_login_action():
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


def get_date():
    driver.get(APPOINTMENT_URL)
    session = driver.get_cookie("_yatri_session")["value"]
    script = "var req = new XMLHttpRequest();req.open('GET', '" + str(DATE_URL) + "', false);req.setRequestHeader('Accept', 'application/json, text/javascript, /; q=0.01');req.setRequestHeader('X-Requested-With', 'XMLHttpRequest'); req.setRequestHeader('Cookie', '_yatri_session=" + session + "'); req.send(null);return req.responseText;"
    NEW_GET = driver.execute_script(script)
    return json.loads(NEW_GET)

def get_time(date):
    time_url = TIME_URL % date
    session = driver.get_cookie("_yatri_session")["value"]
    script = JS_SCRIPT % (str(time_url), session)
    content = driver.execute_script(script)
    data = json.loads(content)
    print(f"Got time successfully! {data}")
    time = data.get("available_times")[-1]
    print(f"Got time successfully! {date} {time}")
    return time


def reschedule(date):
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


def is_logged_in():
    content = driver.page_source
    if(content.find("error") != -1):
        return False
    return True


def print_dates(dates):
    print("Available dates:")
    for d in dates:
        print("%s \t business_day: %s" % (d.get('date'), d.get('business_day')))
    print()


def get_available_date(dates):

    def is_in_period(date, PSD, PED):
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


def push_notification(dates):
    msg = "date: "
    for d in dates:
        msg = msg + d.get('date') + '; '
    send_notification(msg)


if __name__ == "__main__":
    login()
    retry_count = 0
    while 1:
        if retry_count > 6:
            break
        try:
            print("------------------")
            print(datetime.today())
            print(f"Retry count: {retry_count}")
            print()

            dates = get_date()[:5]
            if not dates:
              msg = "List is empty"
              send_notification(msg)
              EXIT = True
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
              send_notification(msg)
              #EXIT = True
              time.sleep(COOLDOWN_TIME)
            else:
              time.sleep(RETRY_TIME)

        except:
            retry_count += 1
            time.sleep(EXCEPTION_TIME)

    if(not EXIT):
        send_notification("HELP! Crashed.")
