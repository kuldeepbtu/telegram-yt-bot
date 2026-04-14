"""
generate_session.py
───────────────────
Run this ONCE on your local machine to generate a Pyrogram session string.
Then paste the printed string into your .env as SESSION_STRING=...

Usage:
    pip install pyrogram TgCrypto
    python generate_session.py
"""

from pyrogram import Client
from pyrogram.enums import ParseMode

API_ID   = input("Enter your API_ID  (from my.telegram.org): ").strip()
API_HASH = input("Enter your API_HASH (from my.telegram.org): ").strip()

with Client(
    ":memory:",          # don't save a file
    api_id=int(API_ID),
    api_hash=API_HASH,
) as client:
    session_string = client.export_session_string()

print("\n" + "="*60)
print("✅ Your session string (copy the whole line below):")
print("="*60)
print(session_string)
print("="*60)
print("\nPaste it into your .env file as:\n  SESSION_STRING=<string above>")
print("Keep it SECRET — it is equivalent to your Telegram password.\n")
