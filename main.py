import asyncio
import itertools
import os
import re
import sys
import time
from datetime import datetime, timedelta, timezone

import aiohttp
import aiosqlite
from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiogram.filters import CommandStart
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(line_buffering=True)

# === НАСТРОЙКИ ===
TG_BOT_TOKEN = "9209605005:AAEy_QfMY6SCCwR_I7a7kFo-TdnW2yKdGKY"

# Пока тестируешь, оставь пустым [], чтобы не дублировать старого бота.
# Когда убедишься, что новый бот работает, впиши ["@wartrezlox", "@loqlux"] и останови старого.
TG_CHANNELS = []

DATA_DIR = os.getenv("DATA_DIR", "/app/data")
try:
    os.makedirs(DATA_DIR, exist_ok=True)
except OSError:
    DATA_DIR = "."
DB_PATH = os.path.join(DATA_DIR, "bot.db")

NEW_TOKEN_VARIANTS = [
    f"NzA3MjI4NDU5NjU0NDQ3MTM0.G{c1}Htq2.7GbGfciwH9{c2}{c3}fyXFrfVQ{c4}HgB6C1Y4UMoREJf88"
    for c1, c2, c3, c4 in itertools.product(["l", "I"], ["I", "l"], ["0", "O"], ["0", "O"])
]
OLD_TOKEN_VARIANTS = [
    "NzA3MjI4NDU5NjU0NDQ3MTM0.G2ilMI.I6ovfyFFQkkw4ITSsrsnPe-nxo6aFe_pbJzF5w",
    "NzA3MjI4NDU5NjU0NDQ3MTM0.G2iIMI.I6ovfyFFQkkw4ITSsrsnPe-nxo6aFe_pbJzF5w",
]
TOKEN_VARIANTS = NEW_TOKEN_VARIANTS + OLD_TOKEN_VARIANTS

DISCORD_CHANNELS = {
    "1476222981654184116": "egg",
    "1567898525059260416": "butterfly",
    "1571350008094269460": "market",
}

BASE_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "*/*",
}

ALL_RARITIES = ["Divine", "Eternal", "Secret", "Cosmic", "Mythic", "Legendary"]
ALL_LOCATIONS = [
    "Alien Planet",
    "Enchanted Forest",
    "Angels",
    "Demons",
    "Prehistoric",
    "Snow",
    "Cosmic",
]

RARITIES_MAP = [
    ("divine", "Divine"),
    ("eternal", "Eternal"),
    ("secret", "Secret"),
    ("cosmic", "Cosmic"),
    ("celestial", "Celestial"),
    ("godly", "Godly"),
    ("mythic", "Mythic"),
    ("legendary", "Legendary"),
    ("epic", "Epic"),
    ("rare", "Rare"),
    ("uncommon", "Uncommon"),
    ("common", "Common"),
]

router = Router()


# === БАЗА ДАННЫХ ===
async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                alerts_on INTEGER DEFAULT 1,
                events_on INTEGER DEFAULT 1,
                rarities TEXT DEFAULT 'Divine,Eternal',
                locations TEXT DEFAULT 'ALL',
                created_at INTEGER
            )
            """
        )
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS spawns (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                discord_msg_id TEXT UNIQUE,
                ctype TEXT,
                name TEXT,
                rarity TEXT,
                location TEXT,
                money TEXT,
                speed TEXT,
                link_url TEXT,
                spawn_ts INTEGER
            )
            """
        )
        await db.commit()


async def get_or_create_user(user_id: int) -> dict:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        await db.execute(
            "INSERT OR IGNORE INTO users (user_id, created_at) VALUES (?, ?)",
            (user_id, int(time.time())),
        )
        await db.commit()
        async with db.execute("SELECT * FROM users WHERE user_id = ?", (user_id,)) as cur:
            row = await cur.fetchone()
            return dict(row)


async def update_user(user_id: int, **fields):
    cols = ", ".join(f"{k} = ?" for k in fields)
    vals = list(fields.values()) + [user_id]
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(f"UPDATE users SET {cols} WHERE user_id = ?", vals)
        await db.commit()


# === ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ===
def time_ago(ts: int) -> str:
    diff = max(0, int(time.time()) - ts)
    if diff < 60:
        return "только что"
    if diff < 3600:
        return f"{diff // 60} мин назад"
    if diff < 86400:
        return f"{diff // 3600} ч назад"
    return f"{diff // 86400} дн назад"


