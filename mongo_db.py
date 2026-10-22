"""
mongo_db.py — Hybrid MongoDB layer for Naruto TCG Bot
======================================================
All USER DATA lives in MongoDB (never wiped on redeploy).
Card definitions, settings, codes stay in SQLite.

Collections in MongoDB:
  - user_cards        : user card collections
  - bot_users         : registered users
  - shinobi_mode      : fav rarity settings
  - draft_vouchers    : spin pity/counts
  - shinobi_coins     : coin wallets
  - daily_claims      : daily/weekly/monthly claims
  - shop_daily_deals  : daily shop deals
  - bot_groups        : active groups
  - weekly_pass       : weekly pass data
  - conv_logs         : message logs
  - trades            : trade history
  - referrals         : referral data
  - clan_members      : clan membership
  - banned_users      : banned user list
"""

import os
import time
from pymongo import MongoClient, UpdateOne, ASCENDING, DESCENDING
from pymongo.errors import ConnectionFailure, OperationFailure

MONGO_URI = os.environ.get(
    "MONGO_URI",
    "mongodb+srv://nishanthofficial11_db_user:nishanthse28@cluster0.luqxd0f.mongodb.net/?appName=Cluster0"
)
DB_NAME = "naruto_tcg"

_client = None
_db = None

def get_db():
    global _client, _db
    if _db is not None:
        return _db
    try:
        _client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=10000)
        _client.admin.command("ping")
        _db = _client[DB_NAME]
        print(f"[MONGO] ✅ Connected to MongoDB: {DB_NAME}")
        _ensure_indexes()
        return _db
    except Exception as e:
        print(f"[MONGO] ❌ Connection failed: {e}")
        return None

def _ensure_indexes():
    """Create indexes for performance."""
    try:
        db = _db
        db.user_cards.create_index([("user_id", ASCENDING)])
        db.user_cards.create_index([("card_id", ASCENDING)])
        db.user_cards.create_index([("sqlite_id", ASCENDING)], sparse=True)
        db.user_cards.create_index([("rarity", ASCENDING)])
        db.user_cards.create_index([("series", ASCENDING)])
        db.bot_users.create_index([("user_id", ASCENDING)], unique=True)
        db.bot_users.create_index([("username", ASCENDING)])
        db.shinobi_coins.create_index([("user_id", ASCENDING)], unique=True)
        db.draft_vouchers.create_index([("user_id", ASCENDING)], unique=True)
        db.daily_claims.create_index([("user_id", ASCENDING)])
        db.bot_groups.create_index([("chat_id", ASCENDING)], unique=True)
        db.banned_users.create_index([("user_id", ASCENDING)], unique=True)
        print("[MONGO] ✅ Indexes created")
    except Exception as e:
        print(f"[MONGO] Index error: {e}")


# ── SAFE WRAPPER ──────────────────────────────────────────────────────────────

def safe(fn, *args, fallback=None, **kwargs):
    """Call a MongoDB function safely, return fallback on error."""
    try:
        db = get_db()
        if db is None:
            return fallback
        return fn(db, *args, **kwargs)
    except Exception as e:
        print(f"[MONGO] Error in {fn.__name__}: {e}")
        return fallback


# ══════════════════════════════════════════════════════════════════════════════
# USER CARDS
# ══════════════════════════════════════════════════════════════════════════════

def insert_user_card_mongo(user_id: int, card_name: str, rarity: str,
                            file_id: str, file_type: str = "photo",
                            card_id: int = None, series: str = "Naruto"):
    def _do(db):
        doc = {
            "user_id":   user_id,
            "card_name": card_name,
            "rarity":    rarity,
            "file_id":   file_id,
            "file_type": file_type,
            "card_id":   card_id,
            "series":    series,
            "caught_at": int(time.time()),
        }
        db.user_cards.insert_one(doc)
        return True
    return safe(_do, fallback=False)


def get_user_cards_mongo(user_id: int):
    """Return all cards for a user as list of dicts."""
    def _do(db):
        return list(db.user_cards.find({"user_id": user_id}))
    return safe(_do, fallback=[])


