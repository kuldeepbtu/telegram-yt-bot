# 🤖 YouTube Downloader Bot — Pyrogram Edition (2 GB uploads)

A production-ready Telegram bot that downloads YouTube videos/audio and sends files up to **2 GB** using a Pyrogram **user session** — bypassing the 50 MB bot API limit entirely.

---

## ✨ Features

| Feature | Details |
|---|---|
| **2 GB file uploads** | Pyrogram user session vs. 50 MB bot token limit |
| **Live download bar** | Real-time `yt-dlp` progress shown in chat |
| **Live upload bar** | Pyrogram progress callback — speed, ETA, percent |
| **Video formats** | MP4 — 240p / 360p / 480p / 720p / 1080p / Best |
| **Audio formats** | MP3 320k / MP3 128k / M4A best / OPUS best |
| **Available resolutions** | Shows which resolutions exist before you pick |
| **Thumbnail preview** | Displays video thumbnail with metadata |
| **Auto temp-cleanup** | Files deleted immediately after upload |
| **Concurrent downloads** | Configurable semaphore (default: 3) |
| **AWS-ready** | systemd + Docker + one-shot deploy script |

---

## 📁 Project Files

```
ytbot-pyrogram/
├── bot_session.py       # Main bot (uses SESSION_STRING env var)
├── generate_session.py  # Run ONCE locally to get SESSION_STRING
├── requirements.txt
├── .env.example
├── ytbot.service        # systemd unit for EC2
├── deploy.sh            # One-command EC2 setup
├── Dockerfile
└── README.md
```

---

## 🚀 Setup Guide

### Step 1 — Get Telegram API credentials

1. Go to **https://my.telegram.org**
2. Log in with your phone number
3. Click **"API development tools"**
4. Create an app → copy **`api_id`** and **`api_hash`**

> ⚠️ Use a **dedicated Telegram account** for the bot, not your personal one.

---

### Step 2 — Generate Session String (run locally ONCE)

On your **local machine**:
```bash
pip install pyrogram TgCrypto
python generate_session.py
```
- Enter your `api_id` and `api_hash`
- Log in with the bot account's phone number + OTP
- Copy the printed `SESSION_STRING` — it's a long base64 string

> Keep this string **secret** — it's equivalent to your account password.

---

### Step 3 — Launch EC2 Instance

- **AMI:** Ubuntu 22.04 or 24.04 LTS
- **Type:** `t3.micro` (free tier) or `t3.small` for better concurrency
- **Storage:** 20 GB GP3
- **Security group:** Allow outbound HTTPS (443). No inbound ports required.

---

### Step 4 — Deploy to EC2

```bash
# From your local machine
scp -i your-key.pem -r ytbot-pyrogram/ ubuntu@<EC2-IP>:~/

# SSH in
ssh -i your-key.pem ubuntu@<EC2-IP>

# Run deploy script
bash ~/ytbot-pyrogram/deploy.sh
```

---

### Step 5 — Configure and Start

```bash
nano ~/ytbot-pyrogram/.env
```

Fill in:
```env
API_ID=12345678
API_HASH=abcdef1234567890abcdef1234567890
SESSION_STRING=BQA...your_session_string_here...
```

Then:
```bash
sudo systemctl start ytbot
sudo systemctl status ytbot

# Live logs
sudo journalctl -u ytbot -f
```

---

## ⚙️ Environment Variables

| Variable | Required | Description |
|---|---|---|
| `API_ID` | ✅ | From my.telegram.org |
| `API_HASH` | ✅ | From my.telegram.org |
| `SESSION_STRING` | ✅ | From generate_session.py |
| `DOWNLOAD_DIR` | ❌ | Default: `/tmp/ytbot_downloads` |
| `MAX_CONCURRENT` | ❌ | Default: `3` |

---

## 🛠 Useful Commands

```bash
# Restart
sudo systemctl restart ytbot

# Stop
sudo systemctl stop ytbot

# Live logs
sudo journalctl -u ytbot -f

# Update yt-dlp (do weekly!)
~/ytbot-pyrogram/venv/bin/pip install -U yt-dlp
sudo systemctl restart ytbot

# Disk usage
df -h /tmp
```

---

## 🔄 Auto-update yt-dlp (weekly cron)

```bash
crontab -e
# Add:
0 3 * * 1 /home/ubuntu/ytbot-pyrogram/venv/bin/pip install -U yt-dlp -q && sudo systemctl restart ytbot
```

---

## 🐳 Docker Option

```bash
echo "API_ID=...
API_HASH=...
SESSION_STRING=..." > .env

docker build -t ytbot .
docker run -d --restart unless-stopped --env-file .env ytbot
```

---

## 📊 How It Works

```
User sends YouTube URL
       ↓
Bot fetches metadata (title, duration, available resolutions)
       ↓
User picks format via inline keyboard
       ↓
yt-dlp downloads with live progress bar in chat
       ↓
Pyrogram uploads with live progress bar (speed / ETA)
       ↓
File delivered, temp folder wiped
```

---

## ⚠️ Notes

- **Age-restricted / private videos** require cookies — not supported out of the box.
- **Playlists** are disabled (`noplaylist: True`).
- The session account must **not** be used for normal Telegram usage while the bot is running, to avoid conflicts.
- Respect YouTube's Terms of Service — only download content you have rights to.
