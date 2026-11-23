import asyncio
import html
import itertools
import os
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import quote

import aiohttp
import aiosqlite
from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiogram.filters import CommandObject, CommandStart
from aiogram.types import (
    CallbackQuery,
    FSInputFile,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    LabeledPrice,
    Message,
    PreCheckoutQuery,
)

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(line_buffering=True)

# === НАСТРОЙКИ ===
TG_BOT_TOKEN = "9209605005:AAEy_QfMY6SCCwR_I7a7kFo-TdnW2yKdGKY"

# Только эти Telegram ID могут менять фото приветствия и админские настройки
ADMIN_IDS = {1171395618}

# Можно оставить пустым — просто отправь боту фото с подписью /setphoto
WELCOME_PHOTO = ""

# Обязательный канал для подписки (бот должен быть там админом)
REQUIRED_CHANNEL = "@wartrezlox"
REQUIRED_CHANNEL_URL = "https://t.me/wartrezlox"

# Экономика VIP и рефералки
VIP_PRICE_STARS = 50
REF_TARGET = 7
FREE_USER_DELAY = 15  # задержка в секундах для обычных пользователей

# Каналы для автопостинга (впиши ["@wartrezlox", "@loqlux"], когда решишь отключить старого бота)
TG_CHANNELS = []

DATA_DIR = os.getenv("DATA_DIR", "/app/data")
try:
    os.makedirs(DATA_DIR, exist_ok=True)
except OSError:
    DATA_DIR = "."
DB_PATH = os.path.join(DATA_DIR, "bot.db")