def get_user_cards_grouped_mongo(user_id: int):
    """
    Return grouped collection: [(card_id, card_name, rarity, count, series), ...]
    Mirrors the SQLite GROUP BY query.
    """
    def _do(db):
        pipeline = [
            {"$match": {"user_id": user_id}},
            {"$group": {
                "_id": {
                    "card_id":   "$card_id",
                    "card_name": "$card_name",
                    "rarity":    "$rarity",
                    "series":    "$series",
                },
                "count": {"$sum": 1}
            }},
            {"$project": {
                "card_id":   "$_id.card_id",
                "card_name": "$_id.card_name",
                "rarity":    "$_id.rarity",
                "series":    "$_id.series",
                "count":     1,
                "_id": 0
            }}
        ]
        rows = list(db.user_cards.aggregate(pipeline))
        # Return as tuples matching SQLite format: (card_id, card_name, rarity, count, series)
        return [
            (r.get("card_id"), r["card_name"], r["rarity"],
             r["count"], r.get("series", "Naruto"))
            for r in rows
        ]
    return safe(_do, fallback=[])


def count_user_cards_mongo(user_id: int):
    def _do(db):
        return db.user_cards.count_documents({"user_id": user_id})
    return safe(_do, fallback=0)


def delete_user_card_mongo(user_id: int, card_name: str, rarity: str, count: int = 1):
    """Delete `count` copies of a card from user's collection."""
    def _do(db):
        # Find one matching document at a time and delete
        for _ in range(count):
            doc = db.user_cards.find_one(
                {"user_id": user_id, "card_name": card_name, "rarity": rarity}
            )
            if doc:
                db.user_cards.delete_one({"_id": doc["_id"]})
        return True
    return safe(_do, fallback=False)


def delete_user_card_by_id_mongo(user_id: int, card_id: int):
    """Delete one copy of a card by card_id."""
    def _do(db):
        doc = db.user_cards.find_one({"user_id": user_id, "card_id": card_id})
        if doc:
            db.user_cards.delete_one({"_id": doc["_id"]})
            return True
        return False
    return safe(_do, fallback=False)


def get_card_owners_mongo(card_name: str, rarity: str):
    """Get distinct user_ids who own a specific card."""
    def _do(db):
        return db.user_cards.distinct("user_id", {"card_name": card_name, "rarity": rarity})
    return safe(_do, fallback=[])


def count_global_collectors_mongo(card_name: str, rarity: str):
    def _do(db):
        return len(db.user_cards.distinct("user_id", {"card_name": card_name, "rarity": rarity}))
    return safe(_do, fallback=0)


def get_top_collectors_by_rarity_mongo(rarity: str, limit: int = 10):
    """Return top collectors for a rarity: [(user_id, count), ...]"""
    def _do(db):
        pipeline = [
            {"$match": {"rarity": rarity}},
            {"$group": {"_id": "$user_id", "count": {"$sum": 1}}},
            {"$sort": {"count": DESCENDING}},
            {"$limit": limit}
        ]
        return [(r["_id"], r["count"]) for r in db.user_cards.aggregate(pipeline)]
    return safe(_do, fallback=[])


def get_top_collectors_by_series_rarity_mongo(series: str, rarity: str, limit: int = 10):
    """Return top collectors for a series+rarity combo."""
    def _do(db):
        pipeline = [
            {"$match": {"series": series, "rarity": rarity}},
            {"$group": {"_id": "$user_id", "count": {"$sum": 1}}},
            {"$sort": {"count": DESCENDING}},
            {"$limit": limit}
        ]
        return [(r["_id"], r["count"]) for r in db.user_cards.aggregate(pipeline)]
    return safe(_do, fallback=[])


def transfer_card_mongo(from_user: int, to_user: int, card_name: str, rarity: str):
    """Move one card from one user to another."""
    def _do(db):
        doc = db.user_cards.find_one(
            {"user_id": from_user, "card_name": card_name, "rarity": rarity}
        )
        if not doc:
            return False
        db.user_cards.update_one(
            {"_id": doc["_id"]},
            {"$set": {"user_id": to_user}}
        )
        return True
    return safe(_do, fallback=False)


# ══════════════════════════════════════════════════════════════════════════════
# BOT USERS
# ══════════════════════════════════════════════════════════════════════════════

