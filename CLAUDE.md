# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

US VISA appointment rescheduler for the ais.usvisa-info.com system, adapted for Colombian applicants. The tool monitors for earlier appointment slots and automatically reschedules when one becomes available.

## Commands

```bash
# Set up virtual environment with uv (recommended)
uv venv
source .venv/bin/activate
uv pip install -r requirements.txt

# Or using pip
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# Run the application
python visa.py
```

## Configuration

Copy `config.ini.example` to `config.ini` and configure:

- **USVISA section**: Login credentials, schedule ID, date range (MY_SCHEDULE_DATE_START to MY_SCHEDULE_DATE), facility ID (25=Bogotá). Optional: set RELATIVE_END_DATE_DAYS (e.g., 14) to filter appointments to the next N days instead of a fixed end date
- **CHROMEDRIVER section**: LOCAL_USE=True for local Chrome, or set HUB_ADDRESS for remote WebDriver
- **Notification sections**: Optional - Pushover, SendGrid, and/or Slack webhook

## Architecture

Single-file application (`visa.py`) with this flow:

1. **login()** - Authenticates via Selenium, extracts session cookie
2. **Main loop** - Polls for available dates using JavaScript-executed XHR (avoids CORS)
3. **is_in_period()** - Filters dates within configured range
4. **reschedule()** - Browser-based form automation to book appointment
5. **send_notification()** - Multi-channel notifications (Pushover/SendGrid/Slack)

## Key Timing Constants

- `STEP_TIME` (0.5s): Delay between form interactions
- `RETRY_TIME` (180s): Interval between availability checks
- `EXCEPTION_TIME` (1800s): Wait time after errors
- `COOLDOWN_TIME` (3600s): Wait when no appointments available

## Notes

- The script targets Spanish-language pages (Colombian locale)
- Successful reschedule triggers `EXIT = True` to stop execution
- Uses webdriver-manager for automatic ChromeDriver version handling
