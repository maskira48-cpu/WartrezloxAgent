import asyncio
import html
import itertools
import os
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import quote, urlencode

import aiohttp
import aiosqlite
from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError, TelegramRetryAfter
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.types import (
    BotCommand,
    CallbackQuery,
    FSInputFile,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    LabeledPrice,
    MenuButtonCommands,
    Message,
    PreCheckoutQuery,
    ReplyKeyboardMarkup,
    WebAppInfo,
)

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(line_buffering=True)

# === НАСТРОЙКИ ===
TG_BOT_TOKEN = "9209605005:AAEy_QfMY6SCCwR_I7a7kFo-TdnW2yKdGKY"

# Ссылка на твоё мини-приложение на GitHub Pages
MINI_APP_URL = "https://maskira48-cpu.github.io/VIP/"

# Только эти Telegram ID имеют доступ к админ-командам и отчётам
ADMIN_IDS = {1171395618}

# Можно оставить пустым — просто отправь боту фото с подписью /setphoto
WELCOME_PHOTO = ""

# Обязательный канал для подписки (бот должен быть там админом)
REQUIRED_CHANNEL = "@wartrezlox"
REQUIRED_CHANNEL_URL = "https://t.me/wartrezlox"

# Экономика VIP и рефералки
VIP_PRICE_STARS = 50
REF_TARGET = 7
REF_VIP_DELAY = 5          # задержка для бесплатного (реферального) VIP после платных VIP
FREE_USER_DELAY = 30       # реальная задержка в секундах для обычных пользователей
VIP_EARLY_TEXT_SEC = 160   # сколько секунд отображается в тексте уведомления

# Каналы для автопостинга (бот должен быть там админом с правами на посты и удаление)
TG_CHANNELS = ["@wartrezlox", "@loqlux"]

DATA_DIR = os.getenv("DATA_DIR", "/app/data")
try:
    os.makedirs(DATA_DIR, exist_ok=True)
except OSError:
    DATA_DIR = "."
DB_PATH = os.path.join(DATA_DIR, "bot.db")

BOT_USERNAME = ""
VIP_INVOICE_URL = ""
MSK_TZ = timezone(timedelta(hours=3))
PENDING_BROADCAST: dict[int, str] = {}

# Новый токен Discord (с автоподбором I/l со скриншота)
TOKEN_VARIANTS = [
    f"NzA3MjI4NDU5NjU0NDQ3MTM0.Gsyur-.{c1}UxQ{c2}qS34ea7xcv96H0sAdfnESa36JY5O_-u{c3}8"
    for c1, c2, c3 in itertools.product(["I", "l"], ["I", "l"], ["l", "I"])
]

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
    "Cherry Blossom",
    "Volcano",
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

RARITY_SQUARES = {
    "Divine": "🟨",
    "Eternal": "🟪",
    "Secret": "🟥",
    "Cosmic": "🟦",
    "Celestial": "🟦",
    "Godly": "🟧",
    "Mythic": "🟧",
    "Legendary": "🟩",
    "Epic": "🟪",
    "Rare": "🟦",
    "Uncommon": "🟩",
    "Common": "⬜",
}

router = Router()


def make_btn(
    text: str,
    *,
    callback_data: str | None = None,
    url: str | None = None,
    web_app: WebAppInfo | None = None,
    style: str | None = None,
) -> InlineKeyboardButton:
    kwargs = {"text": text}
    if callback_data is not None:
        kwargs["callback_data"] = callback_data
    if url is not None:
        kwargs["url"] = url
    if web_app is not None:
        kwargs["web_app"] = web_app
    if style is not None:
        try:
            return InlineKeyboardButton(**kwargs, style=style)
        except Exception:
            pass
    return InlineKeyboardButton(**kwargs)


def make_kb_btn(
    text: str,
    *,
    web_app: WebAppInfo | None = None,
    style: str | None = None,
) -> KeyboardButton:
    kwargs = {"text": text}
    if web_app is not None:
        kwargs["web_app"] = web_app
    if style is not None:
        try:
            return KeyboardButton(**kwargs, style=style)
        except Exception:
            pass
    return KeyboardButton(**kwargs)


# === MINI APP И ОПЛАТА STARS ===
async def ensure_vip_invoice_url(bot: Bot) -> str:
    global VIP_INVOICE_URL
    if VIP_INVOICE_URL:
        return VIP_INVOICE_URL
    try:
        VIP_INVOICE_URL = await bot.create_invoice_link(
            title="👑 VIP-доступ навсегда",
            description="Мгновенные уведомления Steal An Egg самым первым, без задержки.",
            payload="vip_stars_50",
            provider_token="",
            currency="XTR",
            prices=[LabeledPrice(label="VIP-доступ навсегда", amount=VIP_PRICE_STARS)],
        )
    except Exception as e:
        print(f"⚠️ Не удалось создать ссылку оплаты Stars: {e}", flush=True)
    return VIP_INVOICE_URL


def build_webapp_url(user: dict) -> str:
    base = MINI_APP_URL.strip()
    if not base.startswith("https://"):
        return ""
    sep = "&" if "?" in base else "?"
    params = {
        "vip": user.get("vip_level") or 0,
        "refs": user.get("ref_count") or 0,
        "target": REF_TARGET,
        "price": VIP_PRICE_STARS,
        "bot": BOT_USERNAME,
        "uid": user["user_id"],
        "ch": REQUIRED_CHANNEL,
    }
    if VIP_INVOICE_URL:
        params["inv"] = VIP_INVOICE_URL
    return f"{base}{sep}{urlencode(params)}"


def bottom_kb(user: dict) -> ReplyKeyboardMarkup:
    webapp_url = build_webapp_url(user)
    if webapp_url:
        vip_btn = make_kb_btn("💎 VIP", web_app=WebAppInfo(url=webapp_url), style="success")
    else:
        vip_btn = make_kb_btn("💎 VIP", style="success")

    return ReplyKeyboardMarkup(
        keyboard=[
            [
                make_kb_btn("📦 Последний сток", style="primary"),
                make_kb_btn("🔔 Уведомления"),
            ],
            [
                make_kb_btn("💎 Редкости"),
                make_kb_btn("🏛 Локации"),
            ],
            [vip_btn],
        ],
        resize_keyboard=True,
        is_persistent=True,
    )


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
                created_at INTEGER,
                vip_level INTEGER DEFAULT 0,
                referrer_id INTEGER DEFAULT NULL,
                ref_counted INTEGER DEFAULT 0,
                ref_count INTEGER DEFAULT 0
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
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT
            )
            """
        )
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS payments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                amount INTEGER,
                currency TEXT,
                created_at INTEGER
            )
            """
        )
        migrations = [
            "ALTER TABLE users ADD COLUMN vip_level INTEGER DEFAULT 0",
            "ALTER TABLE users ADD COLUMN referrer_id INTEGER DEFAULT NULL",
            "ALTER TABLE users ADD COLUMN ref_counted INTEGER DEFAULT 0",
            "ALTER TABLE users ADD COLUMN ref_count INTEGER DEFAULT 0",
        ]
        for sql in migrations:
            try:
                await db.execute(sql)
            except aiosqlite.OperationalError:
                pass
        await db.commit()


