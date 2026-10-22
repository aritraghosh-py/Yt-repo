# ==============================================================================
#  ⚔️  ARENA WARS  —  deterministic, no-RNG anime team-battle mode
#  ------------------------------------------------------------------------
#  Self-contained module. Does NOT touch any existing bot code/tables.
#  Same setup(telegram_app, DB_PATH, is_admin) signature as before —
#  your main bot file needs ZERO changes to pick up this update.
#
#  THIS VERSION: the entire draft (challenge -> scouting -> role picks ->
#  ready check) happens inside a SINGLE message that gets edited in place
#  (photo swapped via edit_message_media when a new candidate is scouted,
#  caption/buttons updated via edit_message_caption otherwise). No more
#  message spam. Only the final battle report is a separate message
#  (it's too long to fit in a photo caption).
# ==============================================================================

import sqlite3
import random
import time
import itertools
from io import BytesIO
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, InputMediaPhoto
from telegram.ext import CommandHandler, CallbackQueryHandler, ContextTypes

try:
    from PIL import Image
    _PIL_AVAILABLE = True
except ImportError:
    _PIL_AVAILABLE = False

# ------------------------------------------------------------------------------
#  GLOBALS (filled in by setup())
# ------------------------------------------------------------------------------
_conn: sqlite3.Connection = None
_cursor: sqlite3.Cursor = None
_is_admin = None  # injected is_admin(user_id) -> bool function from main bot

_MATCHES: dict[str, dict] = {}
_match_counter = itertools.count(1000)

MAX_SKIPS_PER_PLAYER = 2

# ------------------------------------------------------------------------------
#  SMALL CAPS STYLING  (used for static UI labels only — never for names)
# ------------------------------------------------------------------------------
_SC_MAP = {
    "a": "ᴀ", "b": "ʙ", "c": "ᴄ", "d": "ᴅ", "e": "ᴇ", "f": "ꜰ", "g": "ɢ",
    "h": "ʜ", "i": "ɪ", "j": "ᴊ", "k": "ᴋ", "l": "ʟ", "m": "ᴍ", "n": "ɴ",
    "o": "ᴏ", "p": "ᴘ", "q": "ǫ", "r": "ʀ", "s": "ꜱ", "t": "ᴛ", "u": "ᴜ",
    "v": "ᴠ", "w": "ᴡ", "x": "x", "y": "ʏ", "z": "ᴢ",
}


def sc(text: str) -> str:
    """Convert plain text to small-caps unicode for UI labels/headers."""
    return "".join(_SC_MAP.get(ch.lower(), ch) if ch.isalpha() else ch for ch in text)


# ------------------------------------------------------------------------------
#  ROLES / STATS / WEIGHTS  (tune freely — everything else derives from this)
# ------------------------------------------------------------------------------
ROLE_ORDER = [
    "Commander", "Vice Commander", "Tank", "Assassin",
    "Healer", "Support I", "Support II", "Hidden Agent",
]

ROLE_EMOJI = {
    "Commander":      "👑",
    "Vice Commander": "⚔️",
    "Tank":           "🛡",
    "Assassin":       "🗡",
    "Healer":         "❤️",
    "Support I":      "🎯",
    "Support II":     "🎯",
    "Hidden Agent":   "🕵",
}

ROLE_LABEL_SC = {role: sc(role) for role in ROLE_ORDER}

ROLE_TAGLINE = {
    "Commander":      "Main damage dealer",
    "Vice Commander": "Second strongest — rises if the Commander falls",
    "Tank":           "Shields the whole squad",
    "Assassin":       "Hunts the enemy's weakest link",
    "Healer":         "Keeps the squad alive",
    "Support I":      "Buffs squad offense",
    "Support II":     "Buffs squad defense",
    "Hidden Agent":   "Intel, sabotage & deception",
}

# stat keys: atk def spd iq sta dur lead heal sup
ROLE_WEIGHTS = {
    "Commander":      {"atk": 0.35, "iq": 0.25, "lead": 0.20, "def": 0.20},
    "Vice Commander": {"atk": 0.30, "iq": 0.20, "lead": 0.25, "def": 0.25},
    "Tank":           {"def": 0.40, "dur": 0.35, "sta": 0.25},
    "Assassin":       {"spd": 0.40, "atk": 0.35, "iq": 0.25},
    "Healer":         {"heal": 0.70, "sup": 0.30},
    "Support I":      {"sup": 0.50, "atk": 0.30, "iq": 0.20},
    "Support II":     {"sup": 0.50, "def": 0.30, "dur": 0.20},
    "Hidden Agent":   {"iq": 0.50, "sup": 0.30, "lead": 0.20},
}

UNIVERSE_EMOJI = {
    "Naruto": "🍥", "One Piece": "🏴‍☠️", "Bleach": "⚔", "Black Clover": "🍀",
    "Dragon Ball": "⚡", "Demon Slayer": "👹", "Jujutsu Kaisen": "🌀",
}

# Elemental counter cycle — attacker beats defender => +10% to attacker's role score
ELEMENT_COUNTERS = {
    "Fire": "Ice", "Lightning": "Earth", "Earth": "Water", "Water": "Fire",
}

SIDE_EMOJI = {"blue": "🔵", "red": "🔴"}


# ==============================================================================
#  DB SETUP  — schema is UNCHANGED, existing arena_characters rows preserved.
# ==============================================================================

def _init_db():
    _cursor.executescript("""
    CREATE TABLE IF NOT EXISTS arena_characters (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        anime TEXT NOT NULL,
        name TEXT NOT NULL,
        atk INTEGER, def INTEGER, spd INTEGER, iq INTEGER,
        sta INTEGER, dur INTEGER, lead INTEGER, heal INTEGER, sup INTEGER,
        skill_name TEXT, skill_effect TEXT, element TEXT DEFAULT 'None',
        loyalty INTEGER DEFAULT 100,
        file_id TEXT, added_by INTEGER, added_at INTEGER
    );

    CREATE TABLE IF NOT EXISTS arena_stats (
        user_id INTEGER PRIMARY KEY,
        battles INTEGER DEFAULT 0,
        wins INTEGER DEFAULT 0,
        losses INTEGER DEFAULT 0,
        mvp_count INTEGER DEFAULT 0,
        current_streak INTEGER DEFAULT 0,
        best_streak INTEGER DEFAULT 0
    );

    CREATE TABLE IF NOT EXISTS arena_anime_plays (
        user_id INTEGER NOT NULL,
        anime TEXT NOT NULL,
        plays INTEGER DEFAULT 0,
        PRIMARY KEY (user_id, anime)
    );

    CREATE TABLE IF NOT EXISTS arena_history (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user1_id INTEGER, user2_id INTEGER,
        anime TEXT, score1 INTEGER, score2 INTEGER,
        winner_id INTEGER, mvp_name TEXT, ts INTEGER
    );

    CREATE TABLE IF NOT EXISTS arena_banners (
        anime TEXT PRIMARY KEY,
        file_id TEXT NOT NULL,
        set_by INTEGER,
        set_at INTEGER
    );
    """)
    _conn.commit()


# ==============================================================================
#  CHARACTER HELPERS  (unchanged)
# ==============================================================================

