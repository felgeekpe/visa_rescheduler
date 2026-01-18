# visa_rescheduler
US VISA (ais.usvisa-info.com) appointment re-scheduler - Colombian adaptation

## Prerequisites
- Having a US VISA appointment scheduled already
- Google Chrome installed (to be controlled by the script)
- Python v3 installed (for running the script)
- At least one notification channel configured (Telegram recommended)

## Initial Setup
1. Create a `config.ini` file based on `config.ini.example`
2. Install the required python packages: `pip3 install -r requirements.txt`
3. Configure your notification channel(s) (see below)

## Executing the script
- Simply run `python3 visa.py`
- That's it!

---

## Notification Setup

The script supports multiple notification channels. At least one is recommended so you're alerted when an earlier appointment is found and booked.

### Telegram (Recommended)

Telegram is the recommended notification method - it's free, fast, and reliable.

#### Step 1: Create a Telegram Bot

1. Open Telegram and search for [@BotFather](https://t.me/BotFather)
2. Start a chat and send `/newbot`
3. Follow the prompts to name your bot (e.g., "Visa Rescheduler Bot")
4. BotFather will give you a **bot token** that looks like: `123456789:ABCdefGHIjklMNOpqrsTUVwxyz`
5. Copy this token to `TELEGRAM_BOT_TOKEN` in your `config.ini`

#### Step 2: Get Your Chat ID

1. Search for [@userinfobot](https://t.me/userinfobot) on Telegram
2. Start a chat and send any message
3. The bot will reply with your user information including your **chat ID** (a number like `123456789`)
4. Copy this ID to `TELEGRAM_CHAT_ID` in your `config.ini`

#### Step 3: Start Your Bot

**Important:** You must start a conversation with your bot before it can send you messages.

1. Search for your bot by the username you created
2. Click "Start" or send `/start`
3. Your bot can now send you notifications

For more details, see the [Telegram Bot API documentation](https://core.telegram.org/bots#how-do-i-create-a-bot).

---

### Discord

Discord webhooks allow the script to post messages to a channel of your choice.

#### Step 1: Create a Webhook

1. Open Discord and go to the server where you want notifications
2. Right-click the channel and select **Edit Channel**
3. Go to **Integrations** > **Webhooks**
4. Click **New Webhook**
5. Name it (e.g., "Visa Rescheduler") and optionally set an avatar
6. Click **Copy Webhook URL**
7. Paste this URL into `DISCORD_WEBHOOK` in your `config.ini`

The webhook URL looks like: `https://discord.com/api/webhooks/123456789/ABCdefGHI...`

For more details, see the [Discord Webhooks documentation](https://support.discord.com/hc/en-us/articles/228383668-Intro-to-Webhooks).

---

### Other Notification Options

#### Pushover
Get push notifications on your mobile device via [Pushover](https://pushover.net/) (one-time $5 purchase per platform).

1. Create an account at https://pushover.net/
2. Create an application to get your API token
3. Add `PUSH_TOKEN` and `PUSH_USER` to your `config.ini`

#### SendGrid
Get email notifications via [SendGrid](https://sendgrid.com/) (free tier available).

1. Create an account at https://sendgrid.com/
2. Generate an API key in Settings > API Keys
3. Add `SENDGRID_API_KEY` to your `config.ini`

#### Slack
Get notifications in a Slack channel via webhook.

1. Go to [Slack Apps](https://api.slack.com/apps) and create an app
2. Enable Incoming Webhooks
3. Add a webhook to your workspace and copy the URL
4. Add `SLACK_WEBHOOK` to your `config.ini`

---

## Deployment Options

The script needs to run continuously to monitor for appointment availability. Here are several deployment options:

### Local Machine

The simplest option - run the script on your own computer.

```bash
# Install dependencies
pip3 install -r requirements.txt

# Run the script
python3 visa.py
```

**Pros:** Easy to set up and monitor
**Cons:** Your computer must stay on; stops if you close your terminal

**Tip:** Use `nohup` or `screen` to keep it running in the background:
```bash
# Using nohup (continues after terminal closes)
nohup python3 visa.py > visa.log 2>&1 &

# Using screen (can reattach later)
screen -S visa
python3 visa.py
# Press Ctrl+A, then D to detach
# Run 'screen -r visa' to reattach
```

---

### Docker

Run the script in a Docker container for better isolation and portability.

#### Dockerfile

Create a `Dockerfile` in the project directory:

```dockerfile
FROM python:3.11-slim

# Install Chrome and dependencies
RUN apt-get update && apt-get install -y \
    wget \
    gnupg \
    unzip \
    && wget -q -O - https://dl-ssl.google.com/linux/linux_signing_key.pub | apt-key add - \
    && echo "deb [arch=amd64] http://dl.google.com/linux/chrome/deb/ stable main" >> /etc/apt/sources.list.d/google.list \
    && apt-get update \
    && apt-get install -y google-chrome-stable \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

CMD ["python3", "visa.py"]
```

#### Build and Run

```bash
# Build the image
docker build -t visa-rescheduler .

# Run the container
docker run -d --name visa-rescheduler visa-rescheduler
```

---

### Cloud VPS

Deploy to a cloud virtual private server for 24/7 availability without keeping your local machine running.

#### Recommended Providers
- [DigitalOcean Droplets](https://www.digitalocean.com/products/droplets) - Starting at $4/month
- [AWS EC2](https://aws.amazon.com/ec2/) - Free tier available for 12 months
- [Google Cloud Compute Engine](https://cloud.google.com/compute) - Free tier available
- [Linode](https://www.linode.com/) - Starting at $5/month
- [Vultr](https://www.vultr.com/) - Starting at $2.50/month

#### Basic Setup (Ubuntu/Debian)

```bash
# Update system
sudo apt update && sudo apt upgrade -y

# Install Python and Chrome
sudo apt install -y python3 python3-pip wget gnupg

# Install Chrome
wget -q -O - https://dl-ssl.google.com/linux/linux_signing_key.pub | sudo apt-key add -
echo "deb [arch=amd64] http://dl.google.com/linux/chrome/deb/ stable main" | sudo tee /etc/apt/sources.list.d/google-chrome.list
sudo apt update
sudo apt install -y google-chrome-stable

# Clone/upload your project
# ... transfer your files ...

# Install dependencies
pip3 install -r requirements.txt

# Run with screen for persistence
screen -S visa
python3 visa.py
# Press Ctrl+A, then D to detach
```

#### Using systemd (Recommended for VPS)

Create a systemd service for automatic startup and restart:

```bash
sudo nano /etc/systemd/system/visa-rescheduler.service
```

Add this content (adjust paths as needed):

```ini
[Unit]
Description=Visa Rescheduler
After=network.target

[Service]
Type=simple
User=your-username
WorkingDirectory=/path/to/visa_rescheduler
ExecStart=/usr/bin/python3 /path/to/visa_rescheduler/visa.py
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

Enable and start the service:

```bash
sudo systemctl daemon-reload
sudo systemctl enable visa-rescheduler
sudo systemctl start visa-rescheduler

# Check status
sudo systemctl status visa-rescheduler

# View logs
sudo journalctl -u visa-rescheduler -f
```

---

## Troubleshooting

### Chrome/ChromeDriver Issues
- The script uses `webdriver-manager` to automatically download the correct ChromeDriver version
- If you encounter issues, ensure Google Chrome is installed and up to date
- For headless servers, Chrome runs in headless mode automatically

### Login Failures
- Verify your credentials in `config.ini`
- Check that your `SCHEDULE_ID` is correct (found in your appointment URL)
- The USVISA site may have rate limiting - wait before retrying

### No Notifications Received
- For Telegram: Ensure you've started a conversation with your bot
- For Discord: Verify the webhook URL is complete and the channel permissions allow webhooks
- Check the script output for error messages

---

## Acknowledgement
Thanks to @yaojialyu for creating the initial script and to @cejaramillof for adapting it to Colombia!