async def get_setting(key: str) -> str:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT value FROM settings WHERE key = ?", (key,)) as cur:
            row = await cur.fetchone()
            return row[0] if row else ""


async def set_setting(key: str, value: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )
        await db.commit()


async def record_payment(user_id: int, amount: int, currency: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO payments (user_id, amount, currency, created_at) VALUES (?, ?, ?, ?)",
            (user_id, amount, currency, int(time.time())),
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


async def get_all_users() -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM users") as cur:
            return [dict(r) for r in await cur.fetchall()]


async def update_user(user_id: int, **fields):
    cols = ", ".join(f"{k} = ?" for k in fields)
    vals = list(fields.values()) + [user_id]
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(f"UPDATE users SET {cols} WHERE user_id = ?", vals)
        await db.commit()


# === ПОДПИСКА И РЕФЕРАЛКА ===
async def is_subscribed(bot: Bot, user_id: int) -> bool:
    try:
        member = await bot.get_chat_member(REQUIRED_CHANNEL, user_id)
        if member.status in ("member", "administrator", "creator"):
            return True
        if member.status == "restricted" and getattr(member, "is_member", False):
            return True
        return False
    except Exception as e:
        print(f"⚠️ Ошибка проверки подписки на {REQUIRED_CHANNEL}: {e}", flush=True)
        return False


async def try_credit_referral(bot: Bot, user_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT referrer_id, ref_counted FROM users WHERE user_id = ?",
            (user_id,),
        ) as cur:
            row = await cur.fetchone()

        if not row or not row["referrer_id"] or row["ref_counted"]:
            return

        ref_id = row["referrer_id"]
        await db.execute("UPDATE users SET ref_counted = 1 WHERE user_id = ?", (user_id,))
        await db.execute(
            "UPDATE users SET ref_count = COALESCE(ref_count, 0) + 1 WHERE user_id = ?",
            (ref_id,),
        )
        async with db.execute("SELECT * FROM users WHERE user_id = ?", (ref_id,)) as cur:
            ref_row = await cur.fetchone()

        upgraded = False
        if ref_row and ref_row["ref_count"] >= REF_TARGET and (ref_row["vip_level"] or 0) == 0:
            await db.execute("UPDATE users SET vip_level = 1 WHERE user_id = ?", (ref_id,))
            upgraded = True
        await db.commit()

    if ref_row:
        ref_user = await get_or_create_user(ref_id)
        count = ref_user.get("ref_count") or 0
        try:
            if upgraded:
                await bot.send_message(
                    ref_id,
                    f"🎉 <b>Поздравляем!</b> По твоей ссылке зашли {REF_TARGET} друзей — ты получил <b>бесплатный VIP</b>!\n\n"
                    "Теперь алерты приходят тебе сразу после платных VIP.",
                    reply_markup=bottom_kb(ref_user),
                )
            elif count < REF_TARGET:
                await bot.send_message(
                    ref_id,
                    f"👥 Друг запустил бота и подписался на канал!\n"
                    f"Твой прогресс до VIP: <b>{count} из {REF_TARGET}</b>.",
                    reply_markup=bottom_kb(ref_user),
                )
        except Exception:
            pass


# === ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ===
def clean_field(text: str) -> str:
    return re.sub(r"[*_`]+", "", text).strip()


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
    return datetime.fromtimestamp(ts, MSK_TZ).strftime("%H:%M МСК")


def parse_csv_set(raw: str) -> set[str]:
    return {x.strip() for x in raw.split(",") if x.strip()}


def vip_badge(vip_level: int) -> str:
    if vip_level == 2:
        return "💎 Платный VIP (самое начало очереди)"
    if vip_level == 1:
        return "⭐ Бесплатный VIP (за друзей)"
    return "👤 Без VIP (конец очереди)"


def progress_bar(count: int, total: int) -> str:
    filled = min(count, total)
    return "🟩" * filled + "⬜" * (total - filled)


async def get_welcome_photo():
    saved = await get_setting("welcome_photo")
    if saved:
        return saved
    if WELCOME_PHOTO.strip():
        return WELCOME_PHOTO.strip()
    for path in ("welcome.jpg", "welcome.png", os.path.join(DATA_DIR, "welcome.jpg")):
        if os.path.exists(path):
            return FSInputFile(path)
    return None


async def safe_edit(cb: CallbackQuery, text: str, markup: InlineKeyboardMarkup | None):
    if cb.message.photo:
        try:
            await cb.message.edit_reply_markup(reply_markup=None)
        except Exception:
            pass
        await cb.message.answer(text, reply_markup=markup, disable_web_page_preview=True)
        return
    try:
        await cb.message.edit_text(text, reply_markup=markup, disable_web_page_preview=True)
    except TelegramBadRequest:
        pass


# === АДМИН-РАССЫЛКА ===
async def run_mass_broadcast(bot: Bot, admin_id: int, text: str, refresh_kb: bool = True):
    users = await get_all_users()
    sent, blocked, failed = 0, 0, 0

    for u in users:
        markup = bottom_kb(u) if refresh_kb else None
        try:
            await bot.send_message(
                u["user_id"],
                text,
                reply_markup=markup,
                disable_web_page_preview=True,
            )
            sent += 1
        except TelegramRetryAfter as e:
            await asyncio.sleep(e.retry_after + 0.5)
            try:
                await bot.send_message(
                    u["user_id"],
                    text,
                    reply_markup=markup,
                    disable_web_page_preview=True,
                )
                sent += 1
            except Exception:
                failed += 1
        except TelegramForbiddenError:
            await update_user(u["user_id"], alerts_on=0)
            blocked += 1
        except Exception as e:
            print(f"⚠️ Ошибка рассылки {u['user_id']}: {e}", flush=True)
            failed += 1
        await asyncio.sleep(0.05)

    try:
        await bot.send_message(
            admin_id,
            "✅ <b>Рассылка завершена!</b>\n\n"
            f"📨 Доставлено: <b>{sent}</b>\n"
            f"🚫 Заблокировали бота: <b>{blocked}</b>\n"
            f"⚠️ Прочие ошибки: <b>{failed}</b>",
        )
    except Exception:
        pass


# === АНАЛИТИКА (АДМИН) ===
async def render_stats_screen() -> tuple[str, InlineKeyboardMarkup]:
    now_msk = datetime.now(MSK_TZ)
    today_start = int(now_msk.replace(hour=0, minute=0, second=0, microsecond=0).timestamp())
    week_start = int((now_msk - timedelta(days=7)).timestamp())

    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            """
            SELECT
                COUNT(*) AS total,
                COALESCE(SUM(CASE WHEN alerts_on = 1 THEN 1 ELSE 0 END), 0) AS active,
                COALESCE(SUM(CASE WHEN alerts_on = 0 THEN 1 ELSE 0 END), 0) AS muted,
                COALESCE(SUM(CASE WHEN created_at >= ? THEN 1 ELSE 0 END), 0) AS new_today,
                COALESCE(SUM(CASE WHEN created_at >= ? THEN 1 ELSE 0 END), 0) AS new_week,
                COALESCE(SUM(CASE WHEN vip_level = 2 THEN 1 ELSE 0 END), 0) AS vip_top,
                COALESCE(SUM(CASE WHEN vip_level = 1 THEN 1 ELSE 0 END), 0) AS vip_ref,
                COALESCE(SUM(CASE WHEN COALESCE(vip_level, 0) = 0 THEN 1 ELSE 0 END), 0) AS free_cnt,
                COALESCE(SUM(COALESCE(ref_counted, 0)), 0) AS total_refs
            FROM users
            """,
            (today_start, week_start),
        ) as cur:
            u_stats = await cur.fetchone()

        async with db.execute(
            """
            SELECT
                COUNT(*) AS pay_cnt,
                COALESCE(SUM(amount), 0) AS stars_total,
                COALESCE(SUM(CASE WHEN created_at >= ? THEN amount ELSE 0 END), 0) AS stars_today
            FROM payments
            """,
            (today_start,),
        ) as cur:
            p_stats = await cur.fetchone()

        async with db.execute(
            """
            SELECT
                COUNT(*) AS spawns_total,
                COALESCE(SUM(CASE WHEN spawn_ts >= ? THEN 1 ELSE 0 END), 0) AS spawns_today
            FROM spawns
            """,
            (today_start,),
        ) as cur:
            s_stats = await cur.fetchone()

        async with db.execute(
            "SELECT user_id, ref_count FROM users WHERE COALESCE(ref_count, 0) > 0 "
            "ORDER BY ref_count DESC LIMIT 3"
        ) as cur:
            top_refs = await cur.fetchall()

    total, active, muted, new_today, new_week, vip_top, vip_ref, free_cnt, total_refs = u_stats
    pay_cnt, stars_total, stars_today = p_stats
    spawns_total, spawns_today = s_stats

    top_lines = ""
    if top_refs:
        items = [f"{i}. <code>{uid}</code> — {cnt} др." for i, (uid, cnt) in enumerate(top_refs, 1)]
        top_lines = "\n🏆 <b>Топ рефоводов:</b>\n" + "\n".join(items) + "\n"

    text = (
        f"📊 <b>Аналитика @{BOT_USERNAME}</b>\n"
        f"<i>Обновлено: {now_msk.strftime('%d.%m.%Y %H:%M МСК')}</i>\n\n"
        "👥 <b>Пользователи:</b>\n"
        f"• Всего: <b>{total}</b>\n"
        f"• Активные алерты: <b>{active}</b> | Выкл/блок: <b>{muted}</b>\n"
        f"• Новых сегодня: <b>+{new_today}</b> | За 7 дней: <b>+{new_week}</b>\n\n"
        "👑 <b>VIP и монетизация:</b>\n"
        f"• Платный VIP: <b>{vip_top}</b>\n"
        f"• Бесплатный VIP (реф): <b>{vip_ref}</b>\n"
        f"• Без VIP: <b>{free_cnt}</b>\n"
        f"• Оплат Stars: <b>{pay_cnt}</b> (всего <b>{stars_total} ⭐</b>, сегодня <b>+{stars_today} ⭐</b>)\n\n"
        "🔗 <b>Рефералы:</b>\n"
        f"• Подтверждённых приглашений: <b>{total_refs}</b>\n"
        f"{top_lines}\n"
        "🥚 <b>Спавны в базе:</b>\n"
        f"• Всего: <b>{spawns_total}</b> | Сегодня: <b>{spawns_today}</b>"
    )

    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [make_btn("🔄 Обновить", callback_data="admin:stats", style="primary")],
        ]
    )
    return text, kb