def _row_to_char(row) -> dict:
    (cid, anime, name, atk, deff, spd, iq, sta, dur, lead, heal, sup,
     skill_name, skill_effect, element, loyalty, file_id, *_rest) = row
    return {
        "id": cid, "anime": anime, "name": name,
        "atk": atk, "def": deff, "spd": spd, "iq": iq, "sta": sta, "dur": dur,
        "lead": lead, "heal": heal, "sup": sup,
        "skill_name": skill_name or "—", "skill_effect": skill_effect or "",
        "element": element or "None", "loyalty": loyalty if loyalty is not None else 100,
        "file_id": file_id,
    }


def get_animes_with_pool(min_count: int = 8) -> list[str]:
    _cursor.execute("SELECT anime, COUNT(*) FROM arena_characters GROUP BY anime")
    return [a for a, c in _cursor.fetchall() if c >= min_count]


def get_characters_for_anime(anime: str) -> list[dict]:
    _cursor.execute(
        "SELECT id,anime,name,atk,def,spd,iq,sta,dur,lead,heal,sup,"
        "skill_name,skill_effect,element,loyalty,file_id FROM arena_characters "
        "WHERE anime=? COLLATE NOCASE ORDER BY id", (anime,)
    )
    return [_row_to_char(r) for r in _cursor.fetchall()]


def get_character_by_id(char_id: int) -> dict | None:
    _cursor.execute(
        "SELECT id,anime,name,atk,def,spd,iq,sta,dur,lead,heal,sup,"
        "skill_name,skill_effect,element,loyalty,file_id FROM arena_characters WHERE id=?",
        (char_id,)
    )
    row = _cursor.fetchone()
    return _row_to_char(row) if row else None


def _apply_skill(stats: dict, skill_effect: str) -> dict:
    """skill_effect like 'atk+15' -> boosts that stat by 15% for this battle only."""
    s = dict(stats)
    if skill_effect and "+" in skill_effect:
        try:
            key, pct = skill_effect.split("+")
            key = key.strip().lower()
            pct = float(pct.strip())
            if key in s and s[key] is not None:
                s[key] = s[key] * (1 + pct / 100.0)
        except Exception:
            pass
    return s


def _uni_emoji(anime: str) -> str:
    return UNIVERSE_EMOJI.get(anime, "🌌")


def get_custom_banner(anime: str) -> str | None:
    _cursor.execute("SELECT file_id FROM arena_banners WHERE anime=? COLLATE NOCASE", (anime,))
    row = _cursor.fetchone()
    return row[0] if row else None


def set_custom_banner(anime: str, file_id: str, admin_id: int):
    _cursor.execute(
        "INSERT INTO arena_banners (anime, file_id, set_by, set_at) VALUES (?,?,?,?) "
        "ON CONFLICT(anime) DO UPDATE SET file_id=excluded.file_id, set_by=excluded.set_by, set_at=excluded.set_at",
        (anime, file_id, admin_id, int(time.time()))
    )
    _conn.commit()


def _banner_file_id(anime: str) -> str | None:
    """A custom admin-set banner if one exists, otherwise the first character on file."""
    custom = get_custom_banner(anime)
    if custom:
        return custom
    pool = get_characters_for_anime(anime)
    return pool[0]["file_id"] if pool else None


# ==============================================================================
#  DETERMINISTIC BATTLE ENGINE  —  ZERO RNG  (unchanged)
# ==============================================================================

def _role_base_score(role: str, stats: dict) -> float:
    weights = ROLE_WEIGHTS[role]
    total = sum(stats.get(k, 0) * w for k, w in weights.items())
    return round(total * 100, 1)


def run_battle(squad_a: dict[str, dict], squad_b: dict[str, dict],
                name_a: str, name_b: str) -> dict:
    """
    squad_a / squad_b: {role: character_dict}
    Fully deterministic — identical inputs ALWAYS give identical output.
    """
    rounds = []
    grand_a, grand_b = 0.0, 0.0
    bonus_log_a, bonus_log_b = [], []

    boosted_a = {r: _apply_skill(c, c["skill_effect"]) for r, c in squad_a.items()}
    boosted_b = {r: _apply_skill(c, c["skill_effect"]) for r, c in squad_b.items()}

    commander_a_won = None
    commander_b_won = None
    role_scores_a, role_scores_b = {}, {}

    for role in ROLE_ORDER:
        ca, cb = squad_a[role], squad_b[role]
        sa, sb = boosted_a[role], boosted_b[role]

        score_a = _role_base_score(role, sa)
        score_b = _role_base_score(role, sb)
        note_a, note_b = [], []

        if ELEMENT_COUNTERS.get(ca["element"]) == cb["element"]:
            score_a *= 1.10
            note_a.append(f"🌀 {ca['element']} counters {cb['element']} (+10%)")
        if ELEMENT_COUNTERS.get(cb["element"]) == ca["element"]:
            score_b *= 1.10
            note_b.append(f"🌀 {cb['element']} counters {ca['element']} (+10%)")

        if role == "Vice Commander":
            if commander_a_won is False:
                score_a *= 1.15
                note_a.append("💢 Morale surge (+15%, Commander fell)")
            if commander_b_won is False:
                score_b *= 1.15
                note_b.append("💢 Morale surge (+15%, Commander fell)")

        if role == "Assassin":
            if sa["spd"] - squad_b["Tank"]["spd"] >= 15:
                score_a *= 1.08
                note_a.append("⚡ Speed overwhelms enemy Tank (+8%)")
            if sb["spd"] - squad_a["Tank"]["spd"] >= 15:
                score_b *= 1.08
                note_b.append("⚡ Speed overwhelms enemy Tank (+8%)")

        role_scores_a[role] = score_a
        role_scores_b[role] = score_b

        if role == "Commander":
            commander_a_won = score_a > score_b
            commander_b_won = score_b > score_a

        winner = name_a if score_a > score_b else (name_b if score_b > score_a else "Draw")
        rounds.append({
            "role": role, "char_a": ca, "char_b": cb,
            "score_a": score_a, "score_b": score_b,
            "winner": winner, "notes": note_a + note_b,
        })

        grand_a += score_a
        grand_b += score_b

    healer_a, healer_b = boosted_a["Healer"], boosted_b["Healer"]
    grand_a += healer_a["heal"] * 0.2
    grand_b += healer_b["heal"] * 0.2

    sup1_a, sup1_b = boosted_a["Support I"], boosted_b["Support I"]
    grand_a += sup1_a["sup"] * 0.15
    grand_b += sup1_b["sup"] * 0.15
    if sup1_a["sup"] * 0.15 > 0:
        bonus_log_a.append(f"🎯 {squad_a['Support I']['name']}'s offense buff (+{sup1_a['sup']*0.15:.1f})")
    if sup1_b["sup"] * 0.15 > 0:
        bonus_log_b.append(f"🎯 {squad_b['Support I']['name']}'s offense buff (+{sup1_b['sup']*0.15:.1f})")

    sup2_a, sup2_b = boosted_a["Support II"], boosted_b["Support II"]
    grand_a += sup2_a["def"] * 0.15
    grand_b += sup2_b["def"] * 0.15
    if sup2_a["def"] * 0.15 > 0:
        bonus_log_a.append(f"🎯 {squad_a['Support II']['name']}'s defense buff (+{sup2_a['def']*0.15:.1f})")
    if sup2_b["def"] * 0.15 > 0:
        bonus_log_b.append(f"🎯 {squad_b['Support II']['name']}'s defense buff (+{sup2_b['def']*0.15:.1f})")

    tank_a, tank_b = squad_a["Tank"], squad_b["Tank"]
    reduction_from_a = tank_a["def"] * 0.10
    reduction_from_b = tank_b["def"] * 0.10
    grand_b -= reduction_from_a
    grand_a -= reduction_from_b
    bonus_log_a.append(f"🛡 {tank_a['name']} reduced enemy score by {reduction_from_a:.1f}")
    bonus_log_b.append(f"🛡 {tank_b['name']} reduced enemy score by {reduction_from_b:.1f}")

    agent_a, agent_b = squad_a["Hidden Agent"], squad_b["Hidden Agent"]
    if agent_a["loyalty"] < 70:
        sab = (100 - agent_a["loyalty"]) * 0.5
        grand_b -= sab
        bonus_log_a.append(f"🕵 {agent_a['name']} sabotaged the enemy (-{sab:.1f} to them, loyalty {agent_a['loyalty']})")
    else:
        intel = agent_a["loyalty"] * 0.1
        grand_a += intel
        bonus_log_a.append(f"🕵 {agent_a['name']}'s loyal intel (+{intel:.1f})")

    if agent_b["loyalty"] < 70:
        sab = (100 - agent_b["loyalty"]) * 0.5
        grand_a -= sab
        bonus_log_b.append(f"🕵 {agent_b['name']} sabotaged the enemy (-{sab:.1f} to them, loyalty {agent_b['loyalty']})")
    else:
        intel = agent_b["loyalty"] * 0.1
        grand_b += intel
        bonus_log_b.append(f"🕵 {agent_b['name']}'s loyal intel (+{intel:.1f})")

    grand_a = round(grand_a, 1)
    grand_b = round(grand_b, 1)

    winning_scores = role_scores_a if grand_a >= grand_b else role_scores_b
    winning_squad = squad_a if grand_a >= grand_b else squad_b
    mvp_role = max(winning_scores, key=winning_scores.get)
    mvp = winning_squad[mvp_role]

    best_defender = squad_a["Tank"] if role_scores_a["Tank"] >= role_scores_b["Tank"] else squad_b["Tank"]

    all_support_scores = {
        ("A", "Support I"): role_scores_a["Support I"], ("A", "Support II"): role_scores_a["Support II"],
        ("B", "Support I"): role_scores_b["Support I"], ("B", "Support II"): role_scores_b["Support II"],
    }
    best_key = max(all_support_scores, key=all_support_scores.get)
    best_support = (squad_a if best_key[0] == "A" else squad_b)[best_key[1]]

    top_attacker_side = squad_a if role_scores_a["Commander"] >= role_scores_b["Commander"] else squad_b
    highest_damage = top_attacker_side["Commander"]

    if grand_a > grand_b:
        winner_name = name_a
    elif grand_b > grand_a:
        winner_name = name_b
    else:
        winner_name = "Draw"

    return {
        "rounds": rounds, "grand_a": grand_a, "grand_b": grand_b,
        "winner": winner_name, "mvp": mvp,
        "highest_damage": highest_damage, "best_defender": best_defender,
        "best_support": best_support,
        "bonus_log_a": bonus_log_a, "bonus_log_b": bonus_log_b,
    }