def fmt_msk(ts: int) -> str:
    return datetime.fromtimestamp(ts, timezone(timedelta(hours=3))).strftime("%H:%M МСК")


def parse_csv_set(raw: str) -> set[str]:
    return {x.strip() for x in raw.split(",") if x.strip()}


async def safe_edit(cb: CallbackQuery, text: str, markup: InlineKeyboardMarkup):
    try:
        await cb.message.edit_text(text, reply_markup=markup, disable_web_page_preview=True)
    except TelegramBadRequest:
        pass


# === КЛАВИАТУРЫ И ЭКРАНЫ ===
def main_menu_text(user: dict) -> str:
    status = "🟢 Включены" if user["alerts_on"] else "🔴 Выключены"
    events = "🟢 Вкл" if user["events_on"] else "🔴 Выкл"
    rars = user["rarities"].replace(",", ", ") if user["rarities"] else "Не выбраны"
    locs = "Все локации" if user["locations"] == "ALL" else user["locations"].replace(",", ", ")
    return (
        "🥚 <b>Меню уведомлений Steal An Egg</b>\n\n"
        f"🔔 <b>Уведомления:</b> {status}\n"
        f"💎 <b>Редкости:</b> {rars}\n"
        f"🏛 <b>Локации:</b> {locs}\n"
        f"🎉 <b>События (Tidal/Бабочки/Рынок):</b> {events}\n\n"
        "Настрой фильтры ниже — бот сразу пришлёт нужное яйцо в личку."
    )


def main_menu_kb(user: dict) -> InlineKeyboardMarkup:
    alert_btn = "🔕 Выключить уведомления" if user["alerts_on"] else "🔔 Включить уведомления"
    event_btn = "🎉 События: ВКЛ" if user["events_on"] else "🎉 События: ВЫКЛ"
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="📦 Последний сток", callback_data="stock:all")],
            [
                InlineKeyboardButton(text="💎 Редкости", callback_data="menu:rarities"),
                InlineKeyboardButton(text="🏛 Локации", callback_data="menu:locations"),
            ],
            [InlineKeyboardButton(text=event_btn, callback_data="toggle:events")],
            [InlineKeyboardButton(text=alert_btn, callback_data="toggle:alerts")],
        ]
    )


def rarities_kb(user: dict) -> InlineKeyboardMarkup:
    chosen = parse_csv_set(user["rarities"])
    rows = []
    for i in range(0, len(ALL_RARITIES), 2):
        row = []
        for r in ALL_RARITIES[i : i + 2]:
            mark = "✅" if r in chosen else "❌"
            row.append(InlineKeyboardButton(text=f"{mark} {r}", callback_data=f"rar:{r}"))
        rows.append(row)
    rows.append([InlineKeyboardButton(text="⬅️ Назад в меню", callback_data="menu:main")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def locations_kb(user: dict) -> InlineKeyboardMarkup:
    is_all = user["locations"] == "ALL"
    chosen = set() if is_all else parse_csv_set(user["locations"])
    rows = [
        [
            InlineKeyboardButton(
                text=f"{'✅' if is_all else '⬜'} Все локации",
                callback_data="loc:ALL",
            )
        ]
    ]
    for i in range(0, len(ALL_LOCATIONS), 2):
        row = []
        for loc in ALL_LOCATIONS[i : i + 2]:
            mark = "✅" if (is_all or loc in chosen) else "❌"
            row.append(InlineKeyboardButton(text=f"{mark} {loc}", callback_data=f"loc:{loc}"))
        rows.append(row)
    rows.append([InlineKeyboardButton(text="⬅️ Назад в меню", callback_data="menu:main")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def render_stock_screen(loc_filter: str = "all") -> tuple[str, InlineKeyboardMarkup]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        if loc_filter == "all":
            query = "SELECT * FROM spawns ORDER BY spawn_ts DESC LIMIT 10"
            params = ()
        else:
            query = "SELECT * FROM spawns WHERE lower(location) LIKE ? ORDER BY spawn_ts DESC LIMIT 10"
            params = (f"%{loc_filter.lower()}%",)
        async with db.execute(query, params) as cur:
            rows = [dict(r) for r in await cur.fetchall()]

    title = "📦 <b>Последний сток</b>" + ("" if loc_filter == "all" else f" ({loc_filter})")
    if not rows:
        body = "\n\nПока записей нет — как только появится спавн, он отобразится здесь."
    else:
        lines = []
        for r in rows:
            ago = time_ago(r["spawn_ts"])
            if r["ctype"] == "egg":
                rar = f"[{r['rarity']}] " if r["rarity"] else ""
                lines.append(
                    f"🥚 <b>{rar}{r['name']}</b> — {r['location']} | {r['money']} <i>({ago})</i>"
                )
            elif r["ctype"] == "tidal":
                lines.append(f"🌊 <b>Tidal Surge</b> — {r['location']} <i>({ago})</i>")
            elif r["ctype"] == "butterfly":
                lines.append(f"🦋 <b>Бабочки</b> — {r['location']} <i>({ago})</i>")
            elif r["ctype"] == "market":
                lines.append(f"🛒 <b>Slick Rick</b> — {r['location']} <i>({ago})</i>")
        body = "\n\n" + "\n".join(lines)

    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="🌍 Все", callback_data="stock:all"),
                InlineKeyboardButton(text="👽 Alien", callback_data="stock:Alien"),
                InlineKeyboardButton(text="🌲 Forest", callback_data="stock:Enchanted"),
            ],
            [
                InlineKeyboardButton(text="🔄 Обновить", callback_data=f"stock:{loc_filter}"),
                InlineKeyboardButton(text="⬅️ Назад", callback_data="menu:main"),
            ],
        ]
    )
    return title + body, kb