# === КЛАВИАТУРЫ И ЭКРАНЫ ===
def welcome_text(subscribed: bool) -> str:
    base = (
        "🥚 <b>Добро пожаловать в Wartrez Стоки!</b>\n\n"
        "Этот бот мгновенно уведомляет о редких яйцах (<b>Divine, Eternal, Secret</b> и др.) "
        "и глобальных ивентах в <b>Steal An Egg</b>.\n\n"
        "✨ <b>Внутри тебя ждёт:</b>\n"
        "• Гибкие фильтры по редкостям и локациям\n"
        "• История последних спавнов в один клик\n"
        "• VIP-приоритет для мгновенных уведомлений\n\n"
        "👤 <b>Создатель:</b> @wartrezlox"
    )
    if not subscribed:
        base += f"\n\n🔒 Для доступа подпишись на канал {REQUIRED_CHANNEL} и нажми кнопку ниже."
    return base


def welcome_kb(subscribed: bool) -> InlineKeyboardMarkup:
    if subscribed:
        return InlineKeyboardMarkup(
            inline_keyboard=[
                [make_btn("🚀 Открыть меню", callback_data="menu:main", style="primary")],
                [make_btn("📢 Канал создателя", url=REQUIRED_CHANNEL_URL)],
            ]
        )
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [make_btn("📢 Подписаться на @wartrezlox", url=REQUIRED_CHANNEL_URL, style="primary")],
            [make_btn("✅ Я подписался — Начать", callback_data="check:sub", style="success")],
        ]
    )


def main_menu_text(user: dict) -> str:
    status = "🟢 Включены" if user["alerts_on"] else "🔴 Выключены"
    events = "🟢 Вкл" if user["events_on"] else "🔴 Выкл"
    rars = user["rarities"].replace(",", ", ") if user["rarities"] else "Не выбраны"
    locs = "Все локации" if user["locations"] == "ALL" else user["locations"].replace(",", ", ")
    badge = vip_badge(user.get("vip_level") or 0)
    return (
        "🥚 <b>Меню уведомлений Steal An Egg</b>\n\n"
        f"⭐ <b>Статус:</b> {badge}\n"
        f"🔔 <b>Уведомления:</b> {status}\n"
        f"💎 <b>Редкости:</b> {rars}\n"
        f"🏛 <b>Локации:</b> {locs}\n"
        f"🎉 <b>События (Tidal/Бабочки/Рынок):</b> {events}\n\n"
        "Настрой фильтры ниже — бот сразу пришлёт нужное яйцо в личку."
    )


def main_menu_kb(user: dict) -> InlineKeyboardMarkup:
    alert_btn = (
        make_btn("🔕 Выключить уведомления", callback_data="toggle:alerts", style="danger")
        if user["alerts_on"]
        else make_btn("🔔 Включить уведомления", callback_data="toggle:alerts", style="success")
    )
    event_btn = (
        make_btn("🎉 События: ВКЛ", callback_data="toggle:events", style="success")
        if user["events_on"]
        else make_btn("🎉 События: ВЫКЛ", callback_data="toggle:events", style="danger")
    )
    webapp_url = build_webapp_url(user)
    if webapp_url:
        vip_btn = make_btn("💎 VIP и очередь алертов", web_app=WebAppInfo(url=webapp_url), style="success")
    else:
        vip_btn_text = (
            "👑 Мой VIP и Рефералы" if (user.get("vip_level") or 0) > 0 else "👑 Получить VIP (без задержки)"
        )
        vip_btn = make_btn(vip_btn_text, callback_data="menu:vip")

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [make_btn("📦 Последний сток", callback_data="stock:all", style="primary")],
            [
                make_btn("💎 Редкости", callback_data="menu:rarities"),
                make_btn("🏛 Локации", callback_data="menu:locations"),
            ],
            [vip_btn],
            [event_btn],
            [alert_btn],
        ]
    )