def register_user_mongo(user_id: int, first_name: str, username: str):
    def _do(db):
        db.bot_users.update_one(
            {"user_id": user_id},
            {"$setOnInsert": {
                "user_id":    user_id,
                "first_name": first_name,
                "username":   username or "",
                "joined_at":  int(time.time()),
            }},
            upsert=True
        )
        return True
    return safe(_do, fallback=False)


def get_user_mongo(user_id: int):
    def _do(db):
        return db.bot_users.find_one({"user_id": user_id})
    return safe(_do, fallback=None)


def get_user_by_username_mongo(username: str):
    def _do(db):
        return db.bot_users.find_one({"username": {"$regex": f"^{username}$", "$options": "i"}})
    return safe(_do, fallback=None)


def get_all_users_mongo():
    def _do(db):
        return list(db.bot_users.find({}, {"user_id": 1, "first_name": 1, "_id": 0}))
    return safe(_do, fallback=[])


def has_started_bot_mongo(user_id: int) -> bool:
    def _do(db):
        return db.bot_users.find_one({"user_id": user_id}) is not None
    return safe(_do, fallback=False)


def delete_user_mongo(user_id: int):
    def _do(db):
        db.bot_users.delete_one({"user_id": user_id})
    return safe(_do)


# ══════════════════════════════════════════════════════════════════════════════
# SHINOBI COINS
# ══════════════════════════════════════════════════════════════════════════════

def get_coins_mongo(user_id: int) -> int:
    def _do(db):
        doc = db.shinobi_coins.find_one({"user_id": user_id})
        return doc["balance"] if doc else 0
    return safe(_do, fallback=0)


def add_coins_mongo(user_id: int, amount: int) -> int:
    def _do(db):
        result = db.shinobi_coins.find_one_and_update(
            {"user_id": user_id},
            {"$inc": {"balance": amount}},
            upsert=True,
            return_document=True
        )
        return result["balance"]
    return safe(_do, fallback=0)


def set_coins_mongo(user_id: int, amount: int):
    def _do(db):
        db.shinobi_coins.update_one(
            {"user_id": user_id},
            {"$set": {"balance": amount}},
            upsert=True
        )
    return safe(_do)


def deduct_coins_mongo(user_id: int, amount: int) -> bool:
    """Deduct coins only if user has enough. Returns True if successful."""
    def _do(db):
        result = db.shinobi_coins.find_one_and_update(
            {"user_id": user_id, "balance": {"$gte": amount}},
            {"$inc": {"balance": -amount}},
            return_document=True
        )
        return result is not None
    return safe(_do, fallback=False)


def get_top_coins_mongo(limit: int = 10):
    def _do(db):
        return list(db.shinobi_coins.find(
            {}, {"user_id": 1, "balance": 1, "_id": 0}
        ).sort("balance", DESCENDING).limit(limit))
    return safe(_do, fallback=[])


# ══════════════════════════════════════════════════════════════════════════════
# DRAFT VOUCHERS
# ══════════════════════════════════════════════════════════════════════════════

def get_vouchers_mongo(user_id: int) -> dict:
    def _do(db):
        doc = db.draft_vouchers.find_one({"user_id": user_id})
        if not doc:
            return {
                "user_id": user_id, "vouchers": 0, "total_spins": 0,
                "spins_since_legendary": 0, "spins_since_spring": 0,
                "collab_total": 0, "collab_since_leg": 0, "collab_since_spring": 0
            }
        return doc
    return safe(_do, fallback={
        "user_id": user_id, "vouchers": 0, "total_spins": 0,
        "spins_since_legendary": 0, "spins_since_spring": 0,
        "collab_total": 0, "collab_since_leg": 0, "collab_since_spring": 0
    })


def update_vouchers_mongo(user_id: int, update_fields: dict):
    def _do(db):
        db.draft_vouchers.update_one(
            {"user_id": user_id},
            {"$set": update_fields},
            upsert=True
        )
    return safe(_do)


def add_vouchers_mongo(user_id: int, amount: int):
    def _do(db):
        db.draft_vouchers.update_one(
            {"user_id": user_id},
            {"$inc": {"vouchers": amount}},
            upsert=True
        )
    return safe(_do)