# === ОБРАБОТЧИКИ TELEGRAM ===
@router.message(CommandStart())
async def cmd_start(message: Message):
    user = await get_or_create_user(message.from_user.id)
    await message.answer(
        main_menu_text(user),
        reply_markup=main_menu_kb(user),
        disable_web_page_preview=True,
    )


@router.callback_query(F.data == "menu:main")
async def cb_main(cb: CallbackQuery):
    user = await get_or_create_user(cb.from_user.id)
    await safe_edit(cb, main_menu_text(user), main_menu_kb(user))
    await cb.answer()


@router.callback_query(F.data == "toggle:alerts")
async def cb_toggle_alerts(cb: CallbackQuery):
    user = await get_or_create_user(cb.from_user.id)
    new_val = 0 if user["alerts_on"] else 1
    await update_user(cb.from_user.id, alerts_on=new_val)
    user["alerts_on"] = new_val
    await safe_edit(cb, main_menu_text(user), main_menu_kb(user))
    await cb.answer("Уведомления включены" if new_val else "Уведомления выключены")


@router.callback_query(F.data == "toggle:events")
async def cb_toggle_events(cb: CallbackQuery):
    user = await get_or_create_user(cb.from_user.id)
    new_val = 0 if user["events_on"] else 1
    await update_user(cb.from_user.id, events_on=new_val)
    user["events_on"] = new_val
    await safe_edit(cb, main_menu_text(user), main_menu_kb(user))
    await cb.answer("События включены" if new_val else "События выключены")


@router.callback_query(F.data == "menu:rarities")
async def cb_menu_rarities(cb: CallbackQuery):
    user = await get_or_create_user(cb.from_user.id)
    await safe_edit(
        cb,
        "💎 <b>Выбери редкости яиц</b>\n\nНажимай на кнопки, чтобы включить или выключить уведомления:",
        rarities_kb(user),
    )
    await cb.answer()


@router.callback_query(F.data.startswith("rar:"))
async def cb_toggle_rarity(cb: CallbackQuery):
    rar = cb.data.split(":", 1)[1]
    user = await get_or_create_user(cb.from_user.id)
    chosen = parse_csv_set(user["rarities"])
    if rar in chosen:
        chosen.remove(rar)
    else:
        chosen.add(rar)
    ordered = [r for r in ALL_RARITIES if r in chosen]
    new_str = ",".join(ordered)
    await update_user(cb.from_user.id, rarities=new_str)
    user["rarities"] = new_str
    await safe_edit(
        cb,
        "💎 <b>Выбери редкости яиц</b>\n\nНажимай на кнопки, чтобы включить или выключить уведомления:",
        rarities_kb(user),
    )
    await cb.answer()


@router.callback_query(F.data == "menu:locations")
async def cb_menu_locations(cb: CallbackQuery):
    user = await get_or_create_user(cb.from_user.id)
    await safe_edit(
        cb,
        "🏛 <b>Выбери локации</b>\n\nМожешь оставить «Все локации» или выбрать только нужные:",
        locations_kb(user),
    )
    await cb.answer()