def vip_promo_text() -> str:
    return (
        "🆕 <b>Обновили VIP!</b>\n\n"
        "Теперь всё про VIP — в одном удобном окне 💎\n\n"
        "<b>Внутри видно:</b>\n"
        "• кто получает алерты о яйцах <b>раньше</b> — и где в этой очереди стоишь ты;\n"
        f"• 💎 <b>Платный VIP</b> — алерты раньше всех, <b>{VIP_PRICE_STARS} ⭐</b> один раз и навсегда;\n"
        f"• ⭐ <b>Бесплатный VIP</b> — пригласи <b>{REF_TARGET} друзей</b>, и алерты будут приходить быстрее, чем обычным;\n"
        "• сколько друзей ты уже пригласил.\n\n"
        "Жми кнопку «💎 VIP» внизу 👇"
    )


def vip_menu_text(user: dict) -> str:
    vip_level = user.get("vip_level") or 0
    ref_count = user.get("ref_count") or 0
    ref_link = f"https://t.me/{BOT_USERNAME}?start=r{user['user_id']}"
    bar = progress_bar(ref_count, REF_TARGET)

    return (
        "👑 <b>VIP-доступ и Реферальная программа</b>\n\n"
        f"<b>Твой статус:</b> {vip_badge(vip_level)}\n\n"
        "<b>Что даёт VIP:</b>\n"
        "• Мгновенные уведомления без задержки\n"
        "• Приоритет в очереди рассылки — заходи на сервер первым\n\n"
        "<b>Как получить VIP:</b>\n"
        f"1️⃣ Купить мгновенно за <b>{VIP_PRICE_STARS} ⭐ Telegram Stars</b> (1-й приоритет навсегда)\n"
        f"2️⃣ Бесплатно — пригласи <b>{REF_TARGET} друзей</b> по своей ссылке (друг засчитывается после подписки на {REQUIRED_CHANNEL})\n\n"
        f"<b>Приглашено друзей:</b> {ref_count} из {REF_TARGET}\n"
        f"{bar}\n\n"
        f"🔗 <b>Твоя ссылка:</b>\n<code>{ref_link}</code>"
    )


def vip_menu_kb(user: dict) -> InlineKeyboardMarkup:
    vip_level = user.get("vip_level") or 0
    ref_link = f"https://t.me/{BOT_USERNAME}?start=r{user['user_id']}"
    share_text = quote("Лови редкие Divine и Eternal яйца в Steal An Egg первым!")
    share_url = f"https://t.me/share/url?url={quote(ref_link)}&text={share_text}"
    webapp_url = build_webapp_url(user)

    rows = []
    if webapp_url:
        rows.append([make_btn("💎 Открыть окно VIP", web_app=WebAppInfo(url=webapp_url), style="success")])
    elif vip_level < 2:
        rows.append(
            [make_btn(f"⭐ Купить VIP за {VIP_PRICE_STARS} Stars", callback_data="buy:vip", style="success")]
        )
    rows.append([make_btn("📤 Отправить ссылку другу", url=share_url, style="primary")])
    rows.append([make_btn("📄 Условия покупки VIP", callback_data="menu:terms")])
    rows.append([make_btn("⬅️ Назад в меню", callback_data="menu:main")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def terms_text() -> str:
    return (
        "📄 <b>Условия покупки VIP</b>\n\n"
        f"1. <b>Стоимость:</b> {VIP_PRICE_STARS} ⭐ (Telegram Stars), разовый платёж.\n"
        "2. <b>Срок действия:</b> навсегда, на всё время работы бота.\n"
        "3. <b>Что входит:</b> мгновенная отправка уведомлений без стандартной задержки "
        "и первый приоритет в очереди рассылки (даже раньше бесплатных VIP).\n"
        "4. <b>Активация:</b> происходит автоматически сразу после подтверждения оплаты Telegram.\n"
        "5. <b>Возврат и помощь:</b> если оплата прошла, но VIP не включился или возникла ошибка — "
        "напиши команду <code>/paysupport</code> и опиши проблему. Владелец бота (@wartrezlox) проверит "
        "платёж и активирует доступ вручную либо оформит возврат звёзд."
    )


def terms_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [make_btn("👑 Перейти к VIP", callback_data="menu:vip", style="primary")],
            [make_btn("⬅️ Назад в меню", callback_data="menu:main")],
        ]
    )


def rarities_kb(user: dict) -> InlineKeyboardMarkup:
    chosen = parse_csv_set(user["rarities"])
    rows = []
    for i in range(0, len(ALL_RARITIES), 2):
        row = []
        for r in ALL_RARITIES[i : i + 2]:
            mark = "✅" if r in chosen else "❌"
            row.append(make_btn(f"{mark} {r}", callback_data=f"rar:{r}"))
        rows.append(row)
    rows.append([make_btn("⬅️ Назад в меню", callback_data="menu:main")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def locations_kb(user: dict) -> InlineKeyboardMarkup:
    is_all = user["locations"] == "ALL"
    chosen = set() if is_all else parse_csv_set(user["locations"])
    rows = [
        [
            make_btn(
                f"{'✅' if is_all else '⬜'} Все локации",
                callback_data="loc:ALL",
            )
        ]
    ]
    for i in range(0, len(ALL_LOCATIONS), 2):
        row = []
        for loc in ALL_LOCATIONS[i : i + 2]:
            mark = "✅" if (is_all or loc in chosen) else "❌"
            row.append(make_btn(f"{mark} {loc}", callback_data=f"loc:{loc}"))
        rows.append(row)
    rows.append([make_btn("⬅️ Назад в меню", callback_data="menu:main")])
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

    title = "📦 <b>Последний сток</b>" + ("" if loc_filter == "all" else f" ({html.escape(loc_filter)})")
    if not rows:
        body = "\n\nПока записей нет — как только появится спавн, он отобразится здесь."
    else:
        lines = []
        for r in rows:
            ago = time_ago(r["spawn_ts"])
            loc_clean = html.escape(clean_field(r["location"]))
            name_clean = html.escape(clean_field(r["name"]))
            money_clean = html.escape(clean_field(r["money"]))
            if r["ctype"] == "egg":
                rar = f"[{html.escape(r['rarity'])}] " if r["rarity"] else ""
                lines.append(
                    f"🥚 <b>{rar}{name_clean}</b> — {loc_clean} | {money_clean} <i>({ago})</i>"
                )
            elif r["ctype"] == "tidal":
                lines.append(f"🌊 <b>Tidal Surge</b> — {loc_clean} <i>({ago})</i>")
            elif r["ctype"] == "butterfly":
                lines.append(f"🦋 <b>Бабочки</b> — {loc_clean} <i>({ago})</i>")
            elif r["ctype"] == "market":
                lines.append(f"🛒 <b>Slick Rick</b> — {loc_clean} <i>({ago})</i>")
        body = "\n\n" + "\n".join(lines)

    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                make_btn("🌍 Все", callback_data="stock:all"),
                make_btn("👽 Alien", callback_data="stock:Alien"),
                make_btn("🌲 Forest", callback_data="stock:Enchanted"),
            ],
            [
                make_btn("🔄 Обновить", callback_data=f"stock:{loc_filter}", style="primary"),
                make_btn("⬅️ Назад", callback_data="menu:main"),
            ],
        ]
    )
    return title + body, kb


