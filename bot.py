"""
YouTube Downloader Telegram Bot — Pyrogram Edition
Supports files up to 2 GB (user account, not bot token)
AWS-ready with systemd / Docker support
"""

import os
import re
import logging
import asyncio
import tempfile
import shutil
import time
from pathlib import Path

from pyrogram import Client, filters
from pyrogram.types import (
    Message, CallbackQuery,
    InlineKeyboardMarkup, InlineKeyboardButton,
)
from pyrogram.enums import ParseMode, ChatAction
import yt_dlp

# ─── Logging ──────────────────────────────────────────────────────────────────
logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    level=logging.INFO,
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("bot.log"),
    ],
)
logger = logging.getLogger(__name__)

# ─── Config ───────────────────────────────────────────────────────────────────
API_ID        = int(os.getenv("API_ID", "0"))
API_HASH      = os.getenv("API_HASH", "")
SESSION_NAME  = os.getenv("SESSION_NAME", "ytbot_session")   # saved to disk
DOWNLOAD_DIR  = Path(os.getenv("DOWNLOAD_DIR", "/tmp/ytbot_downloads"))
MAX_CONCURRENT = int(os.getenv("MAX_CONCURRENT", "3"))

DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)

_semaphore = asyncio.Semaphore(MAX_CONCURRENT)

# ─── In-memory state  {user_id: {"url": ..., "msg_id": ...}} ─────────────────
user_state: dict[int, dict] = {}

# ─── Pyrogram Client (User Account) ───────────────────────────────────────────
app = Client(
    SESSION_NAME,
    api_id=API_ID,
    api_hash=API_HASH,
)

# ─── Helpers ──────────────────────────────────────────────────────────────────
YT_REGEX = re.compile(
    r"(https?://)?(www\.)?"
    r"(youtube\.com/watch\?v=|youtu\.be/|youtube\.com/shorts/|youtube\.com/live/)"
    r"[\w\-]{11}"
)

def is_youtube_url(text: str) -> bool:
    return bool(YT_REGEX.search(text))

def extract_url(text: str) -> str:
    m = YT_REGEX.search(text)
    return m.group(0) if m else text.strip()

def human_size(num_bytes: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if num_bytes < 1024:
            return f"{num_bytes:.1f} {unit}"
        num_bytes /= 1024
    return f"{num_bytes:.1f} TB"

def human_time(secs: int) -> str:
    m, s = divmod(secs, 60)
    h, m = divmod(m, 60)
    return f"{h}h {m}m {s}s" if h else f"{m}m {s}s"

def format_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🎬 1080p  MP4", callback_data="dl|1080|mp4"),
            InlineKeyboardButton("🎬  720p  MP4", callback_data="dl|720|mp4"),
        ],
        [
            InlineKeyboardButton("🎬  480p  MP4", callback_data="dl|480|mp4"),
            InlineKeyboardButton("🎬  360p  MP4", callback_data="dl|360|mp4"),
        ],
        [
            InlineKeyboardButton("🎬  240p  MP4", callback_data="dl|240|mp4"),
            InlineKeyboardButton("🎬 Best   MP4", callback_data="dl|best|mp4"),
        ],
        [
            InlineKeyboardButton("🎵 MP3 320k", callback_data="dl|320|mp3"),
            InlineKeyboardButton("🎵 MP3 128k", callback_data="dl|128|mp3"),
        ],
        [
            InlineKeyboardButton("🎵 M4A Best", callback_data="dl|best|m4a"),
            InlineKeyboardButton("🎵 OPUS Best", callback_data="dl|best|opus"),
        ],
        [
            InlineKeyboardButton("❌ Cancel", callback_data="dl|cancel|none"),
        ],
    ])

# ─── yt-dlp helpers ───────────────────────────────────────────────────────────
def _build_ydl_opts(quality: str, fmt: str, out_dir: Path) -> dict:
    base = {
        "outtmpl": str(out_dir / "%(title).80s.%(ext)s"),
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "http_headers": {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            )
        },
    }
    if fmt == "mp3":
        base["format"] = "bestaudio/best"
        base["postprocessors"] = [{
            "key": "FFmpegExtractAudio",
            "preferredcodec": "mp3",
            "preferredquality": quality if quality != "best" else "320",
        }]
    elif fmt == "m4a":
        base["format"] = "bestaudio[ext=m4a]/bestaudio/best"
        base["postprocessors"] = [{"key": "FFmpegExtractAudio", "preferredcodec": "m4a"}]
    elif fmt == "opus":
        base["format"] = "bestaudio/best"
        base["postprocessors"] = [{"key": "FFmpegExtractAudio", "preferredcodec": "opus"}]
    else:  # mp4
        if quality == "best":
            base["format"] = "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best"
        else:
            base["format"] = (
                f"bestvideo[height<={quality}][ext=mp4]+bestaudio[ext=m4a]/"
                f"best[height<={quality}][ext=mp4]/best[height<={quality}]"
            )
        base["merge_output_format"] = "mp4"
    return base