def deduct_vouchers_mongo(user_id: int, amount: int) -> bool:
    def _do(db):
        result = db.draft_vouchers.find_one_and_update(
            {"user_id": user_id, "vouchers": {"$gte": amount}},
            {"$inc": {"vouchers": -amount}},
            return_document=True
        )
        return result is not None
    return safe(_do, fallback=False)


# ══════════════════════════════════════════════════════════════════════════════
# DAILY / WEEKLY / MONTHLY CLAIMS
# ══════════════════════════════════════════════════════════════════════════════

def get_claim_mongo(user_id: int, claim_type: str) -> dict:
    """claim_type: 'daily', 'weekly', 'monthly'"""
    def _do(db):
        return db.daily_claims.find_one({"user_id": user_id, "type": claim_type}) or {}
    return safe(_do, fallback={})


def set_claim_mongo(user_id: int, claim_type: str, epoch: int, streak: int = 0):
    def _do(db):
        db.daily_claims.update_one(
            {"user_id": user_id, "type": claim_type},
            {"$set": {"epoch": epoch, "streak": streak, "claimed_at": int(time.time())}},
            upsert=True
        )
    return safe(_do)


# ══════════════════════════════════════════════════════════════════════════════
# SHINOBI MODE (fav rarity)
# ══════════════════════════════════════════════════════════════════════════════

def get_fav_rarity_mongo(user_id: int):
    def _do(db):
        doc = db.shinobi_mode.find_one({"user_id": user_id})
        return doc["fav_rarity"] if doc else None
    return safe(_do, fallback=None)


def set_fav_rarity_mongo(user_id: int, rarity: str):
    def _do(db):
        db.shinobi_mode.update_one(
            {"user_id": user_id},
            {"$set": {"fav_rarity": rarity}},
            upsert=True
        )
    return safe(_do)


def clear_fav_rarity_mongo(user_id: int):
    def _do(db):
        db.shinobi_mode.delete_one({"user_id": user_id})
    return safe(_do)


# ══════════════════════════════════════════════════════════════════════════════
# BOT GROUPS
# ══════════════════════════════════════════════════════════════════════════════

def register_group_mongo(chat_id: int, title: str):
    def _do(db):
        db.bot_groups.update_one(
            {"chat_id": chat_id},
            {"$set": {"title": title, "last_seen": int(time.time())}},
            upsert=True
        )
    return safe(_do)


def get_all_groups_mongo():
    def _do(db):
        return [r["chat_id"] for r in db.bot_groups.find({}, {"chat_id": 1})]
    return safe(_do, fallback=[])


def remove_group_mongo(chat_id: int):
    def _do(db):
        db.bot_groups.delete_one({"chat_id": chat_id})
    return safe(_do)


# ══════════════════════════════════════════════════════════════════════════════
# BANNED USERS
# ══════════════════════════════════════════════════════════════════════════════

def ban_user_mongo(user_id: int, reason: str = "", banned_by: int = 0):
    def _do(db):
        db.banned_users.update_one(
            {"user_id": user_id},
            {"$set": {
                "user_id":   user_id,
                "reason":    reason,
                "banned_by": banned_by,
                "banned_at": int(time.time())
            }},
            upsert=True
        )
    return safe(_do)


def unban_user_mongo(user_id: int):
    def _do(db):
        db.banned_users.delete_one({"user_id": user_id})
    return safe(_do)


def is_banned_mongo(user_id: int) -> bool:
    def _do(db):
        return db.banned_users.find_one({"user_id": user_id}) is not None
    return safe(_do, fallback=False)


def get_ban_info_mongo(user_id: int) -> dict:
    def _do(db):
        return db.banned_users.find_one({"user_id": user_id}) or {}
    return safe(_do, fallback={})


# ══════════════════════════════════════════════════════════════════════════════
# SHOP DAILY DEALS
# ══════════════════════════════════════════════════════════════════════════════

def get_shop_deals_mongo(user_id: int, reset_epoch: int) -> dict:
    def _do(db):
        doc = db.shop_daily_deals.find_one({"user_id": user_id, "reset_epoch": reset_epoch})
        return doc or {}
    return safe(_do, fallback={})