# === ОБРАБОТЧИКИ КОМАНД МЕНЮ И АДМИНКИ ===
@router.message(F.photo, F.caption.in_({"/setphoto", "/photo"}))
async def cmd_set_welcome_photo(message: Message):
    if message.from_user.id not in ADMIN_IDS:
        return
    fid = message.photo[-1].file_id
    await set_setting("welcome_photo", fid)
    await message.answer(
        "✅ <b>Приветственный баннер установлен!</b>\n\n"
        "Нажми /start, чтобы проверить, как он выглядит."
    )


@router.message(Command("stats", "stat"))
async def cmd_admin_stats(message: Message):
    if message.from_user.id not in ADMIN_IDS:
        return
    text, kb = await render_stats_screen()
    await message.answer(text, reply_markup=kb, disable_web_page_preview=True)


@router.callback_query(F.data == "admin:stats")
async def cb_admin_stats(cb: CallbackQuery):
    if cb.from_user.id not in ADMIN_IDS:
        return
    text, kb = await render_stats_screen()
    await safe_edit(cb, text, kb)
    await cb.answer("Статистика обновлена")


@router.message(Command("sendupdate"))
async def cmd_send_update(message: Message):
    if message.from_user.id not in ADMIN_IDS:
        return
    users = await get_all_users()
    preview = (
        f"👀 <b>Предпросмотр рассылки об обновлении</b> (получателей: <b>{len(users)}</b>):\n"
        "──────────────\n"
        f"{vip_promo_text()}\n"
        "──────────────\n"
        "<i>Вместе с сообщением у всех обновится нижняя клавиатура с кнопкой 💎 VIP.</i>"
    )
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [make_btn(f"🚀 Разослать всем ({len(users)})", callback_data="admin:bcast_update", style="success")],
            [make_btn("❌ Отмена", callback_data="admin:bcast_cancel", style="danger")],
        ]
    )
    await message.answer(preview, reply_markup=kb, disable_web_page_preview=True)


@router.message(Command("broadcast"))
async def cmd_broadcast(message: Message, command: CommandObject):
    if message.from_user.id not in ADMIN_IDS:
        return
    text = (command.args or "").strip()
    if not text:
        await message.answer(
            "📢 <b>Рассылка произвольного сообщения</b>\n\n"
            "Напиши команду и текст в одном сообщении, например:\n"
            "<code>/broadcast 🔥 Новый розыгрыш уже в канале @wartrezlox!</code>"
        )
        return

    PENDING_BROADCAST[message.from_user.id] = text
    users = await get_all_users()
    preview = (
        f"👀 <b>Предпросмотр рассылки</b> (получателей: <b>{len(users)}</b>):\n"
        "──────────────\n"
        f"{text}\n"
        "──────────────"
    )
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [make_btn(f"🚀 Разослать всем ({len(users)})", callback_data="admin:bcast_custom", style="success")],
            [make_btn("❌ Отмена", callback_data="admin:bcast_cancel", style="danger")],
        ]
    )
    try:
        await message.answer(preview, reply_markup=kb, disable_web_page_preview=True)
    except TelegramBadRequest as e:
        await message.answer(f"❌ Ошибка разметки HTML в тексте: {e}")


@router.callback_query(F.data == "admin:bcast_update")
async def cb_bcast_update(cb: CallbackQuery, bot: Bot):
    if cb.from_user.id not in ADMIN_IDS:
        return
    await safe_edit(cb, "⏳ <b>Рассылка об обновлении запущена...</b>\nОтчёт придёт сюда по завершении.", None)
    await cb.answer("Рассылка запущена")
    asyncio.create_task(run_mass_broadcast(bot, cb.from_user.id, vip_promo_text(), refresh_kb=True))


@router.callback_query(F.data == "admin:bcast_custom")
async def cb_bcast_custom(cb: CallbackQuery, bot: Bot):
    if cb.from_user.id not in ADMIN_IDS:
        return
    text = PENDING_BROADCAST.pop(cb.from_user.id, "")
    if not text:
        await cb.answer("Текст рассылки не найден, отправь /broadcast заново", show_alert=True)
        return
    await safe_edit(cb, "⏳ <b>Рассылка запущена...</b>\nОтчёт придёт сюда по завершении.", None)
    await cb.answer("Рассылка запущена")
    asyncio.create_task(run_mass_broadcast(bot, cb.from_user.id, text, refresh_kb=True))


@router.callback_query(F.data == "admin:bcast_cancel")
async def cb_bcast_cancel(cb: CallbackQuery):
    if cb.from_user.id not in ADMIN_IDS:
        return
    PENDING_BROADCAST.pop(cb.from_user.id, None)
    await safe_edit(cb, "❌ Рассылка отменена.", None)
    await cb.answer("Отменено")


@router.message(CommandStart())
async def cmd_start(message: Message, command: CommandObject, bot: Bot):
    uid = message.from_user.id
    user = await get_or_create_user(uid)

    args = command.args
    if args and args.startswith("r") and args[1:].isdigit():
        ref_id = int(args[1:])
        if ref_id != uid and not user.get("referrer_id"):
            await update_user(uid, referrer_id=ref_id)
            user["referrer_id"] = ref_id

    sub = await is_subscribed(bot, uid)
    if sub:
        await try_credit_referral(bot, uid)
        user = await get_or_create_user(uid)

    caption = welcome_text(sub)
    kb = welcome_kb(sub)
    photo = await get_welcome_photo()

    sent_welcome = False
    if photo:
        try:
            await message.answer_photo(photo=photo, caption=caption, reply_markup=kb)
            sent_welcome = True
        except Exception as e:
            print(f"⚠️ Не удалось отправить приветственное фото: {e}", flush=True)

    if not sent_welcome:
        await message.answer(caption, reply_markup=kb, disable_web_page_preview=True)

    if sub:
        await message.answer(
            "👇 Быстрое меню и окно <b>💎 VIP</b> закреплены внизу:",
            reply_markup=bottom_kb(user),
        )


