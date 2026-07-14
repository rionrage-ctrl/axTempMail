"""
axTemp Mail Bot
A professional Telegram temp mail bot using Aiogram 3.x
"""

import asyncio
import json
import logging
import os
import re
import time
from datetime import datetime
from pathlib import Path

import aiohttp
from aiogram import Bot, Dispatcher, F
from aiogram.enums import ParseMode
from aiogram.filters import Command
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    KeyboardButton,
    Message,
    ReplyKeyboardMarkup,
)
from dotenv import load_dotenv

# ─── Config ───────────────────────────────────────────────────────────────────

load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")
DB_FILE = "users.json"
POLL_INTERVAL = 15  # seconds between inbox checks

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("axTempMail")

# ─── Database ─────────────────────────────────────────────────────────────────

def load_db() -> dict:
    if not Path(DB_FILE).exists():
        return {}
    try:
        with open(DB_FILE, "r") as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError):
        return {}


def save_db(data: dict) -> None:
    with open(DB_FILE, "w") as f:
        json.dump(data, f, indent=2)


def get_user(user_id: int) -> dict:
    db = load_db()
    uid = str(user_id)
    if uid not in db:
        db[uid] = {
            "user_id": user_id,
            "active_email": None,
            "active_token": None,
            "inbox": [],
            "total_received": 0,
            "created_time": datetime.utcnow().isoformat(),
        }
        save_db(db)
    return db[uid]


def update_user(user_id: int, data: dict) -> None:
    db = load_db()
    uid = str(user_id)
    db[uid] = data
    save_db(db)

# ─── Keyboard ─────────────────────────────────────────────────────────────────

MAIN_KB = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="✅ Get Mail")],
        [KeyboardButton(text="👤 Profile"), KeyboardButton(text="📥 Inbox")],
    ],
    resize_keyboard=True,
    persistent=True,
)

# ─── Temp Mail API (mail.tm) ──────────────────────────────────────────────────

MAILTM_BASE = "https://api.mail.tm"


async def create_temp_email() -> tuple[str, str] | tuple[None, None]:
    """Create a new temp email. Returns (address, token) or (None, None)."""
    try:
        async with aiohttp.ClientSession() as session:
            # Get available domains
            async with session.get(f"{MAILTM_BASE}/domains") as resp:
                if resp.status != 200:
                    return None, None
                domains_data = await resp.json()
                domains = domains_data.get("hydra:member", [])
                if not domains:
                    return None, None
                domain = domains[0]["domain"]

            # Generate random address
            import random, string
            username = "".join(random.choices(string.ascii_lowercase + string.digits, k=10))
            address = f"{username}@{domain}"
            password = "".join(random.choices(string.ascii_letters + string.digits, k=16))

            # Register account
            payload = {"address": address, "password": password}
            async with session.post(f"{MAILTM_BASE}/accounts", json=payload) as resp:
                if resp.status not in (200, 201):
                    return None, None

            # Get token
            async with session.post(f"{MAILTM_BASE}/token", json=payload) as resp:
                if resp.status != 200:
                    return None, None
                token_data = await resp.json()
                token = token_data.get("token")
                if not token:
                    return None, None

            return address, token

    except Exception as e:
        logger.error(f"create_temp_email error: {e}")
        return None, None


async def fetch_inbox(token: str) -> list[dict]:
    """Fetch all messages for a token. Returns list of message dicts."""
    try:
        headers = {"Authorization": f"Bearer {token}"}
        async with aiohttp.ClientSession() as session:
            async with session.get(f"{MAILTM_BASE}/messages", headers=headers) as resp:
                if resp.status != 200:
                    return []
                data = await resp.json()
                return data.get("hydra:member", [])
    except Exception as e:
        logger.error(f"fetch_inbox error: {e}")
        return []


async def fetch_message_detail(token: str, msg_id: str) -> dict | None:
    """Fetch full message body."""
    try:
        headers = {"Authorization": f"Bearer {token}"}
        async with aiohttp.ClientSession() as session:
            async with session.get(f"{MAILTM_BASE}/messages/{msg_id}", headers=headers) as resp:
                if resp.status != 200:
                    return None
                return await resp.json()
    except Exception as e:
        logger.error(f"fetch_message_detail error: {e}")
        return None

# ─── OTP Detector ─────────────────────────────────────────────────────────────

OTP_PATTERNS = [
    r"\b(\d{8})\b",
    r"\b(\d{6})\b",
    r"\b(\d{5})\b",
    r"\b(\d{4})\b",
]