@router.callback_query(F.data.startswith("loc:"))
async def cb_toggle_location(cb: CallbackQuery):
    loc = cb.data.split(":", 1)[1]
    user = await get_or_create_user(cb.from_user.id)
    if loc == "ALL":
        new_str = "ALL"
    else:
        if user["locations"] == "ALL":
            chosen = {loc}
        else:
            chosen = parse_csv_set(user["locations"])
            if loc in chosen:
                chosen.remove(loc)
            else:
                chosen.add(loc)
        new_str = "ALL" if not chosen else ",".join(l for l in ALL_LOCATIONS if l in chosen)
    await update_user(cb.from_user.id, locations=new_str)
    user["locations"] = new_str
    await safe_edit(
        cb,
        "🏛 <b>Выбери локации</b>\n\nМожешь оставить «Все локации» или выбрать только нужные:",
        locations_kb(user),
    )
    await cb.answer()


@router.callback_query(F.data.startswith("stock:"))
async def cb_stock(cb: CallbackQuery):
    loc_filter = cb.data.split(":", 1)[1]
    text, kb = await render_stock_screen(loc_filter)
    await safe_edit(cb, text, kb)
    await cb.answer()


# === ПАРСИНГ DISCORD ===
def is_spam(m: dict) -> bool:
    author = m.get("author", {}).get("username", "").lower()
    content = m.get("content", "").lower()
    if "add me to your server" in content or "customize" in content:
        return True
    if "sapphire" in author and "egg:" not in str(m).lower():
        return True
    return False


def detect_rarity(title_text: str, raw: str, clean_raw: str) -> str:
    r_match = re.search(r"(?:Rarity|Tier):\*?\*?\s*([^\n]+)", clean_raw, re.IGNORECASE)
    if r_match:
        val = r_match.group(1).strip().lower()
        for key, label in RARITIES_MAP:
            if key in val:
                return label
        return r_match.group(1).strip().capitalize()

    searchable = re.sub(
        r"(?:Location|Where):\*?\*?\s*[^\n]+",
        "",
        f"{title_text}\n{raw}",
        flags=re.IGNORECASE,
    ).lower()
    for key, label in RARITIES_MAP:
        if re.search(rf"(?<![a-z]){key}(?![a-z])", searchable):
            return label
    return ""