@router.message(Command("stock"))
@router.message(F.text == "📦 Последний сток")
async def cmd_stock(message: Message, bot: Bot):
    if not await is_subscribed(bot, message.from_user.id):
        await message.answer(welcome_text(False), reply_markup=welcome_kb(False), disable_web_page_preview=True)
        return
    text, kb = await render_stock_screen("all")
    await message.answer(text, reply_markup=kb, disable_web_page_preview=True)


@router.message(F.text == "🔔 Уведомления")
async def kb_open_menu(message: Message, bot: Bot):
    uid = message.from_user.id
    if not await is_subscribed(bot, uid):
        await message.answer(welcome_text(False), reply_markup=welcome_kb(False), disable_web_page_preview=True)
        return
    user = await get_or_create_user(uid)
    await message.answer(main_menu_text(user), reply_markup=main_menu_kb(user), disable_web_page_preview=True)


@router.message(F.text == "💎 Редкости")
async def kb_open_rarities(message: Message, bot: Bot):
    uid = message.from_user.id
    if not await is_subscribed(bot, uid):
        await message.answer(welcome_text(False), reply_markup=welcome_kb(False), disable_web_page_preview=True)
        return
    user = await get_or_create_user(uid)
    await message.answer(
        "💎 <b>Выбери редкости яиц</b>\n\nНажимай на кнопки, чтобы включить или выключить уведомления:",
        reply_markup=rarities_kb(user),
    )


@router.message(F.text == "🏛 Локации")
async def kb_open_locations(message: Message, bot: Bot):
    uid = message.from_user.id
    if not await is_subscribed(bot, uid):
        await message.answer(welcome_text(False), reply_markup=welcome_kb(False), disable_web_page_preview=True)
        return
    user = await get_or_create_user(uid)
    await message.answer(
        "🏛 <b>Выбери локации</b>\n\nМожешь оставить «Все локации» или выбрать только нужные:",
        reply_markup=locations_kb(user),
    )


@router.message(Command("vip"))
@router.message(F.text == "💎 VIP")
async def cmd_vip(message: Message, bot: Bot):
    uid = message.from_user.id
    if not await is_subscribed(bot, uid):
        await message.answer(welcome_text(False), reply_markup=welcome_kb(False), disable_web_page_preview=True)
        return
    user = await get_or_create_user(uid)
    webapp_url = build_webapp_url(user)
    if webapp_url:
        await message.answer(
            vip_promo_text(),
            reply_markup=bottom_kb(user),
            disable_web_page_preview=True,
        )
    else:
        await message.answer(vip_menu_text(user), reply_markup=vip_menu_kb(user), disable_web_page_preview=True)


@router.message(F.web_app_data)
async def on_web_app_data(message: Message):
    if message.web_app_data.data == "buy_vip":
        user = await get_or_create_user(message.from_user.id)
        if (user.get("vip_level") or 0) == 2:
            await message.answer("👑 У тебя уже активирован максимальный VIP!")
            return
        await message.answer_invoice(
            title="👑 VIP-доступ навсегда",
            description="Мгновенные уведомления без задержки и первый приоритет в очереди рассылки Steal An Egg.",
            payload="vip_stars_50",
            provider_token="",
            currency="XTR",
            prices=[LabeledPrice(label="VIP-доступ навсегда", amount=VIP_PRICE_STARS)],
        )


@router.message(Command("terms"))
async def cmd_terms(message: Message):
    await message.answer(terms_text(), reply_markup=terms_kb(), disable_web_page_preview=True)


@router.message(Command("paysupport"))
async def cmd_paysupport(message: Message, command: CommandObject, bot: Bot):
    text = (command.args or "").strip()
    if not text:
        await message.answer(
            "🆘 <b>Поддержка по оплате</b>\n\n"
            "Напиши одним сообщением: команду и что случилось, например:\n"
            "<code>/paysupport оплатил, но VIP не включился</code>\n\n"
            "Я передам владельцу бота — он ответит тебе здесь, в боте."
        )
        return

    uid = message.from_user.id
    uname = f"@{message.from_user.username}" if message.from_user.username else "без юзернейма"
    admin_msg = (
        "🆘 <b>Обращение в поддержку по оплате</b>\n\n"
        f"👤 От: {html.escape(message.from_user.full_name)} ({uname})\n"
        f"🆔 ID: <code>{uid}</code>\n"
        f"💬 Сообщение:\n<blockquote>{html.escape(text)}</blockquote>\n\n"
        f"Чтобы ответить пользователю через бота, напиши:\n"
        f"<code>/reply {uid} твой ответ</code>"
    )
    for admin_id in ADMIN_IDS:
        try:
            await bot.send_message(admin_id, admin_msg, disable_web_page_preview=True)
        except Exception as e:
            print(f"⚠️ Ошибка отправки поддержки админу {admin_id}: {e}", flush=True)

    await message.answer(
        "✅ <b>Сообщение передано владельцу бота!</b>\n"
        "Ожидай — ответ придёт прямо сюда, в этот чат."
    )


@router.message(Command("reply"))
async def cmd_admin_reply(message: Message, command: CommandObject, bot: Bot):
    if message.from_user.id not in ADMIN_IDS:
        return
    args = (command.args or "").strip()
    parts = args.split(maxsplit=1)
    if len(parts) < 2 or not parts[0].isdigit():
        await message.answer("Формат ответа: <code>/reply ID_ПОЛЬЗОВАТЕЛЯ текст ответа</code>")
        return
    target_id = int(parts[0])
    reply_text = parts[1]
    try:
        await bot.send_message(
            target_id,
            f"💬 <b>Ответ от поддержки:</b>\n\n{html.escape(reply_text)}",
            disable_web_page_preview=True,
        )
        await message.answer(f"✅ Ответ отправлен пользователю <code>{target_id}</code>.")
    except Exception as e:
        await message.answer(f"❌ Не удалось отправить: {e}")


@router.message(Command("givevip"))
async def cmd_give_vip(message: Message, command: CommandObject, bot: Bot):
    if message.from_user.id not in ADMIN_IDS:
        return
    arg = (command.args or "").strip()
    if arg and not arg.isdigit():
        await message.answer("Формат: <code>/givevip</code> (себе) или <code>/givevip ID</code>")
        return
    target_id = int(arg) if arg else message.from_user.id
    await get_or_create_user(target_id)
    await update_user(target_id, vip_level=2)
    target_user = await get_or_create_user(target_id)
    await message.answer(
        f"👑 Выдан максимальный VIP пользователю <code>{target_id}</code>.",
        reply_markup=bottom_kb(target_user) if target_id == message.from_user.id else None,
    )
    if target_id != message.from_user.id:
        try:
            await bot.send_message(
                target_id,
                "👑 <b>Администратор выдал тебе VIP-доступ навсегда!</b>\n\n"
                "Теперь все уведомления приходят тебе первым в очереди, без задержки.",
                reply_markup=bottom_kb(target_user),
            )
        except Exception:
            pass