def extract_otp(text: str) -> str | None:
    """Extract OTP/verification code from email text."""
    if not text:
        return None
    # Try longest code first (8 → 4)
    for pattern in OTP_PATTERNS:
        match = re.search(pattern, text)
        if match:
            candidate = match.group(1)
            # Avoid matching years, port numbers etc. by basic heuristics
            if candidate not in {"2024", "2025", "2026", "8080", "3000", "5000"}:
                return candidate
    return None

# ─── Message Formatters ───────────────────────────────────────────────────────

def fmt_welcome() -> str:
    return (
        "📧 *Welcome to axTemp Mail*\n\n"
        "Generate temporary email addresses instantly and receive\n"
        "verification emails directly inside Telegram\\.\n\n"
        "✨ *Fast & Secure*\n"
        "⚡ *Instant Email Generation*\n"
        "📥 *Built\\-in Inbox*\n"
        "🔒 *Privacy Friendly*\n\n"
        "━━━━━━━━━━━━━━\n"
        "Select an option below to get started\\."
    )


def fmt_new_email(address: str) -> str:
    escaped = address.replace("@", "\\@").replace(".", "\\.")
    return (
        "📧 *Your Temporary Email*\n\n"
        f"`{address}`\n\n"
        "Tap the address above to copy, then use it anywhere\\.\n\n"
        "━━━━━━━━━━━━━━\n"
        "📥 New emails will appear here *automatically*\\.\n"
        "🔄 Press *Get Mail* again to generate a fresh address\\."
    )


def fmt_new_message(subject: str, sender: str, body: str, otp: str | None) -> str:
    # Escape for MarkdownV2
    def esc(s: str) -> str:
        return re.sub(r"([_*\[\]()~`>#+\-=|{}.!\\])", r"\\\1", s or "")

    parts = ["📨 *New Email Received*\n"]

    if otp:
        parts.append(f"🔑 *Verification Code*\n`{otp}`\n\n━━━━━━━━━━━━━━\n")

    parts.append(f"📤 *From:* {esc(sender)}")
    parts.append(f"📌 *Subject:* {esc(subject)}\n")
    parts.append("📄 *Full Message*\n")

    # Truncate very long bodies
    body_display = body[:2000] + "…" if len(body) > 2000 else body
    parts.append(f"{esc(body_display)}")

    return "\n".join(parts)


def fmt_profile(user: dict) -> str:
    email = user.get("active_email") or "_None — press ✅ Get Mail_"
    created = user.get("created_time", "N/A")[:10]
    return (
        "👤 *User Profile*\n\n"
        f"🆔 *User ID:* `{user['user_id']}`\n"
        f"📧 *Active Mail:* `{email}`\n"
        f"📨 *Total Received:* `{user.get('total_received', 0)}`\n"
        f"📅 *Member Since:* `{created}`\n\n"
        "━━━━━━━━━━━━━━"
    )


def fmt_inbox(inbox: list[dict]) -> str:
    if not inbox:
        return (
            "📥 *Inbox*\n\n"
            "_No messages yet\\._\n\n"
            "━━━━━━━━━━━━━━\n"
            "Emails sent to your address will appear here automatically\\."
        )

    def esc(s: str) -> str:
        return re.sub(r"([_*\[\]()~`>#+\-=|{}.!\\])", r"\\\1", s or "")

    emojis = ["1️⃣","2️⃣","3️⃣","4️⃣","5️⃣","6️⃣","7️⃣","8️⃣","9️⃣","🔟"]
    lines = ["📥 *Inbox*\n"]
    for i, msg in enumerate(inbox[:10]):
        num = emojis[i] if i < len(emojis) else f"{i+1}\\."
        subject = esc(msg.get("subject") or "No Subject")
        date_raw = msg.get("received_at", "")[:16].replace("T", " ")
        date = esc(date_raw)
        otp = msg.get("otp")
        otp_tag = f" 🔑 `{otp}`" if otp else ""
        lines.append(f"{num} *{subject}*{otp_tag}\n🕒 {date}\n")

    lines.append("━━━━━━━━━━━━━━")
    lines.append(f"_Showing {min(len(inbox),10)} of {len(inbox)} messages\\._")
    return "\n".join(lines)

# ─── Handlers ─────────────────────────────────────────────────────────────────

async def cmd_start(message: Message) -> None:
    await message.answer(
        fmt_welcome(),
        parse_mode=ParseMode.MARKDOWN_V2,
        reply_markup=MAIN_KB,
    )
    logger.info(f"User {message.from_user.id} started the bot")


