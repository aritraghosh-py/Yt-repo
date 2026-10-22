import asyncio
import re
import random
import string
import sqlite3
import time
import os
import io
import math
from flask import Flask, send_file
import threading
from collections import defaultdict, deque
from datetime import datetime, timezone
from telegram.request import HTTPXRequest
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo
from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
    CallbackQueryHandler,
    InlineQueryHandler,
)
from telegram import InlineQueryResultCachedPhoto, InlineQueryResultArticle, InputTextMessageContent

try:
    from PIL import Image, ImageDraw, ImageFont
    PIL_AVAILABLE = True
except ImportError:
    PIL_AVAILABLE = False
    print("[WARN] Pillow not installed. Draft result images will be text-only.")

# ================= CONFIG =================

BOT_TOKEN = "8660061924:AAFB5isD7Gzpfigv2IQJ9G0HmQh8R7GUGCA"
ADMIN_ID = 5079126270  # Owner Telegram ID
OWNER_ID  = 5079126270  # Same as admin — owner has all permissions
CO_OWNER_ID = 1962197154  # ×͜× ł₮'₴ M̷Ɇ̷ — co-owner, full admin access
LOG_GROUP_ID = -1003746345834  # Admin log group
TRADE_LOG_CHANNEL  = "https://t.me/+IU-AtSXm6qwzZTNl"   # Card trade logs channel (invite link)
CATCH_LOG_CHANNEL = "https://t.me/+y8St-coFIvBiM2Nl"   # Catch logs channel (invite link)

TRADE_LOG_CHAT_ID  = -1003867004471   # Card trade log channel numeric ID
CATCH_LOG_CHAT_ID = -1003813168203   # Catch log channel numeric ID

# ── AirDrop / AdsGram config ────────────────────────────────────────────────
ADSGRAM_TOKEN       = "03a5ec5d8aca4a5daf77d8a9e823319e"
AIRDROP_ANNOUNCE_ID = LOG_GROUP_ID   # Group where global Spring pulls are announced
                                     # Change to your public group ID if different
# ⚠️  Set this to your server URL (e.g. https://yourdomain.com or ngrok URL during dev)
WEBAPP_URL          = "https://worker-production-1c44.up.railway.app/airdrop"   # <-- UPDATE THIS

SPAWN_MIN_MESSAGES = 30
SPAWN_MAX_MESSAGES = 40
ENCOUNTER_DURATION = 120

DAILY_COOLDOWN = 86400
PACK_LIMIT_PER_DAY = 5

SPAM_LIMIT = 12       # Triggers between 10-15 messages
SPAM_WINDOW = 10
BLOCK_DURATION = 600

GUESS_COOLDOWN = 2
app = Flask(__name__)

@app.route("/")
def home():
    return "Naruto TCG Bot Server Running 🌀"

@app.route("/airdrop")
def airdrop():
    return send_file("webapp/airdrop.html")

# ================= DRAFT EVENT CONFIG =================
# Draft event is fully DB-controlled via /start_draft and /stop_draft
# Rarities are configurable per event start command
DRAFT_EVENT_NAME = "🌸 Spring Blossom Edition"

# Default draft rarities and weights (out of 100) — can be overridden per event
DRAFT_RARITY_WEIGHTS_DEFAULT = {
    "Rare":       57,
    "Epic":       35,
    "Legendary":   5,
    "Mythic":      2,
    "Spring":      1,
}

# Live weights — loaded from DB at runtime
DRAFT_RARITY_WEIGHTS = dict(DRAFT_RARITY_WEIGHTS_DEFAULT)

# Referral milestone rewards: {referral_count: (epic_cards, legendary_cards, mythic_spring_cards, draft_vouchers)}
REFERRAL_REWARDS = {
    1:  (1, 0, 0, 5),
    2:  (2, 0, 0, 5),
    3:  (3, 0, 0, 5),
    4:  (3, 0, 0, 5),
    5:  (0, 1, 0, 10),
    6:  (3, 0, 0, 5),
    7:  (3, 0, 0, 5),
    8:  (3, 0, 0, 5),
    9:  (3, 0, 0, 5),
    10: (0, 0, 1, 20),  # 1 mythic/spring card
}

RARITY_WEIGHTS = {
    "Common": 60,
    "Rare": 25,
    "Epic": 10,
    "Legendary": 4,
    "Mythic": 1,
    "Spring": 0,      # Special — milestone only, never in random pool
    "Crossverse": 0,  # Special — milestone only, never in random pool
    "Animation": 0,        # Special — video animation card, /add only, never spawns
    "LeaderBoard": 0,      # Special — leaderboard card, /add only, never spawns
}
RARITY_EMOJI = {
    "Common": "🍥",
    "Rare": "🌀",
    "Epic": "⚡",
    "Legendary": "👑",
    "Mythic": "🦊",
    "Spring": "🌸",
    "Crossverse": "🧬",
    "Animation": "💿",
    "LeaderBoard": "🎖️",
}

# Milestone guaranteed spawns — repeat every 2000 messages
MILESTONE_CYCLE = 2000

# Tracks whether milestone already fired this cycle per chat
# Format: {chat_id: set_of_milestones_fired_this_cycle}
milestone_fired: dict = {}

def get_milestone_rarity(chat_id: int, total: int):
    """
    Check if total messages hits a guaranteed milestone spawn.
    Returns rarity string if milestone due, else None.
    Milestones:
      500–600   → Legendary (fires once in this range per cycle)
      1000      → Spring    (fires exactly at 1000)
      1500–1999 → Mythic    (fires once in this range per cycle)
      2000      → Crossverse (fires exactly at 2000, resets cycle)
    """
    global milestone_fired
    cycle_pos = total % MILESTONE_CYCLE
    if cycle_pos == 0:
        cycle_pos = MILESTONE_CYCLE

    if chat_id not in milestone_fired:
        milestone_fired[chat_id] = set()

    fired = milestone_fired[chat_id]

    # Reset fired set at start of new cycle
    if cycle_pos < 50 and "cycle_reset" not in fired:
        milestone_fired[chat_id] = {"cycle_reset"}
        fired = milestone_fired[chat_id]

    # Crossverse at exactly 2000 (end of cycle)
    if cycle_pos == MILESTONE_CYCLE and "crossverse" not in fired:
        fired.add("crossverse")
        return "Crossverse"

    # Spring at exactly 1000
    if cycle_pos == 1000 and "spring" not in fired:
        fired.add("spring")
        return "Spring"

    # Legendary between 500–600 (fires once per cycle at a random point)
    if 500 <= cycle_pos <= 600 and "legendary" not in fired:
        # 10% chance each message in this window = guaranteed fires within window
        if random.random() < 0.10:
            fired.add("legendary")
            return "Legendary"
        # Force fire at 600 if still not fired
        if cycle_pos == 600:
            fired.add("legendary")
            return "Legendary"

    # Mythic between 1500–1999 (fires once per cycle at a random point)
    if 1500 <= cycle_pos <= 1999 and "mythic" not in fired:
        if random.random() < 0.05:
            fired.add("mythic")
            return "Mythic"
        if cycle_pos == 1999:
            fired.add("mythic")
            return "Mythic"

    return None

user_pages = {}
fav_confirm = {}


def normalize(text: str):
    """Strip everything except letters/numbers/spaces. Used for search/matching."""
    text = text.lower()
    text = re.sub(r"[^a-z0-9 ]", "", text)
    text = " ".join(text.split())
    return text


def make_display_name(text: str):
    """Keep emojis and original casing for display."""
    text = re.sub(r"[\x00-\x1f\x7f]", "", text)
    text = " ".join(text.split())
    return text


def get_random_rarity():
    # Disc and Dragon are admin-only, never spawned
    spawn_weights = {k: v for k, v in RARITY_WEIGHTS.items() if k not in ("Animation", "LeaderBoard")}
    rarities = list(spawn_weights.keys())
    weights = list(spawn_weights.values())
    return random.choices(rarities, weights=weights, k=1)[0]


# ================= DATABASE =================

conn = sqlite3.connect("/data/cards.db", check_same_thread=False)
cursor = conn.cursor()

cursor.execute("""
    CREATE TABLE IF NOT EXISTS users (
        user_id INTEGER PRIMARY KEY,
        last_daily INTEGER,
        pack_today INTEGER DEFAULT 0,
        last_pack_day INTEGER DEFAULT 0
    )
""")

cursor.execute("""
CREATE TABLE IF NOT EXISTS cards (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT,
    rarity TEXT,
    file_id TEXT
)
""")

cursor.execute("""
    CREATE TABLE IF NOT EXISTS user_cards (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        card_name TEXT,
        rarity TEXT,
        file_id TEXT
    )
""")

# ── Add card_id column to user_cards if not already present ──────────────────
# card_id = the master cards.id so every user sees the same card number
try:
    cursor.execute("ALTER TABLE user_cards ADD COLUMN card_id INTEGER DEFAULT NULL")
    conn.commit()
    print("[DB] Added card_id column to user_cards")
except Exception:
    pass  # Column already exists

# ── Backfill card_id for existing rows that don't have it yet ─────────────────
try:
    cursor.execute(
        "SELECT id, card_name, rarity FROM user_cards WHERE card_id IS NULL"
    )
    missing = cursor.fetchall()
    if missing:
        for row_id, card_name, rarity in missing:
            cursor.execute(
                "SELECT id FROM cards WHERE name=? AND rarity=? LIMIT 1",
                (card_name, rarity)
            )
            master = cursor.fetchone()
            if master:
                cursor.execute(
                    "UPDATE user_cards SET card_id=? WHERE id=?",
                    (master[0], row_id)
                )
        conn.commit()
        print(f"[DB] Backfilled card_id for {len(missing)} user_cards rows")
except Exception as e:
    print(f"[DB] Backfill error: {e}")

try:
    cursor.execute(
        "ALTER TABLE user_cards ADD COLUMN is_favorite INTEGER DEFAULT 0"
    )
except:
    pass

# ── Add file_type column to cards and user_cards (for video/animation support) ─
try:
    cursor.execute("ALTER TABLE cards ADD COLUMN file_type TEXT DEFAULT 'photo'")
    conn.commit()
    print("[DB] Added file_type column to cards")
except Exception:
    pass

try:
    cursor.execute("ALTER TABLE user_cards ADD COLUMN file_type TEXT DEFAULT 'photo'")
    conn.commit()
    print("[DB] Added file_type column to user_cards")
except Exception:
    pass

# Backfill: Animation rarity cards are always video/animation
try:
    cursor.execute("UPDATE cards SET file_type='video' WHERE rarity='Animation' AND (file_type IS NULL OR file_type='photo')")
    cursor.execute("UPDATE user_cards SET file_type='video' WHERE rarity='Animation' AND (file_type IS NULL OR file_type='photo')")
    conn.commit()
except Exception:
    pass

# ── Shinobi Coins system tables ───────────────────────────────────────────────
cursor.execute("""
CREATE TABLE IF NOT EXISTS shinobi_coins (
    user_id       INTEGER PRIMARY KEY,
    coins         INTEGER DEFAULT 0,
    last_daily    INTEGER DEFAULT 0,
    last_weekly   INTEGER DEFAULT 0,
    last_monthly  INTEGER DEFAULT 0
)
""")

cursor.execute("""
CREATE TABLE IF NOT EXISTS coin_redeem_codes (
    code          TEXT PRIMARY KEY,
    coins         INTEGER NOT NULL,
    created_by    INTEGER,
    created_at    INTEGER,
    used_by       INTEGER DEFAULT NULL,
    used_at       INTEGER DEFAULT NULL,
    max_uses      INTEGER DEFAULT 1,
    uses_count    INTEGER DEFAULT 0
)
""")

# Migration: add max_uses/uses_count if upgrading from old schema
try:
    cursor.execute("ALTER TABLE coin_redeem_codes ADD COLUMN max_uses INTEGER DEFAULT 1")
    conn.commit()
except Exception:
    pass
try:
    cursor.execute("ALTER TABLE coin_redeem_codes ADD COLUMN uses_count INTEGER DEFAULT 0")
    conn.commit()
except Exception:
    pass
conn.commit()

cursor.execute("""
    CREATE TABLE IF NOT EXISTS trades (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        from_user INTEGER,
        to_user INTEGER,
        card_id INTEGER,
        status TEXT
    )
""")

cursor.execute("""
CREATE TABLE IF NOT EXISTS coin_redeem_user_log (
    code        TEXT NOT NULL,
    user_id     INTEGER NOT NULL,
    redeemed_at INTEGER,
    PRIMARY KEY (code, user_id)
)
""")
conn.commit()

cursor.execute("""
CREATE TABLE IF NOT EXISTS redeem_codes (
    code TEXT PRIMARY KEY,
    card_id INTEGER,
    uses_left INTEGER
)
""")

cursor.execute("""
CREATE TABLE IF NOT EXISTS admins (
    user_id INTEGER PRIMARY KEY,
    added_by INTEGER,
    added_at INTEGER
)
""")

cursor.execute("""
CREATE TABLE IF NOT EXISTS banned_users (
    user_id INTEGER PRIMARY KEY,
    banned_by INTEGER,
    banned_at INTEGER,
    unban_at INTEGER,
    reason TEXT
)
""")

cursor.execute("""
CREATE TABLE IF NOT EXISTS conv_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER,
    username TEXT,
    first_name TEXT,
    message_text TEXT,
    timestamp INTEGER
)
""")

# ── All users who have ever started the bot (for broadcast) ─────────────────
cursor.execute("""
CREATE TABLE IF NOT EXISTS bot_users (
    user_id INTEGER PRIMARY KEY,
    first_name TEXT,
    username TEXT,
    joined_at INTEGER
)
""")

# ── Deleted cards backup (for /rec_acc) ──────────────────────────────────────
cursor.execute("""
CREATE TABLE IF NOT EXISTS deleted_cards_backup (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER,
    card_name TEXT,
    rarity TEXT,
    file_id TEXT,
    deleted_at INTEGER
)
""")

# ── Deleted vouchers backup (for /rec_acc) ────────────────────────────────────
cursor.execute("""
CREATE TABLE IF NOT EXISTS deleted_vouchers_backup (
    user_id INTEGER PRIMARY KEY,
    vouchers INTEGER DEFAULT 0,
    total_spins INTEGER DEFAULT 0,
    spins_since_legendary INTEGER DEFAULT 0,
    spins_since_spring INTEGER DEFAULT 0,
    deleted_at INTEGER
)
""")

# ── Draft Vouchers ──────────────────────────────────────────────────────────
cursor.execute("""
CREATE TABLE IF NOT EXISTS draft_vouchers (
    user_id INTEGER PRIMARY KEY,
    vouchers INTEGER DEFAULT 0,
    total_spins INTEGER DEFAULT 0,
    spins_since_legendary INTEGER DEFAULT 0,
    spins_since_spring INTEGER DEFAULT 0
)
""")

# ── Draft Generated Codes (admin generate) ─────────────────────────────────
cursor.execute("""
CREATE TABLE IF NOT EXISTS draft_codes (
    code TEXT PRIMARY KEY,
    vouchers INTEGER,
    uses_left INTEGER,
    created_by INTEGER,
    created_at INTEGER
)
""")

# ── Track which users claimed which draft codes ──────────────────────────────
cursor.execute("""
CREATE TABLE IF NOT EXISTS draft_code_claims (
    code TEXT,
    user_id INTEGER,
    claimed_at INTEGER,
    PRIMARY KEY (code, user_id)
)
""")

# ── Weekly/Monthly Claim Tracking ──────────────────────────────────────────
cursor.execute("""
CREATE TABLE IF NOT EXISTS weekly_claims (
    user_id INTEGER PRIMARY KEY,
    last_weekly INTEGER DEFAULT 0
)
""")

cursor.execute("""
CREATE TABLE IF NOT EXISTS monthly_claims (
    user_id INTEGER PRIMARY KEY,
    last_monthly INTEGER DEFAULT 0
)
""")

# ── Referral System ─────────────────────────────────────────────────────────
cursor.execute("""
CREATE TABLE IF NOT EXISTS referrals (
    user_id INTEGER PRIMARY KEY,
    referral_code TEXT UNIQUE,
    referred_by INTEGER DEFAULT NULL,
    total_referrals INTEGER DEFAULT 0
)
""")

cursor.execute("""
CREATE TABLE IF NOT EXISTS referral_uses (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    referrer_id INTEGER,
    referred_id INTEGER,
    timestamp INTEGER
)
""")

# ── Bot Settings (key-value) ─────────────────────────────────────────────────
cursor.execute("""
CREATE TABLE IF NOT EXISTS bot_settings (
    key TEXT PRIMARY KEY,
    value TEXT
)
""")

# ── Shop Daily Deals — one row per user per reset cycle ───────────────────────
cursor.execute("""
CREATE TABLE IF NOT EXISTS shop_daily_deals (
    user_id     INTEGER PRIMARY KEY,
    reset_epoch INTEGER,
    common_id   INTEGER,
    rare_id     INTEGER,
    epic_id     INTEGER,
    legendary_id INTEGER,
    mythic_id   INTEGER,
    spring_id   INTEGER
)
""")

# ── Shinobi Mode — per-user favourite rarity display preference ──────────────
cursor.execute("""
CREATE TABLE IF NOT EXISTS shinobi_mode (
    user_id INTEGER PRIMARY KEY,
    fav_rarity TEXT DEFAULT NULL
)
""")

conn.commit()

# Fix any NULL id cards left from old INSERT OR REPLACE bug
try:
    cursor.execute("SELECT rowid, name, rarity, file_id FROM cards WHERE id IS NULL")
    broken = cursor.fetchall()
    for row in broken:
        rowid, name, rarity, file_id = row
        cursor.execute("DELETE FROM cards WHERE rowid=?", (rowid,))
        cursor.execute("INSERT INTO cards (name, rarity, file_id) VALUES (?, ?, ?)", (name, rarity, file_id))
        print(f"[DB FIX] Repaired NULL id card: '{name}' → #{cursor.lastrowid}")
    if broken:
        conn.commit()
        print(f"[DB FIX] Fixed {len(broken)} NULL id card(s) on startup.")
except Exception as e:
    print(f"[DB FIX] Error: {e}")

# ================= MEMORY =================

message_counter = defaultdict(int)       # resets after each spawn
total_message_counter = defaultdict(int)  # never resets — used for milestones
next_spawn = defaultdict(lambda: random.randint(SPAWN_MIN_MESSAGES, SPAWN_MAX_MESSAGES))
current_card: dict = {}
spawn_time: dict = {}

user_messages: dict = defaultdict(lambda: deque())
blocked_users: dict = {}
last_guess_time: dict = {}

# Per-chat custom spawn range set by /changetime
chat_spawn_settings: dict = {}

# ================= ADMIN & BAN HELPERS =================

def is_admin(user_id: int) -> bool:
    """Check if user is owner, co-owner, or added admin."""
    if user_id in (OWNER_ID, CO_OWNER_ID):
        return True
    cursor.execute("SELECT user_id FROM admins WHERE user_id=?", (user_id,))
    return cursor.fetchone() is not None

def has_started_bot(user_id: int) -> bool:
    """Check if user has previously started the bot in DM (registered in bot_users)."""
    cursor.execute("SELECT user_id FROM bot_users WHERE user_id=?", (user_id,))
    return cursor.fetchone() is not None

NOT_STARTED_MSG = (
    "🍃 *Hey, Shinobi!*\n\n"
    "⚠️ *You haven't activated the bot yet!*\n\n"
    "━━━━━━━━━━━━━━━━━━━━━━\n"
    "🚀 To use all bot features you must\n"
    "*start the bot in DM first!*\n\n"
    "👇 *Click below and press START:*\n"
    "👉 [Open Bot in DM](https://t.me/Naruto_tcg_bot)\n\n"
    "━━━━━━━━━━━━━━━━━━━━━━\n"
    "🎴 *What awaits you:*\n"
    "🥷 Catch legendary shinobis\n"
    "🎁 Daily cards & Draft Vouchers\n"
    "🏆 Climb the leaderboard\n"
    "🌸 Spring Blossom Draft Event!\n\n"
    "⚔️ *Your shinobi journey starts with one tap!*"
)

def is_banned(user_id: int) -> bool:
    """Check if user is currently banned. Auto-unban tban if expired."""
    now = int(time.time())
    cursor.execute("SELECT unban_at FROM banned_users WHERE user_id=?", (user_id,))
    row = cursor.fetchone()
    if not row:
        return False
    # unban_at = None means permanent ban (never expires)
    if row[0] is not None and now >= row[0]:
        cursor.execute("DELETE FROM banned_users WHERE user_id=?", (user_id,))
        conn.commit()
        return False
    return True

def get_ban_message(user_id: int) -> str:
    """Return the correct ban message based on ban type."""
    now = int(time.time())
    cursor.execute("SELECT unban_at FROM banned_users WHERE user_id=?", (user_id,))
    row = cursor.fetchone()
    if not row:
        return "You are not banned."
    unban_at = row[0]
    if unban_at is None:
        return (
            "🚫 *You are permanently banned!*\n\n"
            "⛔ You have received a *lifetime ban* from this bot.\n"
            "❌ You cannot use any features of this bot.\n\n"
            "🔒 This ban does not expire."
        )
    remaining = max(0, unban_at - now)
    days = remaining // 86400
    hours = (remaining % 86400) // 3600
    mins = (remaining % 3600) // 60
    return (
        f"🚫 *You are temporarily banned!*\n\n"
        f"⛔ You cannot use any bot features.\n"
        f"⏳ Ban expires in: *{days}d {hours}h {mins}m*\n\n"
        f"🍃 Wait out your sentence, shinobi."
    )

async def log_to_group(bot, text: str, photo_file_id: str = None):
    """Send a log message to the admin log group."""
    try:
        if photo_file_id:
            await bot.send_photo(
                chat_id=LOG_GROUP_ID,
                photo=photo_file_id,
                caption=text,
                parse_mode="Markdown"
            )
        else:
            await bot.send_message(
                chat_id=LOG_GROUP_ID,
                text=text,
                parse_mode="Markdown"
            )
    except Exception as e:
        print(f"[LOG] Failed to send to log group: {e}")

async def log_trade(bot, text: str, photo_file_id: str = None):
    """Send a log message to the dedicated trade log channel."""
    if not TRADE_LOG_CHAT_ID:
        return
    try:
        if photo_file_id:
            await bot.send_photo(
                chat_id=TRADE_LOG_CHAT_ID,
                photo=photo_file_id,
                caption=text,
                parse_mode="Markdown"
            )
        else:
            await bot.send_message(
                chat_id=TRADE_LOG_CHAT_ID,
                text=text,
                parse_mode="Markdown"
            )
    except Exception as e:
        print(f"[TRADE LOG] Failed: {e}")

async def log_catch(bot, text: str, photo_file_id: str = None):
    """Send a log message to the dedicated catch log channel."""
    if not CATCH_LOG_CHAT_ID:
        return  # Not configured yet — set CATCH_LOG_CHAT_ID to enable
    try:
        if photo_file_id:
            await bot.send_photo(
                chat_id=CATCH_LOG_CHAT_ID,
                photo=photo_file_id,
                caption=text,
                parse_mode="Markdown"
            )
        else:
            await bot.send_message(
                chat_id=CATCH_LOG_CHAT_ID,
                text=text,
                parse_mode="Markdown"
            )
    except Exception as e:
        print(f"[CATCH LOG] Failed: {e}")

# ================= DRAFT HELPERS =================

def get_draft_days_remaining():
    """Return days remaining in the draft event from DB, or 0 if ended/not set."""
    cursor.execute("SELECT value FROM bot_settings WHERE key='draft_end'")
    row = cursor.fetchone()
    if not row:
        return 0
    try:
        end_ts = int(row[0])
        now = int(time.time())
        remaining_secs = max(0, end_ts - now)
        return remaining_secs // 86400
    except:
        return 0

def is_draft_event_active():
    """Check if draft event is active based on DB settings."""
    cursor.execute("SELECT value FROM bot_settings WHERE key='draft_active'")
    row = cursor.fetchone()
    if not row or row[0] != "1":
        return False
    cursor.execute("SELECT value FROM bot_settings WHERE key='draft_end'")
    end_row = cursor.fetchone()
    if not end_row:
        return False
    try:
        end_ts = int(end_row[0])
        now = int(time.time())
        return now <= end_ts
    except:
        return False

def load_draft_rarity_weights():
    """Load draft rarity weights from DB into global DRAFT_RARITY_WEIGHTS."""
    global DRAFT_RARITY_WEIGHTS
    cursor.execute("SELECT value FROM bot_settings WHERE key='draft_rarities'")
    row = cursor.fetchone()
    if row:
        try:
            import json
            weights = json.loads(row[0])
            DRAFT_RARITY_WEIGHTS = weights
            return
        except:
            pass
    DRAFT_RARITY_WEIGHTS = dict(DRAFT_RARITY_WEIGHTS_DEFAULT)

def get_draft_banner_file_id():
    """Get draft banner file_id from DB settings."""
    cursor.execute("SELECT value FROM bot_settings WHERE key='draft_banner'")
    row = cursor.fetchone()
    return row[0] if row else None

def get_user_vouchers(user_id: int) -> int:
    cursor.execute("SELECT vouchers FROM draft_vouchers WHERE user_id=?", (user_id,))
    row = cursor.fetchone()
    return row[0] if row else 0

def add_vouchers(user_id: int, amount: int):
    """Add vouchers to user. Uses separate INSERT + UPDATE to avoid upsert bugs."""
    # Ensure row exists first
    cursor.execute("""
        INSERT OR IGNORE INTO draft_vouchers 
        (user_id, vouchers, total_spins, spins_since_legendary, spins_since_spring)
        VALUES (?, 0, 0, 0, 0)
    """, (user_id,))
    # Now add the amount
    cursor.execute(
        "UPDATE draft_vouchers SET vouchers = vouchers + ? WHERE user_id = ?",
        (amount, user_id)
    )
    conn.commit()

def deduct_vouchers(user_id: int, amount: int) -> bool:
    current = get_user_vouchers(user_id)
    if current < amount:
        return False
    cursor.execute("UPDATE draft_vouchers SET vouchers = vouchers - ? WHERE user_id=?", (amount, user_id))
    conn.commit()
    return True

# ── Shinobi Coins helpers ─────────────────────────────────────────────────────

# Coins awarded per rarity on catch/daily/pack
RARITY_COINS = {
    "Common":      50,
    "Rare":       100,
    "Epic":       150,
    "Legendary":  200,
    "Mythic":     400,
    "Spring":     500,
    "Crossverse": 300,
    "Animation":  600,
    "LeaderBoard": 0,   # special — no auto-coin reward
}

COIN_DAILY_AMOUNT   =  300
COIN_WEEKLY_AMOUNT  = 1000
COIN_MONTHLY_AMOUNT = 2000

# ── Shop Price Config ─────────────────────────────────────────────────────────
SHOP_PRICES = {
    "Common":     500,
    "Rare":       750,
    "Epic":       1000,
    "Legendary":  2000,
    "Mythic":     5000,
    "Spring":     7000,
}

# Rarity colour circle emojis for shop display (matches screenshot style)
SHOP_RARITY_CIRCLES = {
    "Common":    "🔵",
    "Rare":      "🔴",
    "Epic":      "🟠",
    "Legendary": "🟡",
    "Mythic":    "🔮",
    "Spring":    "🎐",
}

# Reset timings match the 5:30 AM IST reset used by /daily and /pack
IST_OFFSET_SEC = 5 * 3600 + 30 * 60   # 19800

def _coin_reset_epoch(now: int) -> int:
    """Return the epoch of the most recent 5:30 AM IST reset."""
    now_ist = now + IST_OFFSET_SEC
    secs_since_midnight = now_ist % 86400
    RESET_S = 5 * 3600 + 30 * 60
    if secs_since_midnight >= RESET_S:
        return now - (secs_since_midnight - RESET_S)
    return now - (secs_since_midnight + 86400 - RESET_S)


def _ensure_coin_row(user_id: int):
    cursor.execute(
        "INSERT OR IGNORE INTO shinobi_coins (user_id, coins, last_daily, last_weekly, last_monthly)"
        " VALUES (?,0,0,0,0)",
        (user_id,)
    )


def get_coins(user_id: int) -> int:
    _ensure_coin_row(user_id)
    cursor.execute("SELECT coins FROM shinobi_coins WHERE user_id=?", (user_id,))
    row = cursor.fetchone()
    return row[0] if row else 0


def add_coins(user_id: int, amount: int):
    _ensure_coin_row(user_id)
    cursor.execute(
        "UPDATE shinobi_coins SET coins = coins + ? WHERE user_id=?",
        (amount, user_id)
    )
    conn.commit()


def deduct_coins(user_id: int, amount: int) -> bool:
    current = get_coins(user_id)
    if current < amount:
        return False
    cursor.execute(
        "UPDATE shinobi_coins SET coins = coins - ? WHERE user_id=?",
        (amount, user_id)
    )
    conn.commit()
    return True


# ── Shop helpers ──────────────────────────────────────────────────────────────

def _get_reset_epoch_now() -> int:
    """Current 5:30 AM IST reset epoch."""
    return _coin_reset_epoch(int(time.time()))

def _time_until_next_reset() -> str:
    """Human-readable time until next 5:30 AM IST reset."""
    now = int(time.time())
    current_reset = _coin_reset_epoch(now)
    next_reset = current_reset + 86400
    secs = next_reset - now
    hours = secs // 3600
    mins = (secs % 3600) // 60
    return f"{hours}h {mins}m"

def _get_or_create_deals(user_id: int) -> dict:
    """
    Return the user's daily deals for today's reset cycle.
    If they don't have deals yet (or deals are stale), generate fresh random ones.
    Returns: {rarity: card_row} dict
    card_row = (id, name, rarity, file_id, file_type)
    """
    now = int(time.time())
    reset_epoch = _coin_reset_epoch(now)

    cursor.execute(
        "SELECT reset_epoch, common_id, rare_id, epic_id, legendary_id, mythic_id, spring_id "
        "FROM shop_daily_deals WHERE user_id=?",
        (user_id,)
    )
    row = cursor.fetchone()

    # Valid if same reset cycle
    if row and row[0] == reset_epoch:
        deal_ids = {
            "Common":    row[1],
            "Rare":      row[2],
            "Epic":      row[3],
            "Legendary": row[4],
            "Mythic":    row[5],
            "Spring":    row[6],
        }
        # Load cards
        deals = {}
        for rarity, cid in deal_ids.items():
            if cid:
                cursor.execute(
                    "SELECT id, name, rarity, file_id, COALESCE(file_type,'photo') FROM cards WHERE id=?",
                    (cid,)
                )
                card = cursor.fetchone()
                if card:
                    deals[rarity] = card
        if deals:
            return deals

    # Generate new deals — pick one random card per rarity
    deals = {}
    new_ids = {}
    for rarity in ("Common", "Rare", "Epic", "Legendary", "Mythic", "Spring"):
        cursor.execute(
            "SELECT id, name, rarity, file_id, COALESCE(file_type,'photo') FROM cards "
            "WHERE rarity=? ORDER BY RANDOM() LIMIT 1",
            (rarity,)
        )
        card = cursor.fetchone()
        if card:
            deals[rarity] = card
            new_ids[rarity] = card[0]
        else:
            new_ids[rarity] = None

    # Save to DB
    cursor.execute("""
        INSERT OR REPLACE INTO shop_daily_deals
        (user_id, reset_epoch, common_id, rare_id, epic_id, legendary_id, mythic_id, spring_id)
        VALUES (?,?,?,?,?,?,?,?)
    """, (
        user_id, reset_epoch,
        new_ids.get("Common"),
        new_ids.get("Rare"),
        new_ids.get("Epic"),
        new_ids.get("Legendary"),
        new_ids.get("Mythic"),
        new_ids.get("Spring"),
    ))
    conn.commit()
    return deals

def get_draft_spin_stats(user_id: int):
    """Returns (total_spins, spins_since_legendary, spins_since_spring)."""
    cursor.execute("""
        SELECT total_spins, spins_since_legendary, spins_since_spring
        FROM draft_vouchers WHERE user_id=?
    """, (user_id,))
    row = cursor.fetchone()
    return row if row else (0, 0, 0)

def update_spin_stats(user_id: int, got_legendary: bool, got_spring: bool, num_spins: int = 1):
    """Update spin counters after draft pull."""
    # Ensure row exists
    cursor.execute("""
        INSERT OR IGNORE INTO draft_vouchers 
        (user_id, vouchers, total_spins, spins_since_legendary, spins_since_spring)
        VALUES (?, 0, 0, 0, 0)
    """, (user_id,))
    # Now update
    cursor.execute("""
        UPDATE draft_vouchers SET
            total_spins = total_spins + ?,
            spins_since_legendary = CASE WHEN ? THEN 0 ELSE spins_since_legendary + ? END,
            spins_since_spring = CASE WHEN ? THEN 0 ELSE spins_since_spring + ? END
        WHERE user_id = ?
    """, (num_spins, got_legendary, num_spins, got_spring, num_spins, user_id))
    conn.commit()

def draft_pull_one(user_id: int, spin_number_in_batch: int, total_spins_before: int, spins_since_legendary_before: int, spins_since_spring_before: int):
    """
    Pull a single draft card.
    Applies pity rules:
    - 10th spin in a batch: guaranteed Legendary if none in this batch yet
    - 100th total spin (since last spring): guaranteed Spring
    Returns (card_name, rarity, file_id, forced_rarity)
    """
    forced_rarity = None

    # Check 100-spin spring pity
    spins_since_spring_now = spins_since_spring_before + spin_number_in_batch - 1
    if (spins_since_spring_now + 1) >= 100:
        forced_rarity = "Spring"

    # Check 10-spin legendary pity (within the batch of 10)
    if forced_rarity is None and spin_number_in_batch == 10:
        # Check if a legendary was already drawn in this batch (tracked externally)
        forced_rarity = "Legendary"

    if forced_rarity:
        cursor.execute(
            "SELECT name, rarity, file_id FROM cards WHERE rarity=? ORDER BY RANDOM() LIMIT 1",
            (forced_rarity,)
        )
        card = cursor.fetchone()
        if not card:
            forced_rarity = None  # fallback to normal

    if not forced_rarity:
        # Normal weighted pull from draft rarities
        rarities = list(DRAFT_RARITY_WEIGHTS.keys())
        weights = list(DRAFT_RARITY_WEIGHTS.values())
        chosen_rarity = random.choices(rarities, weights=weights, k=1)[0]
        cursor.execute(
            "SELECT name, rarity, file_id FROM cards WHERE rarity=? ORDER BY RANDOM() LIMIT 1",
            (chosen_rarity,)
        )
        card = cursor.fetchone()
        if not card:
            # Fallback to any card
            cursor.execute("SELECT name, rarity, file_id FROM cards ORDER BY RANDOM() LIMIT 1")
            card = cursor.fetchone()
        if not card:
            return None
        return card[0], card[1], card[2], chosen_rarity

    if card:
        return card[0], card[1], card[2], forced_rarity
    return None

def get_draft_cards_by_rarity(rarity: str, count: int = 1):
    """Get random draft-eligible cards by rarity."""
    cursor.execute(
        "SELECT name, rarity, file_id FROM cards WHERE rarity=? ORDER BY RANDOM() LIMIT ?",
        (rarity, count)
    )
    return cursor.fetchall()

# ================= REFERRAL HELPERS =================

def get_or_create_referral_code(user_id: int) -> str:
    cursor.execute("SELECT referral_code FROM referrals WHERE user_id=?", (user_id,))
    row = cursor.fetchone()
    if row:
        return row[0]
    # Generate unique code
    while True:
        code = "REF" + "".join(random.choices(string.ascii_uppercase + string.digits, k=7))
        cursor.execute("SELECT user_id FROM referrals WHERE referral_code=?", (code,))
        if not cursor.fetchone():
            break
    cursor.execute(
        "INSERT INTO referrals (user_id, referral_code, referred_by, total_referrals) VALUES (?,?,NULL,0)",
        (user_id, code)
    )
    conn.commit()
    return code

def get_referral_count(user_id: int) -> int:
    cursor.execute("SELECT total_referrals FROM referrals WHERE user_id=?", (user_id,))
    row = cursor.fetchone()
    return row[0] if row else 0

async def give_referral_reward(bot, referrer_id: int, new_referral_count: int):
    """Give reward for reaching a referral milestone."""
    if new_referral_count not in REFERRAL_REWARDS:
        return
    epic_count, legendary_count, mythic_spring_count, vouchers = REFERRAL_REWARDS[new_referral_count]
    reward_lines = []

    # Give epic cards
    for _ in range(epic_count):
        cards = get_draft_cards_by_rarity("Epic", 1)
        if cards:
            name, rarity, file_id = cards[0]
            insert_user_card(referrer_id, name, rarity, file_id)
            reward_lines.append(f"⚡ Epic: {name.title()}")

    # Give legendary cards
    for _ in range(legendary_count):
        cards = get_draft_cards_by_rarity("Legendary", 1)
        if cards:
            name, rarity, file_id = cards[0]
            insert_user_card(referrer_id, name, rarity, file_id)
            reward_lines.append(f"👑 Legendary: {name.title()}")

    # Give mythic/spring card
    for _ in range(mythic_spring_count):
        rarity_choice = random.choice(["Mythic", "Spring"])
        cards = get_draft_cards_by_rarity(rarity_choice, 1)
        if not cards:
            rarity_choice = "Mythic" if rarity_choice == "Spring" else "Spring"
            cards = get_draft_cards_by_rarity(rarity_choice, 1)
        if cards:
            name, rarity, file_id = cards[0]
            insert_user_card(referrer_id, name, rarity, file_id)
            emoji = "🌼" if rarity == "Spring" else "🦊"
            reward_lines.append(f"{emoji} {rarity}: {name.title()}")

    # Give draft vouchers
    if vouchers > 0:
        add_vouchers(referrer_id, vouchers)
        reward_lines.append(f"🎟️ {vouchers} Draft Vouchers")

    conn.commit()

    if reward_lines:
        reward_text = "\n".join(reward_lines)
        try:
            await bot.send_message(
                chat_id=referrer_id,
                text=(
                    f"🎉 *Referral Milestone Reached!*\n\n"
                    f"🌟 You have *{new_referral_count}* referral(s)!\n\n"
                    f"🏆 *Your Rewards:*\n{reward_text}\n\n"
                    f"━━━━━━━━━━━━━━━━━━━━━━\n"
                    f"Keep sharing your link to unlock more rewards! 🔥"
                ),
                parse_mode="Markdown"
            )
        except Exception as e:
            print(f"[REFERRAL] Could not DM referrer {referrer_id}: {e}")

    await log_to_group(
        bot,
        f"🎉 *Referral Reward Given*\n\n"
        f"👤 Referrer ID: `{referrer_id}`\n"
        f"🔢 Milestone: {new_referral_count} referrals\n"
        f"🎁 Rewards: {', '.join(reward_lines) if reward_lines else 'None'}"
    )

# ================= UTILS =================

def insert_user_card(user_id: int, card_name: str, rarity: str, file_id: str, file_type: str = "photo"):
    """
    Insert a card into user_cards with the correct master card_id.
    card_id matches the master cards.id so all users see the same number.
    """
    cursor.execute(
        "SELECT id, file_type FROM cards WHERE name=? AND rarity=? LIMIT 1",
        (card_name, rarity)
    )
    master = cursor.fetchone()
    master_card_id = master[0] if master else None
    # Use master file_type if not explicitly passed
    if master and master[1]:
        file_type = master[1]

    cursor.execute(
        "INSERT INTO user_cards (user_id, card_name, rarity, file_id, card_id, file_type) VALUES (?,?,?,?,?,?)",
        (user_id, card_name, rarity, file_id, master_card_id, file_type)
    )


async def send_card_media(target, file_id: str, file_type: str = "photo", caption: str = "", parse_mode: str = "Markdown", reply_markup=None):
    """
    Send a card as photo, video (with sound), or animation depending on file_type.
    target can be update.message, context.bot (if chat_id passed), or a bot object.
    Supports both message.reply_* style and bot.send_* style via duck-typing.
    """
    kwargs = {"caption": caption, "parse_mode": parse_mode}
    if reply_markup:
        kwargs["reply_markup"] = reply_markup

    ft = (file_type or "photo").lower()

    if ft == "video":
        if hasattr(target, "reply_video"):
            await target.reply_video(video=file_id, supports_streaming=True, **kwargs)
        else:
            await target.send_video(video=file_id, supports_streaming=True, **kwargs)
    elif ft == "animation":
        if hasattr(target, "reply_animation"):
            await target.reply_animation(animation=file_id, **kwargs)
        else:
            await target.send_animation(animation=file_id, **kwargs)
    else:
        if hasattr(target, "reply_photo"):
            await target.reply_photo(photo=file_id, **kwargs)
        else:
            await target.send_photo(photo=file_id, **kwargs)


def weighted_random_card():
    # Never include Disc or Dragon in random/pack/daily draws — admin-only
    cursor.execute(
        "SELECT name, rarity, file_id FROM cards WHERE rarity NOT IN ('Animation','LeaderBoard')"
    )
    cards = cursor.fetchall()
    if not cards:
        return None
    weighted = []
    for card in cards:
        rarity = card[1]
        weight = RARITY_WEIGHTS.get(rarity, 1)
        weighted.extend([card] * weight)
    if not weighted:
        return None
    return random.choice(weighted)


def get_spawn_range(chat_id):
    """Return (min, max) spawn range for a chat, using custom if set."""
    if chat_id in chat_spawn_settings:
        val = chat_spawn_settings[chat_id]
        low = max(1, val - 10)
        return (low, val)
    return (SPAWN_MIN_MESSAGES, SPAWN_MAX_MESSAGES)


def reset_spawn(chat_id):
    message_counter[chat_id] = 0
    mn, mx = get_spawn_range(chat_id)
    next_spawn[chat_id] = random.randint(mn, mx)
    current_card.pop(chat_id, None)
    spawn_time.pop(chat_id, None)
    print(f"[SPAWN] Next spawn in chat {chat_id} at message #{next_spawn[chat_id]}")


# ============== Check card table =======
async def check_cards_table(update, context):
    cursor.execute("PRAGMA table_info(cards)")
    columns = cursor.fetchall()
    text = "Cards Table Columns:\n\n"
    for col in columns:
        text += f"{col[1]}\n"
    await update.message.reply_text(text)


# ================= SPAM =================

block_notified: dict = {}

def record_message(user_id: int):
    """Record a message timestamp for spam detection."""
    now = time.time()
    timestamps = user_messages[user_id]
    timestamps.append(now)
    while timestamps and now - timestamps[0] > SPAM_WINDOW:
        timestamps.popleft()
    if len(timestamps) > SPAM_LIMIT:
        blocked_users[user_id] = now + BLOCK_DURATION

def is_blocked(user_id: int) -> bool:
    """Check if user is currently muted."""
    now = time.time()
    if user_id in blocked_users:
        if now < blocked_users[user_id]:
            return True
        else:
            del blocked_users[user_id]
            block_notified.pop(user_id, None)
    return False

def is_spamming(user_id: int) -> bool:
    """Record message + check if blocked."""
    record_message(user_id)
    return is_blocked(user_id)


# ================= SPAWN =================

async def spawn_card(update, context, forced_rarity=None):
    chat_id = update.effective_chat.id

    # Pick rarity first so we can show its emoji immediately
    rarity = forced_rarity if forced_rarity else get_random_rarity()
    emoji = RARITY_EMOJI.get(rarity, "⭐")

    # Step 1: Send rarity emoji as a teaser
    try:
        msg = await context.bot.send_message(
            chat_id=chat_id,
            text=f"{emoji}"
        )
    except Exception as e:
        print(f"[SPAWN] Cannot send message (no permission?): {e}")
        msg = None

    if msg:
        try:
            await asyncio.sleep(0.8)
            await msg.edit_text(f"{emoji}\n🌫️ A strange presence is forming...")
            await asyncio.sleep(0.8)
            await msg.edit_text(f"{emoji}\n🔮 Summoning shinobi...")
            await asyncio.sleep(0.8)
        except Exception as e:
            print(f"[SPAWN] Edit error (non-fatal): {e}")

    cursor.execute(
        """
        SELECT name, rarity, file_id
        FROM cards
        WHERE rarity=?
        ORDER BY RANDOM()
        LIMIT 1
        """,
        (rarity,)
    )
    card = cursor.fetchone()

    if not card:
        try:
            if msg:
                await msg.edit_text(f"❌ No shinobi found for rarity: {rarity}!")
            else:
                await context.bot.send_message(chat_id=chat_id, text=f"❌ No shinobi found for rarity: {rarity}!")
        except Exception:
            await context.bot.send_message(chat_id=chat_id, text=f"❌ No shinobi found for rarity: {rarity}!")
        return

    name, rarity_db, file_id = card
    emoji = RARITY_EMOJI.get(rarity_db, "⭐")

    current_card[chat_id] = (name, rarity_db, file_id)
    spawn_time[chat_id] = time.time()

    print(f"[SPAWN] '{name}' ({rarity_db}) spawned in chat {chat_id}")

    if msg:
        try:
            await msg.delete()
        except Exception:
            pass

    await context.bot.send_photo(
        chat_id=chat_id,
        photo=file_id,
        caption=(
            f"{emoji} ᴀ sʜɪɴᴏʙɪ ʜᴀs ᴀᴘᴘᴇᴀʀᴇᴅ!\n\n"
            f"{emoji} ʀᴀʀɪᴛʏ: {rarity_db}\n\n"
            "━━━━━━━━━━━━━━\n"
            "🎴 ᴀᴅᴅ ɪᴛ ᴛᴏ ʏᴏᴜʀ ᴄᴏʟʟᴇᴄᴛɪᴏɴ\n"
            "ʙʏ sᴇɴᴅɪɴɢ:\n"
            "/catch <shinobi name>"
        )
    )


# ================= List Cards =============
async def list_cards(update, context):
    user_id = update.effective_user.id
    if not is_admin(user_id):
        await update.message.reply_text("❌ Only admins can use this command.")
        return

    cursor.execute("SELECT id, name, rarity FROM cards ORDER BY id")
    rows = cursor.fetchall()

    if not rows:
        await update.message.reply_text("📜 No cards in database yet!")
        return

    text = "📜 Available Shinobi Cards\n\n"
    for row in rows:
        card_id, name, rarity = row
        emoji = RARITY_EMOJI.get(rarity, "⭐")
        id_display = f"#{card_id}" if card_id is not None else "#NULL ⚠️"
        text += f"{id_display} | {emoji} | {name.title()} | {rarity}\n"

    await update.message.reply_text(text)


# ================= Redeem create =========
async def redeem_create(update, context):
    user_id = update.effective_user.id
    if not is_admin(user_id):
        await update.message.reply_text("❌ Only admins can create redeem codes.")
        return

    try:
        card_id = int(context.args[0])
        uses = int(context.args[1])
        if uses <= 0:
            raise ValueError
    except:
        await update.message.reply_text("Usage:\n/redeem_create <card_id> <uses>")
        return

    cursor.execute("SELECT name, rarity FROM cards WHERE id=?", (card_id,))
    card = cursor.fetchone()

    if not card:
        await update.message.reply_text("❌ Card not found.")
        return

    name, rarity = card
    code = "".join(random.choices(string.ascii_uppercase + string.digits, k=7))

    cursor.execute("""
        INSERT INTO redeem_codes (code, card_id, uses_left)
        VALUES (?, ?, ?)
    """, (code, card_id, uses))
    conn.commit()

    await update.message.reply_text(
        f"🎁 Redeem Code Created!\n\n"
        f"Code: {code}\n"
        f"Card: {name} ({rarity})\n"
        f"Uses: {uses}"
    )


# ================== Redeem ================
async def redeem(update, context):
    if not update.effective_user or not update.message:
        return
    user_id = update.effective_user.id
    if not has_started_bot(user_id) and not is_admin(user_id):
        await update.message.reply_text(NOT_STARTED_MSG, parse_mode="Markdown", disable_web_page_preview=True)
        return
    if is_banned(user_id):
        await update.message.reply_text(get_ban_message(user_id), parse_mode="Markdown")
        return

    try:
        code = context.args[0].upper()
    except:
        await update.message.reply_text("Usage:\n/redeem <code>")
        return

    cursor.execute("SELECT card_id, uses_left FROM redeem_codes WHERE code=?", (code,))
    row = cursor.fetchone()

    if not row:
        await update.message.reply_text("❌ Invalid redeem code.")
        return

    card_id, uses_left = row

    if uses_left <= 0:
        await update.message.reply_text("❌ This code has expired.")
        return

    cursor.execute("SELECT name, rarity, file_id FROM cards WHERE id=?", (card_id,))
    card = cursor.fetchone()

    if not card:
        await update.message.reply_text("❌ Card not found.")
        return

    name, rarity, file_id = card

    insert_user_card(user_id, name, rarity, file_id)
    cursor.execute(
        "UPDATE redeem_codes SET uses_left=uses_left-1 WHERE code=?",
        (code,)
    )
    conn.commit()

    emoji = RARITY_EMOJI.get(rarity, "⭐")

    await update.message.reply_photo(
        photo=file_id,
        caption=(
            f"🎉 REDEEM SUCCESSFUL!\n\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"🥷 Shinobi: {name.title()}\n"
            f"{emoji} Rarity: {rarity.title()}\n"
            f"🆔 Card ID: #{card_id}\n"
            f"📦 Added to your collection\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"🔥 A new warrior joins your squad!"
        )
    )


# ================= Favourite ==============
async def fav(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.effective_user or not update.message:
        return

    user_id = update.effective_user.id

    if not has_started_bot(user_id) and not is_admin(user_id):
        await update.message.reply_text(NOT_STARTED_MSG, parse_mode="Markdown", disable_web_page_preview=True)
        return

    if not context.args:
        await update.message.reply_text("Usage: /fav <card_id>")
        return

    try:
        card_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("❌ Invalid card ID")
        return

    # Look up by master card_id (the number shown in /collection), fallback to row id
    cursor.execute(
        "SELECT id, card_name, rarity, file_id, COALESCE(file_type,'photo') FROM user_cards WHERE card_id=? AND user_id=? LIMIT 1",
        (card_id, user_id),
    )
    row = cursor.fetchone()
    if not row:
        cursor.execute(
            "SELECT id, card_name, rarity, file_id, COALESCE(file_type,'photo') FROM user_cards WHERE id=? AND user_id=?",
            (card_id, user_id),
        )
        row = cursor.fetchone()

    if not row:
        await update.message.reply_text("❌ Card not found")
        return

    row_id, card_name, rarity, file_id, file_type = row

    await send_card_media(update.message, file_id, file_type, caption="")
    fav_confirm[user_id] = row_id

    keyboard = [
        [
            InlineKeyboardButton("✅ Yes", callback_data=f"fav_yes_{row_id}_{user_id}"),
            InlineKeyboardButton("❌ No", callback_data=f"fav_no_{row_id}_{user_id}")
        ]
    ]
    await update.message.reply_text(
        "⭐ Are you willing to set this as your favourite shinobi?",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )


# ================== Yes / No handler for fav =======
async def fav_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    data = query.data
    if not data.startswith("fav_"):
        return

    parts = data.split("_")
    action = parts[1]
    card_id = int(parts[2])
    user_id = int(parts[3])

    if query.from_user.id != user_id:
        await query.edit_message_text("❌ Not your action.")
        return

    if action == "no":
        await query.edit_message_text("❌ Favourite selection cancelled.")
        return

    cursor.execute("UPDATE user_cards SET is_favorite=0 WHERE user_id=?", (user_id,))
    cursor.execute(
        "UPDATE user_cards SET is_favorite=1 WHERE id=? AND user_id=?",
        (card_id, user_id)
    )
    conn.commit()
    await query.edit_message_text("⭐ Favourite shinobi updated!")


# ================= TRACKER =================

async def message_tracker(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.effective_chat or not update.effective_user or not update.message:
        return

    chat_id = update.effective_chat.id
    user_id = update.effective_user.id
    user_name = update.effective_user.first_name or "Unknown"

    # Log conversation to DB
    msg_text = update.message.text or update.message.caption or ""
    if msg_text and chat_id != LOG_GROUP_ID:
        try:
            cursor.execute(
                "INSERT INTO conv_logs (user_id, username, first_name, message_text, timestamp) VALUES (?,?,?,?,?)",
                (user_id, update.effective_user.username or "", user_name, msg_text[:500], int(time.time()))
            )
            conn.commit()
        except Exception:
            pass

    # Register user in bot_users for broadcast (silent, non-blocking)
    try:
        cursor.execute("""
            INSERT INTO bot_users (user_id, first_name, username, joined_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(user_id) DO NOTHING
        """, (user_id, user_name, update.effective_user.username or "", int(time.time())))
        conn.commit()
    except Exception:
        pass

    # Ban check — show ban message once then ignore
    if is_banned(user_id):
        if user_id not in block_notified:
            block_notified[user_id] = True
            try:
                await update.message.reply_text(get_ban_message(user_id), parse_mode="Markdown")
            except Exception:
                pass
        return

    record_message(user_id)

    if is_blocked(user_id):
        if user_id not in block_notified:
            block_notified[user_id] = True
            remaining = int(blocked_users[user_id] - time.time())
            mins = remaining // 60
            secs = remaining % 60
            await update.message.reply_text(
                f"🚫 *Spam Detected!*\n\n"
                f"⛔ You have been muted for sending too many messages.\n"
                f"⏳ Cooldown: *{mins}m {secs}s* remaining.\n\n"
                f"🧘 Take a breath, shinobi. Come back when you're ready.",
                parse_mode="Markdown"
            )
        return

    message_counter[chat_id] += 1
    total_message_counter[chat_id] += 1
    total = total_message_counter[chat_id]
    print(f"[MSG] Chat {chat_id} | User: {user_name}({user_id}) | {message_counter[chat_id]} / {next_spawn[chat_id]} | Total: {total}")

    if chat_id in current_card:
        if time.time() - spawn_time.get(chat_id, 0) > ENCOUNTER_DURATION:
            await update.message.reply_text("💨 The shinobi escaped!")
            reset_spawn(chat_id)
            return

    if chat_id not in current_card:
        # Check milestone first — overrides normal spawn
        milestone = get_milestone_rarity(chat_id, total)
        if milestone:
            print(f"[MILESTONE] {milestone} guaranteed spawn at total msg #{total} in chat {chat_id}!")
            message_counter[chat_id] = 0
            mn, mx = get_spawn_range(chat_id)
            next_spawn[chat_id] = random.randint(mn, mx)
            await spawn_card(update, context, forced_rarity=milestone)
        elif message_counter[chat_id] >= next_spawn[chat_id]:
            print(f"[SPAWN] Threshold reached in chat {chat_id}! Spawning...")
            message_counter[chat_id] = 0
            mn, mx = get_spawn_range(chat_id)
            next_spawn[chat_id] = random.randint(mn, mx)
            print(f"[SPAWN] Next spawn at message #{next_spawn[chat_id]}")
            await spawn_card(update, context)


# ================= STICKER TRACKER =================

async def sticker_tracker(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.effective_chat or not update.effective_user or not update.message:
        return

    chat_id = update.effective_chat.id
    user_id = update.effective_user.id
    user_name = update.effective_user.first_name or "Unknown"

    record_message(user_id)

    if is_blocked(user_id):
        if user_id not in block_notified:
            block_notified[user_id] = True
            remaining = int(blocked_users[user_id] - time.time())
            mins = remaining // 60
            secs = remaining % 60
            await update.message.reply_text(
                f"🚫 *Spam Detected!*\n\n"
                f"⛔ You have been muted for sending too many messages.\n"
                f"⏳ Cooldown: *{mins}m {secs}s* remaining.\n\n"
                f"🧘 Take a breath, shinobi. Come back when you're ready.",
                parse_mode="Markdown"
            )
        return

    message_counter[chat_id] += 1
    total_message_counter[chat_id] += 1
    total = total_message_counter[chat_id]
    print(f"[STICKER] Chat {chat_id} | User: {user_name}({user_id}) | {message_counter[chat_id]} / {next_spawn[chat_id]} | Total: {total}")

    if chat_id in current_card:
        if time.time() - spawn_time.get(chat_id, 0) > ENCOUNTER_DURATION:
            await update.message.reply_text("💨 The shinobi escaped!")
            reset_spawn(chat_id)
            return

    if chat_id not in current_card:
        milestone = get_milestone_rarity(chat_id, total)
        if milestone:
            print(f"[MILESTONE] {milestone} guaranteed spawn at total msg #{total} in chat {chat_id}!")
            message_counter[chat_id] = 0
            mn, mx = get_spawn_range(chat_id)
            next_spawn[chat_id] = random.randint(mn, mx)
            await spawn_card(update, context, forced_rarity=milestone)
        elif message_counter[chat_id] >= next_spawn[chat_id]:
            print(f"[SPAWN] Sticker triggered spawn in chat {chat_id}!")
            message_counter[chat_id] = 0
            mn, mx = get_spawn_range(chat_id)
            next_spawn[chat_id] = random.randint(mn, mx)
            print(f"[SPAWN] Next spawn at message #{next_spawn[chat_id]}")
            await spawn_card(update, context)


# ================= CATCH =================

async def catch(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.effective_chat or not update.effective_user or not update.message:
        return

    chat_id = update.effective_chat.id
    user_id = update.effective_user.id
    now = time.time()

    # ── Must have started bot in DM first ────────────────────────────────────
    if not has_started_bot(user_id) and not is_admin(user_id):
        try:
            await update.message.reply_text(
                NOT_STARTED_MSG,
                parse_mode="Markdown",
                disable_web_page_preview=True
            )
        except Exception:
            pass
        return

    if is_banned(user_id):
        await update.message.reply_text(get_ban_message(user_id), parse_mode="Markdown")
        return

    # Block banned users (duplicate safety check removed below)

    # Block muted users from catching
    if is_blocked(user_id):
        remaining = int(blocked_users[user_id] - now)
        mins = remaining // 60
        secs = remaining % 60
        await update.message.reply_text(
            f"🚫 *You are muted!*\n\n"
            f"⛔ Spammers cannot catch shinobi.\n"
            f"⏳ Your mute expires in: *{mins}m {secs}s*\n\n"
            f"😤 Serve your time, then return to battle.",
            parse_mode="Markdown"
        )
        return

    if user_id in last_guess_time:
        if now - last_guess_time[user_id] < GUESS_COOLDOWN:
            await update.message.reply_text("⏳ Wait before guessing again.")
            return

    last_guess_time[user_id] = now

    card = current_card.get(chat_id)
    if not card:
        await update.message.reply_text("❌ No active encounter.")
        return

    if not context.args:
        await update.message.reply_text("Usage: /catch <n>")
        return

    guess = normalize(" ".join(context.args))
    name, rarity, file_id = card
    real_name = normalize(name)

    if (
        guess == real_name
        or guess in real_name.split()
        or real_name.split()[0] in guess
    ):
        insert_user_card(user_id, name, rarity, file_id)
        conn.commit()

        # ── Award Shinobi Coins for catching ────────────────────────────────
        coin_reward = RARITY_COINS.get(rarity, 0)
        if coin_reward > 0:
            add_coins(user_id, coin_reward)
        new_coins = get_coins(user_id)

        taken_time = int(time.time() - spawn_time.get(chat_id, time.time()))
        emoji = RARITY_EMOJI.get(rarity, "⭐")
        user_name = update.effective_user.first_name or "Shinobi"

        coin_line = f"🪙 *+{coin_reward} Shinobi Coins!* (Total: {new_coins})\n" if coin_reward > 0 else ""

        text = (
            f"✅ {user_name}, you got a new shinobi!\n\n"
            f"🌸 𝗡𝗔𝗠𝗘: {name.title()} [{emoji}]\n"
            f"🔮 𝗥𝗔𝗥𝗜𝗧𝗬: {rarity}\n"
            f"❇️ 𝗦𝗢𝗨𝗥𝗖𝗘: Naruto Classic\n\n"
            f"{coin_line}"
            f"⌛ 𝗧𝗜𝗠𝗘 𝗧𝗔𝗞𝗘𝗡: {taken_time}s"
        )

        await update.message.reply_photo(photo=file_id, caption=text)
        reset_spawn(chat_id)

        # Log to catch channel
        await log_catch(
            context.bot,
            f"🎴 *Card Caught!*\n\n"
            f"👤 Shinobi: {user_name} (`{user_id}`)\n"
            f"🥷 Card: {name.title()}\n"
            f"{RARITY_EMOJI.get(rarity, '⭐')} Rarity: {rarity}\n"
            f"⌛ Time taken: {taken_time}s\n"
            f"💬 Chat: `{chat_id}`",
            photo_file_id=file_id
        )

    else:
        await update.message.reply_text("❌ Wrong guess! Try again.")


# ================= DAILY =================

async def daily(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.effective_user or not update.message:
        return
    user_id = update.effective_user.id
    if not has_started_bot(user_id) and not is_admin(user_id):
        await update.message.reply_text(NOT_STARTED_MSG, parse_mode="Markdown", disable_web_page_preview=True)
        return
    if is_banned(user_id):
        await update.message.reply_text(get_ban_message(user_id), parse_mode="Markdown")
        return

    now = int(time.time())

    # ── Reset window: 5:30 AM IST = 00:00 UTC (IST is UTC+5:30) ──────────────
    # Calculate the most recent 5:30 AM IST in UTC epoch
    IST_OFFSET = 5 * 3600 + 30 * 60  # 19800 seconds
    now_ist_seconds = now + IST_OFFSET
    # Seconds since midnight IST today
    seconds_since_midnight_ist = now_ist_seconds % 86400
    # 5:30 AM IST in seconds = 5*3600 + 30*60 = 19800
    RESET_SECONDS = 5 * 3600 + 30 * 60
    if seconds_since_midnight_ist >= RESET_SECONDS:
        # Today's reset was at RESET_SECONDS past midnight IST
        today_reset_epoch = now - (seconds_since_midnight_ist - RESET_SECONDS)
    else:
        # Today's reset hasn't happened yet; use yesterday's
        today_reset_epoch = now - (seconds_since_midnight_ist + 86400 - RESET_SECONDS)

    cursor.execute("SELECT last_daily FROM users WHERE user_id=?", (user_id,))
    row = cursor.fetchone()
    last_daily = row[0] if row and row[0] else 0

    if last_daily >= today_reset_epoch:
        # Already claimed since today's 5:30 AM IST reset
        next_reset = today_reset_epoch + 86400
        remaining = next_reset - now
        hours = remaining // 3600
        minutes = (remaining % 3600) // 60
        await update.message.reply_text(
            f"⏳ *Daily Reward Already Claimed!*\n\n"
            f"🕐 Resets at *5:30 AM* daily.\n"
            f"⏱️ Next reset in: *{hours}h {minutes}m*\n\n"
            f"🍃 Rest now, shinobi. Your next reward awaits!",
            parse_mode="Markdown"
        )
        return

    card = weighted_random_card()
    if not card:
        await update.message.reply_text("❌ No cards available yet. Ask the admin to add some!")
        return

    name, rarity, file_id = card
    emoji = RARITY_EMOJI.get(rarity, "⭐")
    user_name = update.effective_user.first_name or "Shinobi"

    insert_user_card(user_id, name, rarity, file_id)
    cursor.execute(
        "INSERT OR REPLACE INTO users (user_id, last_daily, pack_today, last_pack_day) "
        "VALUES (?, ?, COALESCE((SELECT pack_today FROM users WHERE user_id=?), 0), "
        "COALESCE((SELECT last_pack_day FROM users WHERE user_id=?), 0))",
        (user_id, now, user_id, user_id),
    )
    conn.commit()

    # Give 5 draft vouchers with daily
    add_vouchers(user_id, 5)
    add_coins(user_id, COIN_DAILY_AMOUNT)
    conn.commit()
    current_vouchers = get_user_vouchers(user_id)
    current_coins = get_coins(user_id)

    await update.message.reply_photo(
        photo=file_id,
        caption=(
            f"🎁 𝗗𝗔𝗜𝗟𝗬 𝗥𝗘𝗪𝗔𝗥𝗗!\n\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"👤 Shinobi: {user_name}\n"
            f"🥷 𝗡𝗔𝗠𝗘: {name.title()}\n"
            f"{emoji} 𝗥𝗔𝗥𝗜𝗧𝗬: {rarity}\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"🎟️ +5 Draft Vouchers! (Total: {current_vouchers})\n"
            f"🪙 +{COIN_DAILY_AMOUNT} Shinobi Coins! (Total: {current_coins})\n"
            f"🔥 A new warrior joins your squad!\n"
            f"📅 Resets at *5:30 AM* daily!"
        )
    )


# ================= PACK =================

async def pack(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.effective_user or not update.message:
        return
    user_id = update.effective_user.id
    if not has_started_bot(user_id) and not is_admin(user_id):
        await update.message.reply_text(NOT_STARTED_MSG, parse_mode="Markdown", disable_web_page_preview=True)
        return
    if is_banned(user_id):
        await update.message.reply_text(get_ban_message(user_id), parse_mode="Markdown")
        return

    now = int(time.time())

    # ── Reset window: 5:30 AM IST = 00:00 UTC ────────────────────────────────
    IST_OFFSET = 5 * 3600 + 30 * 60
    now_ist_seconds = now + IST_OFFSET
    seconds_since_midnight_ist = now_ist_seconds % 86400
    RESET_SECONDS = 5 * 3600 + 30 * 60
    if seconds_since_midnight_ist >= RESET_SECONDS:
        today_reset_epoch = now - (seconds_since_midnight_ist - RESET_SECONDS)
    else:
        today_reset_epoch = now - (seconds_since_midnight_ist + 86400 - RESET_SECONDS)

    cursor.execute(
        "SELECT pack_today, last_pack_day FROM users WHERE user_id=?",
        (user_id,),
    )
    row = cursor.fetchone()

    if row:
        pack_today, last_pack_day = row
        pack_today = pack_today or 0
        last_pack_day = last_pack_day or 0
        # last_pack_day now stores epoch of last reset window, compare to today's reset
        if last_pack_day < today_reset_epoch:
            pack_today = 0
    else:
        pack_today = 0

    if pack_today >= PACK_LIMIT_PER_DAY:
        next_reset = today_reset_epoch + 86400
        remaining = next_reset - now
        hours = remaining // 3600
        minutes = (remaining % 3600) // 60
        await update.message.reply_text(
            f"🎴 𝗣𝗔𝗖𝗞 𝗟𝗜𝗠𝗜𝗧 𝗥𝗘𝗔𝗖𝗛𝗘𝗗!\n\n"
            f"⛩️ You have opened all *{PACK_LIMIT_PER_DAY} packs* for today.\n"
            f"🕐 Resets at *5:30 AM* daily.\n"
            f"⏱️ Next reset in: *{hours}h {minutes}m*\n\n"
            f"⏳ Come back after 5:30 AM to continue your journey.",
            parse_mode="Markdown"
        )
        return

    card = weighted_random_card()
    if not card:
        await update.message.reply_text("❌ No cards available yet. Ask the admin to add some!")
        return

    name, rarity, file_id = card
    emoji = RARITY_EMOJI.get(rarity, "⭐")

    insert_user_card(user_id, name, rarity, file_id)
    cursor.execute(
        "INSERT OR REPLACE INTO users (user_id, pack_today, last_pack_day, last_daily) "
        "VALUES (?, ?, ?, COALESCE((SELECT last_daily FROM users WHERE user_id=?), 0))",
        (user_id, pack_today + 1, today_reset_epoch, user_id),
    )
    conn.commit()

    remaining_packs = PACK_LIMIT_PER_DAY - pack_today - 1

    await update.message.reply_photo(
        photo=file_id,
        caption=(
            f"🎴 𝗣𝗔𝗖𝗞 𝗢𝗣𝗘𝗡𝗘𝗗!\n\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"{emoji} 𝗥𝗔𝗥𝗜𝗧𝗬: {rarity}\n"
            f"🥷 𝗦𝗛𝗜𝗡𝗢𝗕𝗜: {name.title()}\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"📦 Packs remaining today: *{remaining_packs}/{PACK_LIMIT_PER_DAY}*\n\n"
            f"🔥 A new warrior joins your squad!"
        )
    )


# ================= COLLECTION =================

async def collection(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.effective_user or not update.message:
        return
    user_id = update.effective_user.id
    if not has_started_bot(user_id) and not is_admin(user_id):
        await update.message.reply_text(NOT_STARTED_MSG, parse_mode="Markdown", disable_web_page_preview=True)
        return
    if is_banned(user_id):
        await update.message.reply_text(get_ban_message(user_id), parse_mode="Markdown")
        return
    user_name = update.effective_user.first_name or "Shinobi"

    fav_rarity = get_user_fav_rarity(user_id)
    rows = get_sorted_collection(user_id, fav_rarity)

    if not rows:
        await update.message.reply_text("📦 No shinobi collected yet!")
        return

    user_pages[user_id] = 0
    await send_collection_page(update, context, user_id, 0, rows, user_name, fav_rarity)


# ================= PAGE RENDER ====================
async def send_collection_page(update, context, user_id, page, rows, user_name, fav_rarity=None):
    cursor.execute(
        "SELECT uc.file_id, COALESCE(c.file_type, uc.file_type, 'photo') "
        "FROM user_cards uc "
        "LEFT JOIN cards c ON c.id = uc.card_id "
        "WHERE uc.user_id=? AND uc.is_favorite=1",
        (user_id,)
    )
    fav = cursor.fetchone()

    per_page = 10
    start = page * per_page
    end = start + per_page
    page_items = rows[start:end]
    total_pages = (len(rows) - 1) // per_page + 1

    mode_badge = f" • {RARITY_EMOJI.get(fav_rarity, '⭐')} {fav_rarity} Mode" if fav_rarity else ""
    text = f"🔥 {user_name}'s Shinobi Collection 🔥{mode_badge}\n"
    text += f"📄 Page {page+1}/{total_pages}\n\n"
    text += "━━━━━━━━━━━━━━━━━━━━━━\n"

    for card_id, name, rarity, count in page_items:
        emoji = RARITY_EMOJI.get(rarity, "⭐")
        star = "⭐ " if fav_rarity and rarity == fav_rarity else ""
        text += f"➥ {star}{emoji} | #{card_id} {name.title()} x{count}\n"
        text += "------------------------------\n"

    text += "━━━━━━━━━━━━━━━━━━━━━━"

    keyboard = [
        [
            InlineKeyboardButton("🌐 View Cards Inline", switch_inline_query_current_chat=f"mycards_{user_id}")
        ],
        [
            InlineKeyboardButton("⬅️ Prev", callback_data=f"prev_{user_id}"),
            InlineKeyboardButton("➡️ Next", callback_data=f"next_{user_id}")
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    if update.message:
        if fav:
            fav_file_id = fav[0]
            fav_file_type = fav[1] if len(fav) > 1 else "photo"
            try:
                if fav_file_type in ("video", "animation"):
                    await update.message.reply_video(
                        video=fav_file_id,
                        caption=text,
                        reply_markup=reply_markup,
                        supports_streaming=True
                    )
                else:
                    await update.message.reply_photo(
                        photo=fav_file_id,
                        caption=text,
                        reply_markup=reply_markup
                    )
            except Exception:
                await update.message.reply_text(text, reply_markup=reply_markup)
        else:
            await update.message.reply_text(text, reply_markup=reply_markup)


# ================= BUTTON HANDLER ==========
async def collection_nav(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    data = query.data
    user_id = int(data.split("_")[1])

    # Check ownership BEFORE answering
    if update.effective_user.id != user_id:
        try:
            await query.answer("❌ Not your collection!", show_alert=True)
        except Exception:
            pass
        return

    try:
        await query.answer()
    except Exception:
        pass  # Old query — still process the page turn

    fav_rarity = get_user_fav_rarity(user_id)
    rows = get_sorted_collection(user_id, fav_rarity)

    if user_id not in user_pages:
        user_pages[user_id] = 0

    if "next" in data:
        user_pages[user_id] += 1
    elif "prev" in data:
        user_pages[user_id] -= 1

    per_page = 10
    max_page = max(0, (len(rows) - 1) // per_page)

    if user_pages[user_id] < 0:
        user_pages[user_id] = 0
    if user_pages[user_id] > max_page:
        user_pages[user_id] = max_page

    page = user_pages[user_id]
    user_name = update.effective_user.first_name or "Shinobi"

    start = page * per_page
    end = start + per_page
    page_items = rows[start:end]

    mode_badge = f" • {RARITY_EMOJI.get(fav_rarity, '⭐')} {fav_rarity} Mode" if fav_rarity else ""
    text = f"🔥 {user_name}'s Shinobi Collection 🔥{mode_badge}\n"
    text += f"📄 Page {page+1}/{max_page+1}\n\n"
    text += "━━━━━━━━━━━━━━━━━━━━━━\n"

    for card_id, name, rarity, count in page_items:
        emoji = RARITY_EMOJI.get(rarity, "⭐")
        star = "⭐ " if fav_rarity and rarity == fav_rarity else ""
        text += f"➥ {star}{emoji} | #{card_id} {name.title()} x{count}\n"
        text += "------------------------------\n"

    text += "━━━━━━━━━━━━━━━━━━━━━━"

    keyboard = [
        [
            InlineKeyboardButton("🌐 View Cards Inline", switch_inline_query_current_chat=f"mycards_{user_id}")
        ],
        [
            InlineKeyboardButton("⬅️ Prev", callback_data=f"prev_{user_id}"),
            InlineKeyboardButton("➡️ Next", callback_data=f"next_{user_id}")
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    try:
        await query.edit_message_caption(caption=text, reply_markup=reply_markup)
    except Exception:
        try:
            await query.edit_message_text(text=text, reply_markup=reply_markup)
        except Exception as e:
            print(f"[NAV] Could not edit message: {e}")


# ================= GIFT =================

async def gift(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.effective_user or not update.message:
        return

    user_id = update.effective_user.id

    if not has_started_bot(user_id) and not is_admin(user_id):
        await update.message.reply_text(NOT_STARTED_MSG, parse_mode="Markdown", disable_web_page_preview=True)
        return

    if not context.args or len(context.args) < 1:
        await update.message.reply_text("Usage: /gift <card_id> (reply to the target user)")
        return

    if not update.message.reply_to_message:
        await update.message.reply_text("💬 Reply to the user you want to gift the card to.")
        return

    try:
        card_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("❌ Invalid card ID. Use a number.")
        return

    target_user = update.message.reply_to_message.from_user
    if not target_user:
        await update.message.reply_text("❌ Could not identify target user.")
        return

    target_id = target_user.id

    if target_id == user_id:
        await update.message.reply_text("❌ You can't gift a card to yourself.")
        return

    cursor.execute(
        "SELECT id, card_name, rarity, file_id FROM user_cards WHERE card_id=? AND user_id=? LIMIT 1",
        (card_id, user_id),
    )
    row = cursor.fetchone()
    if not row:
        # Fallback: try by row id
        cursor.execute(
            "SELECT id, card_name, rarity, file_id FROM user_cards WHERE id=? AND user_id=?",
            (card_id, user_id),
        )
        row = cursor.fetchone()

    if not row:
        await update.message.reply_text("❌ Card not found or it doesn't belong to you.")
        return

    row_id, card_name, card_rarity, card_file_id = row

    cursor.execute("DELETE FROM user_cards WHERE id=?", (row_id,))
    insert_user_card(target_id, card_name, card_rarity, card_file_id)
    conn.commit()
    emoji = RARITY_EMOJI.get(card_rarity, "⭐")

    text = (
        "🎁 𝙎𝙃𝙄𝙉𝙊𝘽𝙄 𝙏𝙍𝘼𝙉𝙎𝙁𝙀𝙍 𝘾𝙊𝙈𝙋𝙇𝙀𝙏𝙀!\n\n"
        f"🌸 𝗡𝗮𝗺𝗲: {card_name.title()} [{emoji}]\n"
        f"🔮 𝗥𝗮𝗿𝗶𝘁𝘆: {card_rarity}\n\n"
        f"📤 𝗙𝗿𝗼𝗺: {update.effective_user.first_name}\n"
        f"📥 𝗧𝗼: {target_user.first_name}\n\n"
        "⚔️ 𝗦𝘁𝗮𝘁𝘂𝘀: Transfer Complete"
    )
    await update.message.reply_text(text)


# ================= LEADERBOARD =================

async def leaderboard(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.effective_user:
        return
    user_id = update.effective_user.id
    if not has_started_bot(user_id) and not is_admin(user_id):
        await update.message.reply_text(NOT_STARTED_MSG, parse_mode="Markdown", disable_web_page_preview=True)
        return
    if is_banned(user_id):
        await update.message.reply_text(get_ban_message(user_id), parse_mode="Markdown")
        return

    cursor.execute(
        "SELECT user_id, COUNT(*) as total FROM user_cards "
        "GROUP BY user_id ORDER BY total DESC LIMIT 10"
    )
    rows = cursor.fetchall()

    if not rows:
        await update.message.reply_text("🏆 No data yet. Start collecting cards!")
        return

    text = "🏆 <b>SHINOBI LEADERBOARD</b>\n\n"
    medals = ["🥇", "🥈", "🥉"]

    for i, row in enumerate(rows, start=1):
        user_id = row[0]
        total = row[1]
        try:
            user = await context.bot.get_chat(user_id)
            name = user.first_name or user.username or "Unknown Shinobi"
        except:
            name = "Unknown Shinobi"
        medal = medals[i - 1] if i <= 3 else f"🔥 {i}."
        text += f"{medal} <b>{name}</b> — 🎴 {total} cards\n"

    await update.message.reply_text(text, parse_mode="HTML")


# ================= ADMIN ADD CARD =================

async def addcard(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.effective_user or not update.message:
        return

    if not is_admin(update.effective_user.id):
        await update.message.reply_text("🚫 Admin only command.")
        return

    if not context.args or len(context.args) < 2:
        await update.message.reply_text(
            "Usage:\n"
            "/addcard <name> <rarity>\n"
            "/addcard #<custom_id> <name> <rarity>\n\n"
            "Example:\n"
            "/addcard Naruto Mythic\n"
            "/addcard #25 Naruto Mythic\n\n"
            "Rarities: Common Rare Epic Legendary Mythic Spring Crossverse\n"
            "Special (admin /add only):\n"
            "💿 Disc — send a video/animation\n"
            "🐉 Dragon — leaderboard card, send a photo"
        )
        return

    args = list(context.args)
    custom_id = None

    # Check if first arg is a custom id like #25 or 25
    if args[0].startswith("#") or args[0].isdigit():
        try:
            custom_id = int(args[0].replace("#", ""))
            args = args[1:]  # remove id from args
        except ValueError:
            pass

    if len(args) < 2:
        await update.message.reply_text("❌ Please provide both name and rarity.")
        return

    raw_name = " ".join(args[:-1])
    rarity = args[-1].capitalize()
    display_name = make_display_name(raw_name)
    search_name = normalize(raw_name)

    if rarity not in RARITY_WEIGHTS:
        await update.message.reply_text(
            f"❌ Invalid rarity. Choose from: {', '.join(RARITY_WEIGHTS.keys())}"
        )
        return

    file_id = None
    file_type = "photo"  # default

    if update.message.photo:
        file_id = update.message.photo[-1].file_id
        file_type = "photo"
    elif update.message.video:
        file_id = update.message.video.file_id
        file_type = "video"
    elif update.message.animation:
        file_id = update.message.animation.file_id
        file_type = "animation"
    elif update.message.reply_to_message:
        replied = update.message.reply_to_message
        if replied.photo:
            file_id = replied.photo[-1].file_id
            file_type = "photo"
        elif replied.video:
            file_id = replied.video.file_id
            file_type = "video"
        elif replied.animation:
            file_id = replied.animation.file_id
            file_type = "animation"

    if not file_id:
        if rarity == "Animation":
            await update.message.reply_text(
                "📸 For 💿 Disc rarity, send a *video/animation* with the command as caption,\n"
                "or reply to a video/animation with the command.\n\n"
                "For other rarities send a photo.",
                parse_mode="Markdown"
            )
        else:
            await update.message.reply_text(
                "📸 Send a photo with the command as caption\n"
                "OR reply to a photo with the command."
            )
        return

    # Warn if wrong file type for Disc
    if rarity == "Animation" and file_type == "photo":
        await update.message.reply_text(
            "⚠️ 💿 Disc rarity should use a *video or animation*, not a photo.\n"
            "Please resend with a video file.",
            parse_mode="Markdown"
        )
        return

    # If custom_id provided, check it's not taken — that's the only block
    if custom_id is not None:
        cursor.execute("SELECT id, name FROM cards WHERE id=?", (custom_id,))
        taken = cursor.fetchone()
        if taken:
            await update.message.reply_text(
                f"❌ ID #{custom_id} is already used by \'{taken[1]}\'.\n"
                f"Choose a different ID or omit it for auto-assignment."
            )
            return
        cursor.execute(
            "INSERT INTO cards (id, name, rarity, file_id, file_type) VALUES (?, ?, ?, ?, ?)",
            (custom_id, display_name, rarity, file_id, file_type),
        )
    else:
        cursor.execute(
            "INSERT INTO cards (name, rarity, file_id, file_type) VALUES (?, ?, ?, ?)",
            (display_name, rarity, file_id, file_type),
        )

    conn.commit()
    new_id = custom_id if custom_id else cursor.lastrowid

    await update.message.reply_text(
        f"✅ Card \'{display_name}\' ({rarity}) added!\n🆔 Card ID: #{new_id}"
    )
    emoji = RARITY_EMOJI.get(rarity, "⭐")
    await log_to_group(
        context.bot,
        f"📥 *Card Added*\n\n"
        f"🆔 ID: #{new_id}\n"
        f"🥷 Name: {display_name}\n"
        f"{emoji} Rarity: {rarity}\n"
        f"👤 By: {update.effective_user.first_name} (ID: {update.effective_user.id})",
        photo_file_id=file_id
    )


# ===== Del card =======
async def delcard(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.effective_user or not update.message:
        return

    if not is_admin(update.effective_user.id):
        await update.message.reply_text("🚫 Admin only command.")
        return

    # Support delete by ID: /delcard #25
    if context.args and (context.args[0].startswith("#") or context.args[0].isdigit()):
        try:
            card_id = int(context.args[0].replace("#", ""))
            cursor.execute("SELECT id, name, rarity FROM cards WHERE id=?", (card_id,))
            card = cursor.fetchone()
            if not card:
                await update.message.reply_text(f"❌ No card found with ID #{card_id}!")
                return
            cursor.execute("DELETE FROM cards WHERE id=?", (card_id,))
            conn.commit()
            emoji = RARITY_EMOJI.get(card[2], "⭐")
            await update.message.reply_text(
                f"🗑️ Deleted: \'{card[1]}\' {emoji} {card[2]} (ID #{card[0]})"
            )
            await log_to_group(
                context.bot,
                f"🗑️ *Card Deleted*\n\n"
                f"🆔 ID: #{card[0]}\n"
                f"🥷 Name: {card[1]}\n"
                f"{emoji} Rarity: {card[2]}\n"
                f"👤 By: {update.effective_user.first_name} (ID: {update.effective_user.id})"
            )
            return
        except ValueError:
            pass

    # Delete by name + rarity: /delcard Naruto Mythic
    if not context.args or len(context.args) < 2:
        await update.message.reply_text(
            "Usage:\n"
            "/delcard #<id>  — delete by ID\n"
            "/delcard <name> <rarity>  — delete by name and rarity\n\n"
            "Example:\n"
            "/delcard #25\n"
            "/delcard Naruto Mythic"
        )
        return

    rarity = context.args[-1].capitalize()
    raw_name = " ".join(context.args[:-1])
    search_name = normalize(raw_name)

    if rarity not in RARITY_WEIGHTS:
        await update.message.reply_text(
            f"❌ Invalid rarity. Rarities: {', '.join(RARITY_WEIGHTS.keys())}"
        )
        return

    cursor.execute("SELECT id, name, rarity FROM cards WHERE rarity=?", (rarity,))
    cards = cursor.fetchall()

    card = None
    for c in cards:
        if normalize(str(c[1])) == search_name:
            card = c
            break

    if not card:
        await update.message.reply_text(
            f"❌ Card \'{raw_name}\' with rarity {rarity} not found!\n"
            f"Use /list_cards to check exact names and IDs."
        )
        return

    cursor.execute("DELETE FROM cards WHERE id=?", (card[0],))
    conn.commit()
    emoji = RARITY_EMOJI.get(card[2], "⭐")
    await update.message.reply_text(
        f"🗑️ Deleted: \'{card[1]}\' {emoji} {card[2]} (ID #{card[0]})"
    )
    await log_to_group(
        context.bot,
        f"🗑️ *Card Deleted*\n\n"
        f"🆔 ID: #{card[0]}\n"
        f"🥷 Name: {card[1]}\n"
        f"{emoji} Rarity: {card[2]}\n"
        f"👤 By: {update.effective_user.first_name} (ID: {update.effective_user.id})"
    )


# ================= EDIT CARD =================

async def editcard(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.effective_user or not update.message:
        return

    if not is_admin(update.effective_user.id):
        await update.message.reply_text("🚫 Admin only command.")
        return

    if not context.args or len(context.args) < 2:
        await update.message.reply_text(
            "Usage:\n"
            "/editcard #<id> name <new name>\n"
            "/editcard #<id> rarity <new rarity>\n"
            "/editcard #<id> photo  (reply to a photo)\n\n"
            "Examples:\n"
            "/editcard #25 name Naruto Uzumaki\n"
            "/editcard #25 rarity Legendary\n"
            "/editcard #25 photo  (reply to new photo)"
        )
        return

    # Parse card id
    try:
        card_id = int(context.args[0].replace("#", ""))
    except ValueError:
        await update.message.reply_text("❌ First argument must be card ID like #25 or 25")
        return

    # Check card exists
    cursor.execute("SELECT id, name, rarity, file_id FROM cards WHERE id=?", (card_id,))
    card = cursor.fetchone()
    if not card:
        await update.message.reply_text(f"❌ No card found with ID #{card_id}")
        return

    field = context.args[1].lower()

    if field == "name":
        if len(context.args) < 3:
            await update.message.reply_text("❌ Provide a new name. Example: /editcard #25 name Naruto Uzumaki")
            return
        new_name = make_display_name(" ".join(context.args[2:]))
        cursor.execute("UPDATE cards SET name=? WHERE id=?", (new_name, card_id))
        conn.commit()
        await update.message.reply_text(
            f"✅ Card #{card_id} name updated!\n"
            f"📝 Old: {card[1]}\n"
            f"📝 New: {new_name}"
        )

    elif field == "rarity":
        if len(context.args) < 3:
            await update.message.reply_text("❌ Provide a new rarity. Example: /editcard #25 rarity Legendary")
            return
        new_rarity = context.args[2].capitalize()
        if new_rarity not in RARITY_WEIGHTS:
            await update.message.reply_text(
                f"❌ Invalid rarity. Choose from: {', '.join(RARITY_WEIGHTS.keys())}"
            )
            return
        cursor.execute("UPDATE cards SET rarity=? WHERE id=?", (new_rarity, card_id))
        conn.commit()
        old_emoji = RARITY_EMOJI.get(card[2], "⭐")
        new_emoji = RARITY_EMOJI.get(new_rarity, "⭐")
        await update.message.reply_text(
            f"✅ Card #{card_id} rarity updated!\n"
            f"{old_emoji} Old: {card[2]}\n"
            f"{new_emoji} New: {new_rarity}"
        )

    elif field == "photo":
        new_file_id = None
        if update.message.reply_to_message and update.message.reply_to_message.photo:
            new_file_id = update.message.reply_to_message.photo[-1].file_id
        elif update.message.photo:
            new_file_id = update.message.photo[-1].file_id

        if not new_file_id:
            await update.message.reply_text(
                "📸 Reply to a photo with /editcard #<id> photo to update the card image."
            )
            return
        cursor.execute("UPDATE cards SET file_id=? WHERE id=?", (new_file_id, card_id))
        conn.commit()
        await update.message.reply_text(f"✅ Card #{card_id} \'{card[1]}\' photo updated!")

    else:
        await update.message.reply_text(
            "❌ Unknown field. Use: name, rarity, or photo\n\n"
            "Examples:\n"
            "/editcard #25 name Naruto Uzumaki\n"
            "/editcard #25 rarity Legendary\n"
            "/editcard #25 photo  (reply to photo)"
        )


# ================= COOLDOWN CHECK =================

async def cooldown(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.effective_user or not update.message:
        return

    user_id = update.effective_user.id
    now = time.time()

    if user_id in blocked_users and now < blocked_users[user_id]:
        remaining = int(blocked_users[user_id] - now)
        mins = remaining // 60
        secs = remaining % 60
        await update.message.reply_text(
            f"⛔ *You are currently muted!*\n\n"
            f"🕐 Time remaining: *{mins}m {secs}s*\n\n"
            f"🌀 Reason: Sending too many messages too quickly.\n"
            f"😤 Calm down, shinobi. Your jutsu will return soon.",
            parse_mode="Markdown"
        )
    else:
        await update.message.reply_text(
            f"✅ *You are not muted!*\n\n"
            f"🟢 Status: Clean — no spam detected on your account.\n"
            f"⚡ You are free to use all bot commands.\n\n"
            f"🍃 Stay sharp, shinobi!",
            parse_mode="Markdown"
        )


# ================= START =================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.effective_user:
        return

    user_id = update.effective_user.id
    user_name = update.effective_user.first_name or "Shinobi"
    username = update.effective_user.username or ""

    # ── Register user in bot_users for broadcast ─────────────────────────────
    cursor.execute("""
        INSERT INTO bot_users (user_id, first_name, username, joined_at)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(user_id) DO UPDATE SET
            first_name = excluded.first_name,
            username = excluded.username
    """, (user_id, user_name, username, int(time.time())))
    conn.commit()

    # ── If used in a group, DM them and tell them to go to DM ─────────────────
    if update.effective_chat and update.effective_chat.type in ("group", "supergroup"):
        try:
            await context.bot.send_message(
                chat_id=user_id,
                text=(
                    f"👋 *Hey {user_name}! Welcome to Naruto TCG Bot!* 🍃\n\n"
                    f"✅ You're all set! You can now use all bot commands right here in DM.\n\n"
                    f"🌸 *Get started:*\n"
                    f"• /daily — Claim your daily card + 5 vouchers\n"
                    f"• /draft_event — Spin the Spring Blossom gacha!\n"
                    f"• /collection — View your cards\n\n"
                    f"🏯 *Join our official group:*\n"
                    f"👉 [Naruto TCG Cards](https://t.me/narutotcgcards)\n\n"
                    f"⚔️ Your shinobi journey begins now!"
                ),
                parse_mode="Markdown",
                disable_web_page_preview=True
            )
            await update.message.reply_text(
                f"✅ {user_name}, I've sent you a DM! Check your messages to get started. 🍃\n"
                f"👉 Also join our group: https://t.me/narutotcgcards"
            )
        except Exception:
            await update.message.reply_text(
                f"👋 *Hey {user_name}!*\n\n"
                f"📩 To use all features, please *send me a private message* first!\n\n"
                f"👉 [Click here to start the bot](https://t.me/Naruto_tcg_bot)\n\n"
                f"🏯 Also join our official group:\n"
                f"https://t.me/narutotcgcards",
                parse_mode="Markdown",
                disable_web_page_preview=False
            )
        return

    # ── DM: Handle referral deep link ─────────────────────────────────────────
    ref_code = None
    if context.args:
        arg = context.args[0]
        if arg.startswith("REF"):
            ref_code = arg

    if ref_code:
        cursor.execute("SELECT referred_by FROM referrals WHERE user_id=?", (user_id,))
        existing = cursor.fetchone()

        if existing is None:
            cursor.execute("SELECT user_id FROM referrals WHERE referral_code=?", (ref_code,))
            referrer_row = cursor.fetchone()
            if referrer_row and referrer_row[0] != user_id:
                referrer_id = referrer_row[0]
                cursor.execute(
                    "INSERT OR IGNORE INTO referrals (user_id, referral_code, referred_by, total_referrals) VALUES (?,?,?,0)",
                    (user_id, get_or_create_referral_code(user_id), referrer_id)
                )
                cursor.execute(
                    "UPDATE referrals SET total_referrals = total_referrals + 1 WHERE user_id=?",
                    (referrer_id,)
                )
                cursor.execute(
                    "INSERT INTO referral_uses (referrer_id, referred_id, timestamp) VALUES (?,?,?)",
                    (referrer_id, user_id, int(time.time()))
                )
                conn.commit()

                new_count = get_referral_count(referrer_id)
                await give_referral_reward(context.bot, referrer_id, new_count)

                try:
                    referrer_chat = await context.bot.get_chat(referrer_id)
                    referrer_name = referrer_chat.first_name or "a friend"
                except:
                    referrer_name = "a friend"

                await update.message.reply_text(
                    f"🌸 *Welcome to Naruto TCG Bot, {user_name}!*\n\n"
                    f"🎉 You joined via *{referrer_name}'s* referral link!\n"
                    f"They have received a special reward. 🎁\n\n"
                    f"🏯 Join our official group:\n"
                    f"👉 https://t.me/narutotcgcards\n\n"
                    f"Let's start your shinobi journey! ⚔️",
                    parse_mode="Markdown",
                    disable_web_page_preview=True
                )
                await log_to_group(
                    context.bot,
                    f"🔗 *Referral Registered*\n\n"
                    f"👤 New User: {user_name} (`{user_id}`)\n"
                    f"👑 Referrer: `{referrer_id}` (now {new_count} referrals)"
                )
                return

    # ── Normal DM start ───────────────────────────────────────────────────────
    await update.message.reply_text(
        f"🍃 *Welcome to Naruto TCG Bot, {user_name}!* 🍃\n\n"
        f"⚔️ *The ultimate Naruto card collecting experience on Telegram!*\n\n"
        f"{'━' * 24}\n"
        f"📦 *Collect & Battle:*\n"
        f"• /daily — Daily card + 5 Draft Vouchers 🎟️\n"
        f"• /weekly — +5 Draft Vouchers 🎟️\n"
        f"• /monthly — +10 Draft Vouchers 🎟️\n"
        f"• /pack — Open a card pack _(5/day)_\n"
        f"• /collection — View your shinobi vault\n"
        f"• /profile — Your shinobi profile & stats\n"
        f"• /leaderboard — Top collectors ranking\n\n"
        f"🥷 *Card Actions:*\n"
        f"• /catch <name> — Catch a spawned shinobi\n"
        f"• /gift <card\\_id> — Gift a card to someone\n"
        f"• /fav <card\\_id> — Set your favourite shinobi\n"
        f"• /redeem <code> — Redeem a card code\n\n"
        f"{'━' * 24}\n"
        f"🌸 *Spring Blossom Draft Event:*\n"
        f"• /draft\\_event — Spin the gacha! 🎰\n"
        f"• /my\\_vouchers — Check vouchers & pity\n\n"
        f"🔗 *Referral System:*\n"
        f"• /referral — Share your link & earn rewards!\n\n"
        f"{'━' * 24}\n"
        f"🏯 *Official Community:*\n"
        f"👉 https://t.me/narutotcgcards\n"
        f"{'━' * 24}\n\n"
        f"🔥 *Your shinobi journey begins NOW!*\n"
        f"Start with /daily to claim your first card! 🃏",
        parse_mode="Markdown",
        disable_web_page_preview=True
    )


# ================= INLINE CARD VIEWER =================

async def inline_card_query(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.inline_query
    if not query:
        return

    user_id = query.from_user.id
    user_name = query.from_user.first_name or "Shinobi"
    raw = query.query.strip()
    raw_lower = raw.lower()

    results = []

    # ── Mode 1: mycards_<user_id> → show that user's collection ──────
    if raw_lower.startswith("mycards_"):
        try:
            target_id = int(raw_lower.split("mycards_")[1])
        except Exception:
            return
        # Any user can view; they just can't modify someone else's collection
        cursor.execute(
            "SELECT card_id, card_name, rarity, file_id FROM user_cards WHERE user_id=? ORDER BY card_id DESC",
            (target_id,)
        )
        rows = cursor.fetchall()
        seen = set()
        for row in rows:
            card_id, name, rarity, file_id = row
            key = f"{normalize(name)}_{rarity}"
            if key in seen:
                continue
            seen.add(key)
            emoji = RARITY_EMOJI.get(rarity, "⭐")
            cursor.execute(
                "SELECT COUNT(DISTINCT user_id) FROM user_cards WHERE card_name=? AND rarity=?",
                (name, rarity)
            )
            global_count = cursor.fetchone()[0]
            caption = (
                f"✅ {user_name}, your shinobi!\n\n"
                f"⚔️ A powerful warrior from your collection!\n\n"
                f"🌸 𝗡𝗔𝗠𝗘: {name.title()} [{emoji}]\n"
                f"🔮 𝗥𝗔𝗥𝗜𝗧𝗬: {rarity}\n"
                f"❇️ 𝗦𝗢𝗨𝗥𝗖𝗘: Naruto Classic\n\n"
                f"🆔 Card ID: #{card_id}\n"
                f"🌐 Globally collected by: {global_count} player(s)"
            )
            keyboard = InlineKeyboardMarkup([[
                InlineKeyboardButton(
                    f"🌐 {global_count} collector(s) worldwide",
                    callback_data=f"gc_{card_id}"
                )
            ]])
            try:
                results.append(
                    InlineQueryResultCachedPhoto(
                        id=f"my_{card_id}",
                        photo_file_id=file_id,
                        caption=caption,
                        title=f"#{card_id} {emoji} {name.title()} — {rarity}",
                        reply_markup=keyboard
                    )
                )
            except Exception as e:
                print(f"[INLINE] mycards error: {e}")
            if len(results) >= 50:
                break

    # ── Mode 2: rarity:<name> → show all bot cards of that rarity ─────────
    elif raw_lower.startswith("rarity:"):
        rarity_search = raw[7:].strip().capitalize()
        cursor.execute(
            "SELECT id, name, rarity, file_id FROM cards WHERE rarity=? ORDER BY id",
            (rarity_search,)
        )
        rows = cursor.fetchall()
        for row in rows:
            card_id, name, rarity, file_id = row
            emoji = RARITY_EMOJI.get(rarity, "⭐")
            cursor.execute(
                "SELECT COUNT(DISTINCT user_id) FROM user_cards WHERE card_name=? AND rarity=?",
                (name, rarity)
            )
            global_count = cursor.fetchone()[0]
            caption = (
                f"{emoji} *{name.title()}*\n"
                f"🔮 Rarity: {rarity}\n"
                f"🌐 Collected by: {global_count} player(s)"
            )
            try:
                results.append(
                    InlineQueryResultCachedPhoto(
                        id=f"r_{card_id}",
                        photo_file_id=file_id,
                        caption=caption,
                        title=f"{emoji} {name.title()} — {rarity}"
                    )
                )
            except Exception as e:
                print(f"[INLINE] rarity error: {e}")
            if len(results) >= 50:
                break

    # ── Mode 3: empty or text → search all bot cards by name/rarity ───────
    else:
        cursor.execute("SELECT id, name, rarity, file_id FROM cards ORDER BY id")
        rows = cursor.fetchall()
        for row in rows:
            card_id, name, rarity, file_id = row
            if raw_lower and raw_lower not in normalize(name) and raw_lower not in rarity.lower():
                continue
            emoji = RARITY_EMOJI.get(rarity, "⭐")
            cursor.execute(
                "SELECT COUNT(DISTINCT user_id) FROM user_cards WHERE card_name=? AND rarity=?",
                (name, rarity)
            )
            global_count = cursor.fetchone()[0]
            caption = (
                f"{emoji} *{name.title()}*\n"
                f"🔮 Rarity: {rarity}\n"
                f"🌐 Collected by: {global_count} player(s)"
            )
            try:
                results.append(
                    InlineQueryResultCachedPhoto(
                        id=f"s_{card_id}",
                        photo_file_id=file_id,
                        caption=caption,
                        title=f"{emoji} {name.title()} — {rarity}"
                    )
                )
            except Exception as e:
                print(f"[INLINE] search error: {e}")
            if len(results) >= 50:
                break

    try:
        await query.answer(results, cache_time=10, is_personal=True, button=None)
    except Exception as e:
        print(f"[INLINE] Answer error: {e}")


# ── Global collector count callback ──────────────────────────────────────

async def global_collector_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    try:
        await query.answer()
    except Exception:
        pass
    data = query.data
    if not data.startswith("gc_"):
        return
    try:
        card_id = int(data[3:])
    except ValueError:
        return
    cursor.execute("SELECT card_name, rarity FROM user_cards WHERE id=?", (card_id,))
    row = cursor.fetchone()
    if not row:
        # try cards table
        cursor.execute("SELECT name, rarity FROM cards WHERE id=?", (card_id,))
        row = cursor.fetchone()
    if not row:
        try:
            await query.answer("❌ Card not found.", show_alert=True)
        except Exception:
            pass
        return
    name, rarity = row
    cursor.execute(
        "SELECT COUNT(DISTINCT user_id) FROM user_cards WHERE card_name=? AND rarity=?",
        (name, rarity)
    )
    count = cursor.fetchone()[0]
    try:
        await query.answer(
            f"🌐 '{name.title()}' is collected by {count} player(s) globally!",
            show_alert=True
        )
    except Exception:
        pass


# ================= ERROR HANDLER =================

async def error_handler(update, context):
    error = context.error
    error_str = str(error)

    # Silently ignore stop_propagation (used by DM-gate)
    from telegram.ext.filters import _MessageFilter
    try:
        from telegram.ext import ApplicationHandlerStop
        if isinstance(error, ApplicationHandlerStop):
            return
    except ImportError:
        pass

    if any(x in error_str for x in [
        "ReadError", "ConnectError", "No address associated",
        "TimedOut", "ReadTimeout", "Timed out", "httpcore", "httpx"
    ]):
        print(f"[NET] Timeout/network hiccup (auto-retry): {type(error).__name__}")
        return

    if "There is no text in the message" in error_str:
        print(f"[WARN] Edit mismatch (handled): {error_str}")
        return

    if "Not enough rights to send text messages" in error_str:
        print(f"[WARN] No text permission in chat — spawn will skip animation.")
        return

    if "query id is invalid" in error_str or "Query is too old" in error_str:
        print(f"[WARN] Old callback query ignored.")
        return

    print(f"[ERROR] {error}")


# ================= FORCE SPAWN =================

async def forcespawn(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.effective_chat:
        return

    if not is_admin(update.effective_user.id):
        await update.message.reply_text("🚫 Admin only command.")
        return

    forced_rarity = None

    if context.args:
        rarity_input = context.args[0].capitalize()
        if rarity_input not in RARITY_WEIGHTS:
            await update.message.reply_text(
                f"❌ Invalid rarity.\nChoose from: {', '.join(RARITY_WEIGHTS.keys())}"
            )
            return
        forced_rarity = rarity_input

    await spawn_card(update, context, forced_rarity=forced_rarity)


# ================= BACKUP =================

async def backup(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.effective_user or not update.message:
        return

    if not is_admin(update.effective_user.id):
        await update.message.reply_text("🚫 Admin only command.")
        return

    db_path = "/data/cards.db"

    if os.path.exists(db_path):
        await update.message.reply_text("📦 Preparing backup...")
        await update.message.reply_document(
            document=open(db_path, "rb"),
            filename="cards_backup.db",
            caption=(
                "✅ *Database Backup*\n\n"
                "📁 File: `cards_backup.db`\n"
                "🕐 All user data, cards and codes included.\n\n"
                "⚠️ Keep this file safe!"
            ),
            parse_mode="Markdown"
        )
    else:
        await update.message.reply_text("❌ Database file not found.")


# ================= CHANGETIME =================

async def changetime(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.effective_chat or not update.effective_user or not update.message:
        return

    chat_id = update.effective_chat.id
    user_id = update.effective_user.id

    if is_banned(user_id):
        await update.message.reply_text(get_ban_message(user_id), parse_mode="Markdown")
        return

    # Only group admins or bot admin can use this
    is_bot_admin = is_admin(user_id)

    if not is_bot_admin:
        try:
            member = await context.bot.get_chat_member(chat_id, user_id)
            if member.status not in ("administrator", "creator"):
                await update.message.reply_text(
                    "🚫 Only group admins can change the spawn time."
                )
                return
        except Exception:
            await update.message.reply_text("🚫 Could not verify your admin status.")
            return

    if not context.args:
        mn, mx = get_spawn_range(chat_id)
        await update.message.reply_text(
            f"⏱️ *Current Spawn Setting*\n\n"
            f"📊 Messages to spawn: *{mn} — {mx}*\n\n"
            f"Usage: `/changetime <30-500>`\n"
            f"Example: `/changetime 100` → spawns every ~90–100 messages",
            parse_mode="Markdown"
        )
        return

    try:
        value = int(context.args[0])
    except ValueError:
        await update.message.reply_text("❌ Please enter a valid number.\nUsage: /changetime <30-500>", parse_mode="Markdown")
        return

    if value < 30 or value > 500:
        await update.message.reply_text(
            "⚠️ ᴛʜᴇ ɴᴇᴡ ᴛɪᴍᴇ ᴍᴜsᴛ ʙᴇ ʙᴇᴛᴡᴇᴇɴ 𝟹𝟶 ᴀɴᴅ 𝟻𝟶𝟶 ᴍᴇssᴀɢᴇs"
        )
        return

    chat_spawn_settings[chat_id] = value
    low = max(1, value - 10)

    await update.message.reply_text(
        f"✅ *Spawn Time Updated!*\n\n"
        f"⚡ New spawn range: *{low} — {value}* messages\n"
        f"🍃 The next shinobi will appear after *~{value}* messages in this group.\n\n"
        f"🔄 Takes effect on the next spawn cycle.",
        parse_mode="Markdown"
    )


# ================= PROFILE =================

async def profile(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.effective_user or not update.message:
        return

    user_id = update.effective_user.id
    if not has_started_bot(user_id) and not is_admin(user_id):
        await update.message.reply_text(NOT_STARTED_MSG, parse_mode="Markdown", disable_web_page_preview=True)
        return

    user_name = update.effective_user.first_name or "Shinobi"

    # Get favourite card
    cursor.execute(
        "SELECT card_name, rarity, file_id, COALESCE(file_type,'photo') FROM user_cards WHERE user_id=? AND is_favorite=1 LIMIT 1",
        (user_id,)
    )
    fav_card = cursor.fetchone()

    # Get total cards per rarity
    cursor.execute(
        "SELECT rarity, COUNT(*) FROM user_cards WHERE user_id=? GROUP BY rarity",
        (user_id,)
    )
    rarity_counts = dict(cursor.fetchall())

    total = sum(rarity_counts.values())

    if total == 0:
        await update.message.reply_text(
            f"📭 *{user_name}* has no shinobi yet!\n\n"
            f"Use /daily or /pack to start your collection.",
            parse_mode="Markdown"
        )
        return

    # Get user rank in leaderboard
    cursor.execute(
        "SELECT user_id, COUNT(*) as total FROM user_cards GROUP BY user_id ORDER BY total DESC"
    )
    all_users = cursor.fetchall()
    rank = next((i+1 for i, r in enumerate(all_users) if r[0] == user_id), None)
    total_users = len(all_users)

    # Build rarity breakdown
    rarity_lines = ""
    for rarity in ["Crossverse", "Spring", "Mythic", "Legendary", "Epic", "Rare", "Common"]:
        emoji = RARITY_EMOJI.get(rarity, "⭐")
        count = rarity_counts.get(rarity, 0)
        if count > 0:
            rarity_lines += f"{emoji} {rarity}: *{count}* cards\n"

    # Rank medal
    if rank == 1:
        rank_display = "🥇 #1 — Top Collector!"
    elif rank == 2:
        rank_display = "🥈 #2 — Elite Shinobi!"
    elif rank == 3:
        rank_display = "🥉 #3 — Rising Legend!"
    elif rank and rank <= 10:
        rank_display = f"🔥 #{rank} — Top 10 Shinobi!"
    else:
        rank_display = f"⚔️ #{rank} out of {total_users} shinobis"

    profile_text = (
        f"🌀 *{user_name}\'s Shinobi Profile* 🌀\n\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🃏 *Total Cards:* {total}\n"
        f"🏆 *Rank:* {rank_display}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"📊 *Collection Breakdown:*\n"
        f"{rarity_lines}"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
    )

    if fav_card:
        fav_name, fav_rarity, fav_file_id, fav_file_type = fav_card
        fav_emoji = RARITY_EMOJI.get(fav_rarity, "⭐")
        profile_text += f"⭐ *Favourite:* {fav_name.title()} [{fav_emoji}]\n"
        profile_text += f"━━━━━━━━━━━━━━━━━━━━━━\n"
        profile_text += f"🍃 Keep collecting, shinobi!"

        await send_card_media(update.message, fav_file_id, fav_file_type, caption=profile_text, parse_mode="Markdown")
    else:
        profile_text += f"💡 *Tip:* Use /fav <card_id> to set your favourite shinobi!\n"
        profile_text += f"━━━━━━━━━━━━━━━━━━━━━━\n"
        profile_text += f"🍃 Keep collecting, shinobi!"

        await update.message.reply_text(profile_text, parse_mode="Markdown")


# ================= ADMIN COMMANDS =================

async def admins(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.effective_user or not update.message:
        return
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("🚫 Only the owner can use this.")
        return
    cursor.execute("SELECT user_id, added_at FROM admins")
    rows = cursor.fetchall()
    text = "👑 *Admin List*\n\n"
    text += f"🔱 Owner: `{OWNER_ID}`\n"
    text += f"🛡️ ×͜× ł₮'₴ M̷Ɇ̷ (`{CO_OWNER_ID}`)\n\n"
    if rows:
        for row in rows:
            try:
                user = await context.bot.get_chat(row[0])
                name = user.first_name or user.username or str(row[0])
            except:
                name = str(row[0])
            text += f"🛡️ {name} (`{row[0]}`)\n"
    else:
        text += "No additional admins added yet."
    await update.message.reply_text(text, parse_mode="Markdown")


async def addadmin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.effective_user or not update.message:
        return
    if update.effective_user.id != OWNER_ID:
        await update.message.reply_text("🚫 Only the owner can add admins.")
        return
    if not context.args:
        await update.message.reply_text("Usage: /addadmin <user_id>")
        return
    try:
        new_admin_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("❌ Invalid user ID.")
        return
    if new_admin_id == OWNER_ID:
        await update.message.reply_text("❌ You are already the owner.")
        return
    if new_admin_id == CO_OWNER_ID:
        await update.message.reply_text("❌ That user is already a co-owner.")
        return
    cursor.execute("SELECT user_id FROM admins WHERE user_id=?", (new_admin_id,))
    if cursor.fetchone():
        await update.message.reply_text(f"⚠️ User `{new_admin_id}` is already an admin.", parse_mode="Markdown")
        return
    cursor.execute(
        "INSERT INTO admins (user_id, added_by, added_at) VALUES (?,?,?)",
        (new_admin_id, OWNER_ID, int(time.time()))
    )
    conn.commit()
    await update.message.reply_text(f"✅ User `{new_admin_id}` added as admin!", parse_mode="Markdown")
    await log_to_group(
        context.bot,
        f"🛡️ *New Admin Added*\n\n"
        f"👤 User ID: `{new_admin_id}`\n"
        f"👑 Added by: {update.effective_user.first_name} (`{OWNER_ID}`)"
    )


async def removeadmin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.effective_user or not update.message:
        return
    if update.effective_user.id != OWNER_ID:
        await update.message.reply_text("🚫 Only the owner can remove admins.")
        return
    if not context.args:
        await update.message.reply_text("Usage: /removeadmin <user_id>")
        return
    try:
        rem_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("❌ Invalid user ID.")
        return
    if rem_id == CO_OWNER_ID:
        await update.message.reply_text("❌ Cannot remove the co-owner.")
        return
    cursor.execute("DELETE FROM admins WHERE user_id=?", (rem_id,))
    conn.commit()
    if cursor.rowcount == 0:
        await update.message.reply_text(f"⚠️ User `{rem_id}` is not an admin.", parse_mode="Markdown")
        return
    await update.message.reply_text(f"✅ Removed `{rem_id}` from admins.", parse_mode="Markdown")
    await log_to_group(
        context.bot,
        f"❌ *Admin Removed*\n\n"
        f"👤 User ID: `{rem_id}`\n"
        f"👑 Removed by: {update.effective_user.first_name} (`{OWNER_ID}`)"
    )


async def broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.effective_user or not update.message:
        return
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("🚫 Admins only.")
        return

    # ── Determine message content ─────────────────────────────────────────────
    msg_text = ""
    photo_file_id = None

    # Check if replying to a photo (with or without caption)
    if update.message.reply_to_message:
        replied = update.message.reply_to_message
        if replied.photo:
            photo_file_id = replied.photo[-1].file_id
            # Caption from the reply or from args
            if context.args:
                msg_text = " ".join(context.args)
            elif replied.caption:
                msg_text = replied.caption
        elif replied.text:
            msg_text = replied.text
    else:
        # No reply — must have args as text
        if context.args:
            msg_text = " ".join(context.args)

    # If this message itself is a photo with caption /broadcast
    if not photo_file_id and update.message.photo:
        photo_file_id = update.message.photo[-1].file_id
        msg_text = update.message.caption or msg_text

    if not msg_text and not photo_file_id:
        await update.message.reply_text(
            "❌ Please provide a message or reply to an image.\n\n"
            "Usage:\n"
            "• `/broadcast <text>` — text broadcast\n"
            "• Reply to an image with `/broadcast <caption>` — image broadcast\n"
            "• Reply to an image with `/broadcast` — broadcast image with its caption",
            parse_mode="Markdown"
        )
        return

    # ── Collect ALL users: bot_users + user_cards (union so no one is missed) ──
    cursor.execute("SELECT DISTINCT user_id FROM bot_users")
    users_set = set(r[0] for r in cursor.fetchall())
    cursor.execute("SELECT DISTINCT user_id FROM user_cards")
    users_set.update(r[0] for r in cursor.fetchall())
    users = list(users_set)

    await update.message.reply_text(
        f"📢 *Broadcasting to {len(users)} users...*\n"
        f"{'🖼️ With image' if photo_file_id else '📝 Text only'}",
        parse_mode="Markdown"
    )

    sent = 0
    failed = 0
    blocked = 0

    broadcast_caption = f"📢 *Announcement*\n\n{msg_text}" if msg_text else "📢 *Announcement*"

    for uid in users:
        try:
            if photo_file_id:
                await context.bot.send_photo(
                    chat_id=uid,
                    photo=photo_file_id,
                    caption=broadcast_caption,
                    parse_mode="Markdown"
                )
            else:
                await context.bot.send_message(
                    chat_id=uid,
                    text=broadcast_caption,
                    parse_mode="Markdown"
                )
            sent += 1
            await asyncio.sleep(0.05)  # Avoid Telegram flood limit
        except Exception as e:
            err = str(e).lower()
            if "blocked" in err or "deactivated" in err or "not found" in err or "forbidden" in err:
                blocked += 1
                # Remove permanently blocked users from bot_users to keep list clean
                cursor.execute("DELETE FROM bot_users WHERE user_id=?", (uid,))
                conn.commit()
            else:
                failed += 1
                print(f"[BROADCAST] Failed uid={uid}: {e}")

    await update.message.reply_text(
        f"✅ *Broadcast Complete!*\n\n"
        f"📨 Sent: *{sent}*\n"
        f"🚫 Blocked/Deleted: *{blocked}* _(removed from list)_\n"
        f"❌ Other errors: *{failed}*",
        parse_mode="Markdown"
    )
    await log_to_group(
        context.bot,
        f"📢 *Broadcast Sent*\n\n"
        f"📝 Message: {msg_text[:200] if msg_text else '(image only)'}\n"
        f"🖼️ Image: {'Yes' if photo_file_id else 'No'}\n"
        f"📨 Sent: {sent} | 🚫 Blocked: {blocked} | ❌ Errors: {failed}\n"
        f"👤 By: {update.effective_user.first_name} (`{update.effective_user.id}`)"
    )


async def ban(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Permanent ban — no expiry. Use /tban for temporary ban."""
    if not update.effective_user or not update.message:
        return
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("🚫 Admins only.")
        return
    target_id = None
    target_name = "Unknown"
    if update.message.reply_to_message:
        target_id = update.message.reply_to_message.from_user.id
        target_name = update.message.reply_to_message.from_user.first_name or str(target_id)
    elif context.args:
        try:
            target_id = int(context.args[0])
            target_name = str(target_id)
        except ValueError:
            await update.message.reply_text("❌ Invalid user ID.")
            return
    if not target_id:
        await update.message.reply_text("Usage: /ban <user_id> [reason] OR reply to a user.")
        return
    if target_id in (OWNER_ID, CO_OWNER_ID):
        await update.message.reply_text("❌ Cannot ban the owner/co-owner.")
        return
    reason = " ".join(context.args[1:]) if context.args and not update.message.reply_to_message else " ".join(context.args) if context.args else "No reason"
    now = int(time.time())
    # unban_at = NULL → permanent ban
    cursor.execute(
        "INSERT OR REPLACE INTO banned_users (user_id, banned_by, banned_at, unban_at, reason) VALUES (?,?,?,NULL,?)",
        (target_id, update.effective_user.id, now, reason)
    )
    conn.commit()
    await update.message.reply_text(
        f"🔨 *Permanent Ban Issued!*\n\n"
        f"👤 User: {target_name} (`{target_id}`)\n"
        f"📝 Reason: {reason}\n"
        f"❌ This is a *lifetime ban* — does not expire.",
        parse_mode="Markdown"
    )
    await log_to_group(
        context.bot,
        f"🔨 *PERMANENT BAN*\n\n"
        f"👤 User: {target_name} (`{target_id}`)\n"
        f"📝 Reason: {reason}\n"
        f"❌ No expiry\n"
        f"👮 By: {update.effective_user.first_name} (`{update.effective_user.id}`)"
    )


async def tban(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Temporary ban with day count. /tban <user_id> <days> [reason]"""
    if not update.effective_user or not update.message:
        return
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("🚫 Admins only.")
        return
    target_id = None
    target_name = "Unknown"
    args = list(context.args) if context.args else []
    if update.message.reply_to_message:
        target_id = update.message.reply_to_message.from_user.id
        target_name = update.message.reply_to_message.from_user.first_name or str(target_id)
    elif args:
        try:
            target_id = int(args[0])
            target_name = str(target_id)
            args = args[1:]
        except ValueError:
            await update.message.reply_text("❌ Invalid user ID.")
            return
    if not target_id:
        await update.message.reply_text(
            "Usage:\n"
            "/tban <user_id> <days> [reason]\n"
            "OR reply to user: /tban <days> [reason]"
        )
        return
    if target_id in (OWNER_ID, CO_OWNER_ID):
        await update.message.reply_text("❌ Cannot ban the owner/co-owner.")
        return
    try:
        days = int(args[0]) if args else 1
        reason = " ".join(args[1:]) if len(args) > 1 else "No reason"
    except (ValueError, IndexError):
        days = 1
        reason = "No reason"
    if days < 1:
        days = 1
    now = int(time.time())
    unban_at = now + (days * 86400)
    cursor.execute(
        "INSERT OR REPLACE INTO banned_users (user_id, banned_by, banned_at, unban_at, reason) VALUES (?,?,?,?,?)",
        (target_id, update.effective_user.id, now, unban_at, reason)
    )
    conn.commit()
    await update.message.reply_text(
        f"⏳ *Temporary Ban Issued!*\n\n"
        f"👤 User: {target_name} (`{target_id}`)\n"
        f"📝 Reason: {reason}\n"
        f"⏰ Duration: *{days} day(s)*\n"
        f"🔓 Auto-unban in {days} days.",
        parse_mode="Markdown"
    )
    await log_to_group(
        context.bot,
        f"⏳ *TEMPORARY BAN*\n\n"
        f"👤 User: {target_name} (`{target_id}`)\n"
        f"📝 Reason: {reason}\n"
        f"⏰ Duration: {days} day(s)\n"
        f"👮 By: {update.effective_user.first_name} (`{update.effective_user.id}`)"
    )


async def unban(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.effective_user or not update.message:
        return
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("🚫 Admins only.")
        return
    target_id = None
    if update.message.reply_to_message:
        target_id = update.message.reply_to_message.from_user.id
    elif context.args:
        try:
            target_id = int(context.args[0])
        except ValueError:
            await update.message.reply_text("❌ Invalid user ID.")
            return
    if not target_id:
        await update.message.reply_text("Usage: /unban <user_id> OR reply to a user's message.")
        return
    cursor.execute("DELETE FROM banned_users WHERE user_id=?", (target_id,))
    conn.commit()
    if cursor.rowcount == 0:
        await update.message.reply_text(f"⚠️ User `{target_id}` is not banned.", parse_mode="Markdown")
        return
    await update.message.reply_text(f"✅ User `{target_id}` has been unbanned.", parse_mode="Markdown")
    await log_to_group(
        context.bot,
        f"✅ *User Unbanned*\n\n"
        f"👤 User ID: `{target_id}`\n"
        f"👮 By: {update.effective_user.first_name} (`{update.effective_user.id}`)"
    )


async def mute_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.effective_user or not update.message:
        return
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("🚫 Admins only.")
        return
    target_id = None
    target_name = "Unknown"
    if update.message.reply_to_message:
        target_id = update.message.reply_to_message.from_user.id
        target_name = update.message.reply_to_message.from_user.first_name or str(target_id)
    elif context.args:
        try:
            target_id = int(context.args[0])
            target_name = str(target_id)
        except ValueError:
            await update.message.reply_text("❌ Invalid user ID.")
            return
    if not target_id:
        await update.message.reply_text("Usage: /mute <user_id> OR reply to a user's message.")
        return
    if target_id == OWNER_ID:
        await update.message.reply_text("❌ Cannot mute the owner.")
        return
    now = time.time()
    blocked_users[target_id] = now + BLOCK_DURATION
    block_notified.pop(target_id, None)
    await update.message.reply_text(
        f"🔇 *User Muted!*\n\n"
        f"👤 {target_name} (`{target_id}`) muted for 10 minutes.\n"
        f"⏳ They cannot use any bot features during this time.",
        parse_mode="Markdown"
    )
    await log_to_group(
        context.bot,
        f"🔇 *User Muted*\n\n"
        f"👤 User: {target_name} (`{target_id}`)\n"
        f"⏳ Duration: 10 minutes\n"
        f"👮 By: {update.effective_user.first_name} (`{update.effective_user.id}`)"
    )


async def convlogs(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.effective_user or not update.message:
        return
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("🚫 Admins only.")
        return
    if not context.args:
        await update.message.reply_text(
            "Usage: /convlogs <user_id>\n"
            "Shows last 20 messages from that user."
        )
        return
    try:
        target_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("❌ Invalid user ID.")
        return
    cursor.execute(
        "SELECT first_name, message_text, timestamp FROM conv_logs "
        "WHERE user_id=? ORDER BY timestamp DESC LIMIT 20",
        (target_id,)
    )
    logs = cursor.fetchall()
    if not logs:
        await update.message.reply_text(f"📭 No logs found for user `{target_id}`.", parse_mode="Markdown")
        return
    text = f"📋 *Conversation Logs*\nUser ID: `{target_id}`\n\n"
    for row in reversed(logs):
        first_name, msg_text, ts = row
        t = time.strftime("%d/%m %H:%M", time.localtime(ts))
        text += f"[{t}] {first_name}: {msg_text[:100]}\n"
    await update.message.reply_text(text, parse_mode="Markdown")



# ================= /collected =================

async def collected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.effective_user or not update.message:
        return
    user_id = update.effective_user.id
    if not has_started_bot(user_id) and not is_admin(user_id):
        await update.message.reply_text(NOT_STARTED_MSG, parse_mode="Markdown", disable_web_page_preview=True)
        return
    if is_banned(user_id):
        await update.message.reply_text(get_ban_message(user_id), parse_mode="Markdown")
        return
    user_name = update.effective_user.first_name or "Shinobi"
    cursor.execute(
        "SELECT rarity, COUNT(*) FROM user_cards WHERE user_id=? GROUP BY rarity",
        (user_id,)
    )
    user_counts = dict(cursor.fetchall())
    cursor.execute("SELECT rarity, COUNT(*) FROM cards GROUP BY rarity")
    total_counts = dict(cursor.fetchall())
    if not user_counts:
        await update.message.reply_text(
            "📭 You have no cards yet!\n\nUse /daily or /pack to start collecting."
        )
        return
    rarity_order = ["Crossverse", "Spring", "Mythic", "Legendary", "Epic", "Rare", "Common"]
    lines = ""
    for rarity in rarity_order:
        emoji = RARITY_EMOJI.get(rarity, "⭐")
        owned = user_counts.get(rarity, 0)
        total = total_counts.get(rarity, 0)
        if owned > 0 or total > 0:
            lines += f"• {emoji} {rarity}: {owned}/{total}\n"
    total_owned = sum(user_counts.values())
    text = (
        f"✨ *Your Character Collection by Rarity* ✨\n\n"
        f"👤 Shinobi: *{user_name}*\n"
        f"🃏 Total Cards: *{total_owned}*\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🔹 *Rarities:*\n{lines}"
        f"━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"✨ *Keep collecting to complete your shinobi!* ✨"
    )
    await update.message.reply_text(text, parse_mode="Markdown")


# ================= WEEKLY =================

async def weekly(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.effective_user or not update.message:
        return
    user_id = update.effective_user.id
    if not has_started_bot(user_id) and not is_admin(user_id):
        await update.message.reply_text(NOT_STARTED_MSG, parse_mode="Markdown", disable_web_page_preview=True)
        return
    if is_banned(user_id):
        await update.message.reply_text(get_ban_message(user_id), parse_mode="Markdown")
        return

    now = int(time.time())
    WEEKLY_COOLDOWN = 7 * 86400

    # Ensure table exists (safety for older DBs)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS weekly_claims (
            user_id INTEGER PRIMARY KEY,
            last_weekly INTEGER DEFAULT 0
        )
    """)
    conn.commit()

    cursor.execute("SELECT last_weekly FROM weekly_claims WHERE user_id=?", (user_id,))
    row = cursor.fetchone()
    last_weekly = row[0] if row else 0

    if last_weekly and (now - last_weekly < WEEKLY_COOLDOWN):
        remaining = WEEKLY_COOLDOWN - (now - last_weekly)
        days = remaining // 86400
        hours = (remaining % 86400) // 3600
        mins = (remaining % 3600) // 60
        await update.message.reply_text(
            f"⏳ *Weekly Reward Already Claimed!*\n\n"
            f"🕐 Come back in: *{days}d {hours}h {mins}m*\n\n"
            f"🍃 Rest now, shinobi. Your weekly vouchers await!",
            parse_mode="Markdown"
        )
        return

    cursor.execute(
        "INSERT INTO weekly_claims (user_id, last_weekly) VALUES (?,?) "
        "ON CONFLICT(user_id) DO UPDATE SET last_weekly=excluded.last_weekly",
        (user_id, now)
    )
    conn.commit()
    add_vouchers(user_id, 5)
    add_coins(user_id, COIN_WEEKLY_AMOUNT)
    conn.commit()
    total = get_user_vouchers(user_id)
    total_coins = get_coins(user_id)

    await update.message.reply_text(
        f"🎉 *Weekly Reward Claimed!*\n\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🎟️ *+5 Draft Vouchers* added!\n"
        f"💼 Total Vouchers: *{total}*\n"
        f"🪙 *+{COIN_WEEKLY_AMOUNT} Shinobi Coins* added!\n"
        f"💰 Total Coins: *{total_coins}*\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"🌸 Use /draft\\_event to spin the gacha!\n"
        f"📅 Come back in *7 days* for more rewards!",
        parse_mode="Markdown"
    )
    await log_to_group(
        context.bot,
        f"📅 *Weekly Claimed*\n👤 {update.effective_user.first_name} (`{user_id}`) got 5 vouchers"
    )


# ================= MONTHLY =================

async def monthly(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.effective_user or not update.message:
        return
    user_id = update.effective_user.id
    if not has_started_bot(user_id) and not is_admin(user_id):
        await update.message.reply_text(NOT_STARTED_MSG, parse_mode="Markdown", disable_web_page_preview=True)
        return
    if is_banned(user_id):
        await update.message.reply_text(get_ban_message(user_id), parse_mode="Markdown")
        return

    now = int(time.time())
    MONTHLY_COOLDOWN = 30 * 86400

    # Ensure table exists (safety for older DBs)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS monthly_claims (
            user_id INTEGER PRIMARY KEY,
            last_monthly INTEGER DEFAULT 0
        )
    """)
    conn.commit()

    cursor.execute("SELECT last_monthly FROM monthly_claims WHERE user_id=?", (user_id,))
    row = cursor.fetchone()
    last_monthly = row[0] if row else 0

    if last_monthly and (now - last_monthly < MONTHLY_COOLDOWN):
        remaining = MONTHLY_COOLDOWN - (now - last_monthly)
        days = remaining // 86400
        hours = (remaining % 86400) // 3600
        mins = (remaining % 3600) // 60
        await update.message.reply_text(
            f"⏳ *Monthly Reward Already Claimed!*\n\n"
            f"🕐 Come back in: *{days}d {hours}h {mins}m*\n\n"
            f"🍃 Your monthly vouchers will be ready soon!",
            parse_mode="Markdown"
        )
        return

    cursor.execute(
        "INSERT INTO monthly_claims (user_id, last_monthly) VALUES (?,?) "
        "ON CONFLICT(user_id) DO UPDATE SET last_monthly=excluded.last_monthly",
        (user_id, now)
    )
    conn.commit()
    add_vouchers(user_id, 10)
    add_coins(user_id, COIN_MONTHLY_AMOUNT)
    conn.commit()
    total = get_user_vouchers(user_id)
    total_coins = get_coins(user_id)

    await update.message.reply_text(
        f"🎉 *Monthly Reward Claimed!*\n\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🎟️ *+10 Draft Vouchers* added!\n"
        f"💼 Total Vouchers: *{total}*\n"
        f"🪙 *+{COIN_MONTHLY_AMOUNT} Shinobi Coins* added!\n"
        f"💰 Total Coins: *{total_coins}*\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"🌸 Use /draft\\_event to spin the gacha!\n"
        f"📅 Come back in *30 days* for more rewards!",
        parse_mode="Markdown"
    )
    await log_to_group(
        context.bot,
        f"📆 *Monthly Claimed*\n👤 {update.effective_user.first_name} (`{user_id}`) got 10 vouchers"
    )


# ================= MY VOUCHERS =================

async def my_vouchers(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.effective_user or not update.message:
        return
    user_id = update.effective_user.id

    if not has_started_bot(user_id) and not is_admin(user_id):
        await update.message.reply_text(NOT_STARTED_MSG, parse_mode="Markdown", disable_web_page_preview=True)
        return

    if is_banned(user_id):
        await update.message.reply_text(get_ban_message(user_id), parse_mode="Markdown")
        return

    # Ensure table exists and row exists for this user
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS draft_vouchers (
            user_id INTEGER PRIMARY KEY,
            vouchers INTEGER DEFAULT 0,
            total_spins INTEGER DEFAULT 0,
            spins_since_legendary INTEGER DEFAULT 0,
            spins_since_spring INTEGER DEFAULT 0
        )
    """)
    cursor.execute("""
        INSERT OR IGNORE INTO draft_vouchers
        (user_id, vouchers, total_spins, spins_since_legendary, spins_since_spring)
        VALUES (?, 0, 0, 0, 0)
    """, (user_id,))
    conn.commit()

    # Fetch fresh from DB
    cursor.execute("""
        SELECT vouchers, total_spins, spins_since_legendary, spins_since_spring
        FROM draft_vouchers WHERE user_id=?
    """, (user_id,))
    row = cursor.fetchone()
    v = row[0] if row else 0
    total_spins = row[1] if row else 0
    spins_since_leg = row[2] if row else 0
    spins_since_spring = row[3] if row else 0

    days_left = get_draft_days_remaining()

    # Pity progress bars
    leg_bar = "🟩" * min(spins_since_leg, 10) + "⬜" * max(0, 10 - spins_since_leg)
    spring_progress = min(spins_since_spring, 100)
    spring_filled = spring_progress // 10
    spring_bar = "🟪" * spring_filled + "⬜" * (10 - spring_filled)

    if is_draft_event_active():
        event_line = f"🟢 *Event Active!* Ends in *{days_left} days*"
    else:
        event_line = "🔴 *No active draft event right now*"

    # Voucher status
    if v == 0:
        voucher_status = "💸 Empty wallet — earn more below!"
    elif v < 5:
        voucher_status = f"💼 Almost there for a 10-spin!"
    elif v >= 10:
        voucher_status = f"🔥 Ready for a 10-spin! Go spin!"
    else:
        voucher_status = f"✅ Ready for singles!"

    user_name = update.effective_user.first_name or "Shinobi"

    await update.message.reply_text(
        f"🎴 *{user_name}'s Draft Wallet* 🎴\n\n"
        f"{'━' * 24}\n"
        f"🎟️  *Vouchers:* `{v}`\n"
        f"💬  _{voucher_status}_\n"
        f"{'━' * 24}\n\n"
        f"📊 *Spin Statistics*\n"
        f"🎰 Total Spins: *{total_spins}*\n\n"
        f"👑 *Legendary Pity:* {spins_since_leg}/10\n"
        f"{leg_bar}\n"
        f"_(Guaranteed at 10th spin!)_\n\n"
        f"🌸 *Spring Pity:* {spins_since_spring}/100\n"
        f"{spring_bar}\n"
        f"_(Guaranteed at 100th spin!)_\n\n"
        f"{'━' * 24}\n"
        f"{event_line}\n"
        f"{'━' * 24}\n\n"
        f"💰 *Earn Vouchers Daily:*\n"
        f"🌅 /daily → +5 🎟️  _(every 24h)_\n"
        f"📅 /weekly → +5 🎟️  _(every 7 days)_\n"
        f"🗓️ /monthly → +10 🎟️  _(every 30 days)_\n\n"
        f"🌀 Spin now → /draft_event",
        parse_mode="Markdown"
    )


# ================= VIEW DRAFTS =================

async def view_drafts(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /view_drafts — Instantly shows how many draft vouchers the user has,
    with cool text, emojis and a motivational status line.
    """
    if not update.effective_user or not update.message:
        return
    user_id = update.effective_user.id

    if not has_started_bot(user_id) and not is_admin(user_id):
        await update.message.reply_text(
            NOT_STARTED_MSG, parse_mode="Markdown", disable_web_page_preview=True
        )
        return

    if is_banned(user_id):
        await update.message.reply_text(get_ban_message(user_id), parse_mode="Markdown")
        return

    user_name = update.effective_user.first_name or "Shinobi"

    # Ensure row exists
    cursor.execute(
        "INSERT OR IGNORE INTO draft_vouchers "
        "(user_id, vouchers, total_spins, spins_since_legendary, spins_since_spring) "
        "VALUES (?, 0, 0, 0, 0)",
        (user_id,)
    )
    conn.commit()

    cursor.execute(
        "SELECT vouchers, total_spins, spins_since_legendary, spins_since_spring "
        "FROM draft_vouchers WHERE user_id=?",
        (user_id,)
    )
    row = cursor.fetchone()
    v              = row[0] if row else 0
    total_spins    = row[1] if row else 0
    since_leg      = row[2] if row else 0
    since_spring   = row[3] if row else 0

    # ── Motivational status based on balance ────────────────────────────────
    if v == 0:
        status_line  = "💸 *Empty wallet!* Grind harder, shinobi! 🥷"
        wallet_emoji = "🪙"
    elif v == 1:
        status_line  = "🌱 *1 voucher!* Just enough for a single spin!"
        wallet_emoji = "🎟️"
    elif v < 5:
        status_line  = f"⚡ *{v} vouchers!* Getting closer to a 10-spin!"
        wallet_emoji = "🎟️"
    elif v < 10:
        status_line  = f"🔥 *{v} vouchers!* Almost ready for a 10-spin!"
        wallet_emoji = "💼"
    elif v < 30:
        status_line  = f"💎 *{v} vouchers!* You're loaded — go spin!"
        wallet_emoji = "💎"
    elif v < 100:
        status_line  = f"👑 *{v} vouchers!* Elite shinobi energy! 🌸"
        wallet_emoji = "👑"
    else:
        status_line  = f"🌟 *{v} vouchers!!* LEGENDARY WALLET! 🦊🌸"
        wallet_emoji = "🌟"

    # ── Pity bars ────────────────────────────────────────────────────────────
    leg_filled   = min(since_leg, 10)
    leg_bar      = "🟩" * leg_filled + "⬜" * (10 - leg_filled)

    spring_filled = min(since_spring, 100) // 10
    spring_bar    = "🟪" * spring_filled + "⬜" * (10 - spring_filled)

    # ── Event status ─────────────────────────────────────────────────────────
    if is_draft_event_active():
        days_left   = get_draft_days_remaining()
        event_badge = f"🟢 Event live — *{days_left} days* remaining"
    else:
        event_badge = "🔴 No active event right now"

    await update.message.reply_text(
        f"{wallet_emoji} *{user_name}'s Draft Vouchers* {wallet_emoji}\n\n"
        f"{'━' * 24}\n"
        f"🎟️  *You have:*  `{v}` Draft Voucher{'s' if v != 1 else ''}\n"
        f"{'━' * 24}\n\n"
        f"{status_line}\n\n"
        f"📊 *Spin Stats*\n"
        f"🎰 Total Spins: *{total_spins}*\n\n"
        f"👑 Legendary Pity: *{since_leg}/10*\n"
        f"{leg_bar}  _{10 - since_leg} spin(s) to guaranteed!_\n\n"
        f"🌸 Spring Pity: *{since_spring}/100*\n"
        f"{spring_bar}  _{100 - since_spring} spin(s) to guaranteed!_\n\n"
        f"{'━' * 24}\n"
        f"{event_badge}\n"
        f"{'━' * 24}\n\n"
        f"💰 *Restock your wallet:*\n"
        f"🌅 /daily → *+5* 🎟️  _(24h cooldown)_\n"
        f"📅 /weekly → *+5* 🎟️  _(7 days)_\n"
        f"🗓️ /monthly → *+10* 🎟️  _(30 days)_\n\n"
        f"🎰 Ready to spin? → /draft\\_event ⚔️",
        parse_mode="Markdown"
    )

async def draft_event(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.effective_user or not update.message:
        return
    user_id = update.effective_user.id
    if not has_started_bot(user_id) and not is_admin(user_id):
        await update.message.reply_text(NOT_STARTED_MSG, parse_mode="Markdown", disable_web_page_preview=True)
        return
    if is_banned(user_id):
        await update.message.reply_text(get_ban_message(user_id), parse_mode="Markdown")
        return

    days_left = get_draft_days_remaining()
    if not is_draft_event_active():
        await update.message.reply_text(
            "🚫 *No draft event is currently active!*\n\n"
            "🌸 Stay tuned — the next event is coming soon!\n"
            "Follow announcements for updates. 🔔",
            parse_mode="Markdown"
        )
        return

    # Always load latest weights from DB
    load_draft_rarity_weights()

    vouchers = get_user_vouchers(user_id)
    total_spins, spins_since_leg, spins_since_spring = get_draft_spin_stats(user_id)

    caption = (
        f"🌸 *SPRING BLOSSOM DRAFT EVENT* 🌸\n\n"
        f"{'━' * 24}\n"
        f"⏳ *Event ends in: {days_left} days*\n"
        f"{'━' * 24}\n\n"
        f"✨ *Pull rare shinobi cards from the gacha!*\n"
        f"🎟️ Your Vouchers: *{vouchers}*\n"
        f"🎰 Total Spins: *{total_spins}* | 🌸 Spring Pity: *{spins_since_spring}/100*\n\n"
        f"📊 *Drop Rates:*\n"
        f"🌀 Rare: 50% | ⚡ Epic: 35%\n"
        f"👑 Legendary: 10% | 🦊 Mythic + 🌸 Spring: 5%\n\n"
        f"💎 *Pity System:*\n"
        f"• Every 10th spin → Guaranteed Legendary\n"
        f"• Every 100th spin → Guaranteed Spring card!\n\n"
        f"🌟 *Guaranteed 1 Mythic/Spring in 100 Drafts!*\n"
        f"{'━' * 24}\n"
        f"👇 *Choose your draft below:*"
    )

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🏆 Prize Pool", callback_data="draft_prizepool"),
        ],
        [
            InlineKeyboardButton("⚡ 1 DRAFT (1 Voucher)", callback_data="draft_1"),
            InlineKeyboardButton("🔥 10 DRAFTS (10 Vouchers)", callback_data="draft_10"),
        ]
    ])

    banner_file_id = get_draft_banner_file_id()
    if banner_file_id:
        await update.message.reply_photo(
            photo=banner_file_id,
            caption=caption,
            reply_markup=keyboard,
            parse_mode="Markdown"
        )
    else:
        await update.message.reply_text(caption, reply_markup=keyboard, parse_mode="Markdown")


# ================= DRAFT CALLBACK =================

async def draft_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    user_id = query.from_user.id
    user_name = query.from_user.first_name or "Shinobi"
    data = query.data

    if not is_draft_event_active():
        await query.edit_message_caption(
            caption="🚫 The draft event has ended!",
            parse_mode="Markdown"
        )
        return

    # Load latest rarity weights from DB
    load_draft_rarity_weights()

    # ── Prize Pool display ────────────────────────────────────────────────────
    if data == "draft_prizepool":
        weight_lines = ""
        for rarity, weight in DRAFT_RARITY_WEIGHTS.items():
            emoji = RARITY_EMOJI.get(rarity, "⭐")
            weight_lines += f"{emoji} *{rarity}*: {weight}%\n"

        pity_info = (
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            "💎 *Pity System:*\n"
            "• Every *10th* spin → Guaranteed 👑 Legendary\n"
            "• Every *100th* spin → Guaranteed 🌸 Spring card!\n"
            "• Pity counter resets to 0 on legendary/Spring drop\n"
            "━━━━━━━━━━━━━━━━━━━━━━"
        )
        keyboard_back = InlineKeyboardMarkup([
            [InlineKeyboardButton("🔙 Back to Spin", callback_data="draft_back")],
        ])
        try:
            await query.edit_message_caption(
                caption=(
                    f"🏆 *Spring Blossom Draft — Prize Pool*\n\n"
                    f"━━━━━━━━━━━━━━━━━━━━━━\n"
                    f"📊 *Drop Rates:*\n{weight_lines}\n"
                    f"{pity_info}\n\n"
                    f"🌸 *Spring cards are the rarest rewards!*\n"
                    f"Collect them all, shinobi! ⚔️"
                ),
                reply_markup=keyboard_back,
                parse_mode="Markdown"
            )
        except Exception:
            await query.answer("Prize pool loaded!", show_alert=False)
        return

    # ── Back to spin menu ────────────────────────────────────────────────────
    if data == "draft_back":
        vouchers = get_user_vouchers(user_id)
        total_spins, spins_since_leg, spins_since_spring = get_draft_spin_stats(user_id)
        days_left = get_draft_days_remaining()
        spin_keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("🏆 Prize Pool", callback_data="draft_prizepool")],
            [
                InlineKeyboardButton("⚡ 1 DRAFT (1 Voucher)", callback_data="draft_1"),
                InlineKeyboardButton("🔥 10 DRAFTS (10 Vouchers)", callback_data="draft_10"),
            ]
        ])
        caption = (
            f"🌸 *SPRING BLOSSOM DRAFT EVENT* 🌸\n\n"
            f"{'━' * 24}\n"
            f"⏳ *Event ends in: {days_left} days*\n"
            f"{'━' * 24}\n\n"
            f"🎟️ Your Vouchers: *{vouchers}*\n"
            f"🎰 Total Spins: *{total_spins}* | 🌸 Spring Pity: *{spins_since_spring}/100*\n\n"
            f"📊 *Drop Rates:*\n"
            f"🌀 Rare: 50% | ⚡ Epic: 35%\n"
            f"👑 Legendary: 10% | 🦊 Mythic + 🌸 Spring: 5%\n\n"
            f"💎 *Pity:* 10th spin → Legendary | 100th spin → Spring\n"
            f"{'━' * 24}\n"
            f"👇 *Choose your draft below:*"
        )
        try:
            await query.edit_message_caption(caption=caption, reply_markup=spin_keyboard, parse_mode="Markdown")
        except Exception:
            pass
        return

    if data == "draft_1":
        num_drafts = 1
        cost = 1
    elif data == "draft_10":
        num_drafts = 10
        cost = 10
    else:
        return

    vouchers = get_user_vouchers(user_id)
    if vouchers < cost:
        try:
            await query.edit_message_caption(
                caption=(
                    f"💔 *Not Enough Vouchers!*\n\n"
                    f"🎟️ You only have *{vouchers}* voucher(s)!\n"
                    f"🚫 You need *{cost}* voucher(s) for this draft.\n\n"
                    f"😅 Come back when you've stocked up, shinobi!\n"
                    f"Don't give up — your legend awaits! 🌟\n\n"
                    f"📅 Earn more with /daily (+5) | /weekly (+5) | /monthly (+10)"
                ),
                parse_mode="Markdown"
            )
        except Exception:
            await context.bot.send_message(
                chat_id=user_id,
                text=(
                    f"💔 *Not Enough Vouchers!*\n\n"
                    f"🎟️ You only have *{vouchers}* voucher(s)!\n"
                    f"Need *{cost}* to spin. Keep grinding, shinobi! 🔥"
                ),
                parse_mode="Markdown"
            )
        return

    # Deduct vouchers
    deduct_vouchers(user_id, cost)

    # Show spinning animation
    try:
        await query.edit_message_caption(
            caption=(
                f"🌀 *Spinning the gacha...*\n\n"
                f"✨ The chakra is gathering...\n"
                f"🎰 Drawing your shinobi...\n\n"
                f"⏳ Please wait for your results!"
            ),
            parse_mode="Markdown"
        )
    except Exception:
        pass

    await asyncio.sleep(2)

    try:
        # ── Fetch fresh spin stats ────────────────────────────────────────────
        cursor.execute("""
            SELECT total_spins, spins_since_legendary, spins_since_spring
            FROM draft_vouchers WHERE user_id=?
        """, (user_id,))
        stats_row = cursor.fetchone()
        total_spins     = stats_row[0] if stats_row else 0
        spins_since_leg = stats_row[1] if stats_row else 0
        spins_since_spring = stats_row[2] if stats_row else 0

        # ── Perform drafts ────────────────────────────────────────────────────
        pulled_cards = []
        got_legendary_in_batch = False
        got_spring_overall = False

        for i in range(1, num_drafts + 1):
            # Pick rarity
            rarities = list(DRAFT_RARITY_WEIGHTS.keys())
            weights  = list(DRAFT_RARITY_WEIGHTS.values())
            chosen_rarity = random.choices(rarities, weights=weights, k=1)[0]

            # 10th spin pity: guarantee Legendary if none in batch
            if i == 10 and not got_legendary_in_batch:
                chosen_rarity = "Legendary"

            # 100th spring pity
            if (spins_since_spring + i) >= 100 and not got_spring_overall:
                chosen_rarity = "Spring"

            cursor.execute(
                "SELECT name, rarity, file_id FROM cards WHERE rarity=? ORDER BY RANDOM() LIMIT 1",
                (chosen_rarity,)
            )
            card = cursor.fetchone()
            if not card:
                # Fallback to any non-special card
                cursor.execute(
                    "SELECT name, rarity, file_id FROM cards "
                    "WHERE rarity NOT IN ('Animation','LeaderBoard') ORDER BY RANDOM() LIMIT 1"
                )
                card = cursor.fetchone()
            if not card:
                continue

            c_name, c_rarity, c_file_id = card

            if c_rarity in ("Legendary", "Mythic", "Spring"):
                got_legendary_in_batch = True
            if c_rarity == "Spring":
                got_spring_overall = True

            pulled_cards.append((c_name, c_rarity, c_file_id))
            insert_user_card(user_id, c_name, c_rarity, c_file_id)

        conn.commit()

        # ── Update spin stats ─────────────────────────────────────────────────
        new_total       = total_spins + num_drafts
        new_since_leg   = 0 if got_legendary_in_batch else spins_since_leg + num_drafts
        new_since_spring = 0 if got_spring_overall else spins_since_spring + num_drafts

        cursor.execute("""
            UPDATE draft_vouchers SET
                total_spins = ?,
                spins_since_legendary = ?,
                spins_since_spring = ?
            WHERE user_id = ?
        """, (new_total, new_since_leg, new_since_spring, user_id))
        conn.commit()

        # ── Build result text ─────────────────────────────────────────────────
        result_lines = []
        for c_name, c_rarity, _ in pulled_cards:
            emoji = RARITY_EMOJI.get(c_rarity, "⭐")
            result_lines.append(f"{emoji} {c_name.title()} [{c_rarity}]")
        result_text  = "\n".join(result_lines) if result_lines else "No cards obtained."
        new_vouchers = get_user_vouchers(user_id)

    except Exception as e:
        print(f"[DRAFT SPIN ERROR] user={user_id} error={e}")
        await context.bot.send_message(
            chat_id=query.message.chat_id,
            text=(
                "❌ *Something went wrong during your spin!*\n\n"
                "Your vouchers have been refunded. Please try again. 🔄"
            ),
            parse_mode="Markdown"
        )
        # Refund
        add_vouchers(user_id, cost)
        return

    # ── Try to build a card collage image ──────────────────────────────────
    result_sent = False
    if PIL_AVAILABLE and pulled_cards:
        try:
            images = []
            for c_name, c_rarity, c_file_id in pulled_cards:
                try:
                    # Use bot's own downloader — works on Railway, no external HTTP needed
                    tg_file = await context.bot.get_file(c_file_id)
                    img_bytes = await tg_file.download_as_bytearray()
                    img = Image.open(io.BytesIO(img_bytes)).convert("RGBA")
                    img = img.resize((200, 280), Image.LANCZOS)
                    images.append((img, c_rarity, c_name))
                except Exception as e:
                    print(f"[DRAFT IMG] Skip '{c_name}': {e}")

            if images:
                cols = min(5, len(images))
                rows_count = math.ceil(len(images) / cols)
                pad = 12
                card_w, card_h = 200, 280
                label_h = 38
                total_w = cols * (card_w + pad) + pad
                total_h = rows_count * (card_h + label_h + pad) + pad

                # Dark gradient background
                canvas = Image.new("RGB", (total_w, total_h), (12, 8, 28))
                draw = ImageDraw.Draw(canvas)

                rarity_colors = {
                    "Rare":       (100, 149, 237),
                    "Epic":       (180, 50, 255),
                    "Legendary":  (255, 215, 0),
                    "Mythic":     (255, 80, 0),
                    "Spring":     (255, 105, 180),
                    "Common":     (180, 180, 180),
                    "Crossverse": (0, 200, 200),
                }

                for idx, (img, rarity, card_name) in enumerate(images):
                    col = idx % cols
                    row_idx = idx // cols
                    x = pad + col * (card_w + pad)
                    y = pad + row_idx * (card_h + label_h + pad)

                    border_color = rarity_colors.get(rarity, (200, 200, 200))

                    # Outer glow border (draw twice, slightly bigger)
                    draw.rectangle(
                        [x - 4, y - 4, x + card_w + 3, y + card_h + 3],
                        outline=tuple(max(0, c - 80) for c in border_color),
                        width=2
                    )
                    # Inner border
                    draw.rectangle(
                        [x - 2, y - 2, x + card_w + 1, y + card_h + 1],
                        outline=border_color,
                        width=2
                    )

                    # Paste card image (handle transparency)
                    img_rgb = img.convert("RGB")
                    canvas.paste(img_rgb, (x, y))

                    # Label background
                    draw.rectangle(
                        [x, y + card_h, x + card_w, y + card_h + label_h],
                        fill=(20, 12, 45)
                    )
                    # Colored bar at top of label
                    draw.rectangle(
                        [x, y + card_h, x + card_w, y + card_h + 3],
                        fill=border_color
                    )

                    # Rarity text on label
                    rarity_emoji = RARITY_EMOJI.get(rarity, "⭐")
                    label = f"{rarity_emoji} {rarity}"
                    draw.text((x + 6, y + card_h + 8), label, fill=border_color)

                # Save to buffer
                buf_out = io.BytesIO()
                canvas.save(buf_out, format="PNG", optimize=True)
                buf_out.seek(0)

                caption_img = (
                    f"🌸 *{user_name}'s Spring Blossom Draft!*\n\n"
                    f"🎰 Spins: *{num_drafts}* | 🎟️ Vouchers left: *{new_vouchers}*\n"
                    f"🌸 Spring Pity: *{new_since_spring}/100*\n\n"
                    f"✨ *Cards obtained:*\n{result_text}\n\n"
                    f"✅ All cards added to your vault! 🏆"
                )

                await context.bot.send_photo(
                    chat_id=query.message.chat_id,
                    photo=buf_out,
                    caption=caption_img,
                    parse_mode="Markdown"
                )
                result_sent = True
                print(f"[DRAFT IMG] Collage sent successfully for {user_name}")

        except Exception as e:
            print(f"[DRAFT IMG] Collage generation failed: {e}")
            result_sent = False

    # Fallback: text-only result if image failed or PIL not available
    if not result_sent:
        await context.bot.send_message(
            chat_id=query.message.chat_id,
            text=(
                f"🌸 *{user_name}'s Draft Results!*\n\n"
                f"{'━' * 24}\n"
                f"{result_text}\n"
                f"{'━' * 24}\n\n"
                f"🎟️ Vouchers left: *{new_vouchers}*\n"
                f"🌸 Spring Pity: *{new_since_spring}/100*\n\n"
                f"✅ All cards have been added to your vault! 🏆"
            ),
            parse_mode="Markdown"
        )

    # Log to group
    days_left = get_draft_days_remaining()
    await log_to_group(
        context.bot,
        f"🎰 *Draft Pull*\n\n"
        f"👤 {user_name} (`{user_id}`)\n"
        f"🎯 Spins: {num_drafts}\n"
        f"📦 Cards: {', '.join([f'{r}:{n}' for n,r,_ in pulled_cards[:5]])}\n"
        f"🎟️ Vouchers left: {new_vouchers}\n"
        f"⏳ Event: {days_left} days remaining"
    )

    # ── Re-show the spin buttons so user can spin again without re-typing /draft_event
    spin_again_keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("🏆 Prize Pool", callback_data="draft_prizepool")],
        [
            InlineKeyboardButton("⚡ 1 DRAFT (1 Voucher)", callback_data="draft_1"),
            InlineKeyboardButton("🔥 10 DRAFTS (10 Vouchers)", callback_data="draft_10"),
        ]
    ])
    await context.bot.send_message(
        chat_id=query.message.chat_id,
        text=(
            f"🎟️ *Vouchers remaining: {new_vouchers}*\n"
            f"🌸 Spring Pity: *{new_since_spring}/100*"
            + (" *(RESET — Spring obtained!)*" if got_spring_overall else "")
            + "\n\n👇 *Spin again or check prize pool:*"
        ),
        reply_markup=spin_again_keyboard,
        parse_mode="Markdown"
    )


# ================= GENERATE DRAFT (Admin) =================

async def generate_draft(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.effective_user or not update.message:
        return
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("🚫 Admins only.")
        return

    if not context.args or len(context.args) < 2:
        await update.message.reply_text(
            "Usage: /generate_draft <vouchers_per_claim> <max_users>\n\n"
            "Example: /generate_draft 10 50\n"
            "Creates a code giving 10 vouchers, claimable by 50 users."
        )
        return

    try:
        vouchers_each = int(context.args[0])
        max_users = int(context.args[1])
        if vouchers_each <= 0 or max_users <= 0:
            raise ValueError
    except ValueError:
        await update.message.reply_text("❌ Both values must be positive numbers.")
        return

    code = "DFT" + "".join(random.choices(string.ascii_uppercase + string.digits, k=8))
    now = int(time.time())
    cursor.execute(
        "INSERT INTO draft_codes (code, vouchers, uses_left, created_by, created_at) VALUES (?,?,?,?,?)",
        (code, vouchers_each, max_users, update.effective_user.id, now)
    )
    conn.commit()

    await update.message.reply_text(
        f"✅ *Draft Voucher Code Generated!*\n\n"
        f"🎟️ Code: `{code}`\n"
        f"💼 Vouchers per claim: *{vouchers_each}*\n"
        f"👥 Max users: *{max_users}*\n\n"
        f"📢 Share this code! Users can claim with:\n"
        f"`/redeem_draft {code}`",
        parse_mode="Markdown"
    )
    await log_to_group(
        context.bot,
        f"🎟️ *Draft Code Generated*\n\n"
        f"Code: `{code}`\n"
        f"Vouchers: {vouchers_each} each\n"
        f"Max users: {max_users}\n"
        f"By: {update.effective_user.first_name} (`{update.effective_user.id}`)"
    )


# ================= REDEEM DRAFT CODE =================

async def redeem_draft(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.effective_user or not update.message:
        return
    user_id = update.effective_user.id
    if is_banned(user_id):
        await update.message.reply_text(get_ban_message(user_id), parse_mode="Markdown")
        return

    if not context.args:
        await update.message.reply_text("Usage: /redeem_draft <code>")
        return

    code = context.args[0].upper()
    cursor.execute("SELECT vouchers, uses_left FROM draft_codes WHERE code=?", (code,))
    row = cursor.fetchone()

    if not row:
        await update.message.reply_text("❌ Invalid draft voucher code.")
        return

    vouchers_each, uses_left = row
    if uses_left <= 0:
        await update.message.reply_text("❌ This code has been fully redeemed.")
        return

    # Check if user already claimed this code
    cursor.execute(
        "SELECT 1 FROM draft_code_claims WHERE code=? AND user_id=?",
        (code, user_id)
    )
    if cursor.fetchone():
        await update.message.reply_text(
            "❌ *Already Redeemed!*\n\nYou have already claimed this code.",
            parse_mode="Markdown"
        )
        return

    # Mark claimed and deduct a use
    cursor.execute(
        "INSERT INTO draft_code_claims (code, user_id, claimed_at) VALUES (?,?,?)",
        (code, user_id, int(time.time()))
    )
    cursor.execute("UPDATE draft_codes SET uses_left = uses_left - 1 WHERE code=?", (code,))
    conn.commit()

    add_vouchers(user_id, vouchers_each)
    total = get_user_vouchers(user_id)

    user_name = update.effective_user.first_name or "Shinobi"
    await update.message.reply_text(
        f"🎉 *Draft Code Redeemed Successfully!*\n\n"
        f"{'━' * 24}\n"
        f"🎟️ *+{vouchers_each} Draft Vouchers* added!\n"
        f"💼 Total Vouchers: *{total}*\n"
        f"{'━' * 24}\n\n"
        f"🌸 Head to /draft\\_event to spin!\n"
        f"🔥 Good luck, {user_name}! ⚔️",
        parse_mode="Markdown"
    )
    await log_to_group(
        context.bot,
        f"🎟️ *Draft Code Redeemed*\n\n"
        f"👤 {update.effective_user.first_name} (`{user_id}`)\n"
        f"Code: `{code}`\n"
        f"Vouchers: +{vouchers_each} | Total now: {total}"
    )


# ================= SET DRAFT BANNER (Admin) =================

async def set_draft_banner(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.effective_user or not update.message:
        return
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("🚫 Admins only.")
        return

    file_id = None
    if update.message.photo:
        file_id = update.message.photo[-1].file_id
    elif update.message.reply_to_message and update.message.reply_to_message.photo:
        file_id = update.message.reply_to_message.photo[-1].file_id

    if not file_id:
        await update.message.reply_text(
            "📸 Send a photo with /set_draft_banner as caption, or reply to a photo."
        )
        return

    cursor.execute(
        "INSERT OR REPLACE INTO bot_settings (key, value) VALUES ('draft_banner', ?)",
        (file_id,)
    )
    conn.commit()
    await update.message.reply_text("✅ Draft event banner updated!")
    await log_to_group(
        context.bot,
        f"🖼️ *Draft Banner Updated*\n👤 By: {update.effective_user.first_name} (`{update.effective_user.id}`)",
        photo_file_id=file_id
    )


# ================= /ADD CARD TO COLLECTION (Admin/Owner) =================

async def add_to_collection(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Admin/Owner:
      /add <card_id>              — Add card to your own collection
      /add <card_id> <user_id>   — Add card to another user by ID
      Reply to user + /add <card_id> — Add card to the replied user
    """
    if not update.effective_user or not update.message:
        return
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("🚫 Admins only.")
        return

    if not context.args:
        await update.message.reply_text(
            "📖 *Usage:*\n"
            "• `/add <card_id>` — add to your own vault\n"
            "• `/add <card_id> <user_id>` — add to another user\n"
            "• Reply to a user's message + `/add <card_id>`",
            parse_mode="Markdown"
        )
        return

    try:
        card_id = int(context.args[0].replace("#", ""))
    except ValueError:
        await update.message.reply_text("❌ Invalid card ID.")
        return

    # ── Determine target: reply > explicit id > self ──────────────────────────
    target_id = None
    target_name = None

    if update.message.reply_to_message and update.message.reply_to_message.from_user:
        replied_user = update.message.reply_to_message.from_user
        if not replied_user.is_bot:
            target_id = replied_user.id
            target_name = replied_user.first_name or str(target_id)

    if target_id is None and len(context.args) >= 2:
        try:
            target_id = int(context.args[1])
            try:
                tc = await context.bot.get_chat(target_id)
                target_name = tc.first_name or str(target_id)
            except Exception:
                target_name = str(target_id)
        except ValueError:
            await update.message.reply_text("❌ Invalid user ID.")
            return

    if target_id is None:
        target_id = update.effective_user.id
        target_name = update.effective_user.first_name or "Self"

    cursor.execute("SELECT name, rarity, file_id FROM cards WHERE id=?", (card_id,))
    card = cursor.fetchone()
    if not card:
        await update.message.reply_text(f"❌ No card found with ID #{card_id}.")
        return

    name, rarity, file_id = card

    insert_user_card(target_id, name, rarity, file_id)
    conn.commit()

    emoji = RARITY_EMOJI.get(rarity, "⭐")
    await update.message.reply_photo(
        photo=file_id,
        caption=(
            f"✅ *Card Added to Collection!*\n\n"
            f"🆔 Card ID: #{card_id}\n"
            f"🥷 Name: {name.title()}\n"
            f"{emoji} Rarity: {rarity}\n\n"
            f"👤 Added to: *{target_name}* (`{target_id}`)\n"
            f"🏆 Card is now in their vault!"
        ),
        parse_mode="Markdown"
    )
    await log_to_group(
        context.bot,
        f"➕ *Admin Add Card*\n\n"
        f"👤 By: {update.effective_user.first_name} (`{update.effective_user.id}`)\n"
        f"🎯 To: {target_name} (`{target_id}`)\n"
        f"🆔 Card: #{card_id} {name} [{rarity}]"
    )


# ================= /REMOVE CARD (Admin) =================

async def remove_from_collection(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Admin: /remove <user_id> <card_entry_id>
       OR  Reply to user + /remove <card_entry_id>
    Removes a specific card entry from a user's collection.
    """
    if not update.effective_user or not update.message:
        return
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("🚫 Admins only.")
        return

    # ── Determine target and card_entry_id ───────────────────────────────────
    target_id = None
    target_name = None
    card_entry_id = None

    replied = update.message.reply_to_message
    if replied and replied.from_user and not replied.from_user.is_bot:
        target_id = replied.from_user.id
        target_name = replied.from_user.first_name or str(target_id)
        # In reply mode first arg is the card entry id
        if context.args:
            try:
                card_entry_id = int(context.args[0].replace("#", ""))
            except ValueError:
                await update.message.reply_text("❌ Invalid card entry ID.")
                return
        else:
            await update.message.reply_text(
                "Usage (reply to user): /remove <card_entry_id>\n"
                "Example: reply to user + /remove 42"
            )
            return
    else:
        # Normal mode: /remove <user_id> <card_entry_id>
        if not context.args or len(context.args) < 2:
            await update.message.reply_text(
                "📖 *Usage:*\n"
                "• `/remove <user_id> <card_entry_id>`\n"
                "• Reply to user + `/remove <card_entry_id>`\n\n"
                "Example: `/remove 123456789 42`\n"
                "💡 Find card entry IDs from /collection.",
                parse_mode="Markdown"
            )
            return
        try:
            target_id = int(context.args[0])
            card_entry_id = int(context.args[1].replace("#", ""))
        except ValueError:
            await update.message.reply_text("❌ Invalid user ID or card entry ID.")
            return
        try:
            tc = await context.bot.get_chat(target_id)
            target_name = tc.first_name or str(target_id)
        except Exception:
            target_name = str(target_id)

    # Fetch the card entry — look up by master card_id first, then row id
    cursor.execute(
        "SELECT id, card_name, rarity, file_id FROM user_cards WHERE card_id=? AND user_id=? LIMIT 1",
        (card_entry_id, target_id)
    )
    card = cursor.fetchone()
    if not card:
        # Fallback: try by internal row id
        cursor.execute(
            "SELECT id, card_name, rarity, file_id FROM user_cards WHERE id=? AND user_id=?",
            (card_entry_id, target_id)
        )
        card = cursor.fetchone()

    if not card:
        await update.message.reply_text(
            f"❌ No card *#{card_entry_id}* found for user `{target_id}`.\n\n"
            f"Check the card ID from their `/collection`.",
            parse_mode="Markdown"
        )
        return

    row_id, card_name, rarity, file_id = card
    emoji = RARITY_EMOJI.get(rarity, "⭐")

    cursor.execute("DELETE FROM user_cards WHERE id=? AND user_id=?", (row_id, target_id))
    conn.commit()

    await update.message.reply_text(
        f"🗑️ *Card Removed Successfully!*\n\n"
        f"👤 User: {target_name} (`{target_id}`)\n"
        f"🥷 Card: {card_name.title()}\n"
        f"{emoji} Rarity: {rarity}\n"
        f"🆔 Card ID: #{card_entry_id}\n\n"
        f"✅ Removed from their collection.",
        parse_mode="Markdown"
    )
    await log_to_group(
        context.bot,
        f"🗑️ *Admin Removed Card*\n\n"
        f"👮 By: {update.effective_user.first_name} (`{update.effective_user.id}`)\n"
        f"👤 From: {target_name} (`{target_id}`)\n"
        f"🥷 Card: {card_name.title()} [{rarity}]\n"
        f"🆔 Card ID: #{card_entry_id}"
    )


# ================= /TRANSFER COLLECTION (Admin only) =================

async def transfer_collection(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Admin only: /transfer <receiver_id> <sender_id>
    Transfers the entire collection from sender to receiver.
    Sender's collection becomes empty after transfer.
    """
    if not update.effective_user or not update.message:
        return
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("🚫 This command is for admins only.")
        return

    if not context.args or len(context.args) < 2:
        await update.message.reply_text(
            "Usage: /transfer <receiver_id> <sender_id>\n\n"
            "📥 First ID = the user who RECEIVES the collection\n"
            "📤 Second ID = the user who SENDS (loses) the collection\n\n"
            "Example:\n/transfer 1234567890 9876543210\n\n"
            "⚠️ The sender's collection will be completely emptied!"
        )
        return

    try:
        receiver_id = int(context.args[0])
        sender_id = int(context.args[1])
    except ValueError:
        await update.message.reply_text("❌ Invalid user IDs. Both must be numbers.")
        return

    if receiver_id == sender_id:
        await update.message.reply_text("❌ Receiver and sender cannot be the same user.")
        return

    # Count sender's cards
    cursor.execute("SELECT COUNT(*) FROM user_cards WHERE user_id=?", (sender_id,))
    card_count = cursor.fetchone()[0]

    if card_count == 0:
        await update.message.reply_text(
            f"⚠️ User `{sender_id}` has no cards to transfer.",
            parse_mode="Markdown"
        )
        return

    # Get user names
    try:
        receiver_chat = await context.bot.get_chat(receiver_id)
        receiver_name = receiver_chat.first_name or str(receiver_id)
    except Exception:
        receiver_name = str(receiver_id)

    try:
        sender_chat = await context.bot.get_chat(sender_id)
        sender_name = sender_chat.first_name or str(sender_id)
    except Exception:
        sender_name = str(sender_id)

    # Perform the transfer — update all sender's cards to receiver
    cursor.execute(
        "UPDATE user_cards SET user_id=? WHERE user_id=?",
        (receiver_id, sender_id)
    )
    conn.commit()

    await update.message.reply_text(
        f"✅ *Collection Transfer Complete!*\n\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"📤 *Sender:* {sender_name} (`{sender_id}`)\n"
        f"📥 *Receiver:* {receiver_name} (`{receiver_id}`)\n"
        f"🃏 *Cards Transferred:* {card_count}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"✅ {receiver_name}'s collection has been updated.\n"
        f"⚠️ {sender_name}'s collection is now empty.",
        parse_mode="Markdown"
    )
    await log_to_group(
        context.bot,
        f"🔄 *Collection Transfer*\n\n"
        f"👮 By: {update.effective_user.first_name} (`{update.effective_user.id}`)\n"
        f"📤 From: {sender_name} (`{sender_id}`)\n"
        f"📥 To: {receiver_name} (`{receiver_id}`)\n"
        f"🃏 Cards: {card_count}"
    )

    # Notify both users
    try:
        await context.bot.send_message(
            chat_id=receiver_id,
            text=(
                f"📥 *Collection Received!*\n\n"
                f"🎉 An admin has transferred *{card_count} cards* to your collection!\n\n"
                f"📦 Use /collection to view your updated vault.\n"
                f"🍃 Welcome your new shinobis! ⚔️"
            ),
            parse_mode="Markdown"
        )
    except Exception:
        pass

    try:
        await context.bot.send_message(
            chat_id=sender_id,
            text=(
                f"📤 *Collection Transferred!*\n\n"
                f"⚠️ An admin has transferred all *{card_count} cards* from your collection.\n\n"
                f"💔 Your vault is now empty.\n"
                f"📞 Contact an admin if you think this was a mistake."
            ),
            parse_mode="Markdown"
        )
    except Exception:
        pass


# ================= /FIND CARD (Search who has a card) =================

async def find_card(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /find <card_id> — Show which users own the card with that exact DB ID.
    Users must use the exact card number from the database.
    """
    if not update.effective_user or not update.message:
        return
    user_id = update.effective_user.id
    if not has_started_bot(user_id) and not is_admin(user_id):
        await update.message.reply_text(NOT_STARTED_MSG, parse_mode="Markdown", disable_web_page_preview=True)
        return
    if is_banned(user_id):
        await update.message.reply_text(get_ban_message(user_id), parse_mode="Markdown")
        return

    if not context.args:
        await update.message.reply_text(
            "🔍 *Find a Card*\n\n"
            "Usage: /find <card\\_id>\n"
            "Example: /find 42\n\n"
            "💡 Use the *exact card number* from the database.\n"
            "Admins can see all card IDs with /list\\_cards.",
            parse_mode="Markdown"
        )
        return

    try:
        card_id = int(context.args[0].replace("#", ""))
    except ValueError:
        await update.message.reply_text(
            "❌ Invalid card ID.\n\n"
            "Use the exact card number, e.g. `/find 42`",
            parse_mode="Markdown"
        )
        return

    # Fetch card — try exact ID first, then fuzzy name search
    cursor.execute("SELECT id, name, rarity, file_id, COALESCE(file_type,'photo') FROM cards WHERE id=?", (card_id,))
    card = cursor.fetchone()
    if not card:
        # Try name search (user may have typed card name or partial name)
        cursor.execute(
            "SELECT id, name, rarity, file_id, COALESCE(file_type,'photo') FROM cards "
            "WHERE LOWER(name) LIKE LOWER(?) ORDER BY id LIMIT 1",
            (f"%{context.args[0].replace('#','')}%",)
        )
        card = cursor.fetchone()
    if not card:
        await update.message.reply_text(
            f"❌ *No card found with ID #{card_id}!*\n\n"
            f"• Check the number shown in your `/collection`\n"
            f"• The card's *#ID* is shown in the caption\n"
            f"• Admins can see all IDs with /list\\_cards",
            parse_mode="Markdown"
        )
        return

    db_id, card_name, rarity, file_id, file_type = card
    emoji = RARITY_EMOJI.get(rarity, "⭐")

    # Find holders by card_name AND rarity to match exactly
    cursor.execute(
        "SELECT user_id, COUNT(*) as copies FROM user_cards "
        "WHERE card_name=? AND rarity=? GROUP BY user_id ORDER BY copies DESC",
        (card_name, rarity)
    )
    holders = cursor.fetchall()

    if not holders:
        _find_caption = (
            f"🔍 *Card Search Results*\n\n"
            f"🆔 Card ID: *#{db_id}*\n"
            f"🥷 Card: {card_name.title()}\n"
            f"{emoji} Rarity: {rarity}\n\n"
            f"😶 *Nobody owns this card yet!*\n"
            f"Be the first to get it! ⚔️"
        )
        if file_type == "video":
            await update.message.reply_video(video=file_id, caption=_find_caption, parse_mode="Markdown", supports_streaming=True)
        else:
            await update.message.reply_photo(photo=file_id, caption=_find_caption, parse_mode="Markdown")
        return

    lines = ""
    total_copies = 0
    shown = 0
    for holder_id, copies in holders:
        total_copies += copies
        if shown < 20:
            try:
                hc = await context.bot.get_chat(holder_id)
                hname = hc.first_name or str(holder_id)
                husertag = f"@{hc.username}" if hc.username else "no username"
            except Exception:
                hname = str(holder_id)
                husertag = "unknown"
            lines += f"• {hname} ({husertag} | `{holder_id}`) — {copies}x\n"
            shown += 1

    extra = f"\n_...and {len(holders) - 20} more players_" if len(holders) > 20 else ""

    _find_caption2 = (
        f"🔍 *Card Search Results*\n\n"
        f"🆔 Card ID: *#{db_id}*\n"
        f"🥷 Card: {card_name.title()}\n"
        f"{emoji} Rarity: {rarity}\n\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"👥 *Owned by {len(holders)} player(s)*\n"
        f"📦 Total copies: {total_copies}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"📋 *Holders:*\n{lines}{extra}"
    )
    if file_type == "video":
        await update.message.reply_video(video=file_id, caption=_find_caption2, parse_mode="Markdown", supports_streaming=True)
    else:
        await update.message.reply_photo(photo=file_id, caption=_find_caption2, parse_mode="Markdown")


# ================= /DELACC — Delete User Account/Collection (Admin only) =================

async def delacc(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Admin only: /delacc <user_id>
    Completely deletes a user's collection (all their cards).
    Also removes their vouchers and spin stats.
    """
    if not update.effective_user or not update.message:
        return
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("🚫 This command is for admins only.")
        return

    if not context.args and not (update.message.reply_to_message and update.message.reply_to_message.from_user):
        await update.message.reply_text(
            "Usage:\n"
            "• /delacc <user_id>\n"
            "• Reply to a user's message with /delacc\n\n"
            "⚠️ Permanently wipes their collection (backed up for /rec_acc).\n"
            "Example: /delacc 123456789"
        )
        return

    # Determine target — reply takes priority
    target_id = None
    if update.message.reply_to_message and update.message.reply_to_message.from_user:
        replied = update.message.reply_to_message.from_user
        if not replied.is_bot:
            target_id = replied.id
    if target_id is None and context.args:
        try:
            target_id = int(context.args[0])
        except ValueError:
            await update.message.reply_text("❌ Invalid user ID.")
            return
    if target_id is None:
        await update.message.reply_text("❌ Could not determine target user.")
        return

    if target_id in (OWNER_ID, CO_OWNER_ID):
        # Only the owner themselves can delete their own account
        if update.effective_user.id != OWNER_ID:
            await update.message.reply_text("❌ Only the owner can delete the owner/co-owner's account.")
            return

    # Get target name
    try:
        tc = await context.bot.get_chat(target_id)
        target_name = tc.first_name or str(target_id)
    except Exception:
        target_name = str(target_id)

    # Count their cards before deleting
    cursor.execute("SELECT COUNT(*) FROM user_cards WHERE user_id=?", (target_id,))
    card_count = cursor.fetchone()[0]

    cursor.execute("SELECT vouchers FROM draft_vouchers WHERE user_id=?", (target_id,))
    v_row = cursor.fetchone()
    voucher_count = v_row[0] if v_row else 0

    # ── Backup cards to deleted_cards_backup BEFORE deleting (fixes /rec_acc) ──
    now_ts = int(time.time())
    cursor.execute(
        "SELECT card_name, rarity, file_id FROM user_cards WHERE user_id=?",
        (target_id,)
    )
    cards_to_backup = cursor.fetchall()
    for card_name, rarity, file_id in cards_to_backup:
        cursor.execute(
            "INSERT INTO deleted_cards_backup (user_id, card_name, rarity, file_id, deleted_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (target_id, card_name, rarity, file_id, now_ts)
        )

    # ── Backup vouchers + spin stats to deleted_vouchers_backup ──────────────
    cursor.execute(
        "SELECT vouchers, total_spins, spins_since_legendary, spins_since_spring "
        "FROM draft_vouchers WHERE user_id=?",
        (target_id,)
    )
    vrow = cursor.fetchone()
    if vrow:
        cursor.execute("""
            INSERT OR REPLACE INTO deleted_vouchers_backup
            (user_id, vouchers, total_spins, spins_since_legendary, spins_since_spring, deleted_at)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (target_id, vrow[0], vrow[1], vrow[2], vrow[3], now_ts))

    # Delete everything
    cursor.execute("DELETE FROM user_cards WHERE user_id=?", (target_id,))
    cursor.execute("DELETE FROM draft_vouchers WHERE user_id=?", (target_id,))
    cursor.execute("DELETE FROM weekly_claims WHERE user_id=?", (target_id,))
    cursor.execute("DELETE FROM monthly_claims WHERE user_id=?", (target_id,))
    conn.commit()

    await update.message.reply_text(
        f"🗑️ *Account Data Deleted!*\n\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"👤 User: {target_name} (`{target_id}`)\n"
        f"🃏 Cards Deleted: {card_count}\n"
        f"🎟️ Vouchers Cleared: {voucher_count}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"✅ Their collection has been completely wiped.",
        parse_mode="Markdown"
    )
    await log_to_group(
        context.bot,
        f"🗑️ *User Account Deleted*\n\n"
        f"👮 By: {update.effective_user.first_name} (`{update.effective_user.id}`)\n"
        f"👤 User: {target_name} (`{target_id}`)\n"
        f"🃏 Cards: {card_count} deleted\n"
        f"🎟️ Vouchers: {voucher_count} cleared"
    )

    # Notify the affected user
    try:
        await context.bot.send_message(
            chat_id=target_id,
            text=(
                f"⚠️ *Account Reset Notice*\n\n"
                f"An admin has deleted your collection and account data.\n\n"
                f"🃏 Cards: {card_count} removed\n"
                f"🎟️ Vouchers: {voucher_count} cleared\n\n"
                f"📞 Contact an admin if you think this was a mistake."
            ),
            parse_mode="Markdown"
        )
    except Exception:
        pass


# ================= /SHINOBI_MODE — Set Favourite Rarity Display Order =================

RARITY_ORDER_DEFAULT = ["Crossverse", "Spring", "Mythic", "Legendary", "Epic", "Rare", "Common"]

def get_user_fav_rarity(user_id: int):
    """Return the user's preferred rarity (or None if not set)."""
    cursor.execute("SELECT fav_rarity FROM shinobi_mode WHERE user_id=?", (user_id,))
    row = cursor.fetchone()
    return row[0] if row else None

def get_sorted_collection(user_id: int, fav_rarity: str = None):
    """
    Return collection rows sorted so the fav_rarity appears first,
    then the rest in descending rarity order.
    Returns: [(card_id, card_name, rarity, count), ...]
    card_id is the MASTER cards.id so every user sees the same number.
    """
    cursor.execute(
        "SELECT card_id, card_name, rarity, COUNT(*) "
        "FROM user_cards WHERE user_id = ? "
        "GROUP BY card_id, card_name, rarity",
        (user_id,)
    )
    rows = cursor.fetchall()
    if not rows:
        return []

    if not fav_rarity:
        order = {r: i for i, r in enumerate(RARITY_ORDER_DEFAULT)}
        return sorted(rows, key=lambda r: order.get(r[2], 99))

    order = {r: i + 1 for i, r in enumerate(RARITY_ORDER_DEFAULT)}
    order[fav_rarity] = 0
    return sorted(rows, key=lambda r: order.get(r[2], 99))

async def shinobi_mode(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /shinobi_mode [rarity] — Set your favourite rarity to show first in /collection.
    /shinobi_mode          — Show current setting and available rarities.
    /shinobi_mode reset    — Clear the preference (default order).
    """
    if not update.effective_user or not update.message:
        return
    user_id = update.effective_user.id
    if not has_started_bot(user_id) and not is_admin(user_id):
        await update.message.reply_text(NOT_STARTED_MSG, parse_mode="Markdown", disable_web_page_preview=True)
        return
    if is_banned(user_id):
        await update.message.reply_text(get_ban_message(user_id), parse_mode="Markdown")
        return

    user_name = update.effective_user.first_name or "Shinobi"

    # Ensure row exists
    cursor.execute(
        "INSERT OR IGNORE INTO shinobi_mode (user_id, fav_rarity) VALUES (?, NULL)",
        (user_id,)
    )
    conn.commit()

    current = get_user_fav_rarity(user_id)

    # No args — show current setting + menu
    if not context.args:
        rarity_list = "\n".join(
            f"  {RARITY_EMOJI.get(r, '⭐')} `{r}`" for r in RARITY_ORDER_DEFAULT
        )
        current_display = (
            f"{RARITY_EMOJI.get(current, '⭐')} *{current}* (shown first)"
            if current else "🔀 Default order (no preference set)"
        )
        await update.message.reply_text(
            f"⚔️ *Shinobi Mode — Rarity Display Order*\n\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"👤 Shinobi: *{user_name}*\n"
            f"🎯 Current Mode: {current_display}\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"🌀 *Available Rarities:*\n{rarity_list}\n\n"
            f"📖 *How to use:*\n"
            f"• `/shinobi_mode Mythic` — show Mythic cards first\n"
            f"• `/shinobi_mode Legendary` — show Legendary cards first\n"
            f"• `/shinobi_mode reset` — restore default order\n\n"
            f"✨ Your chosen rarity will always appear at the *top* of /collection!",
            parse_mode="Markdown"
        )
        return

    arg = context.args[0].strip()

    # Reset
    if arg.lower() == "reset":
        cursor.execute("UPDATE shinobi_mode SET fav_rarity=NULL WHERE user_id=?", (user_id,))
        conn.commit()
        await update.message.reply_text(
            f"🔀 *Shinobi Mode Reset!*\n\n"
            f"✅ Your collection will now display in the default rarity order.\n\n"
            f"💡 Use `/shinobi_mode <rarity>` anytime to set a preference.",
            parse_mode="Markdown"
        )
        return

    # Set rarity
    chosen = arg.capitalize()
    if chosen not in RARITY_WEIGHTS:
        rarity_list = ", ".join(f"`{r}`" for r in RARITY_ORDER_DEFAULT)
        await update.message.reply_text(
            f"❌ *Unknown rarity:* `{arg}`\n\n"
            f"✅ Available rarities:\n{rarity_list}\n\n"
            f"Example: `/shinobi_mode Mythic`",
            parse_mode="Markdown"
        )
        return

    cursor.execute("UPDATE shinobi_mode SET fav_rarity=? WHERE user_id=?", (chosen, user_id))
    conn.commit()
    emoji = RARITY_EMOJI.get(chosen, "⭐")

    await update.message.reply_text(
        f"{emoji} *Shinobi Mode Activated!*\n\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🎯 Favourite Rarity: *{chosen}*\n"
        f"📋 Your *{chosen}* cards will now appear\n"
        f"   at the top of /collection!\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"✨ Head to /collection to see the change!\n"
        f"🔀 Use `/shinobi_mode reset` to go back to default.",
        parse_mode="Markdown"
    )


# ================= REFERRAL COMMAND =================

async def referral_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.effective_user or not update.message:
        return
    user_id = update.effective_user.id
    if not has_started_bot(user_id) and not is_admin(user_id):
        await update.message.reply_text(NOT_STARTED_MSG, parse_mode="Markdown", disable_web_page_preview=True)
        return
    if is_banned(user_id):
        await update.message.reply_text(get_ban_message(user_id), parse_mode="Markdown")
        return

    user_name = update.effective_user.first_name or "Shinobi"
    code = get_or_create_referral_code(user_id)
    count = get_referral_count(user_id)

    # Get bot username for the deep link
    try:
        bot_info = await context.bot.get_me()
        bot_username = bot_info.username
    except:
        bot_username = "YourBot"

    link = f"https://t.me/{bot_username}?start={code}"

    # Next milestone info
    next_milestone = None
    for milestone in sorted(REFERRAL_REWARDS.keys()):
        if milestone > count:
            next_milestone = milestone
            break

    next_reward_text = ""
    if next_milestone:
        e, leg, ms, v = REFERRAL_REWARDS[next_milestone]
        parts = []
        if e: parts.append(f"{e}x Epic card(s)")
        if leg: parts.append(f"{leg}x Legendary card(s)")
        if ms: parts.append(f"1x Mythic/Spring card")
        if v: parts.append(f"{v} Draft Vouchers")
        next_reward_text = f"🎯 Next reward at *{next_milestone}* referrals: {', '.join(parts)}"

    # List recent referrals
    cursor.execute(
        "SELECT referred_id, timestamp FROM referral_uses WHERE referrer_id=? ORDER BY timestamp DESC LIMIT 5",
        (user_id,)
    )
    recent = cursor.fetchall()

    recent_text = ""
    for referred_id, ts in recent:
        t = time.strftime("%d/%m/%Y", time.localtime(ts))
        try:
            u = await context.bot.get_chat(referred_id)
            uname = u.first_name or str(referred_id)
        except:
            uname = str(referred_id)
        recent_text += f"  • {uname} — {t}\n"

    await update.message.reply_text(
        f"🔗 *Your Referral Dashboard*\n\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"👤 Shinobi: *{user_name}*\n"
        f"🏆 Total Referrals: *{count}*\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"🔗 *Your Referral Link:*\n`{link}`\n\n"
        f"📤 Share it! When a new user starts the bot via your link, you earn rewards!\n\n"
        f"{next_reward_text}\n\n"
        f"{'📋 *Recent Referrals:*' + chr(10) + recent_text if recent_text else ''}"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🏅 *Reward Milestones:*\n"
        f"1 ref → 1 Epic + 5🎟️\n"
        f"2 ref → 2 Epic + 5🎟️\n"
        f"3-4 ref → 3 Epic + 5🎟️ each\n"
        f"5 ref → 1 Legendary + 10🎟️\n"
        f"6-9 ref → 3 Epic + 5🎟️ each\n"
        f"10 ref → 1 Mythic/Spring + 20🎟️ 🌟",
        parse_mode="Markdown"
    )


# ================= START / STOP DRAFT EVENT (Owner only) =================

async def start_draft_event(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Owner command: /start_draft [days] [Rare:50,Epic:35,Legendary:10,Mythic:3,Spring:2]
    Starts the draft event for <days> days (default 30).
    Example: /start_draft 30 Rare:50,Epic:35,Legendary:10,Mythic:3,Spring:2
    """
    if not update.effective_user or not update.message:
        return
    if update.effective_user.id not in (OWNER_ID, CO_OWNER_ID):
        await update.message.reply_text("🚫 Owner only command.")
        return

    import json

    args = list(context.args or [])
    days = 30
    custom_weights = None

    if args:
        try:
            days = int(args[0])
            args = args[1:]
        except ValueError:
            pass

    if args:
        weights_str = " ".join(args)
        try:
            pairs = [p.strip() for p in weights_str.replace(" ", "").split(",")]
            custom_weights = {}
            for pair in pairs:
                rarity, weight = pair.split(":")
                rarity = rarity.capitalize()
                if rarity not in RARITY_EMOJI:
                    await update.message.reply_text(f"❌ Unknown rarity: {rarity}\nExample: /start_draft 30 Rare:50,Epic:35,Legendary:10,Mythic:3,Spring:2")
                    return
                custom_weights[rarity] = int(weight)
        except Exception:
            await update.message.reply_text(
                "❌ Invalid rarity format.\n"
                "Example: /start_draft 30 Rare:50,Epic:35,Legendary:10,Mythic:3,Spring:2"
            )
            return

    now = int(time.time())
    end_ts = now + days * 86400
    weights_to_save = custom_weights if custom_weights else dict(DRAFT_RARITY_WEIGHTS_DEFAULT)

    cursor.execute("INSERT OR REPLACE INTO bot_settings (key, value) VALUES ('draft_active', '1')")
    cursor.execute("INSERT OR REPLACE INTO bot_settings (key, value) VALUES ('draft_start', ?)", (str(now),))
    cursor.execute("INSERT OR REPLACE INTO bot_settings (key, value) VALUES ('draft_end', ?)", (str(end_ts),))
    cursor.execute("INSERT OR REPLACE INTO bot_settings (key, value) VALUES ('draft_rarities', ?)", (json.dumps(weights_to_save),))
    conn.commit()

    load_draft_rarity_weights()

    end_str = time.strftime("%d %b %Y %H:%M UTC", time.gmtime(end_ts))
    weight_lines = "\n".join([f"  {RARITY_EMOJI.get(r, '⭐')} {r}: {w}%" for r, w in weights_to_save.items()])

    # Confirm to owner
    await update.message.reply_text(
        f"✅ *Draft Event Started!*\n\n"
        f"⏳ Duration: *{days} days*\n"
        f"🗓️ Ends: *{end_str}*\n\n"
        f"📊 *Drop Rates:*\n{weight_lines}\n\n"
        f"📢 Notifying all users now...",
        parse_mode="Markdown"
    )

    # Broadcast to all users who have interacted with the bot
    cursor.execute("SELECT DISTINCT user_id FROM bot_users")
    users_set = set(r[0] for r in cursor.fetchall())
    cursor.execute("SELECT DISTINCT user_id FROM user_cards")
    users_set.update(r[0] for r in cursor.fetchall())
    users = list(users_set)

    banner_file_id = get_draft_banner_file_id()
    announce_text = (
        f"🌸 *SPRING BLOSSOM DRAFT EVENT IS LIVE!* 🌸\n\n"
        f"{'━' * 24}\n"
        f"⏳ Event Duration: *{days} days*\n"
        f"🗓️ Ends: *{end_str}*\n"
        f"{'━' * 24}\n\n"
        f"🎰 *Pull rare shinobi cards from the gacha!*\n\n"
        f"📊 *Drop Rates:*\n{weight_lines}\n\n"
        f"🎟️ *How to get vouchers:*\n"
        f"• /daily → +5 vouchers\n"
        f"• /weekly → +5 vouchers\n"
        f"• /monthly → +10 vouchers\n\n"
        f"💎 *Pity System:*\n"
        f"• Every 10th spin → Guaranteed Legendary\n"
        f"• Every 100th spin → Guaranteed Spring card!\n\n"
        f"👉 Use /draft_event to start spinning! 🔥"
    )

    sent = 0
    failed = 0
    for uid in users:
        try:
            if banner_file_id:
                await context.bot.send_photo(
                    chat_id=uid,
                    photo=banner_file_id,
                    caption=announce_text,
                    parse_mode="Markdown"
                )
            else:
                await context.bot.send_message(
                    chat_id=uid,
                    text=announce_text,
                    parse_mode="Markdown"
                )
            sent += 1
            await asyncio.sleep(0.05)
        except Exception:
            failed += 1

    await update.message.reply_text(
        f"📢 *Broadcast Complete!*\n\n"
        f"📨 Notified: *{sent}* users\n"
        f"❌ Failed: *{failed}* users",
        parse_mode="Markdown"
    )

    await log_to_group(
        context.bot,
        f"🌸 *Draft Event STARTED*\n\n"
        f"⏳ Duration: {days} days\n"
        f"🗓️ Ends: {end_str}\n"
        f"📊 Weights: {json.dumps(weights_to_save)}\n"
        f"📨 Notified: {sent} users\n"
        f"👑 By: {update.effective_user.first_name} (`{update.effective_user.id}`)"
    )


async def stop_draft_event(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Owner command: /stop_draft — Immediately stops the draft event."""
    if not update.effective_user or not update.message:
        return
    if update.effective_user.id not in (OWNER_ID, CO_OWNER_ID):
        await update.message.reply_text("🚫 Owner only command.")
        return

    cursor.execute("INSERT OR REPLACE INTO bot_settings (key, value) VALUES ('draft_active', '0')")
    conn.commit()

    await update.message.reply_text(
        "🛑 *Draft Event Stopped!*\n\n"
        "The draft event has been manually ended.\n"
        "📢 Notifying all users...\n\n"
        "Use /start_draft to start a new one anytime.",
        parse_mode="Markdown"
    )

    # Broadcast stop to all users
    cursor.execute("SELECT DISTINCT user_id FROM bot_users")
    users_set = set(r[0] for r in cursor.fetchall())
    cursor.execute("SELECT DISTINCT user_id FROM user_cards")
    users_set.update(r[0] for r in cursor.fetchall())
    users = list(users_set)

    announce_text = (
        "🛑 *The Spring Blossom Draft Event Has Ended!*\n\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        "🌸 Thank you for participating, shinobi!\n\n"
        "💼 Your collected cards and vouchers are safe in your vault.\n\n"
        "🔔 Stay tuned — the next event is coming soon!\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        "Keep grinding! ⚔️🔥"
    )

    sent = 0
    failed = 0
    for uid in users:
        try:
            await context.bot.send_message(
                chat_id=uid,
                text=announce_text,
                parse_mode="Markdown"
            )
            sent += 1
            await asyncio.sleep(0.05)
        except Exception:
            failed += 1

    await update.message.reply_text(
        f"📢 *Stop Broadcast Complete!*\n\n"
        f"📨 Notified: *{sent}* users\n"
        f"❌ Failed: *{failed}* users",
        parse_mode="Markdown"
    )

    await log_to_group(
        context.bot,
        f"🛑 *Draft Event STOPPED*\n"
        f"📨 Notified: {sent} users\n"
        f"👑 By: {update.effective_user.first_name} (`{update.effective_user.id}`)"
    )


# ================= /REC_ACC (Admin/Owner) — Restore deleted collection =================

async def rec_acc(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /rec_acc <user_id>  OR  reply to user's message with /rec_acc
    Restores ALL cards backed up by /delacc for a user.
    Supports: replying to the user's message (no args needed).
    """
    if not update.effective_user or not update.message:
        return
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("🚫 Admins only.")
        return

    # Determine target user — reply takes priority over args
    target_id = None
    target_name = None

    if update.message.reply_to_message and update.message.reply_to_message.from_user:
        target_id = update.message.reply_to_message.from_user.id
        target_name = (
            update.message.reply_to_message.from_user.first_name or str(target_id)
        )
    elif context.args:
        try:
            target_id = int(context.args[0])
        except ValueError:
            await update.message.reply_text("❌ Usage: /rec_acc <user_id>")
            return
    else:
        await update.message.reply_text(
            "Usage:\n"
            "• /rec_acc <user_id>\n"
            "• Reply to the user's message with /rec_acc\n\n"
            "Restores the user's collection from the last /delacc backup."
        )
        return

    # Try to get name if we don't have it yet
    if not target_name:
        try:
            tc = await context.bot.get_chat(target_id)
            target_name = tc.first_name or str(target_id)
        except Exception:
            target_name = str(target_id)

    # Fetch all backup records for this user
    cursor.execute("""
        SELECT card_name, rarity, file_id FROM deleted_cards_backup
        WHERE user_id = ?
        ORDER BY deleted_at ASC
    """, (target_id,))
    backups = cursor.fetchall()

    # Fetch voucher backup
    cursor.execute("""
        SELECT vouchers, total_spins, spins_since_legendary, spins_since_spring
        FROM deleted_vouchers_backup WHERE user_id = ?
    """, (target_id,))
    voucher_backup = cursor.fetchone()

    if not backups and not voucher_backup:
        await update.message.reply_text(
            f"📭 *No backup found for {target_name}* (`{target_id}`).\n\n"
            f"Either their account was never deleted via /delacc,\n"
            f"or an older deletion didn't save a backup.",
            parse_mode="Markdown"
        )
        return

    # ── Restore cards ─────────────────────────────────────────────────────────
    restored = 0
    for card_name, rarity, file_id in backups:
        insert_user_card(target_id, card_name, rarity, file_id)
        restored += 1

    # ── Restore vouchers & spin stats ─────────────────────────────────────────
    restored_vouchers = 0
    if voucher_backup:
        v, total_spins, since_leg, since_spring = voucher_backup
        restored_vouchers = v
        cursor.execute("""
            INSERT OR REPLACE INTO draft_vouchers
            (user_id, vouchers, total_spins, spins_since_legendary, spins_since_spring)
            VALUES (?, ?, ?, ?, ?)
        """, (target_id, v, total_spins, since_leg, since_spring))
        # Clear voucher backup
        cursor.execute("DELETE FROM deleted_vouchers_backup WHERE user_id=?", (target_id,))

    # Clear card backup records after restoring
    cursor.execute("DELETE FROM deleted_cards_backup WHERE user_id=?", (target_id,))
    conn.commit()

    await update.message.reply_text(
        f"✅ *Account Recovery Complete!*\n\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"👤 User: *{target_name}* (`{target_id}`)\n"
        f"🃏 Cards Restored: *{restored}*\n"
        f"🎟️ Vouchers Restored: *{restored_vouchers}*\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"🏆 Full account returned to their vault!",
        parse_mode="Markdown"
    )
    await log_to_group(
        context.bot,
        f"🔄 *Account Recovery*\n\n"
        f"👤 For: {target_name} (`{target_id}`)\n"
        f"🃏 Cards Restored: {restored}\n"
        f"🎟️ Vouchers Restored: {restored_vouchers}\n"
        f"👑 By: {update.effective_user.first_name} (`{update.effective_user.id}`)"
    )

    try:
        await context.bot.send_message(
            chat_id=target_id,
            text=(
                f"🎉 *Your account has been fully recovered!*\n\n"
                f"━━━━━━━━━━━━━━━━━━━━━━\n"
                f"🃏 Cards Restored: *{restored}*\n"
                f"🎟️ Draft Vouchers Restored: *{restored_vouchers}*\n"
                f"━━━━━━━━━━━━━━━━━━━━━━\n\n"
                f"⚔️ Use /collection to view your cards!\n"
                f"🌸 Use /view\\_drafts to check your vouchers!"
            ),
            parse_mode="Markdown"
        )
    except Exception:
        pass


# ================= /GIVE_ALL — Give cards or vouchers to ALL users (Owner only) =================

async def give_all(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Owner only: /give_all <type> <value> <reason>

    Types:
      vouchers <amount> <reason>
        → Give X draft vouchers to every registered user
        → Example: /give_all vouchers 10 Event celebration reward!

      card <card_id> <reason>
        → Give a specific card to every registered user
        → Example: /give_all card 42 Anniversary gift for all shinobis!
    """
    if not update.effective_user or not update.message:
        return

    # Owner only
    if update.effective_user.id != OWNER_ID:
        await update.message.reply_text("🚫 This command is for the *owner* only.", parse_mode="Markdown")
        return

    if not context.args or len(context.args) < 3:
        await update.message.reply_text(
            "📖 *Usage:*\n\n"
            "Give vouchers to all users:\n"
            "`/give_all vouchers <amount> <reason>`\n"
            "_Example: /give_all vouchers 10 Spring event reward!_\n\n"
            "Give a card to all users:\n"
            "`/give_all card <card_id> <reason>`\n"
            "_Example: /give_all card 42 Anniversary gift!_\n\n"
            "⚠️ This affects *every registered user* in the bot.",
            parse_mode="Markdown"
        )
        return

    give_type = context.args[0].lower()
    reason = " ".join(context.args[2:]).strip()

    if not reason:
        await update.message.reply_text("❌ Please provide a reason. Example:\n`/give_all vouchers 10 Happy new year!`", parse_mode="Markdown")
        return

    # ── Fetch all registered users ────────────────────────────────────────────
    cursor.execute("SELECT user_id, first_name FROM bot_users")
    all_users = cursor.fetchall()

    if not all_users:
        await update.message.reply_text("⚠️ No registered users found.")
        return

    owner_name = update.effective_user.first_name or "Owner"

    # ════════════════════════════════════════════════════════════
    # VOUCHERS
    # ════════════════════════════════════════════════════════════
    if give_type == "vouchers":
        try:
            amount = int(context.args[1])
            if amount <= 0:
                raise ValueError
        except ValueError:
            await update.message.reply_text("❌ Amount must be a positive number.")
            return

        await update.message.reply_text(
            f"⏳ *Distributing {amount} vouchers to {len(all_users)} users...*\n\n"
            f"📋 Reason: _{reason}_\n\n"
            f"Please wait...",
            parse_mode="Markdown"
        )

        success = 0
        failed = 0
        for uid, fname in all_users:
            try:
                add_vouchers(uid, amount)
                success += 1
            except Exception as e:
                print(f"[GIVE_ALL] Voucher error for {uid}: {e}")
                failed += 1

        conn.commit()

        # Notify all users
        notified = 0
        notify_failed = 0
        for uid, fname in all_users:
            try:
                total_v = get_user_vouchers(uid)
                await context.bot.send_message(
                    chat_id=uid,
                    text=(
                        f"🎁 *Special Gift from the Owner!*\n\n"
                        f"━━━━━━━━━━━━━━━━━━━━━━\n"
                        f"🎟️ *+{amount} Draft Vouchers* have been added to your wallet!\n"
                        f"💼 Your total vouchers: *{total_v}*\n"
                        f"━━━━━━━━━━━━━━━━━━━━━━\n\n"
                        f"📋 *Reason:* {reason}\n\n"
                        f"🌸 Use /draft\\_event to spin!\n"
                        f"⚔️ — {owner_name}"
                    ),
                    parse_mode="Markdown"
                )
                notified += 1
                await asyncio.sleep(0.05)
            except Exception:
                notify_failed += 1

        await update.message.reply_text(
            f"✅ *Voucher Distribution Complete!*\n\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"🎟️ Amount: *+{amount}* vouchers each\n"
            f"👥 Total Users: *{len(all_users)}*\n"
            f"✅ Credited: *{success}*\n"
            f"📨 Notified: *{notified}*\n"
            f"❌ Failed: *{notify_failed}*\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"📋 Reason: _{reason}_",
            parse_mode="Markdown"
        )
        await log_to_group(
            context.bot,
            f"🎁 *Give All — Vouchers*\n\n"
            f"👑 By: {owner_name} (`{update.effective_user.id}`)\n"
            f"🎟️ Amount: +{amount} each\n"
            f"👥 Users: {len(all_users)}\n"
            f"📋 Reason: {reason}"
        )

    # ════════════════════════════════════════════════════════════
    # CARD
    # ════════════════════════════════════════════════════════════
    elif give_type == "card":
        try:
            card_id = int(context.args[1].replace("#", ""))
        except ValueError:
            await update.message.reply_text("❌ Invalid card ID. Example:\n`/give_all card 42 Anniversary gift!`", parse_mode="Markdown")
            return

        cursor.execute("SELECT name, rarity, file_id FROM cards WHERE id=?", (card_id,))
        card = cursor.fetchone()
        if not card:
            await update.message.reply_text(f"❌ No card found with ID #{card_id}.")
            return

        card_name, rarity, file_id = card
        emoji = RARITY_EMOJI.get(rarity, "⭐")

        await update.message.reply_text(
            f"⏳ *Distributing card to {len(all_users)} users...*\n\n"
            f"🥷 Card: *{card_name.title()}* {emoji}\n"
            f"📋 Reason: _{reason}_\n\n"
            f"Please wait...",
            parse_mode="Markdown"
        )

        success = 0
        failed = 0
        for uid, fname in all_users:
            try:
                insert_user_card(uid, card_name, rarity, file_id)
                success += 1
            except Exception as e:
                print(f"[GIVE_ALL] Card error for {uid}: {e}")
                failed += 1

        conn.commit()

        # Notify all users
        notified = 0
        notify_failed = 0
        for uid, fname in all_users:
            try:
                await context.bot.send_photo(
                    chat_id=uid,
                    photo=file_id,
                    caption=(
                        f"🎁 *Special Gift from the Owner!*\n\n"
                        f"━━━━━━━━━━━━━━━━━━━━━━\n"
                        f"🥷 *{card_name.title()}* has been added to your collection!\n"
                        f"{emoji} Rarity: *{rarity}*\n"
                        f"━━━━━━━━━━━━━━━━━━━━━━\n\n"
                        f"📋 *Reason:* {reason}\n\n"
                        f"⚔️ Use /collection to view your vault!\n"
                        f"🍃 — {owner_name}"
                    ),
                    parse_mode="Markdown"
                )
                notified += 1
                await asyncio.sleep(0.05)
            except Exception:
                notify_failed += 1

        await update.message.reply_text(
            f"✅ *Card Distribution Complete!*\n\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"🥷 Card: *{card_name.title()}* {emoji}\n"
            f"🔮 Rarity: *{rarity}*\n"
            f"👥 Total Users: *{len(all_users)}*\n"
            f"✅ Added to: *{success}*\n"
            f"📨 Notified: *{notified}*\n"
            f"❌ Failed: *{notify_failed}*\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"📋 Reason: _{reason}_",
            parse_mode="Markdown"
        )
        await log_to_group(
            context.bot,
            f"🎁 *Give All — Card*\n\n"
            f"👑 By: {owner_name} (`{update.effective_user.id}`)\n"
            f"🥷 Card: #{card_id} {card_name} [{rarity}]\n"
            f"👥 Users: {len(all_users)}\n"
            f"📋 Reason: {reason}"
        )

    else:
        await update.message.reply_text(
            "❌ Unknown type. Use:\n"
            "`/give_all vouchers <amount> <reason>`\n"
            "`/give_all card <card_id> <reason>`",
            parse_mode="Markdown"
        )


# ================= RARITY =================

async def rarity_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.effective_user or not update.message:
        return
    user_id = update.effective_user.id
    if is_banned(user_id):
        await update.message.reply_text(get_ban_message(user_id), parse_mode="Markdown")
        return
    lines = ""
    for rarity, emoji in RARITY_EMOJI.items():
        cursor.execute("SELECT COUNT(*) FROM cards WHERE rarity=?", (rarity,))
        count = cursor.fetchone()[0]
        if count > 0:
            lines += f"• {emoji} {rarity}: {count} card(s)\n"
    text = (
        f"🔹 *All Rarities in Naruto TCG Bot*\n\n"
        f"{lines}\n"
        f"🔍 *Search cards inline:*\n"
        f"`@YourBotUsername` → show all cards\n"
        f"`@YourBotUsername rarity:Mythic` → by rarity\n"
        f"`@YourBotUsername Naruto` → by name"
    )
    await update.message.reply_text(text, parse_mode="Markdown")


# ================= MAIN =================

# ================= /RANDOM_PICK — Giveaway Picker (Owner/Admin) =================

async def random_pick(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /random_pick <number> — Randomly pick N users from all who have started the bot.
    Shows their Telegram name, ID, and username.
    Owner/Admin only.
    """
    if not update.effective_user or not update.message:
        return
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("🚫 Admins only.")
        return

    if not context.args:
        await update.message.reply_text(
            "🎲 *Random Pick*\n\n"
            "Usage: /random\\_pick <number>\n"
            "Example: /random\\_pick 3\n\n"
            "Picks N random users from all who have started the bot.",
            parse_mode="Markdown"
        )
        return

    try:
        count = int(context.args[0])
        if count <= 0:
            raise ValueError
    except ValueError:
        await update.message.reply_text("❌ Please enter a valid positive number.")
        return

    # Get all bot users
    cursor.execute("SELECT user_id, first_name, username FROM bot_users")
    all_users = cursor.fetchall()

    if not all_users:
        await update.message.reply_text("❌ No users have started the bot yet!")
        return

    if count > len(all_users):
        await update.message.reply_text(
            f"⚠️ Only *{len(all_users)}* users have started the bot.\n"
            f"Picking all of them.",
            parse_mode="Markdown"
        )
        count = len(all_users)

    picked = random.sample(all_users, count)

    lines = ""
    for i, (uid, fname, uname) in enumerate(picked, 1):
        name_display = fname or "Unknown"
        tag_display = f"@{uname}" if uname else "_(no username)_"
        lines += f"{i}. *{name_display}* | {tag_display} | `{uid}`\n"

    result_text = (
        f"🎲 *Giveaway Random Pick Results!* 🎉\n\n"
        f"{'━' * 24}\n"
        f"🎯 Picked: *{count}* winner(s) from *{len(all_users)}* users\n"
        f"{'━' * 24}\n\n"
        f"🏆 *Winners:*\n\n"
        f"{lines}\n"
        f"{'━' * 24}\n"
        f"🌟 Congratulations to all winners! ⚔️"
    )

    await update.message.reply_text(result_text, parse_mode="Markdown")
    await log_to_group(
        context.bot,
        f"🎲 *Random Pick Done*\n\n"
        f"🎯 Count: {count}\n"
        f"👥 Pool: {len(all_users)} users\n"
        f"🏆 Winners:\n{lines}\n"
        f"👑 By: {update.effective_user.first_name} (`{update.effective_user.id}`)"
    )


# ================= WORD GAME =================

ANIME_NAMES = [
    "Naruto","Sasuke","Sakura","Kakashi","Itachi","Gaara","Rock Lee","Hinata","Neji","Tenten",
    "Shikamaru","Ino","Choji","Kiba","Shino","Temari","Kankuro","Jiraiya","Tsunade","Orochimaru",
    "Minato","Kushina","Obito","Madara","Hashirama","Tobirama","Hiruzen","Danzo","Nagato","Konan",
    "Yahiko","Kisame","Deidara","Sasori","Hidan","Kakuzu","Zetsu","Tobi","Kabuto","Sai",
    "Yamato","Anko","Kurenai","Asuma","Might Guy","Iruka","Shizune","Karin","Suigetsu","Jugo",
    "Killer Bee","Ay","Mei","Onoki","Gaara","Fu","Torune","Sora","Utakata","Yugito",
    "Han","Roshi","Fuu","Chomei","Saiken","Kokuo","Son Goku","Gyuki","Kurama","Shukaku",
    "Matatabi","Isobu","Isaribi","Menma","Guren","Yukimaru","Sora","Filler","Amaru","Sora",
    "Goku","Vegeta","Gohan","Piccolo","Frieza","Cell","Majin Buu","Trunks","Goten","Krillin",
    "Bulma","Chi-Chi","Master Roshi","Yamcha","Tien","Chiaotzu","Android 17","Android 18","Gotenks","Broly",
    "Bardock","Raditz","Nappa","Ginyu","Zarbon","Dodoria","Recoome","Burter","Jeice","Guldo",
    "Android 16","Imperfect Cell","Semi-Perfect Cell","Perfect Cell","Super Buu","Kid Buu","Fat Buu",
    "Dabura","Babidi","Pui Pui","Yakon","Kibito","Supreme Kai","Old Kai","Beerus","Whis","Champa",
    "Vados","Gowasu","Zamasu","Goku Black","Hit","Cabba","Caulifla","Kale","Kefla","Jiren",
    "Toppo","Dyspo","Ribrianne","Rozie","Katopesla","Maji Kayo","Bergamo","Lavender","Basil",
    "Luffy","Zoro","Nami","Usopp","Sanji","Chopper","Robin","Franky","Brook","Jinbe",
    "Ace","Sabo","Shanks","Blackbeard","Whitebeard","Garp","Sengoku","Akainu","Aokiji","Kizaru",
    "Crocodile","Doflamingo","Katakuri","Big Mom","Kaido","Law","Kid","Killer","Hawkins","Apoo",
    "Boa Hancock","Rayleigh","Coby","Helmeppo","Smoker","Tashigi","Wiper","Ohm","Gedatsu","Satori",
    "Enel","Arlong","Don Krieg","Buggy","Alvida","Foxy","Perona","Moria","Absalom","Bartholomew Kuma",
    "Daz Bones","Mr 1","Bon Clay","Mr 2","Bentham","Ivankov","LeaderBoard","Koala","Hack","Bartolomeo",
    "Cavendish","Rebecca","Kyros","Viola","Sugar","Trebol","Pica","Diamante","Baby 5","Buffalo",
    "Ichigo","Rukia","Orihime","Chad","Uryu","Renji","Byakuya","Kenpachi","Mayuri","Nemu",
    "Ukitake","Kyoraku","Unohana","Retsu","Gin","Aizen","Tosen","Grimmjow","Ulquiorra","Nnoitra",
    "Stark","Barragan","Halibel","Szayelaporro","Aaroniero","Yammy","Wonderweiss","Luppi","Dordonii",
    "Ishida Ryuken","Isshin","Yoruichi","Urahara","Tessai","Jinta","Ururu","Hiyori","Lisa","Love",
    "Kensei","Mashiro","Hachigen","Rose","Shinji","Nel","Pesche","Dondochakka","Bawabawa",
    "Edward","Alphonse","Roy","Riza","Winry","Pinako","Hughes","Armstrong","Olivier","Izumi",
    "Scar","Envy","Lust","Gluttony","Greed","Sloth","Pride","Wrath","Selim","Father","Hohenheim",
    "Ling","Lan Fan","Fu","May","Xiao-Mei","Yoki","Barry","Breda","Havoc","Fuery","Falman",
    "Buccaneer","Miles","Kimblee","Dolcetto","Roa","Martel","Zampano","Jelso","Heinkel",
    "Light","L","Misa","Near","Mello","Matt","Ryuk","Rem","Gelus","Sidoh","Kyosuke",
    "Soichiro","Matsuda","Aizawa","Mogi","Ide","Touta","Sayu","Sachiko","Watari","Roger",
    "Sora","Shiro","Stephanie","Jibril","Izuna","Kurami","Chlammy","Ino","Plum","Laila",
    "Kirito","Asuna","Sinon","Leafa","Lisbeth","Silica","Klein","Agil","Heathcliff","Sugou",
    "Alice","Eugeo","Selka","Administrator","Cardinal","Bercouli","Fanatio","Deusolbert","Eldrie",
    "Linel","Fizel","Renly","Sheyta","Iskahn","Scheta","Quinella","Chudelkin","Raios","Humbert",
    "Deku","Bakugo","Ochako","Todoroki","Iida","Tsuyu","Mineta","Momo","Jiro","Tokoyami",
    "Aoyama","Koda","Ojiro","Sero","Shoji","Hagakure","Sato","Kaminari","Kirishima","Ashido",
    "All Might","Aizawa","Present Mic","Midnight","Thirteen","Cementoss","Ectoplasm","Ectoplasm",
    "Recovery Girl","Gran Torino","Endeavor","Hawks","Best Jeanist","Edgeshot","Gang Orca",
    "Mirko","Fat Gum","Ryukyu","Mt Lady","Kamui Woods","Death Arms","Backdraft",
    "Shigaraki","Dabi","Toga","Twice","Mr Compress","Spinner","Muscular","Mustard","Moonfish",
    "Stain","Overhaul","Chronostasis","Mimic","Nemoto","Shin","Eri","Nighteye","Rock Lock",
    "Lemillion","Suneater","Charge Bolt","Pinky","Creati","Earphone Jack","Froppy","Uravity",
    "Simon","Kamina","Yoko","Nia","Viral","Lordgenome","Rossiu","Leeron","Kittan","Kinon",
    "Kiyoh","Gimmy","Darry","Attenborough","Jorgun","Balinbow","Makken","Iraak","Zorthy",
    "Levi","Eren","Mikasa","Armin","Hange","Erwin","Historia","Ymir","Connie","Sasha",
    "Jean","Marco","Annie","Reiner","Bertholdt","Zeke","Porco","Pieck","Gabi","Falco",
    "Yelena","Floch","Daz","Samuel","Louise","Nile","Dot","Rico","Oluo","Petra","Eld","Gunther",
    "Izayoi","Asuka","You","Shirayuki","Kudou","Shiroyasha","Sandora","Laius","Pest","Almathea",
    "Gildarts","Erza","Gray","Lucy","Happy","Natsu","Wendy","Carla","Elfman","Evergreen",
    "Mirajane","Cana","Loke","Levy","Jet","Droy","Bisca","Alzack","Warren","Max","Nab","Daphne",
    "Makarov","Mavis","Zeref","Acnologia","August","Eileen","Irene","Larcade","DiMaria","Bloodman",
    "Invel","Jacob","Wall","God Serena","Ajeel","Brandish","Neinhart","Zera","Sting","Rogue",
    "Yukino","Frosch","Lector","Rufus","Orga","Minerva","Dobengal","Araña","Beth","Risley",
    "Kageyama","Hinata Shoyo","Tsukishima","Yamaguchi","Daichi","Sugawara","Azumane","Nishinoya",
    "Tanaka","Ennoshita","Narita","Kinoshita","Shimizu","Yachi","Bokuto","Akaashi","Kuroo",
    "Kenma","Lev","Yaku","Inuoka","Fukunaga","Oikawa","Iwaizumi","Hanamaki","Matsukawa",
    "Kyotani","Yahaba","Kindaichi","Kunimi","Ushijima","Tendou","Semi","Shirabu","Kawanishi",
    "Goshiki","Futakuchi","Aone","Obara","Koganegawa","Sakunami","Daishou","Numai","Hiroo",
    "Osamu","Atsumu","Kita","Aran","Suna","Ginjima","Omimi","Akagi","Komori","Sakusa","Motoya",
    "Kuguri","Nohebi","Kobayashi","Bessho","Hanayama","Amai Mask","Metal Bat","Atomic Samurai",
    "Tatsumaki","Fubuki","Bang","Bomb","Child Emperor","Pig God","Tornado","Flash",
    "Genos","King","Saitama","Puri Puri","Drive Knight","Watchdog Man","Metal Knight",
    "Boros","Garou","Elder Centipede","Orochi","Psykos","Gyoro","Do-S","Gums","Nyan","Black Sperm",
    "Fuhrer Ugly","Homeless Emperor","Evil Eye","Pluton","Carnage Kabuto","Awakened Cockroach",
    "Rimuru","Milim","Veldora","Ranga","Benimaru","Shion","Gobta","Ranga","Soei","Geld",
    "Hakurou","Gabiru","Treyni","Diablo","Ultima","Carrera","Zegion","Adalman","Chloe","Hinata",
    "Souei","Kurobe","Vesta","Mjolmil","Byond","Albis","Moyuira","Apito","Suphia","Lupa",
    "Ainz","Albedo","Shalltear","Demiurge","Cocytus","Mare","Aura","Sebas","Solution","Lupusregina",
    "Yuri","CZ","Entoma","Pandora","Gazef","Brain","Climb","Evileye","Gagaran","Tia",
    "Lakyus","Nabe","Nfirea","Enri","Carne","Brita","Zenberu","Zaryusu","Crusch","Shasuryu",
    "Emilia","Subaru","Rem","Ram","Beatrice","Roswaal","Felt","Reinhard","Julius","Wilhelm",
    "Crusch","Ferris","Ricardo","Anastasia","Otto","Garfiel","Elsa","Meili","Petelgeuse",
    "Betelgeuse","Echidna","Satella","Pandora","Stride","Regulus","Sirius","Capet","Capella",
    "Kazuma","Aqua","Megumin","Darkness","Wiz","Vanir","Yunyun","Chris","Eris","Chomusuke",
    "Hans","Dust","Taylor","Keith","Cecily","Beldia","Verdia","Dullahan","Sylvia","Wolbach",
    "Raphtalia","Naofumi","Filo","Melty","Malty","Motoyasu","Ren","Itsuki","Glass","LHero",
    "Rishia","Eclair","Elrasla","Therese","LHero","Kizuna","Ethnobalt","Romina","Nadia","Yomogi",
    "Overlord","Touch Me","Momonga","Peroroncino","Bukubukuchagama","Warrior Takemikazuchi",
    "Zarenthirst","Rigrit","Blue Rose","Momon","Nabe","Pandora Actor","Victim","Neuronist",
    "Isidro","Puck","Schierke","Serpico","Farnese","Casca","Griffith","Guts","Judeau","Rickert",
    "Pippin","Corkus","Adon","Rakshas","Locus","Grunbeld","Irvine","Zodd","Wyald","Rosine",
    "Jura","Slan","Ubik","Conrad","Femto","Skull Knight","Flora","Manifico","Mozgus","Jerome",
    "Tatsuya","Miyuki","Erika","Leo","Mikihiko","Honoka","Shizuku","Mizuki","Azusa","Subaru",
    "Masaki","Mayumi","Mari","Katsuto","Kei","Naotsugu","Nyanta","Akatsuki","Shiroe","Lenessia",
    "Crusty","Minori","Touya","Rundelhaus","Serara","Isuzu","Krusty","Rieze","Raynesia",
    "Indicus","Elias","Guin","Woodstock","Charasin","Soujiro","Nazuna","Dolce","Kawara","Hien",
    "Alibaba","Aladdin","Morgiana","Sinbad","Judal","Hakuryuu","Kougyoku","Ja'far","Masrur",
    "Yamuraiha","Sharrkan","Pisti","Spartos","Drakon","Hinahoho","Rurumu","Serendine","Muu",
    "Lo'lo","Myron","Scheherazade","Titus","Sphintus","Doron","Nerva","Ignatius","Arba","Sheba",
    "Solomon","David","Ugo","Ren","Kouha","Koumei","Kouen","Hakuren","Gyokuen","Isaac",
    "Kirishima Touka","Hide","Kaneki","Rize","Nishiki","Hinami","Itori","Yomo","Yoshimura",
    "Uta","Tsukiyama","Chie","Shachi","Koma","Irimi","Banjo","Seidou","Akira","Amon",
    "Takizawa","Mado Kureo","Juuzou","Shinohara","Houji","Suzuya","Ihei","Chigyou","Miza",
    "Torso","Naki","Kanae","Ayato","Matasaka","Hinami Fueguchi","Tatara","Hairu","Ui",
    "Mutsuki","Urie","Saiko","Shirazu","Hsiao","Roma","Seidou Takizawa","Furuta","Hajime",
    "Nimura","Abara","Koori","Kijima","Arima","Eto","Yusa","Takatsuki","Coyote","Donato",
    "Isekai Quartet","Tanya","Viktoriya","Erich","Kurt","Matheus","Anson","Lehrgen","Zettour",
    "Rudersdorf","Romel","Ugar","Neumann","Grantz","Weiss","Koenig","Elya","Adolfine","Schugel",
    "Vanir","Being X","Falke","Meybert","Rerugen","Lergen","Calandro","Daken","Thoma","Karin",
    "Yuu","Mika","Shinoa","Mitsuba","Kimizuki","Crowley","Ferid","Krul","Lacus","Rene","Chess",
    "Horn","Asuramaru","Gekkouin","Mahiru","Noya","Shikama","Rigr","Urd","Skuld","Verdandi",
    "Anos","Misha","Sasha","Lay","Melheis","Ledoriano","Laos","Ledriano","Zeshia","Nono",
    "Emilia","Kanon","Eleonore","Ahide","Ivis","Dull","Diego","Avos","Libest","Aivis",
    "Zepes","Ivis","Melim","Shin","Ray","Emma","Norman","Isabella","Phil","Nat","Don",
    "Gilda","Anna","Thoma","Lannion","Dominic","Christy","Alicia","Yvette","Violet","Nigel",
    "Oliver","Zack","Krone","Peter","Andrew","James","Zazie","Cislo","Barbara","Violet",
    "Sonju","Mujika","Sung Jinwoo","Cha Haein","Go Gunhee","Choi Jongin","Thomas Andre",
    "Liu Zhiyao","Christopher","Jonas","Hwang Dongsoo","Baek Yoonho","Woo Jinchul","Park Jongsoo",
    "Min Byunggyu","Yoo Jinho","Lim Tae-gyu","Kim Chul","Lee Joohee","Joo Soohyun","Son Kiyong",
    "Sung Ilhwan","Park Kyungsoo","Amamiya Hiroto","Goto Ryuji","Igris","Iron","Tank","Bellion",
    "Kamish","Greed Island","Gon","Killua","Kurapika","Leorio","Hisoka","Illumi","Chrollo",
    "Feitan","Phinks","Nobunaga","Shizuku","Bonolenov","Machi","Kortopi","Pakunoda","Uvogin",
    "Neferpitou","Shaiapouf","Menthuthuyoupi","Meruem","Komugi","Netero","Zeno","Silva","Kikyo",
    "Alluka","Nanika","Milluki","Kalluto","Biscuit","Wing","Zushi","Hanzo","Gido","Riehlvelt",
    "Sadaso","Bodoro","Kastro","Goreinu","Tsezguerra","Genthru","Bara","Sub","Knov","Morel",
    "Knuckle","Shoot","Ikalgo","Palm","Meleoron","Welfin","Bloster","Hina","Shidore","Bizeff",
    "Pegasus","Heroine","Triela","Henrietta","Claes","Angelica","Rico","Hilshire","Giuse","Jean",
    "Marco","Olga","Elsa","Baldo","Priscilla","Cristiano","Ferro","Alessandro","Giorgio","Victor",
    "Alphonse","Caterina","Enrico","William","Iscariot","Major","Schrodinger","Doc","Jan",
    "Heinkel","Yumie","Maxwell","Anderson","Rip","Zorin","Millennium","Tubalcain","Luke","Jan",
    "Joshua","Hannah","Rebecca","Pip","Seras","Walter","Integra","Alucard","Vladcard","Dracula",
]

# Deduplicate and sort
ANIME_NAMES_SET = {n.lower() for n in ANIME_NAMES}
ANIME_NAMES_LIST = sorted(set(ANIME_NAMES))

# Word game state per chat
word_games: dict = {}
# Keep strong references to background asyncio tasks so they aren't garbage collected
_background_tasks: set = set()

WORD_GAME_ROUNDS = [
    {"letters": 3,  "time": 45, "rounds": 2},
    {"letters": 5,  "time": 30, "rounds": 2},
    {"letters": 8,  "time": 20, "rounds": 2},
    {"letters": 10, "time": 15, "rounds": 999},  # final phase, plays till 1 winner
]

WORDGAME_CRATE_WEIGHTS = {
    "Common":   30,
    "Rare":     30,
    "Epic":     20,
    "vouchers": 10,
    "Legendary": 8,
    "Mythic":    1,
    "Spring":    1,
}

def get_wordgame_phase(total_rounds_played: int):
    """Return current phase config based on how many rounds have been played."""
    count = 0
    for phase in WORD_GAME_ROUNDS:
        count += phase["rounds"]
        if total_rounds_played < count:
            return phase
    return WORD_GAME_ROUNDS[-1]

async def give_wordgame_prize(bot, user_id: int, user_name: str, chat_id: int):
    """Give a random crate prize to the word game winner."""
    rarities = list(WORDGAME_CRATE_WEIGHTS.keys())
    weights = list(WORDGAME_CRATE_WEIGHTS.values())
    prize = random.choices(rarities, weights=weights, k=1)[0]

    if prize == "vouchers":
        add_vouchers(user_id, 5)
        prize_text = "🎟️ *5 Draft Vouchers*"
        await bot.send_message(
            chat_id=chat_id,
            text=(
                f"🏆 *Word Game Winner!*\n\n"
                f"🥇 {user_name} wins the Random Crate!\n\n"
                f"🎁 *Prize:* {prize_text}\n\n"
                f"🎟️ 5 Draft Vouchers added to your wallet!\n"
                f"Use /draft\\_event to spin! 🌸"
            ),
            parse_mode="Markdown"
        )
    else:
        # Give a card of that rarity
        cursor.execute(
            "SELECT name, rarity, file_id FROM cards WHERE rarity=? ORDER BY RANDOM() LIMIT 1",
            (prize,)
        )
        card = cursor.fetchone()
        if card:
            name, rarity, file_id = card
            insert_user_card(user_id, name, rarity, file_id)
            conn.commit()
            emoji = RARITY_EMOJI.get(rarity, "⭐")
            await bot.send_photo(
                chat_id=chat_id,
                photo=file_id,
                caption=(
                    f"🏆 *Word Game Winner!*\n\n"
                    f"🥇 {user_name} wins the Random Crate!\n\n"
                    f"🎁 *Prize:*\n"
                    f"{emoji} {name.title()} [{rarity}]\n\n"
                    f"Card added to your vault! ⚔️"
                ),
                parse_mode="Markdown"
            )
        else:
            # Fallback: give vouchers if no card found
            add_vouchers(user_id, 5)
            await bot.send_message(
                chat_id=chat_id,
                text=f"🏆 *{user_name}* wins! 🎟️ +5 Draft Vouchers (no {prize} cards available).",
                parse_mode="Markdown"
            )

async def wordgame_turn_timer(context):
    """Job callback — fires when a player's turn timer expires."""
    job = context.job
    chat_id = job.data["chat_id"]
    expected_user_id = job.data["user_id"]
    game = word_games.get(chat_id)
    if not game or game.get("status") != "playing":
        return
    if game.get("current_player") != expected_user_id:
        return  # Already moved on

    # Eliminate current player
    player_name = game["player_names"].get(expected_user_id, "Unknown")
    game["players"].discard(expected_user_id)
    game["eliminated"].add(expected_user_id)
    remaining = len(game["players"])
    total_started = game["total_started"]

    await context.bot.send_message(
        chat_id=chat_id,
        text=(
            f"⏰ *Time's up!* {player_name} was *eliminated!*\n\n"
            f"👥 Players remaining: *{remaining}/{total_started}*"
        ),
        parse_mode="Markdown"
    )

    if remaining <= 1:
        await end_wordgame(context.bot, chat_id, context.application.job_queue)
        return

    await next_wordgame_turn(context.bot, chat_id, context.application.job_queue)


async def _run_turn_timer(bot, chat_id: int, user_id: int, turn_time: int, token: list, job_queue):
    """
    Asyncio-based turn timer — more reliable than job_queue.
    token is a single-element list; if replaced, this timer is cancelled.
    """
    await asyncio.sleep(turn_time)

    # Check token is still valid (not replaced by a newer timer)
    game = word_games.get(chat_id)
    if not game or game.get("status") != "playing":
        return
    if game.get("_timer_token") is not token:
        return  # A newer timer has replaced this one
    if game.get("current_player") != user_id:
        return

    # Eliminate player
    player_name = game["player_names"].get(user_id, "Unknown")
    game["players"].discard(user_id)
    game["eliminated"].add(user_id)
    remaining = len(game["players"])
    total_started = game["total_started"]

    try:
        await bot.send_message(
            chat_id=chat_id,
            text=(
                f"⏰ *Time's up!* {player_name} was *eliminated!*\n\n"
                f"👥 Players remaining: *{remaining}/{total_started}*"
            ),
            parse_mode="Markdown"
        )
    except Exception as e:
        print(f"[WORDGAME] Timer send error: {e}")

    if remaining <= 1:
        await end_wordgame(bot, chat_id, job_queue)
        return

    await next_wordgame_turn(bot, chat_id, job_queue)

async def next_wordgame_turn(bot, chat_id: int, job_queue):
    """Advance to the next player's turn."""
    game = word_games.get(chat_id)
    if not game:
        return

    players = list(game["players"])
    if not players:
        await end_wordgame(bot, chat_id, job_queue)
        return

    # Advance round counter and phase
    game["round_count"] += 1
    phase = get_wordgame_phase(game["round_count"])
    min_len = phase["letters"]
    turn_time = phase["time"]
    game["min_letters"] = min_len
    game["turn_time"] = turn_time

    # Pick next player (rotate)
    current_idx = game.get("current_idx", -1)
    current_idx = (current_idx + 1) % len(players)
    # Make sure we land on a non-eliminated player
    attempts = 0
    while players[current_idx] in game["eliminated"] and attempts < len(players):
        current_idx = (current_idx + 1) % len(players)
        attempts += 1

    game["current_idx"] = current_idx
    current_player = players[current_idx]
    game["current_player"] = current_player
    player_name = game["player_names"].get(current_player, "Unknown")

    remaining = len(game["players"])
    total_started = game["total_started"]

    # Determine required starting letter
    required_letter = game.get("current_letter")
    if required_letter:
        letter_line = f"🔤 Must start with: *{required_letter.upper()}*\n"
    else:
        # First turn — pick a random letter
        required_letter = random.choice("abcdefghijklmnoprstwy")
        game["current_letter"] = required_letter
        letter_line = f"🎲 Starting letter: *{required_letter.upper()}*\n"

    await bot.send_message(
        chat_id=chat_id,
        text=(
            f"🎮 *Round {game['round_count']}*\n\n"
            f"🥷 *{player_name}'s* turn!\n"
            f"⏱️ Time: *{turn_time}s*\n"
            f"📝 Min letters: *{min_len}*\n"
            f"{letter_line}\n"
            f"Name an anime character starting with *{required_letter.upper()}* "
            f"with *{min_len}+ letters* (spaces not counted) that hasn't been used!\n\n"
            f"👥 *{remaining}/{total_started}* players remaining"
        ),
        parse_mode="Markdown"
    )

    # Cancel old asyncio timer task if still running
    old_task = game.get("_timer_task")
    if old_task and not old_task.done():
        old_task.cancel()
        _background_tasks.discard(old_task)

    # Create a unique token — stale timers compare against this to self-cancel
    token = [object()]
    game["_timer_token"] = token
    game["timer_job"] = None

    # Schedule elimination via asyncio task.
    # Store in _background_tasks to prevent garbage collection killing the timer.
    task = asyncio.create_task(
        _run_turn_timer(bot, chat_id, current_player, turn_time, token, job_queue)
    )
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)
    game["_timer_task"] = task

async def end_wordgame(bot, chat_id: int, job_queue):
    """End the word game and give prize."""
    game = word_games.get(chat_id)
    if not game:
        return

    # Cancel asyncio timer task if running
    old_task = game.get("_timer_task")
    if old_task and not old_task.done():
        old_task.cancel()
        _background_tasks.discard(old_task)
    # Invalidate any pending token so stale tasks won't fire
    game["_timer_token"] = None

    players = list(game["players"])
    game["status"] = "ended"

    if not players:
        await bot.send_message(chat_id=chat_id, text="😮 Everyone was eliminated! No winner this time!")
        word_games.pop(chat_id, None)
        return

    winner_id = players[0]
    winner_name = game["player_names"].get(winner_id, "Unknown")

    await bot.send_message(
        chat_id=chat_id,
        text=(
            f"🎉 *GAME OVER!*\n\n"
            f"🏆 *Winner: {winner_name}!*\n\n"
            f"🎁 Opening your *Random Crate*..."
        ),
        parse_mode="Markdown"
    )

    await asyncio.sleep(1.5)
    await give_wordgame_prize(bot, winner_id, winner_name, chat_id)

    await log_to_group(
        bot,
        f"🎮 *Word Game Ended*\n\n"
        f"🏆 Winner: {winner_name} (`{winner_id}`)\n"
        f"👥 Players: {game['total_started']}\n"
        f"🔠 Chat: `{chat_id}`"
    )

    word_games.pop(chat_id, None)


async def start_wordgame(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin: /start_wordgame — Start a new word game in the group."""
    if not update.effective_user or not update.message:
        return
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("🚫 Admins only.")
        return

    chat_id = update.effective_chat.id
    if chat_id in word_games and word_games[chat_id].get("status") in ("joining", "playing"):
        await update.message.reply_text("⚠️ A word game is already running! Use /stop_wordgame to end it.")
        return

    word_games[chat_id] = {
        "status": "joining",
        "players": set(),
        "player_names": {},
        "player_usernames": {},
        "eliminated": set(),
        "used_words": set(),
        "current_player": None,
        "current_idx": -1,
        "round_count": 0,
        "min_letters": 3,
        "turn_time": 45,
        "timer_job": None,
        "total_started": 0,
        "start_job": None,
        "warn_job": None,
        "current_letter": None,  # Required starting letter for next player
    }

    instructions = (
        f"🎮 *ANIME WORD GAME — Starting Soon!* 🎮\n\n"
        f"{'━' * 24}\n"
        f"📜 *How to Play:*\n"
        f"• Type an *anime character name* on your turn\n"
        f"• Names must not have been used before\n"
        f"• If your time runs out → you're *eliminated!*\n"
        f"• Last player standing wins a *Random Crate!* 🎁\n\n"
        f"{'━' * 24}\n"
        f"⏱️ *Round Structure:*\n"
        f"🔵 Phase 1: 3+ letters | 45 sec (2 rounds)\n"
        f"🟡 Phase 2: 5+ letters | 30 sec (2 rounds)\n"
        f"🟠 Phase 3: 8+ letters | 20 sec (2 rounds)\n"
        f"🔴 Phase 4: 10+ letters | 15 sec (until 1 winner)\n\n"
        f"{'━' * 24}\n"
        f"🎁 *Prize Pool:*\n"
        f"🍥 Common: 30% | 🌀 Rare: 30%\n"
        f"⚡ Epic: 20% | 🎟️ 5 Vouchers: 10%\n"
        f"👑 Legendary: 8% | 🦊 Mythic: 1% | 🌸 Spring: 1%\n\n"
        f"{'━' * 24}\n"
        f"👇 Type /join to participate!\n"
        f"⏳ *Game starts in 60 seconds!*"
    )

    await update.message.reply_text(instructions, parse_mode="Markdown")

    # Schedule 30s warning
    warn_job = context.application.job_queue.run_once(
        wordgame_warning_job,
        when=30,
        data={"chat_id": chat_id},
        name=f"wg_warn_{chat_id}"
    )
    word_games[chat_id]["warn_job"] = warn_job

    # Auto-start after 60 seconds
    job = context.application.job_queue.run_once(
        begin_wordgame_job,
        when=60,
        data={"chat_id": chat_id},
        name=f"wg_start_{chat_id}"
    )
    word_games[chat_id]["start_job"] = job


async def wordgame_warning_job(context):
    """Job: send 30-second warning before game starts."""
    job = context.job
    chat_id = job.data["chat_id"]
    game = word_games.get(chat_id)
    if not game or game.get("status") != "joining":
        return
    count = len(game["players"])
    await context.bot.send_message(
        chat_id=chat_id,
        text=(
            f"⚠️ *Only 30 seconds remaining to join!*\n\n"
            f"👥 Current players: *{count}*\n\n"
            f"Use /join to participate before time runs out! 🎮"
        ),
        parse_mode="Markdown"
    )


async def forcestart_wordgame(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin: /forcestart — Force-start the word game immediately."""
    if not update.effective_user or not update.message:
        return
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("🚫 Admins only.")
        return

    chat_id = update.effective_chat.id
    game = word_games.get(chat_id)

    if not game or game.get("status") != "joining":
        await update.message.reply_text("❌ No word game is currently in the joining phase.")
        return

    if len(game["players"]) < 2:
        await update.message.reply_text(
            "❌ *Not enough players!*\n\nNeed at least *2 players* to force start.",
            parse_mode="Markdown"
        )
        return

    for job_key in ("start_job", "warn_job"):
        j = game.get(job_key)
        if j:
            try:
                j.schedule_removal()
            except Exception:
                pass
    game["start_job"] = None
    game["warn_job"] = None

    await update.message.reply_text("⚡ *Force starting the word game now!*", parse_mode="Markdown")

    players = list(game["players"])
    game["status"] = "playing"
    game["total_started"] = len(players)
    random.shuffle(players)
    game["players"] = set(players)

    names = [game["player_names"].get(p, str(p)) for p in players]
    names_text = "\n".join([f"{i+1}. {n}" for i, n in enumerate(names)])

    await context.bot.send_message(
        chat_id=chat_id,
        text=(
            f"⚔️ *GAME START!*\n\n"
            f"👥 *{len(players)} players* are competing!\n\n"
            f"📋 *Players:*\n{names_text}\n\n"
            f"🎮 First turn coming up..."
        ),
        parse_mode="Markdown"
    )

    await asyncio.sleep(2)
    game["current_idx"] = -1
    await next_wordgame_turn(context.bot, chat_id, context.application.job_queue)


async def begin_wordgame_job(context):
    """Job: begin the actual game after join period."""
    job = context.job
    chat_id = job.data["chat_id"]
    game = word_games.get(chat_id)
    if not game or game.get("status") != "joining":
        return

    players = list(game["players"])
    if len(players) < 2:
        await context.bot.send_message(
            chat_id=chat_id,
            text=(
                "❌ *Not enough players joined!*\n\n"
                "Need at least *2 players* to start.\n"
                "Use /start\\_wordgame to try again!"
            ),
            parse_mode="Markdown"
        )
        word_games.pop(chat_id, None)
        return

    game["status"] = "playing"
    game["total_started"] = len(players)
    random.shuffle(players)
    game["players"] = set(players)

    names = [game["player_names"].get(p, str(p)) for p in players]
    names_text = "\n".join([f"{i+1}. {n}" for i, n in enumerate(names)])

    await context.bot.send_message(
        chat_id=chat_id,
        text=(
            f"⚔️ *GAME START!*\n\n"
            f"👥 *{len(players)} players* are competing!\n\n"
            f"📋 *Players:*\n{names_text}\n\n"
            f"🎮 First turn coming up..."
        ),
        parse_mode="Markdown"
    )

    await asyncio.sleep(2)
    game["current_idx"] = -1
    await next_wordgame_turn(context.bot, chat_id, context.application.job_queue)


async def join_wordgame(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/join — Join the active word game."""
    if not update.effective_user or not update.message:
        return
    chat_id = update.effective_chat.id
    user_id = update.effective_user.id
    user_name = update.effective_user.first_name or "Shinobi"

    game = word_games.get(chat_id)
    if not game or game.get("status") != "joining":
        await update.message.reply_text("❌ No word game is currently open for joining.")
        return

    if user_id in game["players"]:
        await update.message.reply_text(f"✅ {user_name}, you're already in the game!")
        return

    game["players"].add(user_id)
    game["player_names"][user_id] = user_name
    game["player_usernames"][user_id] = update.effective_user.username or ""
    count = len(game["players"])

    # When 2nd player joins, reset countdown to 60s from now
    if count == 2:
        old_start_job = game.get("start_job")
        if old_start_job:
            try:
                old_start_job.schedule_removal()
            except Exception:
                pass
        old_warn_job = game.get("warn_job")
        if old_warn_job:
            try:
                old_warn_job.schedule_removal()
            except Exception:
                pass

        warn_job = context.application.job_queue.run_once(
            wordgame_warning_job,
            when=30,
            data={"chat_id": chat_id},
            name=f"wg_warn_{chat_id}"
        )
        game["warn_job"] = warn_job

        new_start_job = context.application.job_queue.run_once(
            begin_wordgame_job,
            when=60,
            data={"chat_id": chat_id},
            name=f"wg_start_{chat_id}"
        )
        game["start_job"] = new_start_job

        await update.message.reply_text(
            f"✅ *{user_name}* joined the Word Game!\n\n"
            f"👥 Total players: *{count}*\n\n"
            f"⏳ Enough players! Game starts in *60 seconds!*",
            parse_mode="Markdown"
        )
    else:
        await update.message.reply_text(
            f"✅ *{user_name}* joined the Word Game!\n\n"
            f"👥 Total players: *{count}*\n\n"
            f"⏳ Waiting for game to start...",
            parse_mode="Markdown"
        )


async def stop_wordgame(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin: /stop_wordgame — Force stop the word game."""
    if not update.effective_user or not update.message:
        return
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("🚫 Admins only.")
        return

    chat_id = update.effective_chat.id
    game = word_games.get(chat_id)
    if not game:
        await update.message.reply_text("❌ No word game is running.")
        return

    # Cancel asyncio turn timer task
    old_task = game.get("_timer_task")
    if old_task and not old_task.done():
        old_task.cancel()
    game["_timer_token"] = None

    # Cancel job_queue jobs (start/warn)
    for job_key in ("start_job", "warn_job"):
        j = game.get(job_key)
        if j:
            try:
                j.schedule_removal()
            except Exception:
                pass

    word_games.pop(chat_id, None)
    await update.message.reply_text("🛑 *Word game stopped by admin.*", parse_mode="Markdown")


async def handle_wordgame_answer(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    MessageHandler: checks if a text message is a valid word game answer.
    Only fires if a game is active and it's this user's turn.
    """
    if not update.effective_user or not update.message or not update.message.text:
        return

    chat_id = update.effective_chat.id
    game = word_games.get(chat_id)
    if not game or game.get("status") != "playing":
        return

    user_id = update.effective_user.id
    if user_id not in game["players"]:
        return
    if game.get("current_player") != user_id:
        return

    word = update.message.text.strip()
    if word.startswith("/"):
        return

    word_lower = word.lower()
    min_len = game.get("min_letters", 3)
    user_name = game["player_names"].get(user_id, "Shinobi")
    required_letter = game.get("current_letter")

    # Check starting letter (ignore spaces — check first non-space char)
    first_char = word_lower.lstrip()[0] if word_lower.strip() else ""
    if required_letter and first_char != required_letter.lower():
        await update.message.reply_text(
            f"❌ *{user_name}*, the name must start with *{required_letter.upper()}*!\n"
            f"Try again before time runs out! ⏱️",
            parse_mode="Markdown"
        )
        return

    # Count letters ignoring spaces (e.g. "Satoru Gojo" = 6+4 = 10)
    letter_count = len(word_lower.replace(" ", ""))

    # Check length (spaces not counted)
    if letter_count < min_len:
        await update.message.reply_text(
            f"❌ *{user_name}*, the name must be at least *{min_len}* letters (spaces not counted)!\n"
            f"*{word.title()}* only has *{letter_count}* letters.\n"
            f"Try again before time runs out! ⏱️",
            parse_mode="Markdown"
        )
        return

    # Check if it's a valid anime character name
    if word_lower not in ANIME_NAMES_SET:
        await update.message.reply_text(
            f"❌ *'{word}'* is not a recognized anime character name!\n"
            f"Try a different name! ⏱️",
            parse_mode="Markdown"
        )
        return

    # Check if already used
    if word_lower in game["used_words"]:
        await update.message.reply_text(
            f"❌ *'{word}'* has already been used!\n"
            f"Try a different name! ⏱️",
            parse_mode="Markdown"
        )
        return

    # Valid answer!
    game["used_words"].add(word_lower)

    # Next required letter = last letter of the LAST word (ignore spaces)
    last_word = word_lower.split()[-1] if word_lower.split() else word_lower
    game["current_letter"] = last_word[-1]

    # Cancel current timer task (asyncio-based)
    old_task = game.get("_timer_task")
    if old_task and not old_task.done():
        old_task.cancel()
    # Invalidate token so any in-flight timer won't fire
    game["_timer_token"] = None
    game["timer_job"] = None

    remaining = len(game["players"])
    total_started = game["total_started"]
    next_letter = game["current_letter"].upper()

    await update.message.reply_text(
        f"✅ *{user_name}* answered: *{word.title()}*\n\n"
        f"🔤 Next player must start with: *{next_letter}*\n\n"
        f"👥 Players remaining: *{remaining}/{total_started}*",
        parse_mode="Markdown"
    )

    if remaining <= 1:
        await end_wordgame(context.bot, chat_id, context.application.job_queue)
        return

    await next_wordgame_turn(context.bot, chat_id, context.application.job_queue)


# ================= TRADE CARDS =================
# Pending trades: {trade_id: {initiator_id, target_id, offer_card_id, want_card_id, ...}}
pending_trades: dict = {}

async def trade_cards(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /trade_cards @username <your_card_id> <their_card_id>
    Initiates a card trade. The other user must confirm with the inline button.
    Both card IDs are the master card_id shown in /collection.
    """
    if not update.effective_user or not update.message:
        return
    user_id = update.effective_user.id
    user_name = update.effective_user.first_name or "Shinobi"

    if not has_started_bot(user_id) and not is_admin(user_id):
        await update.message.reply_text(NOT_STARTED_MSG, parse_mode="Markdown", disable_web_page_preview=True)
        return
    if is_banned(user_id):
        await update.message.reply_text(get_ban_message(user_id), parse_mode="Markdown")
        return

    args = context.args
    entities = update.message.entities or []

    # Parse target user from @mention or reply
    target_user = None
    target_id = None

    if update.message.reply_to_message and update.message.reply_to_message.from_user:
        target_user = update.message.reply_to_message.from_user
        target_id = target_user.id
    else:
        for ent in entities:
            if ent.type == "mention":
                mention_text = update.message.text[ent.offset:ent.offset + ent.length]
                username = mention_text.lstrip("@")
                # Look up user_id by username from bot_users table
                cursor.execute("SELECT user_id, first_name FROM bot_users WHERE username=? LIMIT 1", (username,))
                row = cursor.fetchone()
                if row:
                    target_id = row[0]
                    # Create a minimal user object substitute
                    class _U:
                        id = row[0]
                        first_name = row[1]
                        username = username
                    target_user = _U()
                break

    if not target_id:
        await update.message.reply_text(
            "❌ *Usage:* `/trade_cards @username <your_card_id> <their_card_id>`\n\n"
            "Or reply to the user's message and use:\n"
            "`/trade_cards <your_card_id> <their_card_id>`",
            parse_mode="Markdown"
        )
        return

    if target_id == user_id:
        await update.message.reply_text("❌ You can't trade with yourself.")
        return

    # Block if initiator already has an active pending trade
    now_ts = int(time.time())
    for tid, tr in list(pending_trades.items()):
        if tr.get("initiator_id") == user_id and now_ts < tr.get("expires", 0):
            await update.message.reply_text(
                "⏳ *You already have a pending trade!*\n\n"
                "Wait for it to be accepted or declined (expires in 5 min) before starting a new one.",
                parse_mode="Markdown"
            )
            return

    # Parse the two card IDs from args (skip any @mention token)
    card_args = [a for a in args if not a.startswith("@")]
    if len(card_args) < 2:
        await update.message.reply_text(
            "❌ Please provide *two card IDs*:\n"
            "`/trade_cards @username <your_card_id> <their_card_id>`",
            parse_mode="Markdown"
        )
        return

    try:
        offer_cid = int(card_args[0])
        want_cid  = int(card_args[1])
    except ValueError:
        await update.message.reply_text("❌ Card IDs must be numbers.")
        return

    # Verify initiator owns a card with offer_cid (master card_id)
    cursor.execute(
        "SELECT id, card_name, rarity, file_id FROM user_cards WHERE card_id=? AND user_id=? LIMIT 1",
        (offer_cid, user_id)
    )
    offer_row = cursor.fetchone()
    if not offer_row:
        await update.message.reply_text(
            f"❌ You don't own card *#{offer_cid}*. Check your `/collection` for your card IDs.",
            parse_mode="Markdown"
        )
        return

    offer_row_id, offer_name, offer_rarity, offer_file_id = offer_row
    offer_emoji = RARITY_EMOJI.get(offer_rarity, "⭐")

    # Verify target owns a card with want_cid (master card_id)
    cursor.execute(
        "SELECT id, card_name, rarity, file_id FROM user_cards WHERE card_id=? AND user_id=? LIMIT 1",
        (want_cid, target_id)
    )
    want_row = cursor.fetchone()
    if not want_row:
        target_display = getattr(target_user, 'first_name', str(target_id))
        await update.message.reply_text(
            f"❌ *{target_display}* doesn't own card *#{want_cid}*.",
            parse_mode="Markdown"
        )
        return

    want_row_id, want_name, want_rarity, want_file_id = want_row
    want_emoji = RARITY_EMOJI.get(want_rarity, "⭐")

    # Create a unique trade ID
    trade_id = f"{user_id}_{target_id}_{int(time.time())}"

    pending_trades[trade_id] = {
        "initiator_id":   user_id,
        "initiator_name": user_name,
        "target_id":      target_id,
        "target_name":    getattr(target_user, 'first_name', str(target_id)),
        "offer_row_id":   offer_row_id,
        "offer_cid":      offer_cid,
        "offer_name":     offer_name,
        "offer_rarity":   offer_rarity,
        "offer_file_id":  offer_file_id,
        "want_row_id":    want_row_id,
        "want_cid":       want_cid,
        "want_name":      want_name,
        "want_rarity":    want_rarity,
        "want_file_id":   want_file_id,
        "expires":        int(time.time()) + 300,  # 5-minute window
    }

    target_display = getattr(target_user, 'first_name', str(target_id))

    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ Accept Trade", callback_data=f"trade_accept_{trade_id}"),
        InlineKeyboardButton("❌ Decline",      callback_data=f"trade_decline_{trade_id}"),
    ]])

    await update.message.reply_text(
        f"🔄 *TRADE REQUEST SENT!*\n\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"📤 *{user_name}* offers:\n"
        f"  {offer_emoji} #{offer_cid} — {offer_name.title()} [{offer_rarity}]\n\n"
        f"📥 Wants from *{target_display}*:\n"
        f"  {want_emoji} #{want_cid} — {want_name.title()} [{want_rarity}]\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"⏳ *{target_display}*, please accept or decline within 5 minutes.",
        parse_mode="Markdown",
        reply_markup=keyboard
    )


async def trade_confirm_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle trade accept/decline button presses."""
    query = update.callback_query
    try:
        await query.answer()
    except Exception:
        pass

    if not query.data:
        return

    data = query.data  # trade_accept_<trade_id>  or  trade_decline_<trade_id>
    parts = data.split("_", 2)
    if len(parts) < 3:
        return

    action   = parts[1]   # accept / decline
    trade_id = parts[2]

    trade = pending_trades.get(trade_id)
    if not trade:
        await query.edit_message_text("❌ This trade has expired or no longer exists.")
        return

    responder_id = query.from_user.id

    # Only the target (receiver of the request) can respond
    if responder_id != trade["target_id"]:
        await query.answer("⚠️ Only the trade recipient can respond to this request.", show_alert=True)
        return

    # Check expiry
    if int(time.time()) > trade["expires"]:
        pending_trades.pop(trade_id, None)
        await query.edit_message_text("⏰ This trade offer has expired.")
        return

    if action == "decline":
        pending_trades.pop(trade_id, None)
        await query.edit_message_text(
            f"❌ *{trade['target_name']}* declined the trade offer.",
            parse_mode="Markdown"
        )
        return

    if action == "accept":
        # Re-verify both still own the cards before swapping
        cursor.execute(
            "SELECT id FROM user_cards WHERE id=? AND user_id=?",
            (trade["offer_row_id"], trade["initiator_id"])
        )
        if not cursor.fetchone():
            pending_trades.pop(trade_id, None)
            await query.edit_message_text(
                f"❌ Trade failed — *{trade['initiator_name']}* no longer owns that card.",
                parse_mode="Markdown"
            )
            return

        cursor.execute(
            "SELECT id FROM user_cards WHERE id=? AND user_id=?",
            (trade["want_row_id"], trade["target_id"])
        )
        if not cursor.fetchone():
            pending_trades.pop(trade_id, None)
            await query.edit_message_text(
                f"❌ Trade failed — *{trade['target_name']}* no longer owns that card.",
                parse_mode="Markdown"
            )
            return

        # Perform the swap — delete both rows and re-insert for new owners
        cursor.execute("DELETE FROM user_cards WHERE id=?", (trade["offer_row_id"],))
        cursor.execute("DELETE FROM user_cards WHERE id=?", (trade["want_row_id"],))

        # Initiator receives want card; target receives offer card
        insert_user_card(trade["initiator_id"], trade["want_name"],  trade["want_rarity"],  trade["want_file_id"])
        insert_user_card(trade["target_id"],    trade["offer_name"], trade["offer_rarity"], trade["offer_file_id"])
        conn.commit()

        offer_emoji = RARITY_EMOJI.get(trade["offer_rarity"], "⭐")
        want_emoji  = RARITY_EMOJI.get(trade["want_rarity"],  "⭐")

        pending_trades.pop(trade_id, None)

        await query.edit_message_text(
            f"✅ *TRADE COMPLETE!*\n\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"🔄 *{trade['initiator_name']}* gave:\n"
            f"  {offer_emoji} #{trade['offer_cid']} — {trade['offer_name'].title()} [{trade['offer_rarity']}]\n\n"
            f"🔄 *{trade['target_name']}* gave:\n"
            f"  {want_emoji} #{trade['want_cid']} — {trade['want_name'].title()} [{trade['want_rarity']}]\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"⚔️ Both shinobis have received their new cards!",
            parse_mode="Markdown"
        )

        await log_trade(
            context.bot,
            f"🔄 *Card Trade Completed*\n\n"
            f"👤 {trade['initiator_name']} (`{trade['initiator_id']}`) gave: "
            f"{trade['offer_name'].title()} [#{trade['offer_cid']}]\n"
            f"👤 {trade['target_name']} (`{trade['target_id']}`) gave: "
            f"{trade['want_name'].title()} [#{trade['want_cid']}]"
        )


# ================= /ADD_DRAFT (Owner only) =================

async def add_draft(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Owner only: /add_draft <amount>
      — adds draft vouchers to yourself
    /add_draft <amount> (reply to user)
      — adds to that user
    /add_draft @username <amount>  OR  /add_draft <user_id> <amount>
      — adds to specified user
    """
    if not update.effective_user or not update.message:
        return
    if update.effective_user.id != OWNER_ID:
        await update.message.reply_text("🚫 Owner only command.")
        return

    args = context.args or []
    target_id = None
    target_name = None
    amount = None

    # ── Determine target ──────────────────────────────────────────────────────
    replied = update.message.reply_to_message
    if replied and replied.from_user and not replied.from_user.is_bot:
        target_id   = replied.from_user.id
        target_name = replied.from_user.first_name or str(target_id)
        # Amount is first arg
        if not args:
            await update.message.reply_text("❌ Usage (reply): /add_draft <amount>")
            return
        try:
            amount = int(args[0])
        except ValueError:
            await update.message.reply_text("❌ Amount must be a number.")
            return
    elif args:
        # Check if first arg is @username, user_id, or just a number
        first = args[0]
        if first.startswith("@"):
            username = first.lstrip("@")
            cursor.execute("SELECT user_id, first_name FROM bot_users WHERE username=? LIMIT 1", (username,))
            row = cursor.fetchone()
            if not row:
                await update.message.reply_text(f"❌ User @{username} not found.")
                return
            target_id, target_name = row
            if len(args) < 2:
                await update.message.reply_text("❌ Usage: /add_draft @username <amount>")
                return
            try:
                amount = int(args[1])
            except ValueError:
                await update.message.reply_text("❌ Amount must be a number.")
                return
        elif first.lstrip("-").isdigit() and len(first) > 5:
            # Likely a user_id
            try:
                target_id = int(first)
            except ValueError:
                await update.message.reply_text("❌ Invalid user ID.")
                return
            try:
                tc = await context.bot.get_chat(target_id)
                target_name = tc.first_name or str(target_id)
            except Exception:
                target_name = str(target_id)
            if len(args) < 2:
                await update.message.reply_text("❌ Usage: /add_draft <user_id> <amount>")
                return
            try:
                amount = int(args[1])
            except ValueError:
                await update.message.reply_text("❌ Amount must be a number.")
                return
        else:
            # Just a number — add to self
            try:
                amount = int(first)
                target_id   = update.effective_user.id
                target_name = update.effective_user.first_name or "Owner"
            except ValueError:
                await update.message.reply_text(
                    "❌ Usage:\n"
                    "• `/add_draft <amount>` — add to yourself\n"
                    "• `/add_draft @username <amount>`\n"
                    "• `/add_draft <user_id> <amount>`\n"
                    "• Reply to user + `/add_draft <amount>`",
                    parse_mode="Markdown"
                )
                return
    else:
        target_id   = update.effective_user.id
        target_name = update.effective_user.first_name or "Owner"
        await update.message.reply_text(
            "❌ Usage:\n"
            "• `/add_draft <amount>` — add to yourself\n"
            "• Reply to user + `/add_draft <amount>`\n"
            "• `/add_draft @username <amount>`\n"
            "• `/add_draft <user_id> <amount>`",
            parse_mode="Markdown"
        )
        return

    if amount <= 0:
        await update.message.reply_text("❌ Amount must be greater than 0.")
        return

    add_vouchers(target_id, amount)
    conn.commit()
    new_total = get_user_vouchers(target_id)

    await update.message.reply_text(
        f"✅ *Draft Vouchers Added!*\n\n"
        f"👤 User: *{target_name}* (`{target_id}`)\n"
        f"🎟️ Added: *+{amount}* vouchers\n"
        f"💼 New Total: *{new_total}* vouchers",
        parse_mode="Markdown"
    )
    await log_to_group(
        context.bot,
        f"🎟️ *Draft Vouchers Added by Owner*\n\n"
        f"👤 To: {target_name} (`{target_id}`)\n"
        f"➕ Amount: {amount}\n"
        f"💼 New Total: {new_total}"
    )


# ================= /TRANSFER_DRAFT (Owner only) =================

async def transfer_draft(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Owner only: /transfer_draft <from_user_id> <to_user_id>
    Transfers ALL draft vouchers from one user to another.
    """
    if not update.effective_user or not update.message:
        return
    if update.effective_user.id != OWNER_ID:
        await update.message.reply_text("🚫 Owner only command.")
        return

    if not context.args or len(context.args) < 2:
        await update.message.reply_text(
            "❌ *Usage:* `/transfer_draft <from_user_id> <to_user_id>`\n\n"
            "Example: `/transfer_draft 8280234896 196219715`\n\n"
            "This transfers ALL draft vouchers from the first user to the second.",
            parse_mode="Markdown"
        )
        return

    try:
        from_id = int(context.args[0])
        to_id   = int(context.args[1])
    except ValueError:
        await update.message.reply_text("❌ Both IDs must be numbers.")
        return

    if from_id == to_id:
        await update.message.reply_text("❌ Sender and receiver can't be the same user.")
        return

    from_vouchers = get_user_vouchers(from_id)
    if from_vouchers <= 0:
        await update.message.reply_text(
            f"❌ User `{from_id}` has *0 draft vouchers* — nothing to transfer.",
            parse_mode="Markdown"
        )
        return

    # Get display names
    try:
        fc = await context.bot.get_chat(from_id)
        from_name = fc.first_name or str(from_id)
    except Exception:
        from_name = str(from_id)

    try:
        tc = await context.bot.get_chat(to_id)
        to_name = tc.first_name or str(to_id)
    except Exception:
        to_name = str(to_id)

    # Perform transfer: deduct from sender, add to receiver
    cursor.execute("UPDATE draft_vouchers SET vouchers = 0 WHERE user_id=?", (from_id,))
    add_vouchers(to_id, from_vouchers)
    conn.commit()

    to_total = get_user_vouchers(to_id)

    await update.message.reply_text(
        f"🔄 *Draft Vouchers Transferred!*\n\n"
        f"📤 From: *{from_name}* (`{from_id}`) — *{from_vouchers}* vouchers → 0\n"
        f"📥 To: *{to_name}* (`{to_id}`) — new total: *{to_total}* vouchers",
        parse_mode="Markdown"
    )
    await log_to_group(
        context.bot,
        f"🔄 *Draft Transfer by Owner*\n\n"
        f"📤 From: {from_name} (`{from_id}`) → 0 vouchers\n"
        f"📥 To: {to_name} (`{to_id}`) → {to_total} vouchers\n"
        f"🎟️ Transferred: {from_vouchers} vouchers"
    )


# ═══════════════════════════════════════════════════════════════════
#                     SHINOBI COINS SYSTEM
# ═══════════════════════════════════════════════════════════════════

async def shinobi_coins_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/shinobi_coins — Show your current coin balance."""
    if not update.effective_user or not update.message:
        return
    user_id = update.effective_user.id
    if not has_started_bot(user_id) and not is_admin(user_id):
        await update.message.reply_text(NOT_STARTED_MSG, parse_mode="Markdown", disable_web_page_preview=True)
        return

    coins = get_coins(user_id)
    user_name = update.effective_user.first_name or "Shinobi"

    # Rank among all users
    cursor.execute("SELECT COUNT(*)+1 FROM shinobi_coins WHERE coins > ?", (coins,))
    rank = cursor.fetchone()[0]

    await update.message.reply_text(
        f"🪙 *SHINOBI COINS WALLET*\n\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🥷 Shinobi: *{user_name}*\n"
        f"💰 Balance: *{coins:,} coins*\n"
        f"🏆 Global Rank: *#{rank}*\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"💡 *Earn coins by:*\n"
        f"  🎴 Catching cards (50–600 per rarity)\n"
        f"  📅 /daily → +{COIN_DAILY_AMOUNT} coins\n"
        f"  📆 /weekly → +{COIN_WEEKLY_AMOUNT} coins\n"
        f"  🗓️ /monthly → +{COIN_MONTHLY_AMOUNT} coins\n\n"
        f"🎲 Use /bet to gamble your coins!",
        parse_mode="Markdown"
    )


async def top_shinobi_coins(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/top_shinobi_coins — Top 10 coin holders."""
    if not update.effective_user or not update.message:
        return

    cursor.execute("""
        SELECT sc.user_id, sc.coins,
               COALESCE(bu.first_name, CAST(sc.user_id AS TEXT)) AS name
        FROM shinobi_coins sc
        LEFT JOIN bot_users bu ON bu.user_id = sc.user_id
        WHERE sc.coins > 0
        ORDER BY sc.coins DESC
        LIMIT 10
    """)
    rows = cursor.fetchall()

    if not rows:
        await update.message.reply_text("🪙 No coin data yet. Start catching shinobis!")
        return

    MEDALS = ["🥇", "🥈", "🥉", "4️⃣", "5️⃣", "6️⃣", "7️⃣", "8️⃣", "9️⃣", "🔟"]
    lines = []
    for i, (uid, coins, name) in enumerate(rows):
        lines.append(f"{MEDALS[i]} *{name}* — `{coins:,}` 🪙")

    board = "\n".join(lines)
    await update.message.reply_text(
        f"🏆 *TOP 10 SHINOBI COIN HOLDERS*\n\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"{board}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"💡 Catch rare cards & claim daily rewards to climb the ranks!",
        parse_mode="Markdown"
    )


# ── /bet ─────────────────────────────────────────────────────────────────────

async def bet(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /bet <amount> h|t|heads|tails       — coin flip
    /bet <amount> o|odd|e|even          — dice odd/even
    /bet <amount> high|low              — dice high (4-6) / low (1-3)
    """
    if not update.effective_user or not update.message:
        return
    user_id = update.effective_user.id
    if not has_started_bot(user_id) and not is_admin(user_id):
        await update.message.reply_text(NOT_STARTED_MSG, parse_mode="Markdown", disable_web_page_preview=True)
        return
    if is_banned(user_id):
        await update.message.reply_text(get_ban_message(user_id), parse_mode="Markdown")
        return

    args = context.args or []
    if len(args) < 2:
        await update.message.reply_text(
            "🎲 *HOW TO BET*\n\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            "🪙 *Coin Flip:*\n"
            "  `/bet <amount> h` — heads\n"
            "  `/bet <amount> t` — tails\n\n"
            "🎲 *Dice Odd/Even:*\n"
            "  `/bet <amount> o` or `odd`\n"
            "  `/bet <amount> e` or `even`\n\n"
            "🎲 *Dice High/Low:*\n"
            "  `/bet <amount> high` — rolls 4,5,6\n"
            "  `/bet <amount> low` — rolls 1,2,3\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            "💡 Win = double your bet | Lose = lose your bet",
            parse_mode="Markdown"
        )
        return

    try:
        amount = int(args[0])
    except ValueError:
        await update.message.reply_text("❌ Bet amount must be a number.")
        return

    if amount <= 0:
        await update.message.reply_text("❌ Bet amount must be greater than 0.")
        return

    current = get_coins(user_id)
    if current < amount:
        await update.message.reply_text(
            f"😔 *Not Enough Shinobi Coins!*\n\n"
            f"💰 You need: *{amount:,} coins*\n"
            f"💸 You have: *{current:,} coins*\n\n"
            f"💡 Catch cards, claim /daily, /weekly, /monthly to earn more!\n"
            f"🌟 Better luck next time, shinobi!",
            parse_mode="Markdown"
        )
        return

    bet_type = args[1].lower()
    user_name = update.effective_user.first_name or "Shinobi"

    # ── COIN FLIP ──────────────────────────────────────────────────────────────
    if bet_type in ("h", "heads", "t", "tails"):
        user_choice = "heads" if bet_type in ("h", "heads") else "tails"
        result = random.choice(["heads", "tails"])
        won = (user_choice == result)
        result_emoji = "🦅" if result == "heads" else "🦁"
        choice_emoji  = "🦅" if user_choice == "heads" else "🦁"

        if won:
            add_coins(user_id, amount)
            new_bal = get_coins(user_id)
            await update.message.reply_text(
                f"🪙 *COIN FLIP RESULT!*\n\n"
                f"━━━━━━━━━━━━━━━━━━━━━━\n"
                f"{choice_emoji} You chose: *{user_choice.title()}*\n"
                f"{result_emoji} Result: *{result.title()}*\n"
                f"━━━━━━━━━━━━━━━━━━━━━━\n\n"
                f"🎉🏆 *CONGRATULATIONS, {user_name}!*\n\n"
                f"⚡ You crushed it, shinobi warrior!\n"
                f"💰 *+{amount:,} coins* added to your wallet!\n"
                f"💼 New Balance: *{new_bal:,} coins* 🪙\n\n"
                f"🔥 The ninja gods smile upon you today! 🌟",
                parse_mode="Markdown"
            )
        else:
            deduct_coins(user_id, amount)
            new_bal = get_coins(user_id)
            await update.message.reply_text(
                f"🪙 *COIN FLIP RESULT!*\n\n"
                f"━━━━━━━━━━━━━━━━━━━━━━\n"
                f"{choice_emoji} You chose: *{user_choice.title()}*\n"
                f"{result_emoji} Result: *{result.title()}*\n"
                f"━━━━━━━━━━━━━━━━━━━━━━\n\n"
                f"😔💸 *Sorry, {user_name}... You Lost!*\n\n"
                f"🌪️ The coin didn't favor you this time!\n"
                f"💸 *-{amount:,} coins* deducted.\n"
                f"💼 Remaining: *{new_bal:,} coins* 🪙\n\n"
                f"💪 Don't give up! Give your best next time! 🌟",
                parse_mode="Markdown"
            )
        return

    # ── DICE BETS ──────────────────────────────────────────────────────────────
    if bet_type in ("o", "odd", "e", "even", "high", "low"):
        # Send the dice and wait for the result
        dice_msg = await update.message.reply_dice(emoji="🎲")
        dice_value = dice_msg.dice.value   # 1–6, already random from Telegram
        await asyncio.sleep(3)             # wait for animation

        if bet_type in ("o", "odd"):
            user_pred = "odd"
            won = (dice_value % 2 != 0)
            pred_emoji = "🔢"
        elif bet_type in ("e", "even"):
            user_pred = "even"
            won = (dice_value % 2 == 0)
            pred_emoji = "🔣"
        elif bet_type == "high":
            user_pred = "high (4–6)"
            won = (dice_value >= 4)
            pred_emoji = "⬆️"
        else:
            user_pred = "low (1–3)"
            won = (dice_value <= 3)
            pred_emoji = "⬇️"

        if won:
            add_coins(user_id, amount)
            new_bal = get_coins(user_id)
            await update.message.reply_text(
                f"🎲 *DICE RESULT: {dice_value}!*\n\n"
                f"━━━━━━━━━━━━━━━━━━━━━━\n"
                f"{pred_emoji} Your bet: *{user_pred}*\n"
                f"🎯 Result: *{dice_value}* ({'odd' if dice_value%2 else 'even'})\n"
                f"━━━━━━━━━━━━━━━━━━━━━━\n\n"
                f"🎉🔥 *WINNER WINNER, {user_name}!*\n\n"
                f"⚔️ Your shinobi instincts were perfect!\n"
                f"💰 *+{amount:,} coins* added!\n"
                f"💼 New Balance: *{new_bal:,} coins* 🪙\n\n"
                f"🌟 The dice bow before a true ninja! 🥷",
                parse_mode="Markdown"
            )
        else:
            deduct_coins(user_id, amount)
            new_bal = get_coins(user_id)
            await update.message.reply_text(
                f"🎲 *DICE RESULT: {dice_value}!*\n\n"
                f"━━━━━━━━━━━━━━━━━━━━━━\n"
                f"{pred_emoji} Your bet: *{user_pred}*\n"
                f"🎯 Result: *{dice_value}* ({'odd' if dice_value%2 else 'even'})\n"
                f"━━━━━━━━━━━━━━━━━━━━━━\n\n"
                f"😤💨 *So close, {user_name}... You Lost!*\n\n"
                f"🌪️ The dice were against you today!\n"
                f"💸 *-{amount:,} coins* deducted.\n"
                f"💼 Remaining: *{new_bal:,} coins* 🪙\n\n"
                f"🔥 Train harder and come back stronger! 💪",
                parse_mode="Markdown"
            )
        return

    await update.message.reply_text(
        "❓ Unknown bet type.\n\n"
        "Valid options: `h` `t` `o` `e` `odd` `even` `high` `low`",
        parse_mode="Markdown"
    )


# ── /add_shinobi_coins (Owner only) ──────────────────────────────────────────

async def add_shinobi_coins_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Owner only: /add_shinobi_coins <amount> — add coins to self or reply/mention target."""
    if not update.effective_user or not update.message:
        return
    if update.effective_user.id != OWNER_ID:
        await update.message.reply_text("🚫 Owner only command.")
        return

    args = context.args or []
    target_id = None
    target_name = None
    amount = None

    replied = update.message.reply_to_message
    if replied and replied.from_user and not replied.from_user.is_bot:
        target_id   = replied.from_user.id
        target_name = replied.from_user.first_name or str(target_id)
        if not args:
            await update.message.reply_text("❌ Usage (reply): /add_shinobi_coins <amount>")
            return
        try:
            amount = int(args[0])
        except ValueError:
            await update.message.reply_text("❌ Amount must be a number.")
            return
    elif args:
        first = args[0]
        if first.startswith("@"):
            username = first.lstrip("@")
            cursor.execute("SELECT user_id, first_name FROM bot_users WHERE username=? LIMIT 1", (username,))
            row = cursor.fetchone()
            if not row:
                await update.message.reply_text(f"❌ User @{username} not found.")
                return
            target_id, target_name = row
            if len(args) < 2:
                await update.message.reply_text("❌ Usage: /add_shinobi_coins @username <amount>")
                return
            try:
                amount = int(args[1])
            except ValueError:
                await update.message.reply_text("❌ Amount must be a number.")
                return
        elif first.lstrip("-").isdigit() and len(first) > 5:
            try:
                target_id = int(first)
            except ValueError:
                await update.message.reply_text("❌ Invalid user ID.")
                return
            try:
                tc = await context.bot.get_chat(target_id)
                target_name = tc.first_name or str(target_id)
            except Exception:
                target_name = str(target_id)
            if len(args) < 2:
                await update.message.reply_text("❌ Usage: /add_shinobi_coins <user_id> <amount>")
                return
            try:
                amount = int(args[1])
            except ValueError:
                await update.message.reply_text("❌ Amount must be a number.")
                return
        else:
            try:
                amount = int(first)
                target_id   = update.effective_user.id
                target_name = update.effective_user.first_name or "Owner"
            except ValueError:
                await update.message.reply_text(
                    "❌ Usage:\n"
                    "• `/add_shinobi_coins <amount>` — add to yourself\n"
                    "• Reply + `/add_shinobi_coins <amount>`\n"
                    "• `/add_shinobi_coins @username <amount>`\n"
                    "• `/add_shinobi_coins <user_id> <amount>`",
                    parse_mode="Markdown"
                )
                return
    else:
        await update.message.reply_text(
            "❌ Usage:\n"
            "• `/add_shinobi_coins <amount>` — add to yourself\n"
            "• Reply + `/add_shinobi_coins <amount>`\n"
            "• `/add_shinobi_coins @username <amount>`\n"
            "• `/add_shinobi_coins <user_id> <amount>`",
            parse_mode="Markdown"
        )
        return

    if amount <= 0:
        await update.message.reply_text("❌ Amount must be greater than 0.")
        return

    add_coins(target_id, amount)
    new_total = get_coins(target_id)

    await update.message.reply_text(
        f"✅ *Shinobi Coins Added!*\n\n"
        f"👤 User: *{target_name}* (`{target_id}`)\n"
        f"🪙 Added: *+{amount:,}*\n"
        f"💰 New Total: *{new_total:,}*",
        parse_mode="Markdown"
    )


# ── /generate_shinobi_coins (Owner only) ─────────────────────────────────────

async def generate_shinobi_coins_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Owner only: /generate_shinobi_coins <amount> <quantity>"""
    if not update.effective_user or not update.message:
        return
    if update.effective_user.id != OWNER_ID:
        await update.message.reply_text("🚫 Owner only command.")
        return

    if not context.args or len(context.args) < 2:
        await update.message.reply_text(
            "❌ *Usage:* `/generate_shinobi_coins <amount> <quantity>`\n\n"
            "• `amount` — coins each redemption gives\n"
            "• `quantity` — how many players can redeem it\n\n"
            "Example: `/generate_shinobi_coins 500 10`\n"
            "_Creates a code worth 500 coins usable by 10 players_",
            parse_mode="Markdown"
        )
        return

    try:
        amount   = int(context.args[0])
        max_uses = int(context.args[1])
    except ValueError:
        await update.message.reply_text("❌ Both amount and quantity must be numbers.")
        return

    if amount <= 0:
        await update.message.reply_text("❌ Amount must be greater than 0.")
        return
    if max_uses <= 0:
        await update.message.reply_text("❌ Quantity must be at least 1.")
        return

    import secrets
    code = "SNC-" + secrets.token_hex(5).upper()
    now  = int(time.time())

    cursor.execute(
        "INSERT INTO coin_redeem_codes (code, coins, created_by, created_at, max_uses, uses_count) VALUES (?,?,?,?,?,0)",
        (code, amount, update.effective_user.id, now, max_uses)
    )
    conn.commit()

    await update.message.reply_text(
        f"🎟️ *Shinobi Coin Redeem Code Generated!*\n\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🔑 Code: `{code}`\n"
        f"🪙 Value: *{amount:,} coins* per redemption\n"
        f"👥 Max Uses: *{max_uses}* player(s)\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"📤 Share this code!\n"
        f"Players redeem with: /redeem `{code}`",
        parse_mode="Markdown"
    )


# ── /redeem ────────────────────────────────────────────────────────────────────

async def redeem_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/redeem <code> — Redeem a shinobi coin code."""
    if not update.effective_user or not update.message:
        return
    user_id = update.effective_user.id
    if not has_started_bot(user_id) and not is_admin(user_id):
        await update.message.reply_text(NOT_STARTED_MSG, parse_mode="Markdown", disable_web_page_preview=True)
        return
    if is_banned(user_id):
        await update.message.reply_text(get_ban_message(user_id), parse_mode="Markdown")
        return

    if not context.args:
        await update.message.reply_text("❌ Usage: /redeem <code>")
        return

    code = context.args[0].strip().upper()

    cursor.execute(
        "SELECT coins, COALESCE(max_uses,1), COALESCE(uses_count,0) FROM coin_redeem_codes WHERE code=?",
        (code,)
    )
    row = cursor.fetchone()

    if not row:
        await update.message.reply_text(
            "❌ *Invalid code!*\n\nDouble-check the code and try again.",
            parse_mode="Markdown"
        )
        return

    coins, max_uses, uses_count = row

    # Check if this specific user already redeemed
    cursor.execute(
        "SELECT 1 FROM coin_redeem_user_log WHERE code=? AND user_id=?",
        (code, user_id)
    )
    if cursor.fetchone():
        await update.message.reply_text(
            "⚠️ *You have already redeemed this code!*\n\nEach code can only be used once per user.",
            parse_mode="Markdown"
        )
        return

    # Check max uses
    if uses_count >= max_uses:
        await update.message.reply_text(
            f"⚠️ *This code has reached its maximum redemptions!*\n\nMax uses: {max_uses}",
            parse_mode="Markdown"
        )
        return

    # Log this redemption and update count
    cursor.execute(
        "INSERT OR IGNORE INTO coin_redeem_user_log (code, user_id, redeemed_at) VALUES (?,?,?)",
        (code, user_id, int(time.time()))
    )
    cursor.execute(
        "UPDATE coin_redeem_codes SET uses_count = uses_count + 1, used_by=?, used_at=? WHERE code=?",
        (user_id, int(time.time()), code)
    )
    add_coins(user_id, coins)
    conn.commit()
    new_bal = get_coins(user_id)
    user_name = update.effective_user.first_name or "Shinobi"

    await update.message.reply_text(
        f"🎉 *CODE REDEEMED SUCCESSFULLY!*\n\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🥷 Shinobi: *{user_name}*\n"
        f"🔑 Code: `{code}`\n"
        f"🪙 Earned: *+{coins:,} Shinobi Coins!*\n"
        f"💰 New Balance: *{new_bal:,} coins*\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"⚔️ Keep collecting and growing your power! 🌟",
        parse_mode="Markdown"
    )


# ================= OWNER RESET & COIN COMMANDS =================

def _is_owner(user_id: int) -> bool:
    return user_id in (OWNER_ID, CO_OWNER_ID)

async def _resolve_target(update, context):
    """Returns (target_id, target_name) from reply or first arg."""
    if update.message.reply_to_message and update.message.reply_to_message.from_user:
        u = update.message.reply_to_message.from_user
        return u.id, u.first_name or str(u.id)
    if context.args:
        try:
            tid = int(context.args[0])
            try:
                tc = await context.bot.get_chat(tid)
                return tid, tc.first_name or str(tid)
            except Exception:
                return tid, str(tid)
        except ValueError:
            pass
    return None, None


# ── /reset_shinobi_coins ──────────────────────────────────────────────────────
async def reset_shinobi_coins(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.effective_user or not update.message:
        return
    if not _is_owner(update.effective_user.id):
        await update.message.reply_text("🚫 Owner only.")
        return
    target_id, target_name = await _resolve_target(update, context)
    if not target_id:
        await update.message.reply_text("Usage: /reset_shinobi_coins <user_id> or reply to user.")
        return
    cursor.execute("UPDATE shinobi_coins SET coins=0 WHERE user_id=?", (target_id,))
    conn.commit()
    await update.message.reply_text(
        f"✅ Shinobi coins reset to *0* for {target_name} (`{target_id}`).", parse_mode="Markdown"
    )
    await log_to_group(context.bot,
        f"🪙 *Coins Reset*\n👤 {target_name} (`{target_id}`)\n👑 By: {update.effective_user.first_name}")


# ── /reset_all_shinobi_coins ──────────────────────────────────────────────────
async def reset_all_shinobi_coins(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.effective_user or not update.message:
        return
    if not _is_owner(update.effective_user.id):
        await update.message.reply_text("🚫 Owner only.")
        return
    cursor.execute("UPDATE shinobi_coins SET coins=0")
    conn.commit()
    await update.message.reply_text("✅ *All users' shinobi coins have been reset to 0!*", parse_mode="Markdown")
    await log_to_group(context.bot,
        f"🪙 *ALL Coins Reset*\n👑 By: {update.effective_user.first_name} (`{update.effective_user.id}`)")


# ── /Add_all_shinobi_coins <amount> <reason> ─────────────────────────────────
async def add_all_shinobi_coins(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.effective_user or not update.message:
        return
    if not _is_owner(update.effective_user.id):
        await update.message.reply_text("🚫 Owner only.")
        return
    if not context.args:
        await update.message.reply_text("Usage: /Add_all_shinobi_coins <amount> <reason>")
        return
    try:
        amount = int(context.args[0])
    except ValueError:
        await update.message.reply_text("❌ Invalid amount.")
        return
    reason = " ".join(context.args[1:]) if len(context.args) > 1 else "Admin reward"

    cursor.execute("SELECT DISTINCT user_id FROM bot_users")
    all_users = [r[0] for r in cursor.fetchall()]

    cursor.execute("""
        INSERT INTO shinobi_coins (user_id, coins) VALUES (?, ?)
        ON CONFLICT(user_id) DO UPDATE SET coins = coins + excluded.coins
    """, (0, 0))  # just ensure table exists

    for uid in all_users:
        cursor.execute("""
            INSERT INTO shinobi_coins (user_id, coins) VALUES (?, ?)
            ON CONFLICT(user_id) DO UPDATE SET coins = coins + excluded.coins
        """, (uid, amount))

    conn.commit()

    sent = 0
    for uid in all_users:
        try:
            await context.bot.send_message(
                chat_id=uid,
                text=(
                    f"🪙 *You received Shinobi Coins!*\n\n"
                    f"💰 Amount: *+{amount} Shinobi Coins*\n"
                    f"📝 Reason: {reason}\n\n"
                    f"Use /shinobi\\_coins to check your balance!"
                ),
                parse_mode="Markdown"
            )
            sent += 1
            await asyncio.sleep(0.05)
        except Exception:
            pass

    await update.message.reply_text(
        f"✅ *+{amount} Shinobi Coins* added to all {len(all_users)} users!\n"
        f"📢 Notified {sent} users.\n📝 Reason: {reason}",
        parse_mode="Markdown"
    )
    await log_to_group(context.bot,
        f"🪙 *Coins Added to ALL*\n💰 +{amount}\n📝 {reason}\n"
        f"👑 By: {update.effective_user.first_name} (`{update.effective_user.id}`)")


# ── /reset_daily <user> ───────────────────────────────────────────────────────
async def reset_daily(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.effective_user or not update.message:
        return
    if not _is_owner(update.effective_user.id):
        await update.message.reply_text("🚫 Owner only.")
        return
    target_id, target_name = await _resolve_target(update, context)
    if not target_id:
        await update.message.reply_text("Usage: /reset_daily <user_id> or reply to user.")
        return
    cursor.execute("UPDATE users SET last_daily=0 WHERE user_id=?", (target_id,))
    cursor.execute("UPDATE shinobi_coins SET last_daily=0 WHERE user_id=?", (target_id,))
    conn.commit()
    await update.message.reply_text(
        f"✅ Daily reset for *{target_name}* (`{target_id}`).", parse_mode="Markdown"
    )
    await log_to_group(context.bot,
        f"🔄 *Daily Reset*\n👤 {target_name} (`{target_id}`)\n👑 By: {update.effective_user.first_name}")


# ── /reset_all_daily ─────────────────────────────────────────────────────────
async def reset_all_daily(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.effective_user or not update.message:
        return
    if not _is_owner(update.effective_user.id):
        await update.message.reply_text("🚫 Owner only.")
        return
    cursor.execute("UPDATE users SET last_daily=0")
    cursor.execute("UPDATE shinobi_coins SET last_daily=0")
    conn.commit()
    await update.message.reply_text("✅ *Daily reset for all users!*", parse_mode="Markdown")
    await log_to_group(context.bot,
        f"🔄 *ALL Daily Reset*\n👑 By: {update.effective_user.first_name} (`{update.effective_user.id}`)")


# ── /reset_weekly <user> ──────────────────────────────────────────────────────
async def reset_weekly(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.effective_user or not update.message:
        return
    if not _is_owner(update.effective_user.id):
        await update.message.reply_text("🚫 Owner only.")
        return
    target_id, target_name = await _resolve_target(update, context)
    if not target_id:
        await update.message.reply_text("Usage: /reset_weekly <user_id> or reply to user.")
        return
    cursor.execute("UPDATE weekly_claims SET last_weekly=0 WHERE user_id=?", (target_id,))
    cursor.execute("UPDATE shinobi_coins SET last_weekly=0 WHERE user_id=?", (target_id,))
    conn.commit()
    await update.message.reply_text(
        f"✅ Weekly reset for *{target_name}* (`{target_id}`).", parse_mode="Markdown"
    )
    await log_to_group(context.bot,
        f"🔄 *Weekly Reset*\n👤 {target_name} (`{target_id}`)\n👑 By: {update.effective_user.first_name}")


# ── /reset_all_weekly ─────────────────────────────────────────────────────────
async def reset_all_weekly(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.effective_user or not update.message:
        return
    if not _is_owner(update.effective_user.id):
        await update.message.reply_text("🚫 Owner only.")
        return
    cursor.execute("UPDATE weekly_claims SET last_weekly=0")
    cursor.execute("UPDATE shinobi_coins SET last_weekly=0")
    conn.commit()
    await update.message.reply_text("✅ *Weekly reset for all users!*", parse_mode="Markdown")
    await log_to_group(context.bot,
        f"🔄 *ALL Weekly Reset*\n👑 By: {update.effective_user.first_name} (`{update.effective_user.id}`)")


# ── /reset_monthly <user> ─────────────────────────────────────────────────────
async def reset_monthly(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.effective_user or not update.message:
        return
    if not _is_owner(update.effective_user.id):
        await update.message.reply_text("🚫 Owner only.")
        return
    target_id, target_name = await _resolve_target(update, context)
    if not target_id:
        await update.message.reply_text("Usage: /reset_monthly <user_id> or reply to user.")
        return
    cursor.execute("UPDATE monthly_claims SET last_monthly=0 WHERE user_id=?", (target_id,))
    cursor.execute("UPDATE shinobi_coins SET last_monthly=0 WHERE user_id=?", (target_id,))
    conn.commit()
    await update.message.reply_text(
        f"✅ Monthly reset for *{target_name}* (`{target_id}`).", parse_mode="Markdown"
    )
    await log_to_group(context.bot,
        f"🔄 *Monthly Reset*\n👤 {target_name} (`{target_id}`)\n👑 By: {update.effective_user.first_name}")


# ── /reset_all_monthly ────────────────────────────────────────────────────────
async def reset_all_monthly(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.effective_user or not update.message:
        return
    if not _is_owner(update.effective_user.id):
        await update.message.reply_text("🚫 Owner only.")
        return
    cursor.execute("UPDATE monthly_claims SET last_monthly=0")
    cursor.execute("UPDATE shinobi_coins SET last_monthly=0")
    conn.commit()
    await update.message.reply_text("✅ *Monthly reset for all users!*", parse_mode="Markdown")
    await log_to_group(context.bot,
        f"🔄 *ALL Monthly Reset*\n👑 By: {update.effective_user.first_name} (`{update.effective_user.id}`)")


# ── /give_shinobi_coins <amount> — Send coins to another user ────────────────
async def give_shinobi_coins(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /give_shinobi_coins <amount> — reply to a user or tag @username
    Transfers coins from sender to target user.
    """
    if not update.effective_user or not update.message:
        return
    user_id = update.effective_user.id
    if not has_started_bot(user_id) and not is_admin(user_id):
        await update.message.reply_text(NOT_STARTED_MSG, parse_mode="Markdown", disable_web_page_preview=True)
        return
    if is_banned(user_id):
        await update.message.reply_text(get_ban_message(user_id), parse_mode="Markdown")
        return

    if not context.args:
        await update.message.reply_text(
            "💸 *Give Shinobi Coins*\n\n"
            "Usage:\n"
            "• Reply to a user's message: `/give_shinobi_coins <amount>`\n"
            "• Tag their username: `/give_shinobi_coins <amount> @username`\n\n"
            "Example: `/give_shinobi_coins 100`",
            parse_mode="Markdown"
        )
        return

    try:
        amount = int(context.args[0])
        if amount <= 0:
            raise ValueError
    except ValueError:
        await update.message.reply_text("❌ Please enter a valid positive number.")
        return

    # ── Resolve target user ───────────────────────────────────────────────────
    target_id = None
    target_name = None

    # Priority 1: reply to message
    if update.message.reply_to_message and update.message.reply_to_message.from_user:
        target_user = update.message.reply_to_message.from_user
        if not target_user.is_bot:
            target_id = target_user.id
            target_name = target_user.first_name or str(target_id)

    # Priority 2: @username in args
    if target_id is None and len(context.args) >= 2:
        raw = context.args[1].lstrip("@")
        try:
            chat = await context.bot.get_chat(f"@{raw}")
            target_id = chat.id
            target_name = chat.first_name or raw
        except Exception:
            await update.message.reply_text(
                f"❌ Could not find user `@{raw}`. Make sure the username is correct.",
                parse_mode="Markdown"
            )
            return

    if target_id is None:
        await update.message.reply_text(
            "❌ Please reply to a user's message or provide their @username.\n\n"
            "Example: `/give_shinobi_coins 100 @username`",
            parse_mode="Markdown"
        )
        return

    if target_id == user_id:
        await update.message.reply_text("❌ You can't send coins to yourself!")
        return

    # ── Check sender balance ──────────────────────────────────────────────────
    sender_coins = get_coins(user_id)
    if sender_coins < amount:
        await update.message.reply_text(
            f"💔 *Insufficient Coins!*\n\n"
            f"🪙 You only have *{sender_coins}* Shinobi Coins.\n"
            f"You need *{amount}* to complete this transfer.\n\n"
            f"Earn more with /daily, /weekly, /monthly! 💪",
            parse_mode="Markdown"
        )
        return

    sender_name = update.effective_user.first_name or "Shinobi"

    # ── Transfer ──────────────────────────────────────────────────────────────
    deduct_coins(user_id, amount)
    add_coins(target_id, amount)

    sender_new = get_coins(user_id)
    target_new = get_coins(target_id)

    await update.message.reply_text(
        f"✅ *Shinobi Coins Transferred!*\n\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"📤 From: *{sender_name}*\n"
        f"📥 To: *{target_name}*\n"
        f"🪙 Amount: *{amount} Shinobi Coins*\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"💼 Your balance: *{sender_new}* coins",
        parse_mode="Markdown"
    )

    # Notify receiver in DM
    try:
        await context.bot.send_message(
            chat_id=target_id,
            text=(
                f"🎁 *You received Shinobi Coins!*\n\n"
                f"📤 From: *{sender_name}*\n"
                f"🪙 Amount: *+{amount} Shinobi Coins*\n"
                f"💼 Your balance: *{target_new}* coins\n\n"
                f"Use /shinobi\\_coins to check your wallet! 🔥"
            ),
            parse_mode="Markdown"
        )
    except Exception:
        pass  # User might not have DM'd the bot

    await log_to_group(
        context.bot,
        f"🪙 *Coin Transfer*\n\n"
        f"📤 {sender_name} (`{user_id}`)\n"
        f"📥 {target_name} (`{target_id}`)\n"
        f"💰 Amount: {amount} coins"
    )


# ================= SHOP SYSTEM =================

async def shop(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/shop — Open the Shinobi Card Shop."""
    if not update.effective_user or not update.message:
        return
    user_id = update.effective_user.id
    if not has_started_bot(user_id) and not is_admin(user_id):
        await update.message.reply_text(NOT_STARTED_MSG, parse_mode="Markdown", disable_web_page_preview=True)
        return
    if is_banned(user_id):
        await update.message.reply_text(get_ban_message(user_id), parse_mode="Markdown")
        return

    coins = get_coins(user_id)
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("🛒 Daily Deals", callback_data="shop_daily")],
        [InlineKeyboardButton("🪙 My Coins", callback_data="shop_coins")],
    ])
    await update.message.reply_text(
        f"🏯 *SHINOBI CARD SHOP* 🏯\n\n"
        f"{'━' * 24}\n"
        f"🪙 Your Coins: *{coins}*\n"
        f"{'━' * 24}\n\n"
        f"✨ *Buy rare shinobi cards using your coins!*\n\n"
        f"🛒 *Daily Deals* — 6 new cards every day at 5:30 AM\n"
        f"Each user gets their own unique set of deals!\n\n"
        f"👇 Choose a category:",
        reply_markup=keyboard,
        parse_mode="Markdown"
    )


async def shop_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle all shop inline button callbacks."""
    query = update.callback_query
    await query.answer()

    user_id = query.from_user.id
    user_name = query.from_user.first_name or "Shinobi"
    data = query.data

    # ── My Coins ──────────────────────────────────────────────────────────────
    if data == "shop_coins":
        coins = get_coins(user_id)
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("🔙 Back to Shop", callback_data="shop_main")],
        ])
        try:
            await query.edit_message_text(
                f"🪙 *Your Shinobi Coin Balance*\n\n"
                f"💰 Coins: *{coins}*\n\n"
                f"Earn more with /daily, /weekly, /monthly!",
                reply_markup=keyboard,
                parse_mode="Markdown"
            )
        except Exception:
            pass
        return

    # ── Back to main shop ─────────────────────────────────────────────────────
    if data == "shop_main":
        coins = get_coins(user_id)
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("🛒 Daily Deals", callback_data="shop_daily")],
            [InlineKeyboardButton("🪙 My Coins", callback_data="shop_coins")],
        ])
        try:
            await query.edit_message_text(
                f"🏯 *SHINOBI CARD SHOP* 🏯\n\n"
                f"{'━' * 24}\n"
                f"🪙 Your Coins: *{coins}*\n"
                f"{'━' * 24}\n\n"
                f"✨ *Buy rare shinobi cards using your coins!*\n\n"
                f"🛒 *Daily Deals* — 6 new cards every day at 5:30 AM\n"
                f"Each user gets their own unique set of deals!\n\n"
                f"👇 Choose a category:",
                reply_markup=keyboard,
                parse_mode="Markdown"
            )
        except Exception:
            pass
        return

    # ── Daily Deals list ──────────────────────────────────────────────────────
    if data == "shop_daily":
        deals = _get_or_create_deals(user_id)
        coins = get_coins(user_id)
        reset_in = _time_until_next_reset()

        lines = ""
        buttons = []
        rarity_order = ["Common", "Rare", "Epic", "Legendary", "Mythic", "Spring"]

        for rarity in rarity_order:
            card = deals.get(rarity)
            if not card:
                continue
            cid, cname, crarity, cfile_id, cfile_type = card
            circle = SHOP_RARITY_CIRCLES.get(rarity, "⭐")
            emoji = RARITY_EMOJI.get(rarity, "⭐")
            price = SHOP_PRICES.get(rarity, 999)
            lines += f"{circle} {cname.title()} {emoji} — *{price}* 🪙\n"
            buttons.append([
                InlineKeyboardButton(
                    f"{circle} {cname.title()} [{emoji}]",
                    callback_data=f"shop_buy_{cid}"
                )
            ])

        buttons.append([InlineKeyboardButton("🔙 Back", callback_data="shop_main")])

        text = (
            f"🥷 *DAILY SHINOBI DEALS!* 🥷\n\n"
            f"{'━' * 24}\n"
            f"{lines}"
            f"{'━' * 24}\n\n"
            f"🪙 Your Coins: *{coins}*\n"
            f"⏰ Next reset in: *{reset_in}*\n\n"
            f"👇 Tap a card to view & buy:"
        )
        try:
            await query.edit_message_text(
                text,
                reply_markup=InlineKeyboardMarkup(buttons),
                parse_mode="Markdown"
            )
        except Exception:
            pass
        return

    # ── Buy card view ─────────────────────────────────────────────────────────
    if data.startswith("shop_buy_"):
        try:
            card_id = int(data.replace("shop_buy_", ""))
        except ValueError:
            return

        cursor.execute(
            "SELECT id, name, rarity, file_id, COALESCE(file_type,'photo') FROM cards WHERE id=?",
            (card_id,)
        )
        card = cursor.fetchone()
        if not card:
            await query.answer("❌ Card no longer available.", show_alert=True)
            return

        cid, cname, crarity, cfile_id, cfile_type = card
        emoji = RARITY_EMOJI.get(crarity, "⭐")
        price = SHOP_PRICES.get(crarity, 999)
        coins = get_coins(user_id)

        caption = (
            f"🛍️ *{cname.title()}*\n\n"
            f"{'━' * 24}\n"
            f"{emoji} Rarity: *{crarity}*\n"
            f"💰 Price: *{price} Shinobi Coins*\n"
            f"{'━' * 24}\n\n"
            f"🪙 Your Coins: *{coins}*\n\n"
            f"Do you want to buy this shinobi?"
        )

        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton(f"✅ Buy ({price} 🪙)", callback_data=f"shop_confirm_{cid}")],
            [InlineKeyboardButton("🔙 Back to Deals", callback_data="shop_daily")],
        ])

        # Delete old message and send new one with card image
        try:
            await query.message.delete()
        except Exception:
            pass

        try:
            if cfile_type == "video":
                await context.bot.send_video(
                    chat_id=query.message.chat_id,
                    video=cfile_id,
                    caption=caption,
                    reply_markup=keyboard,
                    parse_mode="Markdown",
                    supports_streaming=True
                )
            elif cfile_type == "animation":
                await context.bot.send_animation(
                    chat_id=query.message.chat_id,
                    animation=cfile_id,
                    caption=caption,
                    reply_markup=keyboard,
                    parse_mode="Markdown"
                )
            else:
                await context.bot.send_photo(
                    chat_id=query.message.chat_id,
                    photo=cfile_id,
                    caption=caption,
                    reply_markup=keyboard,
                    parse_mode="Markdown"
                )
        except Exception as e:
            print(f"[SHOP] Failed to send card media: {e}")
            await context.bot.send_message(
                chat_id=query.message.chat_id,
                text=caption,
                reply_markup=keyboard,
                parse_mode="Markdown"
            )
        return

    # ── Confirm purchase ──────────────────────────────────────────────────────
    if data.startswith("shop_confirm_"):
        try:
            card_id = int(data.replace("shop_confirm_", ""))
        except ValueError:
            return

        cursor.execute(
            "SELECT id, name, rarity, file_id, COALESCE(file_type,'photo') FROM cards WHERE id=?",
            (card_id,)
        )
        card = cursor.fetchone()
        if not card:
            await query.answer("❌ Card not found.", show_alert=True)
            return

        cid, cname, crarity, cfile_id, cfile_type = card
        price = SHOP_PRICES.get(crarity, 999)
        coins = get_coins(user_id)

        if coins < price:
            await query.answer(
                f"❌ Not enough coins! You have {coins}, need {price}.",
                show_alert=True
            )
            # Also edit message to show error
            keyboard = InlineKeyboardMarkup([
                [InlineKeyboardButton("🔙 Back to Deals", callback_data="shop_daily")],
            ])
            try:
                await query.edit_message_caption(
                    caption=(
                        f"💔 *Not Enough Shinobi Coins!*\n\n"
                        f"🪙 You have: *{coins}* coins\n"
                        f"💰 You need: *{price}* coins\n\n"
                        f"Earn more with /daily, /weekly, /monthly! 💪"
                    ),
                    reply_markup=keyboard,
                    parse_mode="Markdown"
                )
            except Exception:
                pass
            return

        # Deduct and give card
        deduct_coins(user_id, price)
        insert_user_card(user_id, cname, crarity, cfile_id, cfile_type)
        conn.commit()

        new_coins = get_coins(user_id)
        emoji = RARITY_EMOJI.get(crarity, "⭐")

        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("🛒 Back to Deals", callback_data="shop_daily")],
            [InlineKeyboardButton("🏯 Back to Shop", callback_data="shop_main")],
        ])

        try:
            await query.edit_message_caption(
                caption=(
                    f"✅ *Purchase Successful!*\n\n"
                    f"{'━' * 24}\n"
                    f"🥷 Card: *{cname.title()}*\n"
                    f"{emoji} Rarity: *{crarity}*\n"
                    f"💰 Paid: *{price}* 🪙\n"
                    f"{'━' * 24}\n\n"
                    f"🪙 Remaining Coins: *{new_coins}*\n\n"
                    f"⚔️ Card added to your vault! Use /collection to view it."
                ),
                reply_markup=keyboard,
                parse_mode="Markdown"
            )
        except Exception:
            await context.bot.send_message(
                chat_id=query.message.chat_id,
                text=(
                    f"✅ *Purchase Successful!*\n\n"
                    f"🥷 *{cname.title()}* [{emoji}] added to your vault!\n"
                    f"💰 Paid: *{price}* 🪙 | Remaining: *{new_coins}* 🪙"
                ),
                parse_mode="Markdown"
            )

        await log_to_group(
            context.bot,
            f"🛍️ *Shop Purchase*\n\n"
            f"👤 {user_name} (`{user_id}`)\n"
            f"🥷 Card: {cname.title()} [{crarity}]\n"
            f"💰 Paid: {price} coins\n"
            f"🪙 Balance: {new_coins} coins"
        )
        return


# ═══════════════════════════════════════════════════════════════════
#                     WEEKLY PASS SYSTEM
# ═══════════════════════════════════════════════════════════════════

cursor.execute("""
CREATE TABLE IF NOT EXISTS weekly_pass (
    user_id       INTEGER PRIMARY KEY,
    active        INTEGER DEFAULT 0,
    purchased_at  INTEGER DEFAULT 0,
    week_start    INTEGER DEFAULT 0,
    days_claimed  TEXT    DEFAULT '[]'
)
""")
conn.commit()

WEEKLY_PASS_COST = 5000
WEEKLY_PASS_REWARDS = {
    1: {"coins": 500,  "rarity": "Common"},
    2: {"coins": 1000, "rarity": "Rare"},
    3: {"coins": 1500, "rarity": "Epic"},
    4: {"coins": 1500, "rarity": "Rare"},
    5: {"coins": 2000, "rarity": "Legendary"},
    6: {"coins": 2500, "rarity": "Legendary"},
    7: {"coins": 3000, "rarity": "Rare"},
}

def _monday_reset_epoch(now: int) -> int:
    """Epoch of the most recent Monday 00:00 IST."""
    now_ist = now + IST_OFFSET_SEC
    dow = (now_ist // 86400 + 3) % 7   # 0=Mon
    return now - (dow * 86400 + (now_ist % 86400))

def get_weekly_pass_row(user_id: int):
    import json
    cursor.execute("SELECT active, purchased_at, week_start, days_claimed FROM weekly_pass WHERE user_id=?", (user_id,))
    row = cursor.fetchone()
    if not row:
        return None
    active, purchased_at, week_start, dc_raw = row
    try:
        days_claimed = json.loads(dc_raw or "[]")
    except Exception:
        days_claimed = []
    return active, purchased_at, week_start, days_claimed

def is_weekly_pass_active(user_id: int) -> bool:
    now = int(time.time())
    monday = _monday_reset_epoch(now)
    row = get_weekly_pass_row(user_id)
    if not row:
        return False
    active, _, week_start, _ = row
    if week_start < monday:
        cursor.execute("UPDATE weekly_pass SET active=0 WHERE user_id=?", (user_id,))
        conn.commit()
        return False
    return bool(active)

WEEKLY_PASS_GATED_MSG = (
    "🔒 *Weekly Pass Required!*\n\n"
    "This feature is exclusive to *Weekly Pass* holders.\n\n"
    f"💰 Activate for *{WEEKLY_PASS_COST:,} Shinobi Coins* with /weekly\\_pass\n"
    "🔄 Resets every *Monday* — try again next week! 💪"
)

async def weekly_pass_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/weekly_pass — Activate weekly pass for 5000 coins."""
    import json
    if not update.effective_user or not update.message:
        return
    user_id = update.effective_user.id
    if not has_started_bot(user_id) and not is_admin(user_id):
        await update.message.reply_text(NOT_STARTED_MSG, parse_mode="Markdown", disable_web_page_preview=True)
        return
    if is_banned(user_id):
        await update.message.reply_text(get_ban_message(user_id), parse_mode="Markdown")
        return

    now = int(time.time())
    monday = _monday_reset_epoch(now)

    row = get_weekly_pass_row(user_id)
    if row and row[0] and row[2] >= monday:
        days_claimed = row[3]
        next_day = max(days_claimed) + 1 if days_claimed else 1
        next_day = min(next_day, 7)
        await update.message.reply_text(
            f"✅ *Weekly Pass Already Active!*\n\n"
            f"📅 Days Claimed: *{len(days_claimed)}/7*\n"
            f"🎁 Next Reward: *Day {next_day}*\n\n"
            f"Use /claim\\_weekly\\_pass to collect today's reward!\n"
            f"🔄 Resets every *Monday*.",
            parse_mode="Markdown"
        )
        return

    coins = get_coins(user_id)
    if coins < WEEKLY_PASS_COST:
        await update.message.reply_text(
            f"💸 *Not Enough Shinobi Coins!*\n\n"
            f"Weekly Pass costs: *{WEEKLY_PASS_COST:,} 🪙*\n"
            f"Your balance: *{coins:,} 🪙*\n\n"
            f"Earn coins by catching cards, /daily, /weekly, /monthly!\n"
            f"🎲 Or try /bet to win big!",
            parse_mode="Markdown"
        )
        return

    deduct_coins(user_id, WEEKLY_PASS_COST)
    cursor.execute("""
        INSERT OR REPLACE INTO weekly_pass (user_id, active, purchased_at, week_start, days_claimed)
        VALUES (?,1,?,?,?)
    """, (user_id, now, monday, "[]"))
    conn.commit()

    new_bal = get_coins(user_id)
    user_name = update.effective_user.first_name or "Shinobi"
    reward_lines = "\n".join([
        f"  {RARITY_EMOJI.get(r['rarity'],'⭐')} Day {d}: *{r['coins']:,}* coins + {r['rarity']} card"
        for d, r in WEEKLY_PASS_REWARDS.items()
    ])

    await update.message.reply_text(
        f"🏆 *WEEKLY PASS ACTIVATED!* 🎉\n\n"
        f"Welcome, *{user_name}*! Your pass is live!\n\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"💰 Paid: *{WEEKLY_PASS_COST:,} coins*\n"
        f"💼 Remaining: *{new_bal:,} coins*\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"🎁 *7 Day Rewards:*\n{reward_lines}\n\n"
        f"🌟 *Bonus:* Claim all 7 days → *Mythic card!*\n\n"
        f"📅 Use /claim\\_weekly\\_pass daily!\n"
        f"🔄 Resets every *Monday 00:00 IST*.",
        parse_mode="Markdown"
    )


async def claim_weekly_pass(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/claim_weekly_pass — Claim today's weekly pass reward."""
    import json
    if not update.effective_user or not update.message:
        return
    user_id = update.effective_user.id
    if not has_started_bot(user_id) and not is_admin(user_id):
        await update.message.reply_text(NOT_STARTED_MSG, parse_mode="Markdown", disable_web_page_preview=True)
        return
    if is_banned(user_id):
        await update.message.reply_text(get_ban_message(user_id), parse_mode="Markdown")
        return

    now = int(time.time())
    monday = _monday_reset_epoch(now)

    row = get_weekly_pass_row(user_id)
    if not row or not row[0] or row[2] < monday:
        await update.message.reply_text(WEEKLY_PASS_GATED_MSG, parse_mode="Markdown")
        return

    _, _, week_start, days_claimed = row

    next_day = (max(days_claimed) + 1) if days_claimed else 1
    if next_day > 7:
        await update.message.reply_text(
            f"🎊 *All 7 days claimed!*\n\n"
            f"Use /bonus to claim your *Mythic bonus card*!\n"
            f"🔄 New pass resets next *Monday*.",
            parse_mode="Markdown"
        )
        return

    # 24h cooldown per day — one claim per IST calendar day
    now_ist = now + IST_OFFSET_SEC
    today_ist_day = now_ist // 86400
    day_key = f"wp_day_{user_id}_{week_start}_{next_day}"
    cursor.execute("SELECT value FROM bot_settings WHERE key=?", (day_key,))
    lrow = cursor.fetchone()
    last_ist_day = int(lrow[0]) if lrow else 0

    if last_ist_day >= today_ist_day:
        next_midnight_ist = (today_ist_day + 1) * 86400
        rem = next_midnight_ist - now_ist
        hrs, mins = rem // 3600, (rem % 3600) // 60
        await update.message.reply_text(
            f"⏳ *Already claimed today!*\n\n"
            f"Come back in *{hrs}h {mins}m* for Day {next_day + 1}'s reward!\n"
            f"📅 Resets at *midnight IST*.",
            parse_mode="Markdown"
        )
        return

    reward = WEEKLY_PASS_REWARDS[next_day]
    rarity, coins_reward = reward["rarity"], reward["coins"]

    cursor.execute(
        "SELECT name, rarity, file_id, COALESCE(file_type,'photo') FROM cards WHERE rarity=? ORDER BY RANDOM() LIMIT 1",
        (rarity,)
    )
    card = cursor.fetchone()
    if not card:
        await update.message.reply_text(f"❌ No *{rarity}* cards in database. Ask admin to add some!", parse_mode="Markdown")
        return

    cname, crarity, cfile, cfile_type = card
    insert_user_card(user_id, cname, crarity, cfile)
    add_coins(user_id, coins_reward)

    days_claimed.append(next_day)
    cursor.execute("UPDATE weekly_pass SET days_claimed=? WHERE user_id=?", (json.dumps(days_claimed), user_id))
    cursor.execute("INSERT OR REPLACE INTO bot_settings (key, value) VALUES (?,?)", (day_key, str(today_ist_day)))
    conn.commit()

    new_coins = get_coins(user_id)
    user_name = update.effective_user.first_name or "Shinobi"
    card_emoji = RARITY_EMOJI.get(crarity, "⭐")
    days_left = 7 - next_day

    caption = (
        f"🎁 *WEEKLY PASS — DAY {next_day} REWARD!*\n\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🥷 Shinobi: *{user_name}*\n"
        f"🪙 Coins: *+{coins_reward:,}*\n"
        f"🃏 Card: *{cname.title()}* [{card_emoji} {crarity}]\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"💰 Total Coins: *{new_coins:,}*\n"
        f"📅 Days Claimed: *{next_day}/7*\n\n"
        + (f"🌟 *1 more day for Mythic bonus!*" if next_day == 6 else
           f"🎊 *All done! Use /bonus for Mythic card!*" if next_day == 7 else
           f"⏳ *{days_left} day(s) remaining!*")
    )

    if cfile_type == "video":
        await update.message.reply_video(video=cfile, caption=caption, parse_mode="Markdown", supports_streaming=True)
    else:
        await update.message.reply_photo(photo=cfile, caption=caption, parse_mode="Markdown")


async def weekly_pass_bonus(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/bonus — Claim Mythic bonus after all 7 days (only on day 7)."""
    import json
    if not update.effective_user or not update.message:
        return
    user_id = update.effective_user.id
    if not has_started_bot(user_id) and not is_admin(user_id):
        await update.message.reply_text(NOT_STARTED_MSG, parse_mode="Markdown", disable_web_page_preview=True)
        return
    if is_banned(user_id):
        await update.message.reply_text(get_ban_message(user_id), parse_mode="Markdown")
        return

    now = int(time.time())
    monday = _monday_reset_epoch(now)
    row = get_weekly_pass_row(user_id)

    if not row or not row[0] or row[2] < monday:
        await update.message.reply_text(WEEKLY_PASS_GATED_MSG, parse_mode="Markdown")
        return

    _, _, week_start, days_claimed = row

    if set(days_claimed) < {1,2,3,4,5,6,7}:
        missing = sorted(set(range(1,8)) - set(days_claimed))
        await update.message.reply_text(
            f"⏳ *Complete all 7 days first!*\n\nMissing days: *{missing}*\n\nUse /claim\\_weekly\\_pass daily!",
            parse_mode="Markdown"
        )
        return

    bonus_key = f"wp_bonus_{user_id}_{week_start}"
    cursor.execute("SELECT value FROM bot_settings WHERE key=?", (bonus_key,))
    if cursor.fetchone():
        await update.message.reply_text("✅ *Bonus already claimed this week!*\n\n🔄 New bonus next Monday.", parse_mode="Markdown")
        return

    cursor.execute("SELECT name, rarity, file_id, COALESCE(file_type,'photo') FROM cards WHERE rarity='Mythic' ORDER BY RANDOM() LIMIT 1")
    card = cursor.fetchone()
    if not card:
        await update.message.reply_text("❌ No Mythic cards in the database yet!", parse_mode="Markdown")
        return

    cname, crarity, cfile, cfile_type = card
    insert_user_card(user_id, cname, crarity, cfile)
    bonus_coins = 1000
    add_coins(user_id, bonus_coins)
    cursor.execute("INSERT OR REPLACE INTO bot_settings (key, value) VALUES (?,?)", (bonus_key, "1"))
    conn.commit()

    new_coins = get_coins(user_id)
    user_name = update.effective_user.first_name or "Shinobi"
    caption = (
        f"🌟✨ *WEEKLY PASS BONUS UNLOCKED!* ✨🌟\n\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🥷 *{user_name}*\n"
        f"🎊 *ALL 7 DAYS COMPLETED!*\n\n"
        f"🃏 Bonus: *{cname.title()}*\n"
        f"{RARITY_EMOJI.get(crarity,'⭐')} Rarity: *{crarity}*\n"
        f"🪙 Bonus Coins: *+{bonus_coins:,}*\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"💰 Total: *{new_coins:,}* coins\n\n"
        f"⚔️ True dedication of a shinobi! 🔥"
    )
    if cfile_type == "video":
        await update.message.reply_video(video=cfile, caption=caption, parse_mode="Markdown", supports_streaming=True)
    else:
        await update.message.reply_photo(photo=cfile, caption=caption, parse_mode="Markdown")


async def reset_weekly_pass_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/reset_weekly_pass — Owner: reset all users' weekly passes and show activation button."""
    if not update.effective_user or not update.message:
        return
    if not _is_owner(update.effective_user.id):
        await update.message.reply_text("🚫 Owner only.")
        return

    cursor.execute("UPDATE weekly_pass SET active=0, days_claimed='[]'")
    conn.commit()

    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton("🏆 Activate Weekly Pass", callback_data="activate_weekly_pass")
    ]])

    await update.message.reply_text(
        f"🔄 *Weekly Pass Reset for ALL Users!*\n\n"
        f"All passes have been cleared.\n\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"💰 Cost: *{WEEKLY_PASS_COST:,} Shinobi Coins*\n"
        f"🎁 7 daily rewards + Mythic bonus!\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"Tap below to activate your pass! 👇",
        parse_mode="Markdown",
        reply_markup=keyboard
    )


async def weekly_pass_activate_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle the Activate Weekly Pass inline button."""
    import json
    query = update.callback_query
    try:
        await query.answer()
    except Exception:
        pass

    user_id = query.from_user.id
    user_name = query.from_user.first_name or "Shinobi"

    if not has_started_bot(user_id):
        await query.answer("❌ You need to /start the bot first!", show_alert=True)
        return

    now = int(time.time())
    monday = _monday_reset_epoch(now)

    row = get_weekly_pass_row(user_id)
    if row and row[0] and row[2] >= monday:
        await query.answer("✅ You already have an active pass!", show_alert=True)
        return

    coins = get_coins(user_id)
    if coins < WEEKLY_PASS_COST:
        await query.answer(
            f"💸 Not enough coins!\nNeed {WEEKLY_PASS_COST:,} 🪙 | You have {coins:,} 🪙",
            show_alert=True
        )
        return

    deduct_coins(user_id, WEEKLY_PASS_COST)
    cursor.execute("""
        INSERT OR REPLACE INTO weekly_pass (user_id, active, purchased_at, week_start, days_claimed)
        VALUES (?,1,?,?,?)
    """, (user_id, now, monday, "[]"))
    conn.commit()

    new_bal = get_coins(user_id)
    await context.bot.send_message(
        chat_id=query.message.chat_id,
        text=(
            f"🎉✨ *CONGRATULATIONS, {user_name}!* ✨🎉\n\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"🏆 *Weekly Pass Activated!*\n\n"
            f"💰 Paid: *{WEEKLY_PASS_COST:,} coins*\n"
            f"💼 Remaining: *{new_bal:,} coins*\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"🎁 Use /claim\\_weekly\\_pass every day for rewards!\n"
            f"🌟 Complete all 7 days for a *Mythic card bonus!*\n"
            f"🔄 Resets every *Monday*."
        ),
        parse_mode="Markdown"
    )


# ═══════════════════════════════════════════════════════════════════
#              COLLABORATION DRAFT SYSTEM
# ═══════════════════════════════════════════════════════════════════

COLLAB_RARITY_WEIGHTS_DEFAULT = {
    "Rare":       57,
    "Epic":       35,
    "Legendary":   5,
    "Mythic":      2,
    "Spring":      1,   # "Demon Slayer 🗡️" — Spring rarity used for 100-spin pity
}
COLLAB_RARITY_WEIGHTS: dict = dict(COLLAB_RARITY_WEIGHTS_DEFAULT)

# Migration: add collab pity columns to draft_vouchers
for _collab_col in ("collab_total", "collab_since_leg", "collab_since_spring"):
    try:
        cursor.execute(f"ALTER TABLE draft_vouchers ADD COLUMN {_collab_col} INTEGER DEFAULT 0")
        conn.commit()
    except Exception:
        pass


def load_collab_weights():
    import json
    global COLLAB_RARITY_WEIGHTS
    cursor.execute("SELECT value FROM bot_settings WHERE key='collab_draft_rarities'")
    row = cursor.fetchone()
    if row:
        try:
            w = json.loads(row[0])
            if w:
                COLLAB_RARITY_WEIGHTS = w
                return
        except Exception:
            pass
    COLLAB_RARITY_WEIGHTS = dict(COLLAB_RARITY_WEIGHTS_DEFAULT)

load_collab_weights()


def get_collab_pity(user_id: int):
    cursor.execute("""
        SELECT COALESCE(collab_total,0), COALESCE(collab_since_leg,0), COALESCE(collab_since_spring,0)
        FROM draft_vouchers WHERE user_id=?
    """, (user_id,))
    row = cursor.fetchone()
    return row if row else (0, 0, 0)


def update_collab_pity(user_id: int, got_legendary: bool, got_spring: bool, num_spins: int):
    cursor.execute("""
        INSERT OR IGNORE INTO draft_vouchers
        (user_id, vouchers, total_spins, spins_since_legendary, spins_since_spring,
         collab_total, collab_since_leg, collab_since_spring)
        VALUES (?,0,0,0,0,0,0,0)
    """, (user_id,))
    cursor.execute("""
        UPDATE draft_vouchers SET
            collab_total       = COALESCE(collab_total,0) + ?,
            collab_since_leg   = CASE WHEN ? THEN 0 ELSE COALESCE(collab_since_leg,0)    + ? END,
            collab_since_spring= CASE WHEN ? THEN 0 ELSE COALESCE(collab_since_spring,0) + ? END
        WHERE user_id=?
    """, (num_spins, got_legendary, num_spins, got_spring, num_spins, user_id))
    conn.commit()


def collab_pull_single(user_id: int, spin_in_batch: int, since_leg: int, since_spring: int):
    """Single collab pull: 10-spin Legendary pity, 100-spin Spring pity."""
    forced = None

    # 100-spin Spring pity
    effective_spring = since_spring + spin_in_batch - 1
    if (effective_spring + 1) >= 100:
        forced = "Spring"

    # 10-spin Legendary pity (resets after each trigger)
    if forced is None and spin_in_batch == 10:
        forced = "Legendary"

    if forced:
        cursor.execute(
            "SELECT name, rarity, file_id, COALESCE(file_type,'photo') FROM cards WHERE rarity=? ORDER BY RANDOM() LIMIT 1",
            (forced,)
        )
        card = cursor.fetchone()
        if card:
            return card[0], card[1], card[2], card[3], forced
        forced = None

    # Normal pull
    rarities = list(COLLAB_RARITY_WEIGHTS.keys())
    weights  = list(COLLAB_RARITY_WEIGHTS.values())
    chosen   = random.choices(rarities, weights=weights, k=1)[0]
    cursor.execute(
        "SELECT name, rarity, file_id, COALESCE(file_type,'photo') FROM cards WHERE rarity=? ORDER BY RANDOM() LIMIT 1",
        (chosen,)
    )
    card = cursor.fetchone()
    if not card:
        cursor.execute("SELECT name, rarity, file_id, COALESCE(file_type,'photo') FROM cards ORDER BY RANDOM() LIMIT 1")
        card = cursor.fetchone()
    if not card:
        return None
    return card[0], card[1], card[2], card[3], chosen


def get_collab_banner() -> str | None:
    cursor.execute("SELECT value FROM bot_settings WHERE key='collab_draft_banner'")
    row = cursor.fetchone()
    return row[0] if row else None


async def collaboration_draft(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/collaboration_draft [10] — Spin the Collaboration Draft (uses draft vouchers)."""
    import json
    if not update.effective_user or not update.message:
        return
    user_id = update.effective_user.id
    if not has_started_bot(user_id) and not is_admin(user_id):
        await update.message.reply_text(NOT_STARTED_MSG, parse_mode="Markdown", disable_web_page_preview=True)
        return
    if is_banned(user_id):
        await update.message.reply_text(get_ban_message(user_id), parse_mode="Markdown")
        return

    # Check if event active
    cursor.execute("SELECT value FROM bot_settings WHERE key='collab_draft_active'")
    active_row = cursor.fetchone()
    if not active_row or active_row[0] != "1":
        await update.message.reply_text("🎴 No Collaboration Draft event is active right now. Watch for announcements!")
        return

    # Check expiry
    cursor.execute("SELECT value FROM bot_settings WHERE key='collab_draft_end'")
    end_row = cursor.fetchone()
    if end_row and int(time.time()) > int(end_row[0]):
        await update.message.reply_text("⏰ The Collaboration Draft event has ended. Stay tuned for the next one!")
        return

    args = context.args or []
    spin_count = 10 if args and args[0] in ("10", "x10") else 1

    vouchers = get_user_vouchers(user_id)
    if vouchers < spin_count:
        await update.message.reply_text(
            f"🎟️ *Not Enough Draft Vouchers!*\n\n"
            f"Need: *{spin_count}* | Have: *{vouchers}*\n\n"
            f"Earn vouchers from /daily, /weekly, /monthly!",
            parse_mode="Markdown"
        )
        return

    if not deduct_vouchers(user_id, spin_count):
        await update.message.reply_text("❌ Failed to deduct vouchers. Try again.")
        return

    total, since_leg, since_spring = get_collab_pity(user_id)
    cards_won = []
    got_legendary = False
    got_spring = False

    for i in range(1, spin_count + 1):
        result = collab_pull_single(user_id, i, since_leg, since_spring)
        if not result:
            continue
        cname, crarity, cfile, cfile_type, forced = result
        insert_user_card(user_id, cname, crarity, cfile)
        if crarity == "Legendary":
            got_legendary = True
        if crarity == "Spring":
            got_spring = True
        cards_won.append((cname, crarity, cfile, cfile_type, forced))

    conn.commit()
    update_collab_pity(user_id, got_legendary, got_spring, spin_count)

    # Recalculate pity display
    _, new_since_leg, new_since_spring = get_collab_pity(user_id)
    pity_leg_left = 10 - (new_since_leg % 10) if new_since_leg % 10 != 0 else 10
    pity_spring_left = 100 - new_since_spring
    new_vouchers = get_user_vouchers(user_id)

    if not cards_won:
        await update.message.reply_text("❌ No cards available in the collab pool. Ask admin to add cards!")
        return

    if spin_count == 1:
        cname, crarity, cfile, cfile_type, forced = cards_won[0]
        emoji = RARITY_EMOJI.get(crarity, "⭐")
        pity_tag = " ✨ *PITY!*" if forced and forced in ("Legendary","Spring") else ""
        caption = (
            f"🎴 *COLLABORATION DRAFT!*\n\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"🥷 *{cname.title()}*\n"
            f"{emoji} *{crarity}*{pity_tag}\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"🎟️ Vouchers left: *{new_vouchers}*\n"
            f"👑 Legendary pity: *{pity_leg_left}* spins\n"
            f"🗡️ Demon Slayer pity: *{pity_spring_left}* spins\n\n"
            f"Use /collaboration\\_draft 10 for x10 pull!"
        )
        if cfile_type == "video":
            await update.message.reply_video(video=cfile, caption=caption, parse_mode="Markdown", supports_streaming=True)
        else:
            await update.message.reply_photo(photo=cfile, caption=caption, parse_mode="Markdown")
    else:
        # x10 — text summary, show best card image
        priority_order = ["Spring","Mythic","Legendary","Epic","Rare","Common"]
        best = sorted(cards_won, key=lambda x: priority_order.index(x[1]) if x[1] in priority_order else 99)[0]
        lines = []
        for cname, crarity, _, _, forced in cards_won:
            emoji = RARITY_EMOJI.get(crarity, "⭐")
            tag = " ✨" if forced and forced in ("Legendary","Spring") else ""
            lines.append(f"{emoji} {cname.title()} [{crarity}]{tag}")

        caption = (
            f"🎴 *COLLABORATION DRAFT — x10!*\n\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            + "\n".join(lines) +
            f"\n━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"🎟️ Vouchers left: *{new_vouchers}*\n"
            f"👑 Legendary pity: *{pity_leg_left}* spins\n"
            f"🗡️ Demon Slayer pity: *{pity_spring_left}* spins"
        )
        if best[3] == "video":
            await update.message.reply_video(video=best[2], caption=caption, parse_mode="Markdown", supports_streaming=True)
        else:
            await update.message.reply_photo(photo=best[2], caption=caption, parse_mode="Markdown")


async def set_collaboration_draft_banner(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/set_collaboration_draft — Admin: set collab draft banner (reply to photo)."""
    if not update.effective_user or not update.message:
        return
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("🚫 Admin only.")
        return
    replied = update.message.reply_to_message
    if not replied or not replied.photo:
        await update.message.reply_text("📸 Reply to a photo with /set_collaboration_draft")
        return
    file_id = replied.photo[-1].file_id
    cursor.execute("INSERT OR REPLACE INTO bot_settings (key,value) VALUES ('collab_draft_banner',?)", (file_id,))
    conn.commit()
    await update.message.reply_text("✅ Collaboration Draft banner updated!")


async def start_collaboration_draft(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/start_collaboration_draft [days] — Owner: start collab draft event."""
    import json
    if not update.effective_user or not update.message:
        return
    if not _is_owner(update.effective_user.id):
        await update.message.reply_text("🚫 Owner only.")
        return

    args = list(context.args or [])
    days = 30
    if args:
        try:
            days = int(args[0])
        except ValueError:
            pass

    now = int(time.time())
    end_ts = now + days * 86400

    cursor.execute("INSERT OR REPLACE INTO bot_settings (key,value) VALUES ('collab_draft_active','1')")
    cursor.execute("INSERT OR REPLACE INTO bot_settings (key,value) VALUES ('collab_draft_start',?)", (str(now),))
    cursor.execute("INSERT OR REPLACE INTO bot_settings (key,value) VALUES ('collab_draft_end',?)", (str(end_ts),))
    cursor.execute("INSERT OR REPLACE INTO bot_settings (key,value) VALUES ('collab_draft_rarities',?)", (json.dumps(COLLAB_RARITY_WEIGHTS_DEFAULT),))
    conn.commit()
    load_collab_weights()

    end_str = time.strftime("%d %b %Y %H:%M UTC", time.gmtime(end_ts))
    weight_lines = "\n".join([f"  {RARITY_EMOJI.get(r,'⭐')} {r}: {w}%" for r,w in COLLAB_RARITY_WEIGHTS_DEFAULT.items()])

    await update.message.reply_text(
        f"✅ *Collaboration Draft Started!*\n\n"
        f"⏳ Duration: *{days} days*\n"
        f"🗓️ Ends: *{end_str}*\n\n"
        f"📊 *Drop Rates:*\n{weight_lines}\n\n"
        f"💎 *Pity:* 10th spin = Legendary | 100th spin = 🗡️ Demon Slayer\n\n"
        f"📢 Broadcasting to all users...",
        parse_mode="Markdown"
    )

    cursor.execute("SELECT DISTINCT user_id FROM bot_users")
    users = list(set(r[0] for r in cursor.fetchall()))
    banner_fid = get_collab_banner()
    announce = (
        f"⚔️ *COLLABORATION DRAFT IS LIVE!* ⚔️\n\n"
        f"{'━'*24}\n"
        f"⏳ *{days} days* | Ends: {end_str}\n"
        f"{'━'*24}\n\n"
        f"📊 *Drop Rates:*\n{weight_lines}\n\n"
        f"💎 *Pity:*\n"
        f"• Every *10th spin* → Guaranteed Legendary 👑\n"
        f"• Every *100th spin* → Guaranteed 🗡️ Demon Slayer!\n\n"
        f"🎟️ Uses same *Draft Vouchers* as /draft_event\n"
        f"👉 /collaboration\\_draft to spin!"
    )
    sent = 0
    for uid in users:
        try:
            if banner_fid:
                await context.bot.send_photo(chat_id=uid, photo=banner_fid, caption=announce, parse_mode="Markdown")
            else:
                await context.bot.send_message(chat_id=uid, text=announce, parse_mode="Markdown")
            sent += 1
            await asyncio.sleep(0.05)
        except Exception:
            pass
    await update.message.reply_text(f"📢 Notified *{sent}* users!", parse_mode="Markdown")


async def end_collaboration_draft(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/end_collaboration_draft — Owner: end the collab draft event."""
    if not update.effective_user or not update.message:
        return
    if not _is_owner(update.effective_user.id):
        await update.message.reply_text("🚫 Owner only.")
        return
    cursor.execute("INSERT OR REPLACE INTO bot_settings (key,value) VALUES ('collab_draft_active','0')")
    conn.commit()
    await update.message.reply_text("🛑 *Collaboration Draft event ended.*", parse_mode="Markdown")


# ═══════════════════════════════════════════════════════════════════
#              /SELL_SHINOBICARDS — Sell cards for coins
# ═══════════════════════════════════════════════════════════════════

SELL_COIN_VALUES = {
    "Common":    100,
    "Rare":      200,
    "Epic":      300,
    "Legendary": 2000,
    "Mythic":    5000,
    "Spring":    10000,
    # Animation and Crossverse are NOT sellable
}

async def sell_shinobicards(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/sell_shinobicards <card_id> — Sell a card from your collection for Shinobi Coins."""
    if not update.effective_user or not update.message:
        return
    user_id = update.effective_user.id
    if not has_started_bot(user_id) and not is_admin(user_id):
        await update.message.reply_text(NOT_STARTED_MSG, parse_mode="Markdown", disable_web_page_preview=True)
        return
    if is_banned(user_id):
        await update.message.reply_text(get_ban_message(user_id), parse_mode="Markdown")
        return

    if not context.args:
        sell_lines = "\n".join([
            f"  {RARITY_EMOJI.get(r,'⭐')} {r}: *{c:,}* coins"
            for r, c in SELL_COIN_VALUES.items()
        ])
        await update.message.reply_text(
            f"💰 *SELL SHINOBI CARDS*\n\n"
            f"Usage: `/sell_shinobicards #<card_id>`\n"
            f"Example: `/sell_shinobicards 42`\n\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"📊 *Sell Values:*\n{sell_lines}\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"⚠️ *Animation* and *Crossverse* cards cannot be sold.\n"
            f"🔢 Use the card ID from your /collection.",
            parse_mode="Markdown"
        )
        return

    try:
        card_id = int(context.args[0].replace("#", ""))
    except ValueError:
        await update.message.reply_text("❌ Invalid card ID. Use the number from your /collection.")
        return

    # Look up by master card_id first, then row id
    cursor.execute(
        "SELECT id, card_name, rarity, file_id, COALESCE(file_type,'photo') FROM user_cards "
        "WHERE card_id=? AND user_id=? LIMIT 1",
        (card_id, user_id)
    )
    row = cursor.fetchone()
    if not row:
        cursor.execute(
            "SELECT id, card_name, rarity, file_id, COALESCE(file_type,'photo') FROM user_cards "
            "WHERE id=? AND user_id=? LIMIT 1",
            (card_id, user_id)
        )
        row = cursor.fetchone()

    if not row:
        await update.message.reply_text(
            f"❌ *Card #{card_id} not found in your collection!*\n\n"
            f"Use /collection to view your cards and their IDs.",
            parse_mode="Markdown"
        )
        return

    row_id, card_name, rarity, file_id, file_type = row

    # Block unsellable rarities
    if rarity in ("Animation", "Crossverse"):
        await update.message.reply_text(
            f"🚫 *{rarity} cards cannot be sold!*\n\n"
            f"These are special cards and are not part of the sell system.",
            parse_mode="Markdown"
        )
        return

    sell_value = SELL_COIN_VALUES.get(rarity)
    if sell_value is None:
        await update.message.reply_text(f"❌ *{rarity}* rarity cards cannot be sold.", parse_mode="Markdown")
        return

    card_emoji = RARITY_EMOJI.get(rarity, "⭐")

    # Confirmation inline button
    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton(f"✅ Sell for {sell_value:,} 🪙", callback_data=f"sell_confirm_{row_id}_{sell_value}"),
        InlineKeyboardButton("❌ Cancel", callback_data=f"sell_cancel_{row_id}")
    ]])

    caption = (
        f"💰 *SELL CONFIRMATION*\n\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🥷 Card: *{card_name.title()}*\n"
        f"{card_emoji} Rarity: *{rarity}*\n"
        f"🪙 You'll receive: *{sell_value:,} coins*\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"⚠️ This will *permanently remove* the card from your collection!\n"
        f"Are you sure?"
    )

    if file_type == "video":
        await update.message.reply_video(video=file_id, caption=caption, parse_mode="Markdown",
                                         supports_streaming=True, reply_markup=keyboard)
    else:
        await update.message.reply_photo(photo=file_id, caption=caption, parse_mode="Markdown",
                                         reply_markup=keyboard)


async def sell_card_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle sell confirm/cancel callbacks."""
    query = update.callback_query
    try:
        await query.answer()
    except Exception:
        pass

    user_id = query.from_user.id
    data = query.data

    if data.startswith("sell_cancel_"):
        await query.edit_message_caption("❌ *Sale cancelled.* Your card is safe!", parse_mode="Markdown")
        return

    if data.startswith("sell_confirm_"):
        parts = data.split("_")
        try:
            row_id    = int(parts[2])
            sell_value = int(parts[3])
        except (IndexError, ValueError):
            await query.edit_message_caption("❌ Error processing sale.", parse_mode="Markdown")
            return

        # Verify still owns the card
        cursor.execute(
            "SELECT card_name, rarity FROM user_cards WHERE id=? AND user_id=?",
            (row_id, user_id)
        )
        card_row = cursor.fetchone()
        if not card_row:
            await query.edit_message_caption("❌ *Card no longer found in your collection!*", parse_mode="Markdown")
            return

        card_name, rarity = card_row
        card_emoji = RARITY_EMOJI.get(rarity, "⭐")

        # Delete card and award coins
        cursor.execute("DELETE FROM user_cards WHERE id=? AND user_id=?", (row_id, user_id))
        conn.commit()
        add_coins(user_id, sell_value)
        new_bal = get_coins(user_id)

        await query.edit_message_caption(
            f"✅ *CARD SOLD!*\n\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"🥷 Sold: *{card_name.title()}* [{card_emoji} {rarity}]\n"
            f"🪙 Earned: *+{sell_value:,} coins!*\n"
            f"💰 New Balance: *{new_bal:,} coins*\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"⚔️ Good trade, shinobi! 💪",
            parse_mode="Markdown"
        )


# ═══════════════════════════════════════════════════════════════════
#                       🎁 AIRDROP SYSTEM
# ═══════════════════════════════════════════════════════════════════

cursor.execute("""
CREATE TABLE IF NOT EXISTS airdrop_data (
    user_id          INTEGER PRIMARY KEY,
    last_free        INTEGER DEFAULT 0,   -- epoch of last free box open
    last_ad          INTEGER DEFAULT 0,   -- epoch of last ad box open
    streak           INTEGER DEFAULT 0,   -- consecutive daily opens
    total_opens      INTEGER DEFAULT 0,   -- all-time opens
    last_streak_day  INTEGER DEFAULT 0    -- IST day number of last open (for streak)
)
""")
conn.commit()

# ── Airdrop cooldowns ─────────────────────────────────────────────────────────
AIRDROP_FREE_COOLDOWN = 86400   # 24h  — one free box per day
AIRDROP_AD_COOLDOWN   = 600     # 10min — ad box cooldown
AIRDROP_STREAK_BONUS  = 5       # every 5 opens  → bonus reward
AIRDROP_SPRING_OPEN   = 20      # every 20 opens → guaranteed Spring card

# ── Drop rates ────────────────────────────────────────────────────────────────
AIRDROP_COIN_WEIGHT    = 60
AIRDROP_VOUCHER_WEIGHT = 35
AIRDROP_SPRING_WEIGHT  = 5

def _get_airdrop_row(user_id: int):
    cursor.execute(
        "SELECT last_free, last_ad, streak, total_opens, last_streak_day "
        "FROM airdrop_data WHERE user_id=?", (user_id,)
    )
    row = cursor.fetchone()
    if not row:
        cursor.execute(
            "INSERT OR IGNORE INTO airdrop_data "
            "(user_id, last_free, last_ad, streak, total_opens, last_streak_day) "
            "VALUES (?,0,0,0,0,0)", (user_id,)
        )
        conn.commit()
        return 0, 0, 0, 0, 0
    return row


def _generate_airdrop_reward(user_id: int, total_opens: int):
    """
    Returns (reward_type, value, display_text)
    reward_type: 'coins' | 'voucher' | 'spring'
    """
    # Guaranteed Spring on 20th open (or multiples)
    if total_opens > 0 and total_opens % AIRDROP_SPRING_OPEN == 0:
        cursor.execute(
            "SELECT name, rarity, file_id, COALESCE(file_type,'photo') "
            "FROM cards WHERE rarity='Spring' ORDER BY RANDOM() LIMIT 1"
        )
        card = cursor.fetchone()
        if card:
            return "spring", card, f"🌸 *SPRING CARD: {card[0].title()}!*"
        # fallback to coins if no spring cards
    else:
        roll = random.random() * 100
        if roll < AIRDROP_SPRING_WEIGHT:
            cursor.execute(
                "SELECT name, rarity, file_id, COALESCE(file_type,'photo') "
                "FROM cards WHERE rarity='Spring' ORDER BY RANDOM() LIMIT 1"
            )
            card = cursor.fetchone()
            if card:
                return "spring", card, f"🌸 *SPRING CARD: {card[0].title()}!*"
        if roll < (AIRDROP_SPRING_WEIGHT + AIRDROP_VOUCHER_WEIGHT):
            amount = random.randint(2, 20)
            return "voucher", amount, f"🎟️ *{amount} Draft Vouchers!*"

    # Coins (default)
    amount = random.randint(500, 5000)
    return "coins", amount, f"💰 *{amount:,} Shinobi Coins!*"


async def _announce_spring_drop(bot, user_name: str, card_name: str, chat_id: int):
    """Announce a Spring pull to the group."""
    try:
        await bot.send_message(
            chat_id=AIRDROP_ANNOUNCE_ID,
            text=(
                f"🌍 *GLOBAL AIRDROP DROP!*\n\n"
                f"🔥 *{user_name}* just pulled a\n"
                f"🌸 *SPRING CARD: {card_name.title()}!!*\n\n"
                f"from the 🎁 Airdrop Box!\n\n"
                f"⚔️ Will you be next? Use /airdrop now!"
            ),
            parse_mode="Markdown"
        )
    except Exception:
        pass


async def airdrop_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/airdrop — Open the daily airdrop box."""
    if not update.effective_user or not update.message:
        return
    user_id   = update.effective_user.id
    user_name = update.effective_user.first_name or "Shinobi"

    if not has_started_bot(user_id) and not is_admin(user_id):
        await update.message.reply_text(NOT_STARTED_MSG, parse_mode="Markdown", disable_web_page_preview=True)
        return
    if is_banned(user_id):
        await update.message.reply_text(get_ban_message(user_id), parse_mode="Markdown")
        return

    now           = int(time.time())
    last_free, last_ad, streak, total_opens, last_streak_day = _get_airdrop_row(user_id)

    # Work out if free box is available
    free_available = (now - last_free) >= AIRDROP_FREE_COOLDOWN
    ad_available   = (now - last_ad)   >= AIRDROP_AD_COOLDOWN

    # Time until next free box
    if not free_available:
        rem      = AIRDROP_FREE_COOLDOWN - (now - last_free)
        hrs, mn  = rem // 3600, (rem % 3600) // 60
        free_txt = f"⏳ Free box in: *{hrs}h {mn}m*"
    else:
        free_txt = "✅ *Free box available!*"

    if not ad_available:
        rem_ad   = AIRDROP_AD_COOLDOWN - (now - last_ad)
        ad_txt   = f"⏳ Ad box in: *{rem_ad // 60}m {rem_ad % 60}s*"
    else:
        ad_txt   = "📺 *Watch Ad available!*"

    # Streak display
    streak_bar = "🔥" * min(streak, 5)
    next_streak = AIRDROP_STREAK_BONUS - (streak % AIRDROP_STREAK_BONUS) if streak % AIRDROP_STREAK_BONUS != 0 else AIRDROP_STREAK_BONUS

    # Build inline keyboard
    buttons = []
    if free_available:
        buttons.append(InlineKeyboardButton("🎁 Open Free Box", callback_data="airdrop_free"))
    if ad_available:
        # Opens the Mini App WebApp (real AdsGram ad inside)
        buttons.append(InlineKeyboardButton(
            "📺 Watch Ad & Open",
            web_app=WebAppInfo(url=WEBAPP_URL)
        ))

    if not buttons:
        await update.message.reply_text(
            f"🎁 *AIRDROP BOX*\n\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"😴 *All boxes on cooldown!*\n\n"
            f"{free_txt}\n"
            f"{ad_txt}\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"🔥 Streak: *{streak}* opens {streak_bar}\n"
            f"📦 Total opened: *{total_opens}*\n\n"
            f"Come back soon, shinobi! 🥷",
            parse_mode="Markdown"
        )
        return

    keyboard = InlineKeyboardMarkup([buttons])

    await update.message.reply_text(
        f"🎁 *SHINOBI AIRDROP BOX!*\n\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"✨ Mystery rewards await inside!\n\n"
        f"🪙 *Coins* — 60% chance (500–5,000)\n"
        f"🎟️ *Draft Vouchers* — 35% chance (2–20)\n"
        f"🌸 *Spring Card* — 5% chance\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"{free_txt}\n"
        f"{ad_txt}\n\n"
        f"🔥 Streak: *{streak}* {streak_bar}\n"
        f"⚡ *{next_streak} more* opens for streak bonus!\n"
        f"🌸 *{AIRDROP_SPRING_OPEN - (total_opens % AIRDROP_SPRING_OPEN)}* opens until guaranteed Spring!\n\n"
        f"👇 *Tap to open your box!*",
        parse_mode="Markdown",
        reply_markup=keyboard
    )


async def airdrop_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle Free Box and Watch Ad & Open buttons."""
    query = update.callback_query
    try:
        await query.answer()
    except Exception:
        pass

    user_id   = query.from_user.id
    user_name = query.from_user.first_name or "Shinobi"
    data      = query.data
    now       = int(time.time())

    if not has_started_bot(user_id):
        await query.answer("❌ Please /start the bot first!", show_alert=True)
        return

    last_free, last_ad, streak, total_opens, last_streak_day = _get_airdrop_row(user_id)

    if data == "airdrop_free":
        if (now - last_free) < AIRDROP_FREE_COOLDOWN:
            rem     = AIRDROP_FREE_COOLDOWN - (now - last_free)
            hrs, mn = rem // 3600, (rem % 3600) // 60
            await query.answer(f"⏳ Free box resets in {hrs}h {mn}m!", show_alert=True)
            return
        box_type   = "free"
        last_free  = now

    elif data == "airdrop_ad":
        if (now - last_ad) < AIRDROP_AD_COOLDOWN:
            rem_ad = AIRDROP_AD_COOLDOWN - (now - last_ad)
            await query.answer(f"⏳ Ad box resets in {rem_ad // 60}m {rem_ad % 60}s!", show_alert=True)
            return
        # AdsGram — fire verification and proceed
        # (AdsGram webhook validates the token server-side; here we optimistically
        #  grant reward after the user taps "Watch Ad"; server callback at /adsgram_callback
        #  can be used for extra validation)
        box_type = "ad"
        last_ad  = now

    else:
        return

    # ── Update streak ─────────────────────────────────────────────────────────
    now_ist         = now + IST_OFFSET_SEC
    today_ist_day   = now_ist // 86400
    if today_ist_day == last_streak_day + 1:
        streak += 1          # consecutive day
    elif today_ist_day > last_streak_day + 1:
        streak = 1           # streak broken, reset to 1
    # same day → don't change streak

    total_opens += 1

    # ── Generate reward ───────────────────────────────────────────────────────
    reward_type, reward_value, reward_text = _generate_airdrop_reward(user_id, total_opens)

    # ── Apply reward ──────────────────────────────────────────────────────────
    streak_bonus_msg = ""
    if total_opens % AIRDROP_STREAK_BONUS == 0:
        # Streak milestone bonus: +500 coins
        add_coins(user_id, 500)
        streak_bonus_msg = f"\n\n🎊 *STREAK BONUS!* +500 coins for opening *{total_opens}* boxes!"

    if reward_type == "coins":
        add_coins(user_id, reward_value)
        new_bal = get_coins(user_id)
        result_detail = f"💰 *+{reward_value:,} Shinobi Coins!*\n💼 Balance: *{new_bal:,}*"

    elif reward_type == "voucher":
        add_vouchers(user_id, reward_value)
        new_v = get_user_vouchers(user_id)
        result_detail = f"🎟️ *+{reward_value} Draft Vouchers!*\n💼 Total: *{new_v}*"

    elif reward_type == "spring":
        cname, crarity, cfile, cfile_type = reward_value
        insert_user_card(user_id, cname, crarity, cfile)
        result_detail = f"🌸 *SPRING CARD!*\n🥷 {cname.title()}\n{RARITY_EMOJI.get(crarity,'🌸')} {crarity}"
        # Global announcement
        await _announce_spring_drop(context.bot, user_name, cname, query.message.chat_id)

    # ── Save DB ───────────────────────────────────────────────────────────────
    cursor.execute("""
        INSERT OR REPLACE INTO airdrop_data
        (user_id, last_free, last_ad, streak, total_opens, last_streak_day)
        VALUES (?,?,?,?,?,?)
    """, (user_id, last_free, last_ad, streak, total_opens, today_ist_day))
    conn.commit()

    # ── Animation-style reveal ────────────────────────────────────────────────
    open_label = "🆓 Free Box" if box_type == "free" else "📺 Ad Box"
    streak_bar = "🔥" * min(streak, 5)
    next_spring = AIRDROP_SPRING_OPEN - (total_opens % AIRDROP_SPRING_OPEN)

    await query.edit_message_text(
        f"🎁 *OPENING BOX...*\n\n"
        f"✨ ...\n"
        f"💥 *YOU GOT:*\n\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"{result_detail}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"{streak_bonus_msg}\n\n"
        f"🎯 *{open_label}* opened!\n"
        f"🔥 Streak: *{streak}* {streak_bar}\n"
        f"📦 Total opens: *{total_opens}*\n"
        f"🌸 Guaranteed Spring in: *{next_spring}* opens\n\n"
        f"Use /airdrop to open again!",
        parse_mode="Markdown"
    )

    # If it was a Spring card, also send the card image
    if reward_type == "spring":
        cname, crarity, cfile, cfile_type = reward_value
        cap = (
            f"🌸 *AIRDROP SPRING CARD!*\n\n"
            f"🥷 *{cname.title()}*\n"
            f"{RARITY_EMOJI.get(crarity,'🌸')} *{crarity}*\n\n"
            f"⚔️ Congratulations, *{user_name}*! 🔥"
        )
        try:
            if cfile_type == "video":
                await context.bot.send_video(
                    chat_id=query.message.chat_id, video=cfile,
                    caption=cap, parse_mode="Markdown", supports_streaming=True
                )
            else:
                await context.bot.send_photo(
                    chat_id=query.message.chat_id, photo=cfile,
                    caption=cap, parse_mode="Markdown"
                )
        except Exception:
            pass


def main():

    # 🔥 START FLASK SERVER (no reload issue)
    def run_web():
        app.run(
            host="0.0.0.0",
            port=int(os.environ.get("PORT", 5000)),
            use_reloader=False
        )

    threading.Thread(target=run_web, daemon=True).start()

    # 🔥 TELEGRAM BOT SETUP
    request = HTTPXRequest(
        connect_timeout=30,
        read_timeout=30,
        write_timeout=30,
        pool_timeout=30
    )

    get_updates_request = HTTPXRequest(
        connect_timeout=30,
        read_timeout=60,
        write_timeout=30,
        pool_timeout=30
    )

    telegram_app = (
        ApplicationBuilder()
        .token(BOT_TOKEN)
        .request(request)
        .get_updates_request(get_updates_request)
        .build()
    )

    telegram_app.add_handler(CommandHandler("start", start))

    print("Bot is running...")
    telegram_app.run_polling()
    # ── Universal DM-registration gate ──────────────────────────────────────
    # Fires in group -1 BEFORE all other handlers.
    # ApplicationHandlerStop stops ALL subsequent handler groups.
    EXEMPT_COMMANDS = {"start", "join", "forcestart"}  # commands always allowed without /start

    async def require_dm_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not update.effective_user or not update.message:
            return
        if not update.message.text or not update.message.text.startswith("/"):
            return
        user_id = update.effective_user.id
        cmd = update.message.text.split()[0].lower().lstrip("/").split("@")[0]
        if cmd in EXEMPT_COMMANDS:
            return
        if is_admin(user_id):
            return
        if has_started_bot(user_id):
            return
        await update.message.reply_text(
            NOT_STARTED_MSG,
            parse_mode="Markdown",
            disable_web_page_preview=True
        )
        from telegram.ext import ApplicationHandlerStop
        raise ApplicationHandlerStop

    app.add_handler(
        MessageHandler(filters.COMMAND, require_dm_start),
        group=-1
    )
    app.add_handler(CommandHandler("catch", catch))
    app.add_handler(CommandHandler("daily", daily))
    app.add_handler(CommandHandler("weekly", weekly))
    app.add_handler(CommandHandler("monthly", monthly))
    app.add_handler(CommandHandler("pack", pack))
    app.add_handler(CommandHandler("trade_cards", trade_cards))
    app.add_handler(CommandHandler("add_draft", add_draft))
    app.add_handler(CommandHandler("transfer_draft", transfer_draft))
    # ── Shinobi Coins ──────────────────────────────────────────────────────────
    app.add_handler(CommandHandler("shinobi_coins", shinobi_coins_cmd))
    app.add_handler(CommandHandler("top_shinobi_coins", top_shinobi_coins))
    app.add_handler(CommandHandler("bet", bet))
    app.add_handler(CommandHandler("add_shinobi_coins", add_shinobi_coins_cmd))
    app.add_handler(CommandHandler("generate_shinobi_coins", generate_shinobi_coins_cmd))
    app.add_handler(CommandHandler("redeem", redeem_cmd))
    app.add_handler(CallbackQueryHandler(trade_confirm_callback, pattern="^trade_"))
    app.add_handler(CommandHandler("list_cards", list_cards))
    app.add_handler(CommandHandler("leaderboard", leaderboard))
    app.add_handler(CommandHandler("addcard", addcard))
    app.add_handler(CommandHandler("collection", collection))
    app.add_handler(CallbackQueryHandler(collection_nav, pattern="^(next|prev)_"))
    app.add_handler(CommandHandler("fav", fav))
    app.add_handler(CommandHandler("redeem_create", redeem_create))
    app.add_handler(CommandHandler("redeem", redeem))
    app.add_handler(CallbackQueryHandler(fav_button, pattern="^fav_"))
    app.add_handler(CommandHandler("check_table", check_cards_table))
    app.add_handler(CommandHandler("forcespawn", forcespawn))
    app.add_handler(CommandHandler("delcard", delcard))
    app.add_handler(CommandHandler("editcard", editcard))
    app.add_handler(CommandHandler("cooldown", cooldown))
    app.add_handler(CommandHandler("backup", backup))
    app.add_handler(CommandHandler("changetime", changetime))
    app.add_handler(CommandHandler("profile", profile))
    app.add_handler(CommandHandler("admins", admins))
    app.add_handler(CommandHandler("addadmin", addadmin))
    app.add_handler(CommandHandler("removeadmin", removeadmin))
    app.add_handler(CommandHandler("broadcast", broadcast))
    app.add_handler(CommandHandler("ban", ban))
    app.add_handler(CommandHandler("unban", unban))
    app.add_handler(CommandHandler("mute", mute_cmd))
    app.add_handler(CommandHandler("convlogs", convlogs))
    app.add_handler(CommandHandler("tban", tban))
    app.add_handler(CommandHandler("collected", collected))
    app.add_handler(CommandHandler("rarity", rarity_cmd))
    app.add_handler(CallbackQueryHandler(global_collector_callback, pattern="^gc_"))
    # ── New Draft & Referral Commands ─────────────────────────────────────────
    app.add_handler(CommandHandler("draft_event", draft_event))
    app.add_handler(CommandHandler("my_vouchers", my_vouchers))
    app.add_handler(CommandHandler("view_drafts", view_drafts))
    app.add_handler(CommandHandler("generate_draft", generate_draft))
    app.add_handler(CommandHandler("redeem_draft", redeem_draft))
    app.add_handler(CommandHandler("set_draft_banner", set_draft_banner))
    app.add_handler(CommandHandler("add", add_to_collection))
    app.add_handler(CommandHandler("remove", remove_from_collection))
    app.add_handler(CommandHandler("transfer", transfer_collection))
    app.add_handler(CommandHandler("find", find_card))
    app.add_handler(CommandHandler("delacc", delacc))
    app.add_handler(CommandHandler("shinobi_mode", shinobi_mode))
    app.add_handler(CommandHandler("referral", referral_cmd))
    app.add_handler(CallbackQueryHandler(draft_callback, pattern="^draft_"))
    app.add_handler(CommandHandler("start_draft", start_draft_event))
    app.add_handler(CommandHandler("stop_draft", stop_draft_event))
    app.add_handler(CommandHandler("rec_acc", rec_acc))
    app.add_handler(CommandHandler("give_all", give_all))
    app.add_handler(CommandHandler("random_pick", random_pick))
    app.add_handler(CommandHandler("start_wordgame", start_wordgame))
    app.add_handler(CommandHandler("stop_wordgame", stop_wordgame))
    app.add_handler(CommandHandler("forcestart", forcestart_wordgame))
    app.add_handler(CommandHandler("join", join_wordgame))
    # ── Owner Reset Commands ───────────────────────────────────────────────────
    app.add_handler(CommandHandler("reset_shinobi_coins", reset_shinobi_coins))
    app.add_handler(CommandHandler("reset_all_shinobi_coins", reset_all_shinobi_coins))
    app.add_handler(CommandHandler("Add_all_shinobi_coins", add_all_shinobi_coins))
    app.add_handler(CommandHandler("reset_daily", reset_daily))
    app.add_handler(CommandHandler("reset_all_daily", reset_all_daily))
    app.add_handler(CommandHandler("reset_weekly", reset_weekly))
    app.add_handler(CommandHandler("reset_all_weekly", reset_all_weekly))
    app.add_handler(CommandHandler("reset_monthly", reset_monthly))
    app.add_handler(CommandHandler("reset_all_monthly", reset_all_monthly))
    app.add_handler(CommandHandler("give_shinobi_coins", give_shinobi_coins))
    app.add_handler(CommandHandler("shop", shop))
    app.add_handler(CallbackQueryHandler(shop_callback, pattern="^shop_"))
    # ── Airdrop ────────────────────────────────────────────────────────────────
    app.add_handler(CommandHandler("airdrop", airdrop_cmd))
    app.add_handler(CallbackQueryHandler(airdrop_callback, pattern="^airdrop_"))
    # ── Weekly Pass ────────────────────────────────────────────────────────────
    app.add_handler(CommandHandler("weekly_pass", weekly_pass_cmd))
    app.add_handler(CommandHandler("claim_weekly_pass", claim_weekly_pass))
    app.add_handler(CommandHandler("bonus", weekly_pass_bonus))
    app.add_handler(CommandHandler("reset_weekly_pass", reset_weekly_pass_cmd))
    app.add_handler(CallbackQueryHandler(weekly_pass_activate_callback, pattern="^activate_weekly_pass$"))
    # ── Collaboration Draft ────────────────────────────────────────────────────
    app.add_handler(CommandHandler("collaboration_draft", collaboration_draft))
    app.add_handler(CommandHandler("set_collaboration_draft", set_collaboration_draft_banner))
    app.add_handler(CommandHandler("start_collaboration_draft", start_collaboration_draft))
    app.add_handler(CommandHandler("end_collaboration_draft", end_collaboration_draft))
    # ── Sell Cards ────────────────────────────────────────────────────────────
    app.add_handler(CommandHandler("sell_shinobicards", sell_shinobicards))
    app.add_handler(CallbackQueryHandler(sell_card_callback, pattern="^sell_"))
    app.add_handler(MessageHandler(
        filters.TEXT & ~filters.COMMAND, handle_wordgame_answer
    ), group=2)

    app.add_error_handler(error_handler)

    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, message_tracker))
    # Use group=1 so command handlers fire first, then message_tracker runs for spawn counting
    app.add_handler(MessageHandler(filters.COMMAND, message_tracker), group=1)
    app.add_handler(MessageHandler(filters.Sticker.ALL, sticker_tracker))
    app.add_handler(InlineQueryHandler(inline_card_query))

    print("✅ Bot running...")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