def parse_discord_message(m: dict, ctype: str) -> dict | None:
    if is_spam(m):
        return None

    raw_parts = [m.get("content", "")]
    title_parts = []
    for e in m.get("embeds", []):
        if e.get("author", {}).get("name"):
            title_parts.append(e["author"]["name"])
        if e.get("title"):
            title_parts.append(e["title"])
            raw_parts.append(e["title"])
        raw_parts.append(e.get("description", ""))
        for f in e.get("fields", []):
            raw_parts.append(f"{f.get('name', '')}: {f.get('value', '')}")
        if e.get("footer", {}).get("text"):
            raw_parts.append(e["footer"]["text"])

    raw = "\n".join(raw_parts)
    if not raw.strip():
        return None

    clean_raw = re.sub(r"<a?:\w+:\d+>", "", raw)
    raw_lower = clean_raw.lower()
    title_text = " ".join(title_parts)

    ts_match = re.search(r"<t:(\d+)", raw)
    spawn_ts = int(ts_match.group(1)) if ts_match else int(time.time())

    link_match = re.search(r"\((https?://[^\)]+)\)", raw)
    link_url = link_match.group(1) if link_match else ""

    # Tidal Surge
    if ctype == "egg" and any(
        k in raw_lower
        for k in ("tidal surge", "flooded", "not a spawn alert", "possible eggs", "event alert")
    ):
        where_match = re.search(r"(?:Where|Location):\*?\*?\s*([^\n]+)", clean_raw, re.IGNORECASE)
        loc = where_match.group(1).strip() if where_match else "Alien Planet"
        return {
            "discord_msg_id": m["id"],
            "ctype": "tidal",
            "name": "Tidal Surge",
            "rarity": "",
            "location": loc,
            "money": "—",
            "speed": "—",
            "link_url": link_url,
            "spawn_ts": spawn_ts,
        }

    # Яйца
    if ctype == "egg":
        egg_match = re.search(r"(?:Egg|Name):\*?\*?\s*([^\n]+)", clean_raw, re.IGNORECASE)
        if not egg_match:
            return None
        egg_name = egg_match.group(1).strip()
        loc_match = re.search(r"(?:Location|Where):\*?\*?\s*([^\n]+)", clean_raw, re.IGNORECASE)
        loc = loc_match.group(1).strip() if loc_match else "Unknown"
        money_match = re.search(r"(?:Money|Value|Income):\*?\*?\s*([^\n]+)", clean_raw, re.IGNORECASE)
        if money_match:
            money = money_match.group(1).strip()
        else:
            m_alt = re.search(r"\$\d+[a-zA-Z]*(?:/s|/sec)?", clean_raw, re.IGNORECASE)
            money = m_alt.group(0) if m_alt else "—"
        speed_match = re.search(r"(?:Recommended Speed|Speed):\*?\*?\s*([^\n]+)", clean_raw, re.IGNORECASE)
        speed = speed_match.group(1).strip() if speed_match else "—"
        rarity = detect_rarity(title_text, raw, clean_raw)
        return {
            "discord_msg_id": m["id"],
            "ctype": "egg",
            "name": egg_name,
            "rarity": rarity,
            "location": loc,
            "money": money,
            "speed": speed,
            "link_url": link_url,
            "spawn_ts": spawn_ts,
        }

    # Бабочки
    if ctype == "butterfly":
        where_match = re.search(r"Where:\*?\*?\s*([^\n]+)", clean_raw, re.IGNORECASE)
        loc = where_match.group(1).strip() if where_match else "Enchanted Forest"
        return {
            "discord_msg_id": m["id"],
            "ctype": "butterfly",
            "name": "Butterfly Event",
            "rarity": "",
            "location": loc,
            "money": "—",
            "speed": "—",
            "link_url": link_url,
            "spawn_ts": spawn_ts,
        }

    # Чёрный рынок
    if ctype == "market":
        speed_match = re.search(r"(\d+[A-Za-z]*)\s*Speed", clean_raw, re.IGNORECASE)
        speed = speed_match.group(1) if speed_match else "250B"
        loc = "Alien Planet" if "Alien Planet" in clean_raw else "Unknown"
        return {
            "discord_msg_id": m["id"],
            "ctype": "market",
            "name": "Slick Rick",
            "rarity": "",
            "location": loc,
            "money": "—",
            "speed": speed,
            "link_url": link_url,
            "spawn_ts": spawn_ts,
        }

    return None


def format_post(item: dict) -> str:
    msk_time = fmt_msk(item["spawn_ts"])
    link_str = (
        f'\n\n🔗 <a href="{item["link_url"]}">Присоединиться к игре</a>'
        if item["link_url"]
        else ""
    )

    if item["ctype"] == "tidal":
        return (
            f"🌊 Затопление (TIDAL SURGE) началось!! -\n\n"
            f"🏛 Локация: {item['location']}\n"
            f"💧 Инфо: Доступны редкие водные яйца\n"
            f"⏰ Заспавнилось: {msk_time}"
            f"{link_str}"
        )
    if item["ctype"] == "egg":
        header = (
            f"🔥 {item['rarity']} Egg появилось!! -"
            if item["rarity"]
            else "🔥 Новое Egg появилось!! -"
        )
        return (
            f"{header}\n\n"
            f"🥚 Яйцо: {item['name']}\n"
            f"🏛 Локация: {item['location']}\n"
            f"⏰ Заспавнилось: {msk_time}\n"
            f"💰 Приносит: {item['money']}\n"
            f"🏃 Рек. скорость: {item['speed']}"
            f"{link_str}"
        )
    if item["ctype"] == "butterfly":
        return (
            f"🦋 Ивент бабочек начался!! -\n\n"
            f"🏛 Локация: {item['location']}\n"
            f"🎯 Инфо: Ловите бабочек сачком\n"
            f"⏰ Заспавнилось: {msk_time}"
            f"{link_str}"
        )
    return (
        f"🛒 Торговец Slick Rick прибыл!! -\n\n"
        f"🏛 Локация: {item['location']}\n"
        f"📦 Товары: Контрабанда и мутации\n"
        f"🏃 Рек. скорость: {item['speed']}\n"
        f"⏰ Заспавнилось: {msk_time}"
        f"{link_str}"
    )


async def delete_later(bot: Bot, chat_id: str, msg_id: int):
    await asyncio.sleep(900)
    try:
        await bot.delete_message(chat_id, msg_id)
    except Exception:
        pass