def _build_ai_squad(anime: str) -> dict[str, dict] | None:
    """Deterministic AI squad: for each role, the best unused fit by raw role-score."""
    pool = get_characters_for_anime(anime)
    if len(pool) < 8:
        return None
    used = set()
    squad = {}
    for role in ROLE_ORDER:
        best, best_score = None, -1
        for c in pool:
            if c["id"] in used:
                continue
            score = _role_base_score(role, c)
            if score > best_score or (score == best_score and (best is None or c["id"] < best["id"])):
                best, best_score = c, score
        squad[role] = best
        used.add(best["id"])
    return squad


# ==============================================================================
#  STATS / LEADERBOARD PERSISTENCE  (unchanged)
# ==============================================================================

def _ensure_stats_row(user_id: int):
    _cursor.execute("INSERT OR IGNORE INTO arena_stats (user_id) VALUES (?)", (user_id,))


def record_battle(user_a: int, user_b: int | None, anime: str,
                   score_a: float, score_b: float, mvp_name: str):
    ts = int(time.time())
    winner_id = None
    if score_a > score_b:
        winner_id = user_a
    elif user_b and score_b > score_a:
        winner_id = user_b

    _cursor.execute(
        "INSERT INTO arena_history (user1_id,user2_id,anime,score1,score2,winner_id,mvp_name,ts) "
        "VALUES (?,?,?,?,?,?,?,?)",
        (user_a, user_b, anime, score_a, score_b, winner_id, mvp_name, ts)
    )

    for uid, participated in ((user_a, True), (user_b, bool(user_b))):
        if not participated or not uid:
            continue
        _ensure_stats_row(uid)
        _cursor.execute("SELECT battles,wins,losses,current_streak,best_streak FROM arena_stats WHERE user_id=?", (uid,))
        battles, wins, losses, streak, best = _cursor.fetchone()
        battles += 1
        if winner_id == uid:
            wins += 1
            streak += 1
            best = max(best, streak)
        elif winner_id is None:
            pass
        else:
            losses += 1
            streak = 0
        _cursor.execute(
            "UPDATE arena_stats SET battles=?,wins=?,losses=?,current_streak=?,best_streak=? WHERE user_id=?",
            (battles, wins, losses, streak, best, uid)
        )
        _cursor.execute("INSERT OR IGNORE INTO arena_anime_plays (user_id,anime,plays) VALUES (?,?,0)", (uid, anime))
        _cursor.execute("UPDATE arena_anime_plays SET plays=plays+1 WHERE user_id=? AND anime=?", (uid, anime))

    _conn.commit()


def bump_mvp(user_id: int):
    _ensure_stats_row(user_id)
    _cursor.execute("UPDATE arena_stats SET mvp_count=mvp_count+1 WHERE user_id=?", (user_id,))
    _conn.commit()


def get_stats(user_id: int) -> dict:
    _cursor.execute("SELECT battles,wins,losses,mvp_count,current_streak,best_streak FROM arena_stats WHERE user_id=?", (user_id,))
    row = _cursor.fetchone()
    if not row:
        return {"battles": 0, "wins": 0, "losses": 0, "mvp_count": 0, "current_streak": 0, "best_streak": 0, "favorite_anime": "—"}
    battles, wins, losses, mvp_count, streak, best = row
    _cursor.execute("SELECT anime FROM arena_anime_plays WHERE user_id=? ORDER BY plays DESC LIMIT 1", (user_id,))
    fav = _cursor.fetchone()
    return {
        "battles": battles, "wins": wins, "losses": losses, "mvp_count": mvp_count,
        "current_streak": streak, "best_streak": best,
        "favorite_anime": fav[0] if fav else "—",
    }


def get_top_players(limit: int = 10) -> list[tuple]:
    _cursor.execute(
        "SELECT user_id, wins, battles, "
        "CASE WHEN battles>0 THEN ROUND(100.0*wins/battles,1) ELSE 0 END as wr "
        "FROM arena_stats WHERE battles>0 ORDER BY wins DESC, wr DESC LIMIT ?", (limit,)
    )
    return _cursor.fetchall()