BOT_USERNAME = ""

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
        async with db.execute(
            "SELECT ref_count, vip_level FROM users WHERE user_id = ?",
            (ref_id,),
        ) as cur:
            ref_row = await cur.fetchone()

        upgraded = False
        if ref_row and ref_row["ref_count"] >= REF_TARGET and (ref_row["vip_level"] or 0) == 0:
            await db.execute("UPDATE users SET vip_level = 1 WHERE user_id = ?", (ref_id,))
            upgraded = True
        await db.commit()

    if ref_row:
        count = ref_row["ref_count"]
        try:
            if upgraded:
                await bot.send_message(
                    ref_id,
                    f"🎉 <b>Поздравляем!</b> По твоей ссылке зашли {REF_TARGET} друзей — ты получил <b>бесплатный VIP</b>!\n\n"
                    "Теперь все уведомления приходят тебе <b>мгновенно, без задержки</b>.",
                )
            elif count < REF_TARGET:
                await bot.send_message(
                    ref_id,
                    f"👥 Друг запустил бота и подписался на канал!\n"
                    f"Твой прогресс до VIP: <b>{count} из {REF_TARGET}</b>.",
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
    return datetime.fromtimestamp(ts, timezone(timedelta(hours=3))).strftime("%H:%M МСК")


def parse_csv_set(raw: str) -> set[str]:
    return {x.strip() for x in raw.split(",") if x.strip()}


def vip_badge(vip_level: int) -> str:
    if vip_level == 2:
        return "👑 VIP (Stars — максимальный приоритет)"
    if vip_level == 1:
        return "⚡ VIP (Реферальный — без задержки)"
    return f"👤 Обычный (задержка {FREE_USER_DELAY} сек)"


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


async def safe_edit(cb: CallbackQuery, text: str, markup: InlineKeyboardMarkup):
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
                [InlineKeyboardButton(text="🚀 Открыть меню", callback_data="menu:main")],
                [InlineKeyboardButton(text="📢 Канал создателя", url=REQUIRED_CHANNEL_URL)],
            ]
        )
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="📢 Подписаться на @wartrezlox", url=REQUIRED_CHANNEL_URL)],
            [InlineKeyboardButton(text="✅ Я подписался — Начать", callback_data="check:sub")],
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
    alert_btn = "🔕 Выключить уведомления" if user["alerts_on"] else "🔔 Включить уведомления"
    event_btn = "🎉 События: ВКЛ" if user["events_on"] else "🎉 События: ВЫКЛ"
    vip_btn = "👑 Мой VIP и Рефералы" if (user.get("vip_level") or 0) > 0 else "👑 Получить VIP (без задержки)"
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="📦 Последний сток", callback_data="stock:all")],
            [
                InlineKeyboardButton(text="💎 Редкости", callback_data="menu:rarities"),
                InlineKeyboardButton(text="🏛 Локации", callback_data="menu:locations"),
            ],
            [InlineKeyboardButton(text=vip_btn, callback_data="menu:vip")],
            [InlineKeyboardButton(text=event_btn, callback_data="toggle:events")],
            [InlineKeyboardButton(text=alert_btn, callback_data="toggle:alerts")],
        ]
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
        f"• Мгновенные уведомления без задержки ({FREE_USER_DELAY} сек)\n"
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

    rows = []
    if vip_level < 2:
        rows.append(
            [InlineKeyboardButton(text=f"⭐ Купить VIP за {VIP_PRICE_STARS} Stars", callback_data="buy:vip")]
        )
    rows.append([InlineKeyboardButton(text="📤 Отправить ссылку другу", url=share_url)])
    rows.append([InlineKeyboardButton(text="⬅️ Назад в меню", callback_data="menu:main")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


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

    caption = welcome_text(sub)
    kb = welcome_kb(sub)
    photo = await get_welcome_photo()

    if photo:
        try:
            await message.answer_photo(photo=photo, caption=caption, reply_markup=kb)
            return
        except Exception as e:
            print(f"⚠️ Не удалось отправить приветственное фото: {e}", flush=True)

    await message.answer(caption, reply_markup=kb, disable_web_page_preview=True)


@router.callback_query(F.data == "check:sub")
async def cb_check_sub(cb: CallbackQuery, bot: Bot):
    uid = cb.from_user.id
    if not await is_subscribed(bot, uid):
        await cb.answer(f"❌ Подписка на {REQUIRED_CHANNEL} не найдена!", show_alert=True)
        return

    await try_credit_referral(bot, uid)
    user = await get_or_create_user(uid)
    await safe_edit(cb, main_menu_text(user), main_menu_kb(user))
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


@router.callback_query(F.data == "buy:vip")
async def cb_buy_vip(cb: CallbackQuery):
    user = await get_or_create_user(cb.from_user.id)
    if (user.get("vip_level") or 0) == 2:
        await cb.answer("👑 У тебя уже активирован максимальный VIP!", show_alert=True)
        return

    await cb.message.answer_invoice(
        title="👑 VIP-доступ навсегда",
        description=(
            f"Мгновенные уведомления без задержки ({FREE_USER_DELAY} сек) "
            "и первый приоритет в очереди рассылки Steal An Egg."
        ),
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
async def on_successful_payment(message: Message):
    if message.successful_payment.invoice_payload == "vip_stars_50":
        await update_user(message.from_user.id, vip_level=2)
        user = await get_or_create_user(message.from_user.id)
        await message.answer(
            "👑 <b>Оплата прошла успешно! VIP активирован навсегда.</b>\n\n"
            "Теперь все уведомления приходят тебе первым в очереди, без задержки.",
            reply_markup=main_menu_kb(user),
            disable_web_page_preview=True,
        )


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


def format_post(item: dict) -> str:
    msk_time = fmt_msk(item["spawn_ts"])
    name = html.escape(item["name"])
    loc = html.escape(item["location"])
    money = html.escape(item["money"])
    speed = html.escape(item["speed"])
    rarity = html.escape(item["rarity"])
    link_str = (
        f'\n\n🔗 <a href="{html.escape(item["link_url"], quote=True)}">Присоединиться к игре</a>'
        if item["link_url"]
        else ""
    )

    if item["ctype"] == "tidal":
        return (
            f"🌊 Затопление (TIDAL SURGE) началось!! -\n\n"
            f"🏛 Локация: {loc}\n"
            f"💧 Инфо: Доступны редкие водные яйца\n"
            f"⏰ Заспавнилось: {msk_time}"
            f"{link_str}"
        )
    if item["ctype"] == "egg":
        header = f"🔥 {rarity} Egg появилось!! -" if rarity else "🔥 Новое Egg появилось!! -"
        return (
            f"{header}\n\n"
            f"🥚 Яйцо: {name}\n"
            f"🏛 Локация: {loc}\n"
            f"⏰ Заспавнилось: {msk_time}\n"
            f"💰 Приносит: {money}\n"
            f"🏃 Рек. скорость: {speed}"
            f"{link_str}"
        )
    if item["ctype"] == "butterfly":
        return (
            f"🦋 Ивент бабочек начался!! -\n\n"
            f"🏛 Локация: {loc}\n"
            f"🎯 Инфо: Ловите бабочек сачком\n"
            f"⏰ Заспавнилось: {msk_time}"
            f"{link_str}"
        )
    return (
        f"🛒 Торговец Slick Rick прибыл!! -\n\n"
        f"🏛 Локация: {loc}\n"
        f"📦 Товары: Контрабанда и мутации\n"
        f"🏃 Рек. скорость: {speed}\n"
        f"⏰ Заспавнилось: {msk_time}"
        f"{link_str}"
    )


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


async def send_batch(bot: Bot, users: list[dict], text: str):
    for u in users:
        try:
            await bot.send_message(u["user_id"], text, disable_web_page_preview=True)
        except TelegramForbiddenError:
            await update_user(u["user_id"], alerts_on=0)
        except Exception as e:
            print(f"⚠️ Ошибка отправки {u['user_id']}: {e}", flush=True)
        await asyncio.sleep(0.05)


async def broadcast_item(bot: Bot, item: dict):
    text = format_post(item)

    if TG_CHANNELS and (item["ctype"] != "egg" or item["rarity"] in {"Divine", "Eternal"}):
        for ch in TG_CHANNELS:
            try:
                msg = await bot.send_message(ch, text, disable_web_page_preview=True)
                asyncio.create_task(delete_later(bot, ch, msg.message_id))
            except Exception as e:
                print(f"❌ Ошибка канала {ch}: {e}", flush=True)

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
        await send_batch(bot, vip_paid, text)
    if vip_ref:
        await send_batch(bot, vip_ref, text)
    if free_users:
        await asyncio.sleep(FREE_USER_DELAY)
        await send_batch(bot, free_users, text)


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
            await asyncio.sleep(3)


async def main():
    global BOT_USERNAME
    await init_db()
    bot = Bot(token=TG_BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    me = await bot.get_me()
    BOT_USERNAME = me.username or ""

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