async def broadcast_item(bot: Bot, item: dict):
    text = format_post(item)

    # 1. Публикация в каналы (только Eternal/Divine яйца + все события)
    if TG_CHANNELS and (item["ctype"] != "egg" or item["rarity"] in {"Divine", "Eternal"}):
        for ch in TG_CHANNELS:
            try:
                msg = await bot.send_message(ch, text, disable_web_page_preview=True)
                asyncio.create_task(delete_later(bot, ch, msg.message_id))
            except Exception as e:
                print(f"❌ Ошибка канала {ch}: {e}", flush=True)

    # 2. Персональная рассылка пользователям по их фильтрам
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM users WHERE alerts_on = 1") as cur:
            users = [dict(r) for r in await cur.fetchall()]

    for u in users:
        if item["ctype"] == "egg":
            user_rars = parse_csv_set(u["rarities"])
            if item["rarity"] not in user_rars:
                continue
            if u["locations"] != "ALL":
                user_locs = parse_csv_set(u["locations"])
                if not any(loc.lower() in item["location"].lower() for loc in user_locs):
                    continue
        else:
            if not u["events_on"]:
                continue

        try:
            await bot.send_message(u["user_id"], text, disable_web_page_preview=True)
        except TelegramForbiddenError:
            await update_user(u["user_id"], alerts_on=0)
        except Exception as e:
            print(f"⚠️ Ошибка отправки {u['user_id']}: {e}", flush=True)
        await asyncio.sleep(0.05)


async def discord_watcher(bot: Bot):
    timeout = aiohttp.ClientTimeout(total=10)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        token = None
        for tk in TOKEN_VARIANTS:
            try:
                async with session.get(
                    "https://discord.com/api/v9/users/@me",
                    headers={**BASE_HEADERS, "Authorization": tk},
                ) as r:
                    if r.status == 200:
                        data = await r.json()
                        token = tk
                        print(f"✅ Авторизован в Discord как: {data.get('username')}", flush=True)
                        break
            except Exception:
                pass

        if not token:
            print("❌ Не подошёл ни один токен Discord!", flush=True)
            return

        # Загружаем уже известные ID из базы, чтобы не слать дубли после перезапуска
        async with aiosqlite.connect(DB_PATH) as db:
            async with db.execute("SELECT discord_msg_id FROM spawns") as cur:
                seen_ids = {row[0] for row in await cur.fetchall()}

        first_fill = len(seen_ids) == 0
        headers = {**BASE_HEADERS, "Authorization": token}

        while True:
            for cid, ctype in DISCORD_CHANNELS.items():
                try:
                    url = f"https://discord.com/api/v9/channels/{cid}/messages?limit=10"
                    async with session.get(url, headers=headers) as r:
                        if r.status != 200:
                            continue
                        msgs = await r.json()
                        if not isinstance(msgs, list):
                            continue

                    for m in reversed(msgs):
                        mid = m.get("id")
                        if not mid or mid in seen_ids:
                            continue
                        seen_ids.add(mid)
                        item = parse_discord_message(m, ctype)
                        if not item:
                            continue

                        async with aiosqlite.connect(DB_PATH) as db:
                            await db.execute(
                                """
                                INSERT OR IGNORE INTO spawns
                                (discord_msg_id, ctype, name, rarity, location, money, speed, link_url, spawn_ts)
                                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                                """,
                                (
                                    item["discord_msg_id"],
                                    item["ctype"],
                                    item["name"],
                                    item["rarity"],
                                    item["location"],
                                    item["money"],
                                    item["speed"],
                                    item["link_url"],
                                    item["spawn_ts"],
                                ),
                            )
                            await db.commit()

                        # При самом первом заполнении пустой базы просто сохраняем историю без спама
                        if not first_fill:
                            print(f"📢 Новый спавн: {item['name']} ({item['rarity'] or item['ctype']})", flush=True)
                            await broadcast_item(bot, item)

                except Exception as e:
                    print(f"⚠️ Ошибка канала {cid}: {e}", flush=True)
                await asyncio.sleep(1.5)

            first_fill = False
            await asyncio.sleep(3)


async def main():
    await init_db()
    bot = Bot(token=TG_BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher()
    dp.include_router(router)

    watcher_task = asyncio.create_task(discord_watcher(bot))
    print("🚀 Бот-сток (Этап 1) запущен!", flush=True)
    try:
        await dp.start_polling(bot)
    finally:
        watcher_task.cancel()
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