def get_top_mvp(limit: int = 10) -> list[tuple]:
    _cursor.execute("SELECT user_id, mvp_count FROM arena_stats WHERE mvp_count>0 ORDER BY mvp_count DESC LIMIT ?", (limit,))
    return _cursor.fetchall()


def get_top_anime(limit: int = 10) -> list[tuple]:
    _cursor.execute("SELECT anime, SUM(plays) as p FROM arena_anime_plays GROUP BY anime ORDER BY p DESC LIMIT ?", (limit,))
    return _cursor.fetchall()


# ==============================================================================
#  LIVE MATCH STATE + SINGLE-MESSAGE RENDERING
# ==============================================================================

def _new_match_id() -> str:
    return str(next(_match_counter))


def _other(side: str) -> str:
    return "red" if side == "blue" else "blue"


def _team_name(m: dict, side: str) -> str:
    return m["blue_name"] if side == "blue" else m["red_name"]


def _team_uid(m: dict, side: str) -> int | None:
    return m["blue_id"] if side == "blue" else m["red_id"]


def _render_board(m: dict) -> str:
    """
    The full draft board as a plain TEXT message — both squads, a blank-line
    gap between them, role labels in small caps. Plain text messages allow
    up to 4096 chars so there's no need for a compact fallback here.
    """
    header = f"⚔️ <b>{sc('Arena Draft')}</b> — {_uni_emoji(m['anime'])} {m['anime']}"
    sep = "━━━━━━━━━━━━━━━━━━━━"

    def team_block(side):
        lines = [f"{SIDE_EMOJI[side]} <b>{_team_name(m, side)}'s {sc('Squad')}</b>"]
        for role in ROLE_ORDER:
            c = m["squads"][side].get(role)
            lines.append(f"{ROLE_EMOJI[role]} {ROLE_LABEL_SC[role]}: {c['name'] if c else '· · ·'}")
        return "\n".join(lines)

    return "\n".join([header, sep, team_block("blue"), "", team_block("red"), sep])


def _scout_button_label(m: dict, side: str) -> str:
    name = _team_name(m, side)
    if len(name) > 20:
        name = name[:19] + "…"
    return f"🎲 {name}'s {sc('Turn')} — {sc('Scout')}"


def _draft_progress(m: dict) -> str:
    b = len(m["squads"]["blue"])
    r = len(m["squads"]["red"])
    return f"📋 {sc('Progress')}: 🔵 {b}/8   🔴 {r}/8"


def _candidate_caption(candidate: dict, team_name: str) -> str:
    return (
        f"✨ <b>{team_name}</b> {sc('scouted')}:\n"
        f"🎴 <b>{candidate['name']}</b>\n"
        f"⭐ {candidate['skill_name']}   🌀 {candidate['element']}"
    )


# Team-colored accent bar composited onto the LEFT edge of a scouted
# character's image, mimicking a trading-card frame. Falls back to the
# plain unframed image if Pillow isn't installed.
_BAR_WIDTH = 20
_BAR_COLOR = {"blue": (59, 130, 246), "red": (239, 68, 68)}


async def _framed_card_bytes(context, file_id: str, side: str):
    """Download a character's photo and add a colored side-bar. Returns a
    BytesIO ready to send, or None if framing isn't possible (caller should
    fall back to sending the plain file_id in that case)."""
    if not _PIL_AVAILABLE:
        return None
    try:
        tg_file = await context.bot.get_file(file_id)
        raw = BytesIO()
        await tg_file.download_to_memory(out=raw)
        raw.seek(0)
        img = Image.open(raw).convert("RGB")
        color = _BAR_COLOR.get(side, (124, 77, 255))
        framed = Image.new("RGB", (img.width + _BAR_WIDTH, img.height), color)
        framed.paste(img, (_BAR_WIDTH, 0))
        out = BytesIO()
        framed.save(out, format="JPEG", quality=92)
        out.seek(0)
        out.name = "card.jpg"
        return out
    except Exception:
        return None


async def _update_status(context, m: dict, footer: str, markup=None):
    """Edit the persistent TEXT status message (full board + whatever's happening)."""
    tm = m.get("text_msg")
    if not tm:
        return
    text = _render_board(m) + "\n" + footer
    try:
        await context.bot.edit_message_text(
            chat_id=tm["chat_id"], message_id=tm["message_id"], text=text,
            parse_mode="HTML", reply_markup=markup
        )
    except Exception:
        pass


async def _clear_status_buttons(context, m: dict):
    tm = m.get("text_msg")
    if not tm:
        return
    try:
        await context.bot.edit_message_reply_markup(chat_id=tm["chat_id"], message_id=tm["message_id"], reply_markup=None)
    except Exception:
        pass


async def _show_candidate_photo(context, m: dict, side: str, candidate: dict, markup):
    """
    Show the scouted candidate's (framed) image with role/skip buttons. If a
    decision-photo is already open for this match, edit it in place (used by
    Skip); otherwise send a brand-new message — this is the ONE moment in
    the whole match where a fresh message appears, exactly when a candidate
    needs deciding on, per design.
    """
    caption = _candidate_caption(candidate, _team_name(m, side))
    framed = await _framed_card_bytes(context, candidate["file_id"], side)
    photo_arg = framed if framed else candidate["file_id"]

    pm = m.get("photo_msg")
    if pm:
        try:
            await context.bot.edit_message_media(
                chat_id=pm["chat_id"], message_id=pm["message_id"],
                media=InputMediaPhoto(media=photo_arg, caption=caption, parse_mode="HTML"),
                reply_markup=markup
            )
            return
        except Exception:
            pass  # fall through and send fresh if the edit failed

    msg = await context.bot.send_photo(
        chat_id=m["chat_id"], photo=photo_arg, caption=caption,
        parse_mode="HTML", reply_markup=markup
    )
    m["photo_msg"] = {"chat_id": m["chat_id"], "message_id": msg.message_id}


async def _clear_candidate_photo(context, m: dict):
    """Remove the decision photo entirely once a role has been chosen — no
    image lingers once you're back to waiting/board states."""
    pm = m.get("photo_msg")
    if not pm:
        return
    try:
        await context.bot.delete_message(chat_id=pm["chat_id"], message_id=pm["message_id"])
    except Exception:
        pass
    m["photo_msg"] = None




# ==============================================================================
#  BATTLE REPORT  (separate message — too long for a photo caption)
# ==============================================================================

async def _run_and_report_battle(context, match_id: str):
    m = _MATCHES.get(match_id)
    if not m:
        return
    result = run_battle(m["squads"]["blue"], m["squads"]["red"], m["blue_name"], m["red_name"])
    await _send_battle_report(context, m["chat_id"], m["anime"], m["blue_name"], m["red_name"], result)

    record_battle(m["blue_id"], m["red_id"], m["anime"], result["grand_a"], result["grand_b"], result["mvp"]["name"])
    mvp_owner = None
    if result["winner"] == m["blue_name"]:
        mvp_owner = m["blue_id"]
    elif result["winner"] == m["red_name"]:
        mvp_owner = m["red_id"]
    if mvp_owner:
        bump_mvp(mvp_owner)

    _MATCHES.pop(match_id, None)