async def handle_get_mail(message: Message) -> None:
    user_id = message.from_user.id
    wait_msg = await message.answer("⏳ *Generating your email address…*", parse_mode=ParseMode.MARKDOWN_V2)

    address, token = await create_temp_email()
    if not address:
        await wait_msg.edit_text(
            "❌ *Failed to generate email\\.*\n\nThe mail service may be temporarily unavailable\\. Please try again in a moment\\.",
            parse_mode=ParseMode.MARKDOWN_V2,
        )
        return

    user = get_user(user_id)
    user["active_email"] = address
    user["active_token"] = token
    user["inbox"] = []
    update_user(user_id, user)

    await wait_msg.edit_text(
        fmt_new_email(address),
        parse_mode=ParseMode.MARKDOWN_V2,
    )
    logger.info(f"User {user_id} got new email: {address}")


async def handle_profile(message: Message) -> None:
    user = get_user(message.from_user.id)
    await message.answer(fmt_profile(user), parse_mode=ParseMode.MARKDOWN_V2)


async def handle_inbox(message: Message) -> None:
    user = get_user(message.from_user.id)
    if not user.get("active_email"):
        await message.answer(
            "📭 *No active email\\.*\n\nPress *✅ Get Mail* to generate your address first\\.",
            parse_mode=ParseMode.MARKDOWN_V2,
        )
        return

    inbox = user.get("inbox", [])
    # Show newest first
    inbox_sorted = sorted(inbox, key=lambda x: x.get("received_at", ""), reverse=True)
    await message.answer(fmt_inbox(inbox_sorted), parse_mode=ParseMode.MARKDOWN_V2)

# ─── Background Inbox Poller ──────────────────────────────────────────────────

async def poll_inboxes(bot: Bot) -> None:
    """Background task: poll every user's inbox and notify on new emails."""
    logger.info("Inbox poller started")
    # Track which message IDs we've already notified per user
    notified: dict[str, set] = {}

    while True:
        try:
            db = load_db()
            for uid, user in db.items():
                token = user.get("active_token")
                if not token:
                    continue

                user_id = user["user_id"]
                seen = notified.setdefault(uid, set())

                messages = await fetch_inbox(token)
                changed = False

                for msg_summary in messages:
                    msg_id = msg_summary.get("id", "")
                    if msg_id in seen:
                        continue
                    seen.add(msg_id)

                    # Fetch full body
                    detail = await fetch_message_detail(token, msg_id)
                    if not detail:
                        continue

                    subject = detail.get("subject") or "No Subject"
                    sender_info = detail.get("from", {})
                    sender = sender_info.get("address", "Unknown")
                    body_parts = detail.get("text", "") or ""
                    if not body_parts and detail.get("html"):
                        # Strip HTML tags for plain text
                        body_parts = re.sub(r"<[^>]+>", "", detail["html"][0] if isinstance(detail["html"], list) else detail["html"])

                    otp = extract_otp(body_parts)
                    received_at = detail.get("createdAt", datetime.utcnow().isoformat())

                    # Save to user inbox
                    inbox_entry = {
                        "id": msg_id,
                        "subject": subject,
                        "sender": sender,
                        "body": body_parts,
                        "otp": otp,
                        "received_at": received_at,
                    }
                    user.setdefault("inbox", []).append(inbox_entry)
                    user["total_received"] = user.get("total_received", 0) + 1
                    changed = True

                    # Notify user
                    try:
                        await bot.send_message(
                            chat_id=user_id,
                            text=fmt_new_message(subject, sender, body_parts, otp),
                            parse_mode=ParseMode.MARKDOWN_V2,
                        )
                        logger.info(f"Notified user {user_id} about email: {subject}")
                    except Exception as notify_err:
                        logger.warning(f"Could not notify user {user_id}: {notify_err}")

                if changed:
                    update_user(user_id, user)

        except Exception as e:
            logger.error(f"Poller error: {e}")

        await asyncio.sleep(POLL_INTERVAL)

# ─── Main ─────────────────────────────────────────────────────────────────────

async def main() -> None:
    if not BOT_TOKEN:
        raise ValueError("BOT_TOKEN is not set in environment/.env")

    bot = Bot(token=BOT_TOKEN)
    dp = Dispatcher(storage=MemoryStorage())

    # Register handlers
    dp.message.register(cmd_start, Command("start"))
    dp.message.register(handle_get_mail, F.text == "✅ Get Mail")
    dp.message.register(handle_profile, F.text == "👤 Profile")
    dp.message.register(handle_inbox, F.text == "📥 Inbox")

    # Start background poller
    asyncio.create_task(poll_inboxes(bot))

    logger.info("axTemp Mail bot is starting…")
    await dp.start_polling(bot, allowed_updates=["message"])


if __name__ == "__main__":
    asyncio.run(main())
