"""
YouTube Downloader Telegram Bot — Pyrogram Edition (Session String Mode)
Uses SESSION_STRING env var so no .session file is needed on the server.
Supports files up to 2 GB.
"""

import os
import re
import logging
import asyncio
import tempfile
import shutil
import time
from pathlib import Path

from pyrogram import Client, filters, idle
from pyrogram.types import (
    Message, CallbackQuery,
    InlineKeyboardMarkup, InlineKeyboardButton,
)
from pyrogram.enums import ParseMode
import yt_dlp

# ─── Logging ──────────────────────────────────────────────────────────────────
logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    level=logging.INFO,
    handlers=[logging.StreamHandler(), logging.FileHandler("bot.log")],
)
logger = logging.getLogger(__name__)

# ─── Config ───────────────────────────────────────────────────────────────────
API_ID         = int(os.environ["API_ID"])
API_HASH       = os.environ["API_HASH"]
SESSION_STRING = os.environ["SESSION_STRING"]   # from generate_session.py
DOWNLOAD_DIR   = Path(os.getenv("DOWNLOAD_DIR", "/tmp/ytbot_downloads"))
MAX_CONCURRENT = int(os.getenv("MAX_CONCURRENT", "3"))

DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
_semaphore = asyncio.Semaphore(MAX_CONCURRENT)
user_state: dict[int, str] = {}   # uid → url

# ─── Client ───────────────────────────────────────────────────────────────────
app = Client(
    name="ytbot",
    api_id=API_ID,
    api_hash=API_HASH,
    session_string=SESSION_STRING,
)

# ─── Utilities ────────────────────────────────────────────────────────────────
YT_REGEX = re.compile(
    r"(https?://)?(www\.)?"
    r"(youtube\.com/watch\?v=|youtu\.be/|youtube\.com/shorts/|youtube\.com/live/)"
    r"[\w\-]{11}"
)

def is_yt(text: str) -> bool:
    return bool(YT_REGEX.search(text))

def extract_url(text: str) -> str:
    m = YT_REGEX.search(text)
    return m.group(0) if m else text.strip()

def h_size(b: int) -> str:
    for u in ("B","KB","MB","GB"):
        if b < 1024: return f"{b:.1f} {u}"
        b /= 1024
    return f"{b:.1f} TB"

def h_time(s: int) -> str:
    m, s = divmod(s, 60); h, m = divmod(m, 60)
    return f"{h}h {m}m {s}s" if h else f"{m}m {s}s"

def kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🎬 1080p MP4", callback_data="dl|1080|mp4"),
         InlineKeyboardButton("🎬  720p MP4", callback_data="dl|720|mp4")],
        [InlineKeyboardButton("🎬  480p MP4", callback_data="dl|480|mp4"),
         InlineKeyboardButton("🎬  360p MP4", callback_data="dl|360|mp4")],
        [InlineKeyboardButton("🎬  240p MP4", callback_data="dl|240|mp4"),
         InlineKeyboardButton("🎬 Best  MP4", callback_data="dl|best|mp4")],
        [InlineKeyboardButton("🎵 MP3 320k",  callback_data="dl|320|mp3"),
         InlineKeyboardButton("🎵 MP3 128k",  callback_data="dl|128|mp3")],
        [InlineKeyboardButton("🎵 M4A Best",  callback_data="dl|best|m4a"),
         InlineKeyboardButton("🎵 OPUS Best", callback_data="dl|best|opus")],
        [InlineKeyboardButton("❌ Cancel",    callback_data="dl|cancel|none")],
    ])

# ─── yt-dlp ───────────────────────────────────────────────────────────────────
async def fetch_info(url: str) -> dict:
    loop = asyncio.get_event_loop()
    def _r():
        with yt_dlp.YoutubeDL({"quiet":True,"no_warnings":True,"skip_download":True}) as y:
            return y.extract_info(url, download=False)
    return await loop.run_in_executor(None, _r)