def set_shop_deals_mongo(user_id: int, reset_epoch: int, deals: dict):
    def _do(db):
        db.shop_daily_deals.update_one(
            {"user_id": user_id},
            {"$set": {"reset_epoch": reset_epoch, "deals": deals}},
            upsert=True
        )
    return safe(_do)


# ══════════════════════════════════════════════════════════════════════════════
# MIGRATION: SQLite → MongoDB
# ══════════════════════════════════════════════════════════════════════════════

def migrate_sqlite_to_mongo(sqlite_conn):
    """
    One-time migration of all user data from SQLite to MongoDB.
    Safe to run multiple times (uses upsert).
    """
    db = get_db()
    if db is None:
        print("[MIGRATE] ❌ MongoDB not available")
        return False

    cur = sqlite_conn.cursor()
    errors = 0
    total = 0

    print("[MIGRATE] Starting SQLite → MongoDB migration...")

    # 1. bot_users
    try:
        cur.execute("SELECT user_id, first_name, username, joined_at FROM bot_users")
        rows = cur.fetchall()
        ops = []
        for user_id, first_name, username, joined_at in rows:
            ops.append(UpdateOne(
                {"user_id": user_id},
                {"$setOnInsert": {
                    "user_id": user_id,
                    "first_name": first_name or "",
                    "username": username or "",
                    "joined_at": joined_at or int(time.time()),
                }},
                upsert=True
            ))
        if ops:
            db.bot_users.bulk_write(ops, ordered=False)
        print(f"[MIGRATE] ✅ bot_users: {len(rows)} records")
        total += len(rows)
    except Exception as e:
        print(f"[MIGRATE] ❌ bot_users: {e}")
        errors += 1

    # 2. user_cards
    try:
        cur.execute("""
            SELECT uc.id, uc.user_id, uc.card_name, uc.rarity,
                   uc.file_id, COALESCE(uc.file_type,'photo'),
                   uc.card_id, COALESCE(uc.series, c.series, 'Naruto')
            FROM user_cards uc
            LEFT JOIN cards c ON c.id = uc.card_id
        """)
        rows = cur.fetchall()
        docs = []
        for row_id, user_id, card_name, rarity, file_id, file_type, card_id, series in rows:
            docs.append({
                "sqlite_id": row_id,
                "user_id":   user_id,
                "card_name": card_name,
                "rarity":    rarity,
                "file_id":   file_id or "",
                "file_type": file_type,
                "card_id":   card_id,
                "series":    series,
                "caught_at": int(time.time()),
            })
        if docs:
            # Upsert by sqlite_id so re-runs never duplicate cards
            ops = [
                UpdateOne(
                    {"sqlite_id": d["sqlite_id"]},
                    {"$setOnInsert": d},
                    upsert=True,
                )
                for d in docs
            ]
            for i in range(0, len(ops), 1000):
                try:
                    db.user_cards.bulk_write(ops[i:i+1000], ordered=False)
                except Exception:
                    pass
        print(f"[MIGRATE] ✅ user_cards: {len(rows)} records")
        total += len(rows)
    except Exception as e:
        print(f"[MIGRATE] ❌ user_cards: {e}")
        errors += 1

    # 3. shinobi_coins
    try:
        cur.execute("SELECT user_id, balance FROM shinobi_coins")
        rows = cur.fetchall()
        ops = []
        for user_id, balance in rows:
            ops.append(UpdateOne(
                {"user_id": user_id},
                {"$setOnInsert": {"user_id": user_id, "balance": balance or 0}},
                upsert=True
            ))
        if ops:
            db.shinobi_coins.bulk_write(ops, ordered=False)
        print(f"[MIGRATE] ✅ shinobi_coins: {len(rows)} records")
        total += len(rows)
    except Exception as e:
        print(f"[MIGRATE] ❌ shinobi_coins: {e}")
        errors += 1

    # 4. draft_vouchers
    try:
        cur.execute("""
            SELECT user_id, vouchers, total_spins, spins_since_legendary,
                   spins_since_spring, collab_total, collab_since_leg, collab_since_spring
            FROM draft_vouchers
        """)
        rows = cur.fetchall()
        ops = []
        for row in rows:
            uid = row[0]
            ops.append(UpdateOne(
                {"user_id": uid},
                {"$setOnInsert": {
                    "user_id": uid,
                    "vouchers": row[1] or 0,
                    "total_spins": row[2] or 0,
                    "spins_since_legendary": row[3] or 0,
                    "spins_since_spring": row[4] or 0,
                    "collab_total": row[5] or 0,
                    "collab_since_leg": row[6] or 0,
                    "collab_since_spring": row[7] or 0,
                }},
                upsert=True
            ))
        if ops:
            db.draft_vouchers.bulk_write(ops, ordered=False)
        print(f"[MIGRATE] ✅ draft_vouchers: {len(rows)} records")
        total += len(rows)
    except Exception as e:
        print(f"[MIGRATE] ❌ draft_vouchers: {e}")
        errors += 1

    # 5. shinobi_mode
    try:
        cur.execute("SELECT user_id, fav_rarity FROM shinobi_mode")
        rows = cur.fetchall()
        ops = []
        for user_id, fav_rarity in rows:
            if fav_rarity:
                ops.append(UpdateOne(
                    {"user_id": user_id},
                    {"$setOnInsert": {"user_id": user_id, "fav_rarity": fav_rarity}},
                    upsert=True
                ))
        if ops:
            db.shinobi_mode.bulk_write(ops, ordered=False)
        print(f"[MIGRATE] ✅ shinobi_mode: {len(rows)} records")
        total += len(rows)
    except Exception as e:
        print(f"[MIGRATE] ❌ shinobi_mode: {e}")
        errors += 1

    # 6. bot_groups
    try:
        cur.execute("SELECT chat_id, title, last_seen FROM bot_groups")
        rows = cur.fetchall()
        ops = []
        for chat_id, title, last_seen in rows:
            ops.append(UpdateOne(
                {"chat_id": chat_id},
                {"$set": {"chat_id": chat_id, "title": title or "", "last_seen": last_seen or 0}},
                upsert=True
            ))
        if ops:
            db.bot_groups.bulk_write(ops, ordered=False)
        print(f"[MIGRATE] ✅ bot_groups: {len(rows)} records")
        total += len(rows)
    except Exception as e:
        print(f"[MIGRATE] ❌ bot_groups: {e}")
        errors += 1

    # 7. banned_users
    try:
        cur.execute("SELECT user_id, reason, banned_by, banned_at FROM banned_users")
        rows = cur.fetchall()
        ops = []
        for user_id, reason, banned_by, banned_at in rows:
            ops.append(UpdateOne(
                {"user_id": user_id},
                {"$setOnInsert": {
                    "user_id": user_id,
                    "reason": reason or "",
                    "banned_by": banned_by or 0,
                    "banned_at": banned_at or int(time.time()),
                }},
                upsert=True
            ))
        if ops:
            db.banned_users.bulk_write(ops, ordered=False)
        print(f"[MIGRATE] ✅ banned_users: {len(rows)} records")
        total += len(rows)
    except Exception as e:
        print(f"[MIGRATE] ❌ banned_users: {e}")
        errors += 1

    # 8. weekly_claims
    try:
        cur.execute("SELECT user_id, last_weekly_epoch FROM weekly_claims")
        rows = cur.fetchall()
        ops = []
        for user_id, epoch in rows:
            ops.append(UpdateOne(
                {"user_id": user_id, "type": "weekly"},
                {"$setOnInsert": {"user_id": user_id, "type": "weekly", "epoch": epoch or 0}},
                upsert=True
            ))
        if ops:
            db.daily_claims.bulk_write(ops, ordered=False)
        print(f"[MIGRATE] ✅ weekly_claims: {len(rows)} records")
        total += len(rows)
    except Exception as e:
        print(f"[MIGRATE] ❌ weekly_claims: {e}")

    # 9. monthly_claims
    try:
        cur.execute("SELECT user_id, last_monthly_epoch FROM monthly_claims")
        rows = cur.fetchall()
        ops = []
        for user_id, epoch in rows:
            ops.append(UpdateOne(
                {"user_id": user_id, "type": "monthly"},
                {"$setOnInsert": {"user_id": user_id, "type": "monthly", "epoch": epoch or 0}},
                upsert=True
            ))
        if ops:
            db.daily_claims.bulk_write(ops, ordered=False)
        print(f"[MIGRATE] ✅ monthly_claims: {len(rows)} records")
        total += len(rows)
    except Exception as e:
        print(f"[MIGRATE] ❌ monthly_claims: {e}")

    print(f"\n[MIGRATE] ✅ Migration complete! {total} records migrated, {errors} errors.")
    return errors == 0