@router.message(Command("delvip"))
async def cmd_del_vip(message: Message, command: CommandObject):
    if message.from_user.id not in ADMIN_IDS:
        return
    arg = (command.args or "").strip()
    if arg and not arg.isdigit():
        await message.answer("Формат: <code>/delvip</code> (у себя) или <code>/delvip ID</code>")
        return
    target_id = int(arg) if arg else message.from_user.id
    await get_or_create_user(target_id)
    await update_user(target_id, vip_level=0)
    target_user = await get_or_create_user(target_id)
    await message.answer(
        f"👤 VIP снят у пользователя <code>{target_id}</code>.",
        reply_markup=bottom_kb(target_user) if target_id == message.from_user.id else None,
    )


# === ОБРАБОТЧИКИ КНОПОК ===
@router.callback_query(F.data == "check:sub")
async def cb_check_sub(cb: CallbackQuery, bot: Bot):
    uid = cb.from_user.id
    if not await is_subscribed(bot, uid):
        await cb.answer(f"❌ Подписка на {REQUIRED_CHANNEL} не найдена!", show_alert=True)
        return

    await try_credit_referral(bot, uid)
    user = await get_or_create_user(uid)
    await safe_edit(cb, main_menu_text(user), main_menu_kb(user))
    await cb.message.answer(
        "👇 Быстрое меню и окно <b>💎 VIP</b> закреплены внизу:",
        reply_markup=bottom_kb(user),
    )
    await cb.answer("✅ Подписка подтверждена!")


@router.callback_query(F.data == "menu:main")
async def cb_main(cb: CallbackQuery):
    user = await get_or_create_user(cb.from_user.id)
    await safe_edit(cb, main_menu_text(user), main_menu_kb(user))
    await cb.answer()


@router.callback_query(F.data == "menu:vip")
async def cb_vip(cb: CallbackQuery):
    user = await get_or_create_user(cb.from_user.id)
    await safe_edit(cb, vip_menu_text(user), vip_menu_kb(user))
    await cb.answer()


@router.callback_query(F.data == "open:vip")
async def cb_open_vip_new_msg(cb: CallbackQuery):
    user = await get_or_create_user(cb.from_user.id)
    webapp_url = build_webapp_url(user)
    if webapp_url:
        kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [make_btn("💎 Открыть окно VIP", web_app=WebAppInfo(url=webapp_url), style="success")]
            ]
        )
        await cb.message.answer(vip_promo_text(), reply_markup=kb, disable_web_page_preview=True)
    else:
        await cb.message.answer(
            vip_menu_text(user),
            reply_markup=vip_menu_kb(user),
            disable_web_page_preview=True,
        )
    await cb.answer()


@router.callback_query(F.data == "menu:terms")
async def cb_terms(cb: CallbackQuery):
    await safe_edit(cb, terms_text(), terms_kb())
    await cb.answer()


@router.callback_query(F.data == "buy:vip")
async def cb_buy_vip(cb: CallbackQuery):
    user = await get_or_create_user(cb.from_user.id)
    if (user.get("vip_level") or 0) == 2:
        await cb.answer("👑 У тебя уже активирован максимальный VIP!", show_alert=True)
        return

    await cb.message.answer_invoice(
        title="👑 VIP-доступ навсегда",
        description="Мгновенные уведомления без задержки и первый приоритет в очереди рассылки Steal An Egg.",
        payload="vip_stars_50",
        provider_token="",
        currency="XTR",
        prices=[LabeledPrice(label="VIP-доступ навсегда", amount=VIP_PRICE_STARS)],
    )
    await cb.answer()


@router.pre_checkout_query()
async def on_pre_checkout(query: PreCheckoutQuery):
    await query.answer(ok=True)


@router.message(F.successful_payment)
async def on_successful_payment(message: Message, bot: Bot):
    payment = message.successful_payment
    if payment.invoice_payload == "vip_stars_50":
        uid = message.from_user.id
        await update_user(uid, vip_level=2)
        await record_payment(uid, payment.total_amount, payment.currency)
        user = await get_or_create_user(uid)
        await message.answer(
            "👑 <b>Оплата прошла успешно! Платный VIP активирован навсегда.</b>\n\n"
            "Теперь все уведомления приходят тебе в самом начале очереди, без задержки.",
            reply_markup=bottom_kb(user),
            disable_web_page_preview=True,
        )

        uname = f"@{message.from_user.username}" if message.from_user.username else "без юзернейма"
        admin_text = (
            "⭐ <b>Новая покупка VIP!</b>\n\n"
            f"👤 Пользователь: {html.escape(message.from_user.full_name)} ({uname})\n"
            f"🆔 ID: <code>{uid}</code>\n"
            f"💰 Сумма: <b>{payment.total_amount} {payment.currency}</b>"
        )
        for admin_id in ADMIN_IDS:
            try:
                await bot.send_message(admin_id, admin_text, disable_web_page_preview=True)
            except Exception as e:
                print(f"⚠️ Не удалось уведомить админа {admin_id}: {e}", flush=True)


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
        val = clean_field(r_match.group(1)).lower()
        for key, label in RARITIES_MAP:
            if key in val:
                return label
        return clean_field(r_match.group(1)).capitalize()

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
        loc = clean_field(where_match.group(1)) if where_match else "Alien Planet"
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
        egg_name = clean_field(egg_match.group(1))
        loc_match = re.search(r"(?:Location|Where):\*?\*?\s*([^\n]+)", clean_raw, re.IGNORECASE)
        loc = clean_field(loc_match.group(1)) if loc_match else "Unknown"
        money_match = re.search(r"(?:Money|Value|Income):\*?\*?\s*([^\n]+)", clean_raw, re.IGNORECASE)
        if money_match:
            money = clean_field(money_match.group(1))
        else:
            m_alt = re.search(r"\$\d+[a-zA-Z]*(?:/s|/sec)?", clean_raw, re.IGNORECASE)
            money = m_alt.group(0) if m_alt else "—"
        speed_match = re.search(r"(?:Recommended Speed|Speed):\*?\*?\s*([^\n]+)", clean_raw, re.IGNORECASE)
        speed = clean_field(speed_match.group(1)) if speed_match else "—"
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
        loc = clean_field(where_match.group(1)) if where_match else "Enchanted Forest"
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