async def fetch_info(url: str) -> dict:
    loop = asyncio.get_event_loop()
    def _run():
        with yt_dlp.YoutubeDL({"quiet": True, "no_warnings": True, "skip_download": True}) as ydl:
            return ydl.extract_info(url, download=False)
    return await loop.run_in_executor(None, _run)

async def download_media(url: str, quality: str, fmt: str, out_dir: Path) -> Path:
    loop = asyncio.get_event_loop()
    def _run():
        opts = _build_ydl_opts(quality, fmt, out_dir)
        with yt_dlp.YoutubeDL(opts) as ydl:
            ydl.download([url])
        files = list(out_dir.iterdir())
        if not files:
            raise FileNotFoundError("yt-dlp produced no output file.")
        return max(files, key=lambda f: f.stat().st_size)
    return await loop.run_in_executor(None, _run)

# ─── Progress callback factory ────────────────────────────────────────────────
def make_progress(msg: Message, label: str):
    """Returns a Pyrogram-compatible upload progress callback."""
    start = time.time()
    last_update = [0.0]

    async def _cb(current: int, total: int):
        now = time.time()
        if now - last_update[0] < 3:          # throttle: update every 3 s
            return
        last_update[0] = now
        pct  = current / total * 100 if total else 0
        done = human_size(current)
        full = human_size(total)
        elapsed = now - start
        speed = current / elapsed if elapsed else 0
        eta   = (total - current) / speed if speed else 0
        bar   = "█" * int(pct / 10) + "░" * (10 - int(pct / 10))
        text  = (
            f"📤 *Uploading {label}…*\n"
            f"`[{bar}]` {pct:.1f}%\n"
            f"{done} / {full}  •  {human_size(int(speed))}/s  •  ETA {human_time(int(eta))}"
        )
        try:
            await msg.edit_text(text, parse_mode=ParseMode.MARKDOWN)
        except Exception:
            pass

    return _cb

# ─── Command handlers ─────────────────────────────────────────────────────────
@app.on_message(filters.command("start") & filters.private)
async def cmd_start(client: Client, msg: Message):
    await msg.reply_text(
        "👋 **Welcome to YouTube Downloader Bot!**\n\n"
        "Send me any YouTube link and choose your format.\n\n"
        "📌 **Formats:**\n"
        "• 🎬 Video MP4 — 240p / 360p / 480p / 720p / 1080p / Best\n"
        "• 🎵 Audio — MP3 320k / MP3 128k / M4A / OPUS\n\n"
        "📦 **Max file size: 2 GB** (user-account upload)\n\n"
        "/help — Usage guide\n"
        "/about — About this bot",
        parse_mode=ParseMode.MARKDOWN,
    )

@app.on_message(filters.command("help") & filters.private)
async def cmd_help(client: Client, msg: Message):
    await msg.reply_text(
        "📖 **How to use:**\n\n"
        "1️⃣ Paste a YouTube URL\n"
        "2️⃣ Pick format & quality from the buttons\n"
        "3️⃣ Watch the download + upload progress\n"
        "4️⃣ Receive your file 🎉\n\n"
        "✅ Supports YouTube Shorts & Live replays too.",
        parse_mode=ParseMode.MARKDOWN,
    )

@app.on_message(filters.command("about") & filters.private)
async def cmd_about(client: Client, msg: Message):
    await msg.reply_text(
        "🤖 **YouTube Downloader Bot**\n"
        "Built with **Pyrogram** + **yt-dlp**\n"
        "Hosted on **AWS EC2** — runs 24/7\n"
        "Upload limit: **2 GB** (user session)",
        parse_mode=ParseMode.MARKDOWN,
    )