def init_mongo_and_migrate():
    """
    Called once at bot startup.
    - Connects to MongoDB
    - Runs SQLite→MongoDB migration if not already done
    """
    import os, sqlite3
    db = get_db()
    if db is None:
        print("[INIT] ⚠️ MongoDB unavailable, running on SQLite only.")
        return

    # Check if migration already done
    marker = db.migration_status.find_one({"key": "sqlite_migrated"})
    if marker and marker.get("done"):
        print("[INIT] ✅ MongoDB ready (migration already done).")
        return

    # Run migration
    # Use same safe path detection as bot.py
    def _is_writable(path):
        import tempfile
        if not os.path.isdir(path): return False
        try:
            tempfile.NamedTemporaryFile(dir=path, delete=True).close()
            return True
        except: return False
    _env_db = os.environ.get("CARDS_DB_PATH", "").strip()
    _bot_dir = os.path.dirname(os.path.abspath(__file__))
    if _env_db:
        _env_dir = os.path.dirname(_env_db) or _bot_dir
        sqlite_path = _env_db if _is_writable(_env_dir) else os.path.join(_bot_dir, "cards.db")
    elif _is_writable("/data"):
        sqlite_path = "/data/cards.db"
    else:
        sqlite_path = os.path.join(_bot_dir, "cards.db")
    if not os.path.exists(sqlite_path):
        print("[INIT] ⚠️ cards.db not found, skipping migration.")
        db.migration_status.update_one(
            {"key": "sqlite_migrated"},
            {"$set": {"done": True, "note": "no sqlite file"}},
            upsert=True
        )
        return

    print("[INIT] 🔄 Starting one-time SQLite → MongoDB migration...")
    try:
        conn = sqlite3.connect(sqlite_path)
        success = migrate_sqlite_to_mongo(conn)
        conn.close()
        if success:
            db.migration_status.update_one(
                {"key": "sqlite_migrated"},
                {"$set": {"done": True}},
                upsert=True
            )
            print("[INIT] ✅ Migration complete and marked as done.")
        else:
            print("[INIT] ⚠️ Migration finished with some errors.")
    except Exception as e:
        print(f"[INIT] ❌ Migration failed: {e}")