async def _send_battle_report(context, chat_id, anime, name_a, name_b, result):
    lines = [
        f"⚔️ <b>{sc('Arena Battle')}</b> ⚔️",
        f"{_uni_emoji(anime)} <b>{anime}</b>",
        f"🔵 {name_a}   <b>{sc('vs')}</b>   🔴 {name_b}",
        "━━━━━━━━━━━━━━━━━━━━",
    ]
    for r in result["rounds"]:
        e = ROLE_EMOJI[r["role"]]
        lines.append(f"{e} <b>{ROLE_LABEL_SC[r['role']]}</b>")
        lines.append(f"🔵 {r['char_a']['name']} — {r['score_a']:.0f}")
        lines.append(f"🔴 {r['char_b']['name']} — {r['score_b']:.0f}")
        if r["notes"]:
            lines.append("   " + " | ".join(r["notes"]))
        lines.append(f"🏆 {r['winner']} {sc('wins this round')}")
        lines.append("━━━━━━━━━━━━━━━━━━━━")

    lines.append(f"🏆 <b>{sc('Battle Complete')}</b>")
    lines.append(f"🔵 {name_a} {sc('Squad')} — <b>{result['grand_a']:.0f} pts</b>")
    lines.append(f"🔴 {name_b} {sc('Squad')} — <b>{result['grand_b']:.0f} pts</b>")
    lines.append("━━━━━━━━━━━━━━━━━━━━")
    lines.append(f"⭐ {sc('MVP')}: <b>{result['mvp']['name']}</b>")
    lines.append(f"💥 {sc('Highest Damage')}: {result['highest_damage']['name']}")
    lines.append(f"🛡 {sc('Best Defender')}: {result['best_defender']['name']}")
    lines.append(f"🎯 {sc('Best Support')}: {result['best_support']['name']}")
    lines.append("━━━━━━━━━━━━━━━━━━━━")
    if result["winner"] == "Draw":
        lines.append(f"🤝 <b>{sc('Result: Draw')}</b>")
    else:
        lines.append(f"🥇 <b>{sc('Winner')}: {result['winner']}</b>")

    text = "\n".join(lines)
    if len(text) <= 4000:
        await context.bot.send_message(chat_id, text, parse_mode="HTML")
    else:
        mid = len(lines) // 2
        await context.bot.send_message(chat_id, "\n".join(lines[:mid]), parse_mode="HTML")
        await context.bot.send_message(chat_id, "\n".join(lines[mid:]), parse_mode="HTML")


# ==============================================================================
#  COMMAND: /arena  — open a live PvP challenge in this chat
# ==============================================================================