# ─── URL handler ──────────────────────────────────────────────────────────────
@app.on_message(filters.text & filters.private & ~filters.command(["start","help","about"]))
async def handle_url(client: Client, msg: Message):
    text = msg.text.strip()
    if not is_youtube_url(text):
        await msg.reply_text(
            "❌ Please send a valid YouTube URL.\n"
            "Example: `https://youtu.be/dQw4w9WgXcQ`",
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    url = extract_url(text)
    status = await msg.reply_text("🔍 Fetching video info…")

    try:
        info = await fetch_info(url)
    except Exception as e:
        await status.edit_text(f"❌ Could not fetch video info:\n`{str(e)[:300]}`", parse_mode=ParseMode.MARKDOWN)
        return

    title    = info.get("title", "Unknown")[:80]
    uploader = info.get("uploader", "Unknown")
    duration = info.get("duration", 0)
    views    = info.get("view_count", 0)
    thumb    = info.get("thumbnail")

    caption = (
        f"🎬 **{title}**\n"
        f"👤 {uploader}\n"
        f"⏱ {human_time(duration)}  •  👁 {views:,} views\n\n"
        "Choose your desired format & quality:"
    )

    # Save state
    user_state[msg.from_user.id] = {"url": url}

    await status.delete()

    if thumb:
        try:
            await msg.reply_photo(thumb, caption=caption, parse_mode=ParseMode.MARKDOWN, reply_markup=format_keyboard())
            return
        except Exception:
            pass

    await msg.reply_text(caption, parse_mode=ParseMode.MARKDOWN, reply_markup=format_keyboard())

# ─── Callback: format chosen ──────────────────────────────────────────────────
@app.on_callback_query(filters.regex(r"^dl\|"))
async def handle_choice(client: Client, cb: CallbackQuery):
    await cb.answer()
    _, quality, fmt = cb.data.split("|")

    uid = cb.from_user.id

    if fmt == "none":
        user_state.pop(uid, None)
        await cb.message.edit_caption("❌ Download cancelled.") if cb.message.photo else await cb.message.edit_text("❌ Download cancelled.")
        return

    state = user_state.get(uid)
    if not state:
        await cb.answer("⚠️ Session expired. Send the URL again.", show_alert=True)
        return

    url = state["url"]

    label_map = {
        "mp4":  f"Video {quality}p MP4" if quality != "best" else "Video Best MP4",
        "mp3":  f"Audio MP3 {quality}kbps",
        "m4a":  "Audio M4A Best",
        "opus": "Audio OPUS Best",
    }
    label = label_map.get(fmt, fmt)

    # Edit the keyboard message to show status
    edit = cb.message.edit_caption if cb.message.photo else cb.message.edit_text
    await edit(f"⏳ **Downloading** {label}…\nPlease wait.", parse_mode=ParseMode.MARKDOWN)

    async with _semaphore:
        tmp_dir = Path(tempfile.mkdtemp(dir=DOWNLOAD_DIR))
        try:
            # ── Download ──────────────────────────────────────────────────
            file_path = await download_media(url, quality, fmt, tmp_dir)
            size_bytes = file_path.stat().st_size

            await edit(
                f"✅ Download complete!  **{human_size(size_bytes)}**\n"
                f"📤 Uploading to Telegram…",
                parse_mode=ParseMode.MARKDOWN,
            )

            # ── Upload progress message ───────────────────────────────────
            prog_msg = await cb.message.reply_text("📤 Starting upload…")
            progress_cb = make_progress(prog_msg, label)

            info = await fetch_info(url)   # re-fetch lightweight info for metadata
            title   = info.get("title", "video")[:60]
            thumb   = info.get("thumbnail")

            # ── Send file ─────────────────────────────────────────────────
            caption = (
                f"✅ **{title}**\n"
                f"📁 `{file_path.name}`\n"
                f"💾 {human_size(size_bytes)}  •  🎚 {label}"
            )

            send_kwargs = dict(
                chat_id=cb.message.chat.id,
                caption=caption,
                parse_mode=ParseMode.MARKDOWN,
                progress=progress_cb,
                thumb=thumb,
            )

            if fmt in ("mp3", "m4a", "opus"):
                await client.send_audio(
                    audio=str(file_path),
                    title=title,
                    **send_kwargs,
                )
            else:
                await client.send_video(
                    video=str(file_path),
                    supports_streaming=True,
                    **send_kwargs,
                )

            await prog_msg.delete()
            await edit(f"✅ **{label}** delivered below! 🎉", parse_mode=ParseMode.MARKDOWN)

        except yt_dlp.utils.DownloadError as e:
            logger.error("DownloadError: %s", e)
            await edit(
                f"❌ **Download failed!**\n"
                f"The video may be private, age-restricted, or geo-blocked.\n\n"
                f"`{str(e)[:250]}`",
                parse_mode=ParseMode.MARKDOWN,
            )
        except Exception as e:
            logger.exception("Unexpected error")
            await edit(f"❌ Unexpected error:\n`{str(e)[:300]}`", parse_mode=ParseMode.MARKDOWN)
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)
            user_state.pop(uid, None)

# ─── Run ──────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    if not API_ID or not API_HASH:
        raise RuntimeError("Set API_ID and API_HASH environment variables.")
    logger.info("🤖 Starting YouTube Downloader Bot (Pyrogram / user session)…")
    app.run()