def fix_onepiece_summer_inflation_mongo():
    """
    Remove extra copies of bulk-given One Piece Summer cards (card_ids 1133-1153).
    Each user should have at most 1 copy of each of these cards.
    Call once from admin panel or on startup.
    """
    if not MONGO_AVAILABLE:
        return 0

    bulk_card_ids = [1133,1134,1135,1136,1137,1138,1139,1140,1141,1142,1143,1144,
                     1145,1146,1148,1150,1151,1152,1153]
    removed = 0
    try:
        # Get all users who have OP Summer cards
        users = user_cards_col.distinct("user_id", {
            "rarity": "Summer",
            "series": "One Piece",
            "card_id": {"$in": bulk_card_ids}
        })
        for uid in users:
            for cid in bulk_card_ids:
                # Find all docs for this user + card
                docs = list(user_cards_col.find(
                    {"user_id": uid, "card_id": cid, "rarity": "Summer", "series": "One Piece"},
                    {"_id": 1}
                ).sort("_id", 1))
                if len(docs) > 1:
                    # Keep the first (oldest), delete the rest
                    ids_to_delete = [d["_id"] for d in docs[1:]]
                    result = user_cards_col.delete_many({"_id": {"$in": ids_to_delete}})
                    removed += result.deleted_count
    except Exception as e:
        print(f"[MONGO FIX] Error during OP Summer cleanup: {e}")
    return removed