async def cmd_arena(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.effective_user or not update.message:
        return
    animes = get_animes_with_pool(min_count=8)
    if not animes:
        await update.message.reply_text(
            f"⚔️ <b>{sc('Arena Wars')}</b>\n\nNo universe has 8+ characters loaded yet. "
            "Ask an admin to add some with <code>/addarenachar</code>.",
            parse_mode="HTML"
        )
        return
    buttons, row = [], []
    for a in animes:
        row.append(InlineKeyboardButton(f"{_uni_emoji(a)} {a}", callback_data=f"aru:{a}"))
        if len(row) == 2:
            buttons.append(row); row = []
    if row:
        buttons.append(row)

    text = (
        f"⚔️ <b>{sc('Arena Wars')}</b> ⚔️\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"🌍 <b>{sc('Select a Universe')}</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        "Draft an 8-role squad live against another player. Fully "
        "<b>deterministic</b> combat — no luck, no RNG, only stats and skill.\n\n"
        "👇 Pick the anime for this challenge:"
    )
    await update.message.reply_text(text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(buttons))


async def cb_open_challenge(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    anime = query.data.split(":", 1)[1]
    user = update.effective_user

    match_id = _new_match_id()
    _MATCHES[match_id] = {
        "anime": anime, "chat_id": update.effective_chat.id,
        "blue_id": user.id, "blue_name": user.first_name or "Player 1",
        "red_id": None, "red_name": None,
        "squads": {"blue": {}, "red": {}},
        "used_ids": {"blue": set(), "red": set()},
        "rejected": {"blue": set(), "red": set()},
        "skips": {"blue": 0, "red": 0},
        "candidate": None, "turn": "blue", "mode": "pvp",
        "text_msg": None, "photo_msg": None,
    }
    m = _MATCHES[match_id]

    status_text = (
        f"⚔️ <b>{sc('Arena Challenge Opened')}</b>\n"
        f"{_uni_emoji(anime)} Universe: <b>{anime}</b>\n"
        f"🔵 Challenger: <b>{user.first_name}</b>\n\n"
        f"Tap <b>Accept</b> below to become the 🔴 rival and draft against them!"
    )
    kb = [[
        InlineKeyboardButton("✅ Accept Challenge", callback_data=f"arjoin:{match_id}"),
        InlineKeyboardButton("❌ Cancel", callback_data=f"arcancel:{match_id}"),
    ]]
    status_msg = await context.bot.send_message(
        chat_id=update.effective_chat.id, text=status_text, parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(kb)
    )
    m["text_msg"] = {"chat_id": update.effective_chat.id, "message_id": status_msg.message_id}

    try:
        await query.edit_message_text("✅ Challenge posted below! ⬇️")
    except Exception:
        pass


async def cb_cancel_challenge(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    match_id = query.data.split(":", 1)[1]
    m = _MATCHES.get(match_id)
    if not m:
        await query.answer("This challenge no longer exists.", show_alert=True)
        return
    if update.effective_user.id != m["blue_id"]:
        await query.answer("Only the challenger can cancel this.", show_alert=True)
        return
    if m["red_id"] is not None:
        await query.answer("This challenge has already been accepted.", show_alert=True)
        return
    _MATCHES.pop(match_id, None)
    await query.answer("Challenge cancelled.")
    try:
        await query.edit_message_text("❌ <b>Arena challenge cancelled.</b>", parse_mode="HTML")
    except Exception:
        pass


async def cb_accept_challenge(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    match_id = query.data.split(":", 1)[1]
    m = _MATCHES.get(match_id)
    if not m:
        await query.answer("This challenge no longer exists.", show_alert=True)
        return
    if m["red_id"] is not None:
        await query.answer("Someone already accepted this challenge!", show_alert=True)
        return
    user = update.effective_user
    if user.id == m["blue_id"]:
        await query.answer("You can't accept your own challenge!", show_alert=True)
        return

    m["red_id"] = user.id
    m["red_name"] = user.first_name or "Player 2"
    await query.answer("Challenge accepted!")

    kb = [[InlineKeyboardButton(_scout_button_label(m, "blue"), callback_data=f"ard:{match_id}")]]
    await _update_status(context, m, f"{_draft_progress(m)}\n{SIDE_EMOJI['blue']} <b>{m['blue_name']}'s {sc('Turn')}</b>", InlineKeyboardMarkup(kb))


# ==============================================================================
#  COMMAND: /arena_ai  — instant solo draft vs the AI Champion
# ==============================================================================

async def cmd_arena_ai(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.effective_user or not update.message:
        return
    animes = get_animes_with_pool(min_count=8)
    if not animes:
        await update.message.reply_text("No universe has 8+ characters loaded yet.")
        return
    buttons, row = [], []
    for a in animes:
        row.append(InlineKeyboardButton(f"{_uni_emoji(a)} {a}", callback_data=f"arx:{a}"))
        if len(row) == 2:
            buttons.append(row); row = []
    if row:
        buttons.append(row)
    await update.message.reply_text(
        f"🤖 <b>{sc('Practice Mode')}</b>\nPick a universe to draft against the Arena Champion (AI):",
        parse_mode="HTML", reply_markup=InlineKeyboardMarkup(buttons)
    )


async def cb_start_ai(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    anime = query.data.split(":", 1)[1]
    user = update.effective_user

    ai_squad = _build_ai_squad(anime)
    if not ai_squad:
        try:
            await query.edit_message_text("⚠️ Not enough characters in this universe to build an AI squad.")
        except Exception:
            pass
        return

    match_id = _new_match_id()
    _MATCHES[match_id] = {
        "anime": anime, "chat_id": update.effective_chat.id,
        "blue_id": user.id, "blue_name": user.first_name or "Player",
        "red_id": None, "red_name": "🤖 Arena Champion",
        "squads": {"blue": {}, "red": ai_squad},
        "used_ids": {"blue": set(), "red": {c["id"] for c in ai_squad.values()}},
        "rejected": {"blue": set(), "red": set()},
        "skips": {"blue": 0, "red": 0},
        "candidate": None, "turn": "blue", "mode": "ai",
        "text_msg": None, "photo_msg": None,
    }
    m = _MATCHES[match_id]

    kb = [[InlineKeyboardButton(_scout_button_label(m, "blue"), callback_data=f"ard:{match_id}")]]
    status_text = _render_board(m) + "\n" + f"{_draft_progress(m)}\n🔵 <b>{m['blue_name']}'s {sc('Turn')}</b>"
    status_msg = await context.bot.send_message(
        chat_id=update.effective_chat.id, text=status_text, parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(kb)
    )
    m["text_msg"] = {"chat_id": update.effective_chat.id, "message_id": status_msg.message_id}

    try:
        await query.edit_message_text(f"🤖 Practice draft started in {_uni_emoji(anime)} {anime}! ⬇️")
    except Exception:
        pass


# ==============================================================================
#  TURN ACTIONS: Scout / Skip / Assign / Ready
#  No image is shown at all until a player actually taps Scout — the status
#  message stays plain text throughout waiting/board/ready states. The
#  moment a candidate is scouted, a fresh photo (with a team-colored card
#  border) appears with role/skip buttons; the instant a role is chosen,
#  that photo is deleted and control returns to the plain text status.
# ==============================================================================

def _draw_candidate(m: dict, side: str) -> dict | None:
    pool = [
        c for c in get_characters_for_anime(m["anime"])
        if c["id"] not in m["used_ids"][side] and c["id"] not in m["rejected"][side]
    ]
    if not pool:
        m["rejected"][side] = set()
        pool = [c for c in get_characters_for_anime(m["anime"]) if c["id"] not in m["used_ids"][side]]
    if not pool:
        return None
    return random.choice(pool)


def _role_and_skip_buttons(m: dict, match_id: str, side: str) -> InlineKeyboardMarkup:
    remaining_roles = [r for r in ROLE_ORDER if r not in m["squads"][side]]
    kb_rows, row = [], []
    for role in remaining_roles:
        row.append(InlineKeyboardButton(
            f"{ROLE_EMOJI[role]} {role}",
            callback_data=f"arc:{match_id}:{ROLE_ORDER.index(role)}"
        ))
        if len(row) == 2:
            kb_rows.append(row); row = []
    if row:
        kb_rows.append(row)
    skips_left = MAX_SKIPS_PER_PLAYER - m["skips"][side]
    if skips_left > 0:
        kb_rows.append([InlineKeyboardButton(f"⏭ {sc('Skip')} ({skips_left} left)", callback_data=f"ars:{match_id}")])
    return InlineKeyboardMarkup(kb_rows)


async def cb_scout(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    match_id = query.data.split(":", 1)[1]
    m = _MATCHES.get(match_id)
    if not m:
        await query.answer("This draft has expired.", show_alert=True)
        return
    side = m["turn"]
    if update.effective_user.id != _team_uid(m, side):
        await query.answer("It's not your turn!", show_alert=True)
        return

    candidate = _draw_candidate(m, side)
    if not candidate:
        await query.answer("No characters left in this universe's pool!", show_alert=True)
        return
    m["candidate"] = candidate
    await query.answer("🎲 Scouting...")

    await _clear_status_buttons(context, m)
    markup = _role_and_skip_buttons(m, match_id, side)
    await _show_candidate_photo(context, m, side, candidate, markup)


async def cb_skip(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    match_id = query.data.split(":", 1)[1]
    m = _MATCHES.get(match_id)
    if not m:
        await query.answer("This draft has expired.", show_alert=True)
        return
    side = m["turn"]
    if update.effective_user.id != _team_uid(m, side):
        await query.answer("It's not your turn!", show_alert=True)
        return
    if not m["candidate"]:
        await query.answer("Nothing to skip right now.", show_alert=True)
        return
    if m["skips"][side] >= MAX_SKIPS_PER_PLAYER:
        await query.answer("No skips left!", show_alert=True)
        return

    m["skips"][side] += 1
    m["rejected"][side].add(m["candidate"]["id"])
    m["candidate"] = None
    await query.answer("⏭ Skipped!")

    candidate = _draw_candidate(m, side)
    if not candidate:
        await query.answer("No more characters left!", show_alert=True)
        return
    m["candidate"] = candidate
    markup = _role_and_skip_buttons(m, match_id, side)
    await _show_candidate_photo(context, m, side, candidate, markup)


async def cb_assign_role(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    _, match_id, role_idx = query.data.split(":")
    m = _MATCHES.get(match_id)
    if not m:
        await query.answer("This draft has expired.", show_alert=True)
        return
    side = m["turn"]
    if update.effective_user.id != _team_uid(m, side):
        await query.answer("It's not your turn!", show_alert=True)
        return
    if not m["candidate"]:
        await query.answer("Scout a candidate first.", show_alert=True)
        return

    role = ROLE_ORDER[int(role_idx)]
    if role in m["squads"][side]:
        await query.answer("That role is already filled.", show_alert=True)
        return

    char = m["candidate"]
    m["squads"][side][role] = char
    m["used_ids"][side].add(char["id"])
    m["candidate"] = None
    await query.answer(f"{char['name']} locked in as {role}!")

    # The decision is made — the image's job is done, remove it immediately.
    await _clear_candidate_photo(context, m)

    both_complete = len(m["squads"]["blue"]) >= 8 and len(m["squads"]["red"]) >= 8

    if m["mode"] == "ai" and len(m["squads"]["blue"]) >= 8:
        await _update_status(context, m, f"{_draft_progress(m)}\n⚔️ {sc('Both squads ready — battle begins!')}")
        await _run_and_report_battle(context, match_id)
        return

    if both_complete:
        kb = [[
            InlineKeyboardButton(f"✅ {m['blue_name']} {sc('Ready')}", callback_data=f"arr:{match_id}:blue"),
            InlineKeyboardButton(f"✅ {m['red_name']} {sc('Ready')}", callback_data=f"arr:{match_id}:red"),
        ]]
        await _update_status(
            context, m,
            f"✅ {sc('Both squads are fully drafted!')}\n"
            f"{sc('Tap your Ready button when set — battle starts once BOTH are ready')}.",
            InlineKeyboardMarkup(kb)
        )
        return

    other = _other(side)
    next_side = other if len(m["squads"][other]) < 8 else side
    m["turn"] = next_side
    kb = [[InlineKeyboardButton(_scout_button_label(m, next_side), callback_data=f"ard:{match_id}")]]
    await _update_status(context, m, f"{_draft_progress(m)}\n{SIDE_EMOJI[next_side]} <b>{_team_name(m, next_side)}'s {sc('Turn')}</b>", InlineKeyboardMarkup(kb))


async def cb_ready(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    _, match_id, side = query.data.split(":")
    m = _MATCHES.get(match_id)
    if not m:
        await query.answer("This draft has expired.", show_alert=True)
        return
    if update.effective_user.id != _team_uid(m, side):
        await query.answer("This isn't your Ready button!", show_alert=True)
        return

    m.setdefault("ready", {"blue": False, "red": False})
    m["ready"][side] = True
    await query.answer("✅ Ready!")

    if m["ready"]["blue"] and m["ready"]["red"]:
        await _update_status(context, m, f"✅ {sc('Both players ready — battle starting!')}")
        await _run_and_report_battle(context, match_id)
    else:
        waiting_side = "red" if side == "blue" else "blue"
        kb = [[InlineKeyboardButton(f"✅ {_team_name(m, waiting_side)} {sc('Ready')}", callback_data=f"arr:{match_id}:{waiting_side}")]]
        await _update_status(
            context, m, f"✅ {_team_name(m, side)} is ready! {sc('Waiting on')} {_team_name(m, waiting_side)}...",
            InlineKeyboardMarkup(kb)
        )


# ==============================================================================
#  COMMAND: /arena_cancel — clean up a stuck challenge/draft you're part of
# ==============================================================================

async def cmd_arena_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.effective_user or not update.message:
        return
    uid = update.effective_user.id
    to_remove = [mid for mid, m in _MATCHES.items() if m["blue_id"] == uid or m["red_id"] == uid]
    if not to_remove:
        await update.message.reply_text("You have no active Arena draft or challenge.")
        return
    for mid in to_remove:
        _MATCHES.pop(mid, None)
    await update.message.reply_text("🗑 Your active Arena draft/challenge has been cancelled.")


# ==============================================================================
#  COMMAND: /arena_stats   /arena_top
# ==============================================================================

async def cmd_arena_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.effective_user or not update.message:
        return
    s = get_stats(update.effective_user.id)
    wr = round(100 * s["wins"] / s["battles"], 1) if s["battles"] else 0.0
    text = (
        f"📊 <b>{sc('Your Arena Stats')}</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"⚔️ {sc('Battles')}: <b>{s['battles']}</b>\n"
        f"🏆 {sc('Wins')}: <b>{s['wins']}</b>\n"
        f"💀 {sc('Losses')}: <b>{s['losses']}</b>\n"
        f"📈 {sc('Win Rate')}: <b>{wr}%</b>\n"
        f"🔥 {sc('Current Streak')}: <b>{s['current_streak']}</b>\n"
        f"⭐ {sc('Best Streak')}: <b>{s['best_streak']}</b>\n"
        f"👑 {sc('MVP Count')}: <b>{s['mvp_count']}</b>\n"
        f"🎭 {sc('Favorite Universe')}: <b>{s['favorite_anime']}</b>"
    )
    await update.message.reply_text(text, parse_mode="HTML")


async def cmd_arena_top(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message:
        return
    top_players = get_top_players(10)
    top_mvp = get_top_mvp(5)
    top_anime = get_top_anime(5)

    lines = [f"🏆 <b>{sc('Arena Leaderboard')}</b>", "━━━━━━━━━━━━━━━━━━━━", f"🥇 <b>{sc('Top Players (by wins)')}</b>"]
    if top_players:
        for i, (uid, wins, battles, wr) in enumerate(top_players, 1):
            lines.append(f"{i}. ID {uid} — {wins}W / {battles}B ({wr}%)")
    else:
        lines.append("No battles fought yet.")

    lines.append(f"\n👑 <b>{sc('Top MVPs')}</b>")
    if top_mvp:
        for i, (uid, mvp_count) in enumerate(top_mvp, 1):
            lines.append(f"{i}. ID {uid} — {mvp_count} MVPs")
    else:
        lines.append("No MVPs recorded yet.")

    lines.append(f"\n🌍 <b>{sc('Most Played Universes')}</b>")
    if top_anime:
        for i, (anime, plays) in enumerate(top_anime, 1):
            lines.append(f"{i}. {_uni_emoji(anime)} {anime} — {plays} battles")
    else:
        lines.append("No data yet.")

    await update.message.reply_text("\n".join(lines), parse_mode="HTML")


# ==============================================================================
#  ADMIN: /addarenachar  /delarenachar  /arena_list   (unchanged)
# ==============================================================================

ADDARENACHAR_HELP = (
    "🛠 <b>Add Arena Character</b>\n"
    "Reply to a <u>photo</u> of the character with:\n\n"
    "<code>/addarenachar Anime; Name; ATK DEF SPD IQ STA DUR LEAD HEAL SUP; "
    "SkillName; statcode+percent; Element; Loyalty</code>\n\n"
    "Only <b>Anime</b>, <b>Name</b> and the 9 stats are required — the rest "
    "are optional (defaults: no skill, Element=None, Loyalty=100).\n\n"
    "Stat codes for the skill bonus: atk def spd iq sta dur lead heal sup\n"
    "Elements (for counters): Fire, Water, Earth, Lightning, Ice, None\n\n"
    "<b>Example (full):</b>\n"
    "<code>/addarenachar Naruto; Itachi Uchiha; 90 75 85 99 70 65 40 0 0; "
    "Tsukuyomi; iq+20; Fire; 100</code>\n\n"
    "<b>Example (minimal):</b>\n"
    "<code>/addarenachar Naruto; Sakura Haruno; 60 55 70 75 65 60 30 90 40</code>"
)


async def cmd_addarenachar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.effective_user or not update.message:
        return
    if not _is_admin(update.effective_user.id):
        await update.message.reply_text("🚫 Admin only command.")
        return

    raw = update.message.text.split(" ", 1)
    if len(raw) < 2:
        await update.message.reply_text(ADDARENACHAR_HELP, parse_mode="HTML")
        return

    parts = [p.strip() for p in raw[1].split(";")]
    if len(parts) < 3:
        await update.message.reply_text(ADDARENACHAR_HELP, parse_mode="HTML")
        return

    anime = parts[0]
    name = parts[1]
    stat_tokens = parts[2].split()
    if len(stat_tokens) != 9:
        await update.message.reply_text(
            "❌ Expected exactly 9 stats (ATK DEF SPD IQ STA DUR LEAD HEAL SUP).\n\n" + ADDARENACHAR_HELP,
            parse_mode="HTML"
        )
        return
    try:
        atk, deff, spd, iq, sta, dur, lead, heal, sup = [float(x) for x in stat_tokens]
    except ValueError:
        await update.message.reply_text("❌ Stats must be numbers.")
        return

    skill_name = parts[3] if len(parts) > 3 and parts[3] else None
    skill_effect = parts[4] if len(parts) > 4 and parts[4] else None
    element = parts[5] if len(parts) > 5 and parts[5] else "None"
    loyalty = 100
    if len(parts) > 6 and parts[6]:
        try:
            loyalty = int(parts[6])
        except ValueError:
            loyalty = 100

    file_id = None
    if update.message.photo:
        file_id = update.message.photo[-1].file_id
    elif update.message.reply_to_message and update.message.reply_to_message.photo:
        file_id = update.message.reply_to_message.photo[-1].file_id

    if not file_id:
        await update.message.reply_text(
            "📸 Send this command as a photo caption, or reply to a photo with this command."
        )
        return

    _cursor.execute(
        "INSERT INTO arena_characters (anime,name,atk,def,spd,iq,sta,dur,lead,heal,sup,"
        "skill_name,skill_effect,element,loyalty,file_id,added_by,added_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (anime, name, atk, deff, spd, iq, sta, dur, lead, heal, sup,
         skill_name, skill_effect, element, loyalty, file_id,
         update.effective_user.id, int(time.time()))
    )
    _conn.commit()
    new_id = _cursor.lastrowid

    pool_count = len(get_characters_for_anime(anime))
    ready_note = "✅ Enough for a full squad!" if pool_count >= 8 else f"({pool_count}/8 needed to unlock this universe)"

    await update.message.reply_photo(
        photo=file_id,
        caption=(
            f"✅ <b>{name}</b> added to {_uni_emoji(anime)} <b>{anime}</b>!\n"
            f"🆔 #{new_id}\n"
            f"⚔️ ATK {atk:.0f} 🛡 DEF {deff:.0f} 💨 SPD {spd:.0f} 🧠 IQ {iq:.0f}\n"
            f"🔋 STA {sta:.0f} 💪 DUR {dur:.0f} 👑 LEAD {lead:.0f} ❤️ HEAL {heal:.0f} 🎯 SUP {sup:.0f}\n"
            f"⭐ Skill: {skill_name or '—'} {('(' + skill_effect + ')') if skill_effect else ''}\n"
            f"🌀 Element: {element}   🔒 Loyalty: {loyalty}\n\n"
            f"{ready_note}"
        ),
        parse_mode="HTML"
    )


async def cmd_arena_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message:
        return
    _cursor.execute("SELECT anime, COUNT(*) FROM arena_characters GROUP BY anime ORDER BY anime")
    rows = _cursor.fetchall()
    if not rows:
        await update.message.reply_text("No Arena characters uploaded yet.")
        return
    lines = [f"🗂 <b>{sc('Arena Character Pool')}</b>", "━━━━━━━━━━━━━━━━━━━━"]
    for anime, count in rows:
        status = "✅ playable" if count >= 8 else f"⏳ needs {8-count} more"
        lines.append(f"{_uni_emoji(anime)} {anime} — {count} characters ({status})")
    await update.message.reply_text("\n".join(lines), parse_mode="HTML")


async def cmd_delarenachar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.effective_user or not update.message:
        return
    if not _is_admin(update.effective_user.id):
        await update.message.reply_text("🚫 Admin only command.")
        return
    if not context.args:
        await update.message.reply_text("Usage: /delarenachar <id>")
        return
    try:
        cid = int(context.args[0])
    except ValueError:
        await update.message.reply_text("❌ Provide a numeric character ID.")
        return
    _cursor.execute("SELECT name FROM arena_characters WHERE id=?", (cid,))
    row = _cursor.fetchone()
    if not row:
        await update.message.reply_text(f"❌ No arena character with ID #{cid}.")
        return
    _cursor.execute("DELETE FROM arena_characters WHERE id=?", (cid,))
    _conn.commit()
    await update.message.reply_text(f"🗑 Removed '{row[0]}' (#{cid}) from the Arena pool.")


async def cmd_arena_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message:
        return
    text = (
        f"⚔️ <b>{sc('Arena Wars — How to Play')}</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "/arena — open a live 1v1 challenge; another player taps Accept\n"
        "/arena_ai — instant solo draft vs the AI Champion\n"
        "/arena_cancel — cancel your stuck challenge/draft\n"
        "/arena_stats — your personal battle record\n"
        "/arena_top — global leaderboard\n\n"
        "🎲 <b>Drafting:</b> the whole match happens in ONE message. Each "
        "turn, Scout reveals one random character with its photo. Pick "
        "which open role to slot them into, or Skip (max 2 per player) if "
        "you don't want them. Once both squads are full, both players tap "
        "Ready to start the battle.\n\n"
        "🧠 <b>No luck in battle</b> — the same two squads always produce the "
        "same result. Victory comes from smart role assignments, elemental "
        "counters, and skills that fit their role."
    )
    await update.message.reply_text(text, parse_mode="HTML")


# ==============================================================================
#  SETUP — call this once from your main bot's main(), after telegram_app is built
# ==============================================================================

def setup(telegram_app, db_path: str, is_admin_func):
    """
    Wire Arena Wars into an existing python-telegram-bot Application.

        import arena_game
        arena_game.setup(telegram_app, DB_PATH, is_admin)

    Opens its OWN sqlite connection to the same DB file, creates its own
    tables, and registers its own commands/callbacks. Nothing in your
    existing bot file needs to change for this update.
    """
    global _conn, _cursor, _is_admin
    _conn = sqlite3.connect(db_path, check_same_thread=False)
    _cursor = _conn.cursor()
    _is_admin = is_admin_func
    _init_db()

    telegram_app.add_handler(CommandHandler("arena", cmd_arena))
    telegram_app.add_handler(CommandHandler("arena_ai", cmd_arena_ai))
    telegram_app.add_handler(CommandHandler("arena_cancel", cmd_arena_cancel))
    telegram_app.add_handler(CommandHandler("arena_stats", cmd_arena_stats))
    telegram_app.add_handler(CommandHandler("arena_top", cmd_arena_top))
    telegram_app.add_handler(CommandHandler("arena_help", cmd_arena_help))
    telegram_app.add_handler(CommandHandler("addarenachar", cmd_addarenachar))
    telegram_app.add_handler(CommandHandler("delarenachar", cmd_delarenachar))
    telegram_app.add_handler(CommandHandler("arena_list", cmd_arena_list))

    telegram_app.add_handler(CallbackQueryHandler(cb_open_challenge, pattern="^aru:"))
    telegram_app.add_handler(CallbackQueryHandler(cb_cancel_challenge, pattern="^arcancel:"))
    telegram_app.add_handler(CallbackQueryHandler(cb_accept_challenge, pattern="^arjoin:"))
    telegram_app.add_handler(CallbackQueryHandler(cb_start_ai, pattern="^arx:"))
    telegram_app.add_handler(CallbackQueryHandler(cb_scout, pattern="^ard:"))
    telegram_app.add_handler(CallbackQueryHandler(cb_skip, pattern="^ars:"))
    telegram_app.add_handler(CallbackQueryHandler(cb_assign_role, pattern="^arc:"))
    telegram_app.add_handler(CallbackQueryHandler(cb_ready, pattern="^arr:"))

    print("[arena_game] Arena Wars (single-message live draft) loaded ✅")