async def do_download(url: str, quality: str, fmt: str, out: Path) -> Path:
    loop = asyncio.get_event_loop()
    def _r():
        opts: dict = {
            "outtmpl": str(out / "%(title).80s.%(ext)s"),
            "noplaylist": True, "quiet": True, "no_warnings": True,
            "http_headers": {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/124.0.0.0"},
        }
        if fmt == "mp3":
            opts["format"] = "bestaudio/best"
            opts["postprocessors"] = [{"key":"FFmpegExtractAudio","preferredcodec":"mp3",
                                        "preferredquality": quality if quality!="best" else "320"}]
        elif fmt == "m4a":
            opts["format"] = "bestaudio[ext=m4a]/bestaudio/best"
            opts["postprocessors"] = [{"key":"FFmpegExtractAudio","preferredcodec":"m4a"}]
        elif fmt == "opus":
            opts["format"] = "bestaudio/best"
            opts["postprocessors"] = [{"key":"FFmpegExtractAudio","preferredcodec":"opus"}]
        else:
            if quality == "best":
                opts["format"] = "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best"
            else:
                opts["format"] = (
                    f"bestvideo[height<={quality}][ext=mp4]+bestaudio[ext=m4a]/"
                    f"best[height<={quality}][ext=mp4]/best[height<={quality}]"
                )
            opts["merge_output_format"] = "mp4"

        with yt_dlp.YoutubeDL(opts) as y:
            y.download([url])

        files = list(out.iterdir())
        if not files: raise FileNotFoundError("yt-dlp produced no output.")
        return max(files, key=lambda f: f.stat().st_size)
    return await loop.run_in_executor(None, _r)

# ─── Upload progress ──────────────────────────────────────────────────────────
def make_progress(status_msg: Message, label: str):
    t0 = time.time(); last = [0.0]
    async def _cb(cur: int, tot: int):
        now = time.time()
        if now - last[0] < 4: return
        last[0] = now
        pct   = cur / tot * 100 if tot else 0
        speed = cur / (now - t0) if (now - t0) else 0
        eta   = (tot - cur) / speed if speed else 0
        bar   = "█" * int(pct/10) + "░" * (10-int(pct/10))
        try:
            await status_msg.edit_text(
                f"📤 **Uploading {label}**\n"
                f"`[{bar}]` {pct:.1f}%\n"
                f"{h_size(cur)} / {h_size(tot)}  •  {h_size(int(speed))}/s  •  ETA {h_time(int(eta))}",
                parse_mode=ParseMode.MARKDOWN,
            )
        except Exception: pass
    return _cb

# ─── Download progress (shown while yt-dlp runs) ──────────────────────────────
class DLProgress:
    def __init__(self, status_msg: Message, loop: asyncio.AbstractEventLoop):
        self.msg   = status_msg
        self.loop  = loop
        self.last  = 0.0

    def hook(self, d: dict):
        if d["status"] != "downloading": return
        now = time.time()
        if now - self.last < 3: return
        self.last = now
        downloaded = d.get("downloaded_bytes", 0)
        total      = d.get("total_bytes") or d.get("total_bytes_estimate", 0)
        speed      = d.get("speed", 0) or 0
        eta        = d.get("eta", 0) or 0
        pct        = downloaded / total * 100 if total else 0
        bar        = "█" * int(pct/10) + "░" * (10-int(pct/10))
        text = (
            f"⬇️ **Downloading…**\n"
            f"`[{bar}]` {pct:.1f}%\n"
            f"{h_size(downloaded)} / {h_size(total)}  •  {h_size(int(speed))}/s  •  ETA {h_time(int(eta))}"
        )
        asyncio.run_coroutine_threadsafe(
            self._edit(text), self.loop
        )

    async def _edit(self, text: str):
        try:
            await self.msg.edit_text(text, parse_mode=ParseMode.MARKDOWN)
        except Exception: pass

# ─── Commands ─────────────────────────────────────────────────────────────────
@app.on_message(filters.command("start") & filters.private)
async def cmd_start(_: Client, msg: Message):
    await msg.reply_text(
        "👋 **YouTube Downloader Bot**\n\n"
        "Send me any YouTube link — I'll show you format options.\n\n"
        "📦 Up to **2 GB** supported (full user-account upload)\n\n"
        "🎬 Video: 240p / 360p / 480p / 720p / 1080p / Best (MP4)\n"
        "🎵 Audio: MP3 320k / MP3 128k / M4A / OPUS\n\n"
        "/help  /about",
        parse_mode=ParseMode.MARKDOWN,
    )

@app.on_message(filters.command("help") & filters.private)
async def cmd_help(_: Client, msg: Message):
    await msg.reply_text(
        "📖 **Usage:**\n1️⃣ Paste YouTube URL\n2️⃣ Pick format\n"
        "3️⃣ Watch live progress bars\n4️⃣ Get your file 🎉",
        parse_mode=ParseMode.MARKDOWN,
    )

@app.on_message(filters.command("about") & filters.private)
async def cmd_about(_: Client, msg: Message):
    await msg.reply_text(
        "🤖 Built with **Pyrogram 2** + **yt-dlp**\nHosted on **AWS EC2** 24/7",
        parse_mode=ParseMode.MARKDOWN,
    )

# ─── URL received ─────────────────────────────────────────────────────────────
@app.on_message(filters.text & filters.private & ~filters.command(["start","help","about"]))
async def handle_url(_: Client, msg: Message):
    text = msg.text.strip()
    if not is_yt(text):
        await msg.reply_text("❌ Send a valid YouTube URL.", parse_mode=ParseMode.MARKDOWN)
        return
    url    = extract_url(text)
    status = await msg.reply_text("🔍 Fetching video info…")
    try:
        info = await fetch_info(url)
    except Exception as e:
        await status.edit_text(f"❌ Cannot fetch info:\n`{str(e)[:300]}`", parse_mode=ParseMode.MARKDOWN)
        return

    title    = info.get("title","Unknown")[:80]
    uploader = info.get("uploader","Unknown")
    dur      = info.get("duration",0)
    views    = info.get("view_count",0)
    thumb    = info.get("thumbnail")
    fmts     = info.get("formats",[])

    # Collect available heights for display
    heights = sorted({f.get("height") for f in fmts if f.get("height")}, reverse=True)
    avail   = "  ".join(f"`{h}p`" for h in heights[:8]) if heights else "N/A"

    caption = (
        f"🎬 **{title}**\n"
        f"👤 {uploader}  •  ⏱ {h_time(dur)}  •  👁 {views:,}\n"
        f"📐 Available: {avail}\n\n"
        "Choose format & quality:"
    )
    user_state[msg.from_user.id] = url
    await status.delete()
    try:
        await msg.reply_photo(thumb, caption=caption, parse_mode=ParseMode.MARKDOWN, reply_markup=kb())
    except Exception:
        await msg.reply_text(caption, parse_mode=ParseMode.MARKDOWN, reply_markup=kb())

# ─── Format chosen ────────────────────────────────────────────────────────────
@app.on_callback_query(filters.regex(r"^dl\|"))
async def handle_choice(client: Client, cb: CallbackQuery):
    await cb.answer()
    _, quality, fmt = cb.data.split("|")
    uid = cb.from_user.id

    edit = cb.message.edit_caption if cb.message.photo else cb.message.edit_text

    if fmt == "none":
        user_state.pop(uid, None)
        await edit("❌ Cancelled.")
        return

    url = user_state.get(uid)
    if not url:
        await cb.answer("⚠️ Session expired. Send the URL again.", show_alert=True)
        return

    label = {
        "mp4":  f"Video {quality}p MP4" if quality != "best" else "Video Best MP4",
        "mp3":  f"Audio MP3 {quality}kbps",
        "m4a":  "Audio M4A Best",
        "opus": "Audio OPUS Best",
    }.get(fmt, fmt)

    status = await cb.message.reply_text("⬇️ **Starting download…**", parse_mode=ParseMode.MARKDOWN)

    async with _semaphore:
        tmp = Path(tempfile.mkdtemp(dir=DOWNLOAD_DIR))
        try:
            # Inject live download progress hook
            loop = asyncio.get_event_loop()
            dlp  = DLProgress(status, loop)

            loop2 = asyncio.get_event_loop()
            def _run():
                opts: dict = {
                    "outtmpl": str(tmp / "%(title).80s.%(ext)s"),
                    "noplaylist": True, "quiet": True, "no_warnings": True,
                    "progress_hooks": [dlp.hook],
                    "http_headers": {"User-Agent": "Mozilla/5.0 Chrome/124.0.0.0"},
                }
                if fmt == "mp3":
                    opts["format"] = "bestaudio/best"
                    opts["postprocessors"] = [{"key":"FFmpegExtractAudio","preferredcodec":"mp3",
                                                "preferredquality": quality if quality!="best" else "320"}]
                elif fmt == "m4a":
                    opts["format"] = "bestaudio[ext=m4a]/bestaudio/best"
                    opts["postprocessors"] = [{"key":"FFmpegExtractAudio","preferredcodec":"m4a"}]
                elif fmt == "opus":
                    opts["format"] = "bestaudio/best"
                    opts["postprocessors"] = [{"key":"FFmpegExtractAudio","preferredcodec":"opus"}]
                else:
                    opts["format"] = (
                        "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best"
                        if quality == "best" else
                        f"bestvideo[height<={quality}][ext=mp4]+bestaudio[ext=m4a]/"
                        f"best[height<={quality}][ext=mp4]/best[height<={quality}]"
                    )
                    opts["merge_output_format"] = "mp4"

                with yt_dlp.YoutubeDL(opts) as y:
                    y.download([url])

                files = list(tmp.iterdir())
                if not files: raise FileNotFoundError("No output file produced.")
                return max(files, key=lambda f: f.stat().st_size)

            file_path = await loop2.run_in_executor(None, _run)
            size      = file_path.stat().st_size

            await status.edit_text(
                f"✅ Downloaded **{h_size(size)}** — uploading now…",
                parse_mode=ParseMode.MARKDOWN,
            )

            info  = await fetch_info(url)
            title = info.get("title","video")[:60]
            thumb = info.get("thumbnail")

            caption = (
                f"✅ **{title}**\n"
                f"📁 `{file_path.name}`\n"
                f"💾 {h_size(size)}  •  🎚 {label}"
            )

            progress_cb = make_progress(status, label)
            send_kw = dict(
                chat_id=cb.message.chat.id,
                caption=caption,
                parse_mode=ParseMode.MARKDOWN,
                progress=progress_cb,
            )

            if fmt in ("mp3","m4a","opus"):
                await client.send_audio(audio=str(file_path), title=title, thumb=thumb, **send_kw)
            else:
                await client.send_video(video=str(file_path), supports_streaming=True, thumb=thumb, **send_kw)

            await status.delete()
            await edit(f"✅ **{label}** sent below 🎉", parse_mode=ParseMode.MARKDOWN)

        except yt_dlp.utils.DownloadError as e:
            logger.error("DownloadError: %s", e)
            await status.edit_text(
                f"❌ **Download failed**\nMay be private / age-restricted / geo-blocked.\n\n`{str(e)[:250]}`",
                parse_mode=ParseMode.MARKDOWN,
            )
        except Exception as e:
            logger.exception("Unexpected error")
            await status.edit_text(f"❌ Error:\n`{str(e)[:300]}`", parse_mode=ParseMode.MARKDOWN)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
            user_state.pop(uid, None)

# ─── Entry point ──────────────────────────────────────────────────────────────
if __name__ == "__main__":
    logger.info("🤖 Bot starting (Pyrogram / user session)…")
    app.run()