# === ФОРМАТИРОВАНИЕ УВЕДОМЛЕНИЙ ===
def format_post(item: dict, delay_sec: int | None = None) -> str:
    msk_time = fmt_msk(item["spawn_ts"])
    name = html.escape(item["name"])
    loc = html.escape(item["location"])
    money = html.escape(item["money"])
    speed = html.escape(item["speed"])
    rarity = item["rarity"]

    vip_note = ""
    if delay_sec is not None:
        if item["ctype"] == "egg":
            vip_note = f"\n\n⭐ <i>VIP узнали об этом яйце на {delay_sec} сек раньше</i>"
        else:
            vip_note = f"\n\n⭐ <i>VIP узнали об этом событии на {delay_sec} сек раньше</i>"

    if item["ctype"] == "egg":
        sq = RARITY_SQUARES.get(rarity, "⬜")
        rar_label = html.escape(rarity.upper()) if rarity else "NEW EGG"
        return (
            "🔔 <b>Новое яйцо!</b>\n\n"
            f"{sq} <b>{rar_label}</b>\n"
            f"🥚 <b>{name}</b>\n\n"
            "<blockquote>"
            f"📍 Локация: 🏛 {loc}\n"
            f"💰 Доход: {money}\n"
            f"⚡ Скорость: {speed}"
            "</blockquote>"
            f"{vip_note}"
        )

    if item["ctype"] == "tidal":
        return (
            "🔔 <b>Новое событие!</b>\n\n"
            "🟦 <b>TIDAL SURGE</b>\n"
            "🌊 <b>Затопление началось</b>\n\n"
            "<blockquote>"
            f"📍 Локация: 🏛 {loc}\n"
            "💧 Инфо: Доступны редкие водные яйца\n"
            f"⏰ Время: {msk_time}"
            "</blockquote>"
            f"{vip_note}"
        )

    if item["ctype"] == "butterfly":
        return (
            "🔔 <b>Новое событие!</b>\n\n"
            "🟪 <b>BUTTERFLY EVENT</b>\n"
            "🦋 <b>Ивент бабочек начался</b>\n\n"
            "<blockquote>"
            f"📍 Локация: 🏛 {loc}\n"
            "🎯 Инфо: Ловите бабочек сачком\n"
            f"⏰ Время: {msk_time}"
            "</blockquote>"
            f"{vip_note}"
        )

    return (
        "🔔 <b>Новое событие!</b>\n\n"
        "🟧 <b>BLACK MARKET</b>\n"
        "🛒 <b>Торговец Slick Rick прибыл</b>\n\n"
        "<blockquote>"
        f"📍 Локация: 🏛 {loc}\n"
        "📦 Товары: Контрабанда и мутации\n"
        f"⚡ Скорость: {speed}\n"
        f"⏰ Время: {msk_time}"
        "</blockquote>"
        f"{vip_note}"
    )


def alert_kb(item: dict, is_vip: bool) -> InlineKeyboardMarkup | None:
    rows = []
    if item.get("link_url"):
        rows.append([make_btn("🎮 Зайти в игру", url=item["link_url"], style="success")])
    if not is_vip:
        rows.append([make_btn("⭐ Хочу VIP", callback_data="open:vip")])
    return InlineKeyboardMarkup(inline_keyboard=rows) if rows else None


async def delete_later(bot: Bot, chat_id: str, msg_id: int):
    await asyncio.sleep(900)
    try:
        await bot.delete_message(chat_id, msg_id)
    except Exception:
        pass


def user_matches_item(u: dict, item: dict) -> bool:
    if item["ctype"] == "egg":
        user_rars = parse_csv_set(u["rarities"])
        if item["rarity"] not in user_rars:
            return False
        if u["locations"] != "ALL":
            user_locs = parse_csv_set(u["locations"])
            if not any(loc.lower() in item["location"].lower() for loc in user_locs):
                return False
        return True
    return bool(u["events_on"])


async def send_batch(bot: Bot, users: list[dict], text: str, markup: InlineKeyboardMarkup | None):
    for u in users:
        try:
            await bot.send_message(
                u["user_id"],
                text,
                reply_markup=markup,
                disable_web_page_preview=True,
            )
        except TelegramForbiddenError:
            await update_user(u["user_id"], alerts_on=0)
        except Exception as e:
            print(f"⚠️ Ошибка отправки {u['user_id']}: {e}", flush=True)
        await asyncio.sleep(0.05)


async def broadcast_item(bot: Bot, item: dict):
    vip_text = format_post(item, delay_sec=None)
    vip_markup = alert_kb(item, is_vip=True)

    # 1. Каналы (только Eternal/Divine яйца + все события, с удалением через 15 мин)
    if TG_CHANNELS and (item["ctype"] != "egg" or item["rarity"] in {"Divine", "Eternal"}):
        for ch in TG_CHANNELS:
            try:
                msg = await bot.send_message(
                    ch,
                    vip_text,
                    reply_markup=vip_markup,
                    disable_web_page_preview=True,
                )
                asyncio.create_task(delete_later(bot, ch, msg.message_id))
            except Exception as e:
                print(f"❌ Ошибка канала {ch}: {e}", flush=True)

    # 2. Персональная рассылка по очереди: Платный VIP -> Бесплатный VIP -> Обычные
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM users WHERE alerts_on = 1") as cur:
            all_users = [dict(r) for r in await cur.fetchall()]

    vip_paid, vip_ref, free_users = [], [], []
    for u in all_users:
        if not user_matches_item(u, item):
            continue
        lvl = u.get("vip_level") or 0
        if lvl == 2:
            vip_paid.append(u)
        elif lvl == 1:
            vip_ref.append(u)
        else:
            free_users.append(u)

    if vip_paid:
        await send_batch(bot, vip_paid, vip_text, vip_markup)
    if vip_ref:
        await asyncio.sleep(REF_VIP_DELAY)
        await send_batch(bot, vip_ref, vip_text, vip_markup)
    if free_users:
        remain = max(0, FREE_USER_DELAY - (REF_VIP_DELAY if vip_ref else 0))
        if remain:
            await asyncio.sleep(remain)
        free_text = format_post(item, delay_sec=VIP_EARLY_TEXT_SEC)
        free_markup = alert_kb(item, is_vip=False)
        await send_batch(bot, free_users, free_text, free_markup)


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
            await asyncio.sleep(0.3)

        if not token:
            print("❌ Не подошёл ни один токен Discord!", flush=True)
            return

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

                        if not first_fill:
                            print(
                                f"📢 Новый спавн: {item['name']} ({item['rarity'] or item['ctype']})",
                                flush=True,
                            )
                            asyncio.create_task(broadcast_item(bot, item))

                except Exception as e:
                    print(f"⚠️ Ошибка канала {cid}: {e}", flush=True)
                await asyncio.sleep(1.5)

            first_fill = False
            await asyncio.sleep(6)


async def setup_bot_menu(bot: Bot):
    await bot.set_my_commands(
        [
            BotCommand(command="start", description="Главное меню"),
            BotCommand(command="stock", description="Показать последний сток"),
            BotCommand(command="vip", description="VIP: алерты первыми"),
            BotCommand(command="terms", description="Условия покупки VIP"),
            BotCommand(command="paysupport", description="Поддержка по оплате"),
        ]
    )
    await bot.set_chat_menu_button(menu_button=MenuButtonCommands())


async def main():
    global BOT_USERNAME
    await init_db()
    bot = Bot(token=TG_BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    me = await bot.get_me()
    BOT_USERNAME = me.username or ""

    await setup_bot_menu(bot)
    await ensure_vip_invoice_url(bot)

    dp = Dispatcher()
    dp.include_router(router)

    watcher_task = asyncio.create_task(discord_watcher(bot))
    print(f"🚀 Бот @{BOT_USERNAME} запущен!", flush=True)
    try:
        await dp.start_polling(bot)
    finally:
        watcher_task.cancel()
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
