"""
FarmBot - Nuôi acc FB/IG có hoạt động thật + cấu hình session.
Chạy: DISPLAY=:99 python Bot.py
"""
import asyncio
import datetime
import logging
import os
import random
import sys
from pathlib import Path

import aiosqlite
from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup,
    KeyboardButton, Message, ReplyKeyboardMarkup,
)

try:
    from patchright.async_api import async_playwright
    ENGINE = "patchright"
except ImportError:
    from playwright.async_api import async_playwright
    ENGINE = "playwright"

# ==================== CONFIG ====================
BOT_TOKEN = os.getenv("BOT_TOKEN", "")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))

DATA_DIR = Path("./data")
PROFILES_DIR = Path("./profiles")
DATA_DIR.mkdir(exist_ok=True)
PROFILES_DIR.mkdir(exist_ok=True)
DB_PATH = DATA_DIR / "botfarm.sqlite"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    stream=sys.stdout)
log = logging.getLogger("botfarm")


# ==================== DATABASE ====================
async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.executescript("""
        CREATE TABLE IF NOT EXISTS accounts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            uid INTEGER, platform TEXT, cookie TEXT, proxy TEXT,
            status TEXT DEFAULT 'new',
            born_at TEXT, last_check TEXT, last_action TEXT);

        CREATE TABLE IF NOT EXISTS tasks (
            uid INTEGER PRIMARY KEY,
            platform TEXT DEFAULT 'both',
            qty INTEGER DEFAULT 3,
            delay INTEGER DEFAULT 10,
            running INTEGER DEFAULT 0,
            started_at TEXT,
            session_minutes INTEGER DEFAULT 15,
            max_likes INTEGER DEFAULT 10,
            max_scrolls INTEGER DEFAULT 20,
            max_stories INTEGER DEFAULT 5,
            rest_minutes INTEGER DEFAULT 30,
            total_sessions INTEGER DEFAULT 0);

        CREATE TABLE IF NOT EXISTS logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            uid INTEGER, account_id INTEGER,
            action TEXT, result TEXT, ts TEXT);

        CREATE TABLE IF NOT EXISTS session_count (
            uid INTEGER, account_id INTEGER,
            count INTEGER DEFAULT 0,
            PRIMARY KEY (uid, account_id));
        """)


async def db_exec(q, *a):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(q, a)
        await db.commit()


async def db_fetch(q, *a):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(q, a)
        return [dict(r) for r in await cur.fetchall()]


async def db_fetchone(q, *a):
    r = await db_fetch(q, *a)
    return r[0] if r else None


async def log_action(uid, acc_id, action, result):
    await db_exec(
        "INSERT INTO logs (uid,account_id,action,result,ts) VALUES (?,?,?,?,?)",
        uid, acc_id, action, result, datetime.datetime.now().isoformat())


async def get_session_count(uid, acc_id):
    r = await db_fetchone(
        "SELECT count FROM session_count WHERE uid=? AND account_id=?",
        uid, acc_id)
    return r["count"] if r else 0


async def bump_session_count(uid, acc_id):
    await db_exec(
        "INSERT INTO session_count (uid, account_id, count) VALUES (?,?,1) "
        "ON CONFLICT(uid, account_id) DO UPDATE SET count=count+1",
        uid, acc_id)
    return await get_session_count(uid, acc_id)


# ==================== SESSION ====================
class SessionManager:
    _pw = None
    _lock = asyncio.Lock()
    sessions = {}

    @classmethod
    async def pw(cls):
        async with cls._lock:
            if cls._pw is None:
                cls._pw = await async_playwright().start()
            return cls._pw

    @classmethod
    async def create(cls, slot, acc):
        pw = await cls.pw()
        prof = PROFILES_DIR / f"slot{slot}"
        prof.mkdir(parents=True, exist_ok=True)

        launch = {
            "user_data_dir": str(prof),
            "headless": False,
            "locale": "vi-VN",
            "timezone_id": "Asia/Ho_Chi_Minh",
            "viewport": {"width": 1366, "height": 768},
            "args": ["--disable-blink-features=AutomationControlled",
                     "--no-first-run", "--no-default-browser-check"],
        }
        if acc.get("proxy"):
            p = acc["proxy"].split(":")
            if len(p) >= 2:
                cfg = {"server": f"http://{p[0]}:{p[1]}"}
                if len(p) == 4:
                    cfg["username"], cfg["password"] = p[2], p[3]
                launch["proxy"] = cfg

        ctx = await pw.chromium.launch_persistent_context(**launch)
        await ctx.add_init_script("""
            Object.defineProperty(navigator,'webdriver',{get:()=>undefined});
            Object.defineProperty(navigator,'languages',{get:()=>['vi-VN','vi','en-US']});
            window.chrome={runtime:{}};
        """)
        page = ctx.pages[0] if ctx.pages else await ctx.new_page()

        ck = acc["cookie"]
        domain = "facebook.com" if acc["platform"] == "fb" else "instagram.com"
        if "=" in ck and ";" in ck:
            jar = []
            for item in ck.split(";"):
                item = item.strip()
                if "=" not in item:
                    continue
                n, v = item.split("=", 1)
                n, v = n.strip(), v.strip()
                if n:
                    jar.append({
                        "name": n, "value": v,
                        "domain": f".{domain}", "path": "/",
                        "secure": True, "httpOnly": False,
                        "sameSite": "None",
                    })
            if jar:
                try:
                    await ctx.add_cookies(jar)
                except Exception as e:
                    log.warning(f"cookie inject: {e}")

        s = {
            "context": ctx, "page": page, "platform": acc["platform"],
            "acc_id": acc["id"],
            "last_activity": datetime.datetime.now(),
            "born_at": acc.get("born_at") or datetime.datetime.now().isoformat(),
        }
        cls.sessions[slot] = s
        return s

    @classmethod
    async def close_all(cls):
        for s in cls.sessions.values():
            try:
                await s["context"].close()
            except Exception:
                pass
        cls.sessions.clear()


# ==================== HOẠT ĐỘNG THẬT ====================
async def pause(page, lo=1.0, hi=3.0):
    await page.wait_for_timeout(int(random.uniform(lo, hi) * 1000))


async def check_alive(sess):
    try:
        h = (await sess["page"].content()).lower()
        bad = ["checkpoint", "confirm your identity", "suspended",
               "tài khoản của bạn đã bị"]
        return not any(s in h for s in bad)
    except Exception:
        return False


async def do_activity_session(sess, cfg):
    """
    Chạy 1 session hoạt động thật.
    Dừng khi ĐẠT BẤT KỲ điều kiện:
      - Hết session_minutes phút
      - Đã like đủ max_likes bài
      - Đã cuộn đủ max_scrolls lần
      - Đã xem đủ max_stories story
    """
    page = sess["page"]
    plat = sess["platform"]

    start = datetime.datetime.now()
    end = start + datetime.timedelta(minutes=cfg["session_minutes"])

    likes = 0
    scrolls = 0
    stories = 0
    actions_log = []

    def time_left():
        return datetime.datetime.now() < end

    def over_limit():
        if cfg["max_likes"] > 0 and likes >= cfg["max_likes"]:
            return f"đủ {likes} like"
        if cfg["max_scrolls"] > 0 and scrolls >= cfg["max_scrolls"]:
            return f"đủ {scrolls} cuộn"
        if cfg["max_stories"] > 0 and stories >= cfg["max_stories"]:
            return f"đủ {stories} story"
        return None

    try:
        # 1. Vào feed
        base = ("https://www.facebook.com/" if plat == "fb"
                else "https://www.instagram.com/")
        if not page.url or page.url == "about:blank":
            await page.goto(base, wait_until="domcontentloaded", timeout=60000)
            await pause(page, 3, 6)
            actions_log.append("vào feed")

        # 2. Vòng lặp
        while time_left():
            reason = over_limit()
            if reason:
                actions_log.append(f"⏹️ dừng vì {reason}")
                break

            # Chọn action
            action = random.choices(
                ["scroll", "like", "story", "noti"],
                weights=[50, 25, 15, 10],
                k=1)[0]

            # Fallback nếu vượt limit
            if action == "like" and cfg["max_likes"] > 0 \
                    and likes >= cfg["max_likes"]:
                action = "scroll"
            if action == "story" and cfg["max_stories"] > 0 \
                    and stories >= cfg["max_stories"]:
                action = "scroll"

            try:
                if action == "scroll":
                    await page.mouse.wheel(0, random.randint(250, 600))
                    await page.wait_for_timeout(random.randint(800, 2500))
                    scrolls += 1

                elif action == "like":
                    btns = await page.query_selector_all(
                        'div[aria-label="Thích"], div[aria-label="Like"]')
                    if btns:
                        try:
                            await random.choice(btns).click(timeout=3000)
                            likes += 1
                            await page.wait_for_timeout(
                                random.randint(1500, 4000))
                        except Exception:
                            scrolls += 1
                    else:
                        scrolls += 1

                elif action == "story":
                    els = await page.query_selector_all(
                        'svg[aria-label="Story"], div[aria-label="Tin"]')
                    if els:
                        try:
                            await els[0].click(timeout=3000)
                            await page.wait_for_timeout(3000)
                            for _ in range(2):
                                await page.keyboard.press("ArrowRight")
                                await page.wait_for_timeout(
                                    random.randint(2000, 4000))
                            await page.keyboard.press("Escape")
                            stories += 1
                        except Exception:
                            scrolls += 1
                    else:
                        scrolls += 1

                elif action == "noti":
                    try:
                        nurl = ("https://www.facebook.com/notifications"
                                if plat == "fb" else
                                "https://www.instagram.com/accounts/activity/")
                        await page.goto(nurl, wait_until="domcontentloaded",
                                        timeout=30000)
                        await page.wait_for_timeout(random.randint(2000, 5000))
                        await page.goto(base, wait_until="domcontentloaded",
                                        timeout=30000)
                        await page.wait_for_timeout(2000)
                    except Exception:
                        pass

            except Exception as e:
                log.debug(f"action {action} err: {e}")

        # 3. Kết thúc
        duration = int((datetime.datetime.now() - start).total_seconds() // 60)
        summary = (f"{duration}p | {likes}👍 | {scrolls}📜 | {stories}📷")
        sess["last_activity"] = datetime.datetime.now()
        return True, summary

    except Exception as e:
        return False, f"lỗi {type(e).__name__}: {str(e)[:40]}"


# ==================== WORKER ====================
running_tasks = {}
active_bot: Bot = None


async def worker(uid):
    log.info(f"[worker] start uid={uid}")

    try:
        while True:
            t = await db_fetchone(
                "SELECT * FROM tasks WHERE uid=? AND running=1", uid)
            if not t:
                break

            where = {"fb": "platform='fb'", "ig": "platform='ig'"}.get(
                t["platform"], "1=1")
            accs = await db_fetch(
                f"SELECT * FROM accounts WHERE uid=? AND status!='dead' "
                f"AND {where} ORDER BY id", uid)

            if not accs:
                try:
                    await active_bot.send_message(
                        uid, "⚠️ Không có acc để nuôi.")
                except Exception:
                    pass
                await db_exec("UPDATE tasks SET running=0 WHERE uid=?", uid)
                break

            cfg = {
                "session_minutes": t["session_minutes"],
                "max_likes": t["max_likes"],
                "max_scrolls": t["max_scrolls"],
                "max_stories": t["max_stories"],
                "rest_minutes": t["rest_minutes"],
                "total_sessions": t["total_sessions"],
            }

            for slot in range(1, t["qty"] + 1):
                row = await db_fetchone(
                    "SELECT running FROM tasks WHERE uid=?", uid)
                if not row or row["running"] == 0:
                    break

                acc = accs[(slot - 1) % len(accs)]
                acc_id = acc["id"]
                status = "..."

                # Check tổng session
                done = await get_session_count(uid, acc_id)
                if cfg["total_sessions"] > 0 and done >= cfg["total_sessions"]:
                    status = f"🏁 Đủ {cfg['total_sessions']} session"
                    await asyncio.sleep(t["delay"])
                    continue

                try:
                    if slot not in SessionManager.sessions:
                        await SessionManager.create(slot, acc)
                        status = f"🆕 Profile mới ({ENGINE})"
                        await db_exec(
                            "UPDATE accounts SET status='warming' WHERE id=?",
                            acc_id)
                        await log_action(uid, acc_id, "init", "ok")

                    else:
                        sess = SessionManager.sessions[slot]
                        el = (datetime.datetime.now() -
                              sess["last_activity"]).total_seconds()
                        rest_sec = cfg["rest_minutes"] * 60

                        if el < rest_sec:
                            rem = int(rest_sec - el)
                            status = f"💤 Nghỉ còn {rem//60}p{rem%60}s"

                        else:
                            # Bắt đầu session
                            try:
                                await active_bot.send_message(
                                    uid,
                                    f"🔥 acc#{acc_id} bắt đầu session "
                                    f"#{done + 1} | {cfg['session_minutes']}p | "
                                    f"like≤{cfg['max_likes']} | "
                                    f"cuộn≤{cfg['max_scrolls']} | "
                                    f"story≤{cfg['max_stories']}")
                            except Exception:
                                pass

                            ok, detail = await do_activity_session(sess, cfg)
                            new_done = await bump_session_count(uid, acc_id)

                            if ok:
                                if await check_alive(sess):
                                    status = (f"✅ Session #{new_done}: "
                                              f"{detail}")
                                    await db_exec(
                                        "UPDATE accounts SET last_check=?,"
                                        "last_action=? WHERE id=?",
                                        datetime.datetime.now().isoformat(),
                                        detail, acc_id)
                                    await log_action(uid, acc_id,
                                                     "session", "ok")
                                else:
                                    status = "💀 ACC DIE"
                                    await db_exec(
                                        "UPDATE accounts SET status='dead'"
                                        " WHERE id=?", acc_id)
                                    await log_action(uid, acc_id,
                                                     "check", "dead")
                            else:
                                status = f"⚠️ {detail}"
                                await log_action(uid, acc_id, "session", "fail")

                except Exception as e:
                    status = f"⚠️ {type(e).__name__}: {str(e)[:60]}"

                try:
                    await active_bot.send_message(
                        uid,
                        f"🛡️ `{datetime.datetime.now():%H:%M:%S}` | "
                        f"slot {slot}/{t['qty']} | acc#{acc_id}\n{status}",
                        parse_mode="Markdown")
                except Exception:
                    pass

                await asyncio.sleep(t["delay"])

            await asyncio.sleep(30)

    finally:
        await SessionManager.close_all()
        log.info(f"[worker] end uid={uid}")


async def start_worker(uid):
    if uid in running_tasks and not running_tasks[uid].done():
        return
    running_tasks[uid] = asyncio.create_task(worker(uid))


def stop_worker(uid):
    if uid in running_tasks:
        running_tasks[uid].cancel()
        running_tasks.pop(uid, None)


# ==================== BOT UI ====================
dp = Dispatcher()


class AddState(StatesGroup):
    fb = State()
    ig = State()
    proxy = State()
    qty = State()
    delay = State()
    session_minutes = State()
    max_likes = State()
    max_scrolls = State()
    max_stories = State()
    rest_minutes = State()
    total_sessions = State()


def main_kb():
    return ReplyKeyboardMarkup(keyboard=[
        [KeyboardButton(text="📂 Quản lý acc")],
        [KeyboardButton(text="🤖 Nuôi acc"), KeyboardButton(text="📊 Thống kê")],
        [KeyboardButton(text="📜 Log"), KeyboardButton(text="❓ Hướng dẫn")],
    ], resize_keyboard=True)


def cfg_kb(t):
    run_txt = "⏹️ Dừng nuôi" if t["running"] else "▶️ Bắt đầu nuôi"
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"🎯 {t['platform']}",
                              callback_data="set_plat")],
        [InlineKeyboardButton(text=f"🔢 Slot: {t['qty']}",
                              callback_data="set_qty")],
        [InlineKeyboardButton(text=f"⏱️ Delay: {t['delay']}s",
                              callback_data="set_delay")],
        [InlineKeyboardButton(text="⚙️ Cấu hình session",
                              callback_data="session_cfg")],
        [InlineKeyboardButton(text=run_txt, callback_data="toggle")],
    ])


def session_cfg_kb(t):
    total = "∞" if t["total_sessions"] == 0 else str(t["total_sessions"])
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text=f"⏰ Session: {t['session_minutes']} phút",
            callback_data="set_sess_min")],
        [InlineKeyboardButton(
            text=f"👍 Like tối đa: {t['max_likes']}",
            callback_data="set_max_likes")],
        [InlineKeyboardButton(
            text=f"📜 Cuộn tối đa: {t['max_scrolls']}",
            callback_data="set_max_scrolls")],
        [InlineKeyboardButton(
            text=f"📷 Story tối đa: {t['max_stories']}",
            callback_data="set_max_stories")],
        [InlineKeyboardButton(
            text=f"😴 Nghỉ giữa session: {t['rest_minutes']} phút",
            callback_data="set_rest_min")],
        [InlineKeyboardButton(
            text=f"🔁 Tổng session: {total}",
            callback_data="set_total_sess")],
        [InlineKeyboardButton(text="⬅️ Quay lại",
                              callback_data="back_cfg")],
    ])


async def ensure_task(uid):
    t = await db_fetchone("SELECT * FROM tasks WHERE uid=?", uid)
    if not t:
        await db_exec("INSERT INTO tasks (uid) VALUES (?)", uid)
        t = await db_fetchone("SELECT * FROM tasks WHERE uid=?", uid)
    return t


# ==================== COMMANDS ====================
@dp.message(Command("start"))
async def cmd_start(m: Message):
    await m.answer(
        f"👋 **FarmBot** — Nuôi acc FB/IG\n"
        f"🔧 Engine: `{ENGINE}`\n\n"
        f"**Cách dùng:**\n"
        f"1️⃣ 📂 Quản lý acc → thêm cookie FB/IG\n"
        f"2️⃣ 🌐 Gán proxy (tùy chọn)\n"
        f"3️⃣ 🤖 Nuôi acc → ⚙️ Cấu hình session → ▶️ Bắt đầu",
        reply_markup=main_kb(), parse_mode="Markdown")


@dp.message(F.text == "❓ Hướng dẫn")
async def cmd_help(m: Message):
    await m.answer(
        "📖 **HƯỚNG DẪN**\n\n"
        "**1. Thêm cookie**\n"
        "📂 → ➕ Cookie FB/IG\n"
        "Gửi mỗi dòng 1 cookie\n"
        "FB: `c_user=...; xs=...;`\n"
        "IG: `sessionid=...; ds_user_id=...;`\n\n"
        "**2. Gán proxy**\n"
        "📂 → 🌐 Gán proxy\n"
        "Mỗi dòng `ip:port` hoặc `ip:port:user:pass`\n\n"
        "**3. Cấu hình session**\n"
        "🤖 → ⚙️ Cấu hình session\n"
        "• ⏰ Session: thời gian mỗi lần chạy\n"
        "• 👍 Like tối đa\n"
        "• 📜 Cuộn tối đa\n"
        "• 📷 Story tối đa\n"
        "• 😴 Nghỉ giữa session\n"
        "• 🔁 Tổng session (0 = vô hạn)\n\n"
        "**4. Bắt đầu**\n"
        "▶️ Bắt đầu nuôi\n\n"
        "**Session sẽ dừng khi:**\n"
        "• Hết thời gian HOẶC\n"
        "• Đủ like HOẶC đủ cuộn HOẶC đủ story\n\n"
        "**Gợi ý cấu hình:**\n"
        "• Acc mới: 5p | 2 like | 10 cuộn | 1 story | nghỉ 90p\n"
        "• Acc thường: 15p | 10 like | 20 cuộn | 5 story | nghỉ 45p",
        parse_mode="Markdown")


# ==================== QUẢN LÝ ACC ====================
@dp.message(F.text == "📂 Quản lý acc")
async def cmd_manage(m: Message, state: FSMContext):
    await state.clear()
    uid = m.from_user.id
    fb = await db_fetchone(
        "SELECT COUNT(*) c FROM accounts WHERE uid=? AND platform='fb'", uid)
    ig = await db_fetchone(
        "SELECT COUNT(*) c FROM accounts WHERE uid=? AND platform='ig'", uid)
    dead = await db_fetchone(
        "SELECT COUNT(*) c FROM accounts WHERE uid=? AND status='dead'", uid)

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"➕ Cookie FB ({fb['c']})",
                              callback_data="add_fb"),
         InlineKeyboardButton(text=f"➕ Cookie IG ({ig['c']})",
                              callback_data="add_ig")],
        [InlineKeyboardButton(text="🌐 Gán proxy", callback_data="add_proxy")],
        [InlineKeyboardButton(text="📋 Xem danh sách",
                              callback_data="list_acc")],
        [InlineKeyboardButton(text=f"🗑️ Dọn acc chết ({dead['c']})",
                              callback_data="clean_dead")],
    ])
    await m.answer(
        f"📂 **Quản lý acc**\n"
        f"• FB: {fb['c']} | IG: {ig['c']} | Chết: {dead['c']}",
        reply_markup=kb, parse_mode="Markdown")


@dp.callback_query(F.data == "add_fb")
async def c_add_fb(c: CallbackQuery, state: FSMContext):
    await state.set_state(AddState.fb)
    await c.message.answer(
        "📘 Gửi cookie Facebook, mỗi dòng 1 cookie:\n"
        "`c_user=...; xs=...;`",
        parse_mode="Markdown")


@dp.message(AddState.fb, F.text)
async def p_add_fb(m: Message, state: FSMContext):
    uid = m.from_user.id
    n = 0
    for line in m.text.splitlines():
        line = line.strip()
        if line and "=" in line:
            await db_exec(
                "INSERT INTO accounts (uid,platform,cookie,born_at) "
                "VALUES (?,?,?,?)",
                uid, "fb", line, datetime.datetime.now().isoformat())
            n += 1
    await state.clear()
    await m.answer(f"✅ Đã lưu **{n}** cookie FB.",
                   reply_markup=main_kb(), parse_mode="Markdown")


@dp.callback_query(F.data == "add_ig")
async def c_add_ig(c: CallbackQuery, state: FSMContext):
    await state.set_state(AddState.ig)
    await c.message.answer(
        "📸 Gửi cookie Instagram, mỗi dòng 1:\n"
        "`sessionid=...; ds_user_id=...;`",
        parse_mode="Markdown")


@dp.message(AddState.ig, F.text)
async def p_add_ig(m: Message, state: FSMContext):
    uid = m.from_user.id
    n = 0
    for line in m.text.splitlines():
        line = line.strip()
        if line and "=" in line:
            await db_exec(
                "INSERT INTO accounts (uid,platform,cookie,born_at) "
                "VALUES (?,?,?,?)",
                uid, "ig", line, datetime.datetime.now().isoformat())
            n += 1
    await state.clear()
    await m.answer(f"✅ Đã lưu **{n}** cookie IG.",
                   reply_markup=main_kb(), parse_mode="Markdown")


@dp.callback_query(F.data == "add_proxy")
async def c_add_proxy(c: CallbackQuery, state: FSMContext):
    await state.set_state(AddState.proxy)
    await c.message.answer(
        "🌐 Gửi danh sách proxy, mỗi dòng:\n"
        "`ip:port` hoặc `ip:port:user:pass`")


@dp.message(AddState.proxy, F.text)
async def p_add_proxy(m: Message, state: FSMContext):
    uid = m.from_user.id
    px = [l.strip() for l in m.text.splitlines() if l.strip()]
    if not px:
        await m.answer("❌ Không có proxy.")
        return
    accs = await db_fetch(
        "SELECT id FROM accounts WHERE uid=? ORDER BY id", uid)
    for i, acc in enumerate(accs):
        await db_exec("UPDATE accounts SET proxy=? WHERE id=?",
                      px[i % len(px)], acc["id"])
    await state.clear()
    await m.answer(
        f"✅ Đã gán **{len(px)}** proxy cho **{len(accs)}** acc.",
        reply_markup=main_kb(), parse_mode="Markdown")


@dp.callback_query(F.data == "list_acc")
async def c_list_acc(c: CallbackQuery):
    rows = await db_fetch(
        "SELECT id,platform,status,proxy,last_action FROM accounts "
        "WHERE uid=? ORDER BY id LIMIT 50", c.from_user.id)
    if not rows:
        await c.answer("Chưa có acc.", show_alert=True)
        return
    txt = "📋 **Danh sách acc**\n\n"
    for r in rows:
        emoji = {"new": "🆕", "warming": "🔥", "active": "✅",
                 "dead": "💀"}.get(r["status"], "⚪")
        act = (r["last_action"][:35] + "...") if r["last_action"] else "-"
        txt += (f"{emoji} `#{r['id']}` {r['platform'].upper()} | "
                f"{r['status']}\n   └ {act}\n")
    await c.message.answer(txt, parse_mode="Markdown")


@dp.callback_query(F.data == "clean_dead")
async def c_clean_dead(c: CallbackQuery):
    n = await db_fetchone(
        "SELECT COUNT(*) c FROM accounts WHERE uid=? AND status='dead'",
        c.from_user.id)
    if not n["c"]:
        await c.answer("Không có acc chết.", show_alert=True)
        return
    await db_exec("DELETE FROM accounts WHERE uid=? AND status='dead'",
                  c.from_user.id)
    await c.answer(f"Đã xóa {n['c']} acc.", show_alert=True)


# ==================== CẤU HÌNH NUÔI ====================
@dp.message(F.text == "🤖 Nuôi acc")
async def cmd_setup(m: Message, state: FSMContext):
    await state.clear()
    t = await ensure_task(m.from_user.id)
    await m.answer(
        f"🤖 **Cấu hình nuôi**\n"
        f"• Platform: `{t['platform']}`\n"
        f"• Slot: `{t['qty']}` | Delay: `{t['delay']}s`\n"
        f"• Session: `{t['session_minutes']}p` "
        f"| Like≤`{t['max_likes']}` | Cuộn≤`{t['max_scrolls']}` "
        f"| Story≤`{t['max_stories']}`\n"
        f"• Nghỉ giữa session: `{t['rest_minutes']}p`\n"
        f"• Tổng session: "
        f"`{'∞' if t['total_sessions']==0 else t['total_sessions']}`\n"
        f"• Trạng thái: "
        f"`{'🟢 Đang chạy' if t['running'] else '🔴 Đang dừng'}`",
        reply_markup=cfg_kb(t), parse_mode="Markdown")


@dp.callback_query(F.data == "set_plat")
async def c_set_plat(c: CallbackQuery):
    uid = c.from_user.id
    t = await ensure_task(uid)
    nxt = {"both": "fb", "fb": "ig", "ig": "both"}[t["platform"]]
    await db_exec("UPDATE tasks SET platform=? WHERE uid=?", nxt, uid)
    t = await ensure_task(uid)
    await c.message.edit_reply_markup(reply_markup=cfg_kb(t))
    await c.answer(f"→ {nxt}")


@dp.callback_query(F.data == "set_qty")
async def c_set_qty(c: CallbackQuery, state: FSMContext):
    await state.set_state(AddState.qty)
    await c.message.answer("🔢 Nhập số slot song song (1-20):")


@dp.message(AddState.qty, F.text)
async def p_set_qty(m: Message, state: FSMContext):
    if not m.text.isdigit():
        return
    n = max(1, min(20, int(m.text)))
    await db_exec("UPDATE tasks SET qty=? WHERE uid=?", n, m.from_user.id)
    await state.clear()
    t = await ensure_task(m.from_user.id)
    await m.answer(f"✅ Slot = **{n}**",
                   reply_markup=cfg_kb(t), parse_mode="Markdown")


@dp.callback_query(F.data == "set_delay")
async def c_set_delay(c: CallbackQuery, state: FSMContext):
    await state.set_state(AddState.delay)
    await c.message.answer("⏱️ Nhập delay giây giữa slot (5-300):")


@dp.message(AddState.delay, F.text)
async def p_set_delay(m: Message, state: FSMContext):
    if not m.text.isdigit():
        return
    n = max(5, min(300, int(m.text)))
    await db_exec("UPDATE tasks SET delay=? WHERE uid=?", n, m.from_user.id)
    await state.clear()
    t = await ensure_task(m.from_user.id)
    await m.answer(f"✅ Delay = **{n}s**",
                   reply_markup=cfg_kb(t), parse_mode="Markdown")


# ==================== CẤU HÌNH SESSION ====================
@dp.callback_query(F.data == "session_cfg")
async def c_session_cfg(c: CallbackQuery):
    t = await ensure_task(c.from_user.id)
    total = "∞" if t["total_sessions"] == 0 else str(t["total_sessions"])
    await c.message.edit_text(
        f"⚙️ **Cấu hình session nuôi**\n\n"
        f"Mỗi session: chạy **{t['session_minutes']} phút** HOẶC "
        f"dừng khi đủ like/cuộn/story.\n"
        f"Nghỉ giữa session: **{t['rest_minutes']} phút**\n"
        f"Tổng session mỗi acc: **{total}**\n\n"
        f"👇 Chọn thông số muốn sửa:",
        reply_markup=session_cfg_kb(t), parse_mode="Markdown")


@dp.callback_query(F.data == "back_cfg")
async def c_back_cfg(c: CallbackQuery):
    t = await ensure_task(c.from_user.id)
    await c.message.edit_text(
        f"🤖 **Cấu hình nuôi**\n"
        f"• Platform: `{t['platform']}`\n"
        f"• Slot: `{t['qty']}` | Delay: `{t['delay']}s`\n"
        f"• Session: `{t['session_minutes']}p` "
        f"| Like≤`{t['max_likes']}` | Cuộn≤`{t['max_scrolls']}` "
        f"| Story≤`{t['max_stories']}`\n"
        f"• Nghỉ: `{t['rest_minutes']}p`\n"
        f"• Tổng session: "
        f"`{'∞' if t['total_sessions']==0 else t['total_sessions']}`\n"
        f"• Trạng thái: "
        f"`{'🟢 Đang chạy' if t['running'] else '🔴 Đang dừng'}`",
        reply_markup=cfg_kb(t), parse_mode="Markdown")


@dp.callback_query(F.data == "set_sess_min")
async def c_set_sess_min(c: CallbackQuery, state: FSMContext):
    await state.set_state(AddState.session_minutes)
    await c.message.answer(
        "⏰ Nhập số **phút** mỗi session (1-120):\n"
        "VD: `15` → mỗi lần chạy 15 phút rồi dừng")


@dp.message(AddState.session_minutes, F.text)
async def p_set_sess_min(m: Message, state: FSMContext):
    if not m.text.isdigit():
        return
    n = max(1, min(120, int(m.text)))
    await db_exec("UPDATE tasks SET session_minutes=? WHERE uid=?",
                  n, m.from_user.id)
    await state.clear()
    t = await ensure_task(m.from_user.id)
    await m.answer(f"✅ Session = **{n} phút**",
                   reply_markup=session_cfg_kb(t), parse_mode="Markdown")


@dp.callback_query(F.data == "set_max_likes")
async def c_set_max_likes(c: CallbackQuery, state: FSMContext):
    await state.set_state(AddState.max_likes)
    await c.message.answer(
        "👍 Nhập số bài **like tối đa** mỗi session (0-100):\n"
        "VD: `10` → like 10 bài thì dừng session\n"
        "`0` = không giới hạn")


@dp.message(AddState.max_likes, F.text)
async def p_set_max_likes(m: Message, state: FSMContext):
    if not m.text.isdigit():
        return
    n = max(0, min(100, int(m.text)))
    await db_exec("UPDATE tasks SET max_likes=? WHERE uid=?",
                  n, m.from_user.id)
    await state.clear()
    t = await ensure_task(m.from_user.id)
    await m.answer(f"✅ Like tối đa = **{n}**",
                   reply_markup=session_cfg_kb(t), parse_mode="Markdown")


@dp.callback_query(F.data == "set_max_scrolls")
async def c_set_max_scrolls(c: CallbackQuery, state: FSMContext):
    await state.set_state(AddState.max_scrolls)
    await c.message.answer(
        "📜 Nhập số lần **cuộn tối đa** mỗi session (0-200):\n"
        "`0` = không giới hạn")


@dp.message(AddState.max_scrolls, F.text)
async def p_set_max_scrolls(m: Message, state: FSMContext):
    if not m.text.isdigit():
        return
    n = max(0, min(200, int(m.text)))
    await db_exec("UPDATE tasks SET max_scrolls=? WHERE uid=?",
                  n, m.from_user.id)
    await state.clear()
    t = await ensure_task(m.from_user.id)
    await m.answer(f"✅ Cuộn tối đa = **{n}**",
                   reply_markup=session_cfg_kb(t), parse_mode="Markdown")


@dp.callback_query(F.data == "set_max_stories")
async def c_set_max_stories(c: CallbackQuery, state: FSMContext):
    await state.set_state(AddState.max_stories)
    await c.message.answer(
        "📷 Nhập số **story tối đa** mỗi session (0-50):\n"
        "`0` = không giới hạn")


@dp.message(AddState.max_stories, F.text)
async def p_set_max_stories(m: Message, state: FSMContext):
    if not m.text.isdigit():
        return
    n = max(0, min(50, int(m.text)))
    await db_exec("UPDATE tasks SET max_stories=? WHERE uid=?",
                  n, m.from_user.id)
    await state.clear()
    t = await ensure_task(m.from_user.id)
    await m.answer(f"✅ Story tối đa = **{n}**",
                   reply_markup=session_cfg_kb(t), parse_mode="Markdown")


@dp.callback_query(F.data == "set_rest_min")
async def c_set_rest_min(c: CallbackQuery, state: FSMContext):
    await state.set_state(AddState.rest_minutes)
    await c.message.answer(
        "😴 Nhập số **phút nghỉ** giữa các session (5-360):\n"
        "VD: `30` → sau mỗi session nghỉ 30 phút")


@dp.message(AddState.rest_minutes, F.text)
async def p_set_rest_min(m: Message, state: FSMContext):
    if not m.text.isdigit():
        return
    n = max(5, min(360, int(m.text)))
    await db_exec("UPDATE tasks SET rest_minutes=? WHERE uid=?",
                  n, m.from_user.id)
    await state.clear()
    t = await ensure_task(m.from_user.id)
    await m.answer(f"✅ Nghỉ = **{n} phút**",
                   reply_markup=session_cfg_kb(t), parse_mode="Markdown")


@dp.callback_query(F.data == "set_total_sess")
async def c_set_total_sess(c: CallbackQuery, state: FSMContext):
    await state.set_state(AddState.total_sessions)
    await c.message.answer(
        "🔁 Nhập **tổng số session** cho mỗi acc (0-1000):\n"
        "VD: `10` → chạy 10 session rồi tự dừng\n"
        "`0` = vô hạn")


@dp.message(AddState.total_sessions, F.text)
async def p_set_total_sess(m: Message, state: FSMContext):
    if not m.text.isdigit():
        return
    n = max(0, min(1000, int(m.text)))
    await db_exec("UPDATE tasks SET total_sessions=? WHERE uid=?",
                  n, m.from_user.id)
    await state.clear()
    t = await ensure_task(m.from_user.id)
    txt = "∞ (vô hạn)" if n == 0 else str(n)
    await m.answer(f"✅ Tổng session = **{txt}**",
                   reply_markup=session_cfg_kb(t), parse_mode="Markdown")


# ==================== TOGGLE RUN ====================
@dp.callback_query(F.data == "toggle")
async def c_toggle(c: CallbackQuery):
    uid = c.from_user.id
    t = await ensure_task(uid)

    if t["running"] == 0:
        cnt = await db_fetchone(
            "SELECT COUNT(*) c FROM accounts WHERE uid=? AND status!='dead'",
            uid)
        if not cnt["c"]:
            await c.answer("❌ Chưa có acc. Thêm trước!", show_alert=True)
            return

        # Reset session count khi bật lại
        await db_exec("DELETE FROM session_count WHERE uid=?", uid)
        await db_exec(
            "UPDATE tasks SET running=1, started_at=? WHERE uid=?",
            datetime.datetime.now().isoformat(), uid)
        await start_worker(uid)
        await c.answer("🚀 Đã bật nuôi!", show_alert=True)
    else:
        await db_exec("UPDATE tasks SET running=0 WHERE uid=?", uid)
        stop_worker(uid)
        await c.answer("⏹️ Đã dừng.", show_alert=True)

    t = await ensure_task(uid)
    await c.message.edit_reply_markup(reply_markup=cfg_kb(t))


# ==================== THỐNG KÊ & LOG ====================
@dp.message(F.text == "📊 Thống kê")
async def cmd_stats(m: Message):
    uid = m.from_user.id

    async def c(q):
        r = await db_fetchone(q, uid)
        return r["c"] if r else 0

    total = await c("SELECT COUNT(*) c FROM accounts WHERE uid=?")
    alive = await c("SELECT COUNT(*) c FROM accounts WHERE uid=? "
                    "AND status!='dead'")
    dead = await c("SELECT COUNT(*) c FROM accounts WHERE uid=? "
                   "AND status='dead'")
    fb = await c("SELECT COUNT(*) c FROM accounts WHERE uid=? "
                 "AND platform='fb'")
    ig = await c("SELECT COUNT(*) c FROM accounts WHERE uid=? "
                 "AND platform='ig'")

    sessions = await db_fetch(
        "SELECT account_id, count FROM session_count WHERE uid=?", uid)
    total_sessions = sum(s["count"] for s in sessions)

    t = await ensure_task(uid)
    total_txt = "∞" if t["total_sessions"] == 0 else str(t["total_sessions"])

    await m.answer(
        f"📊 **THỐNG KÊ**\n\n"
        f"**Tài khoản:**\n"
        f"• Tổng: **{total}**\n"
        f"• Sống: **{alive}** ✅\n"
        f"• Chết: **{dead}** 💀\n"
        f"• FB: {fb} | IG: {ig}\n\n"
        f"**Hoạt động:**\n"
        f"• Tổng session đã chạy: **{total_sessions}**\n\n"
        f"**Cấu hình nuôi:**\n"
        f"• Platform: `{t['platform']}`\n"
        f"• Slot: `{t['qty']}` | Delay: `{t['delay']}s`\n"
        f"• Session: `{t['session_minutes']}p`\n"
        f"• Like≤`{t['max_likes']}` | Cuộn≤`{t['max_scrolls']}` "
        f"| Story≤`{t['max_stories']}`\n"
        f"• Nghỉ: `{t['rest_minutes']}p` | Tổng: `{total_txt}`\n"
        f"• Trạng thái: "
        f"`{'🟢 Đang chạy' if t['running'] else '🔴 Đang dừng'}`",
        parse_mode="Markdown")


@dp.message(F.text == "📜 Log")
async def cmd_logs(m: Message):
    rows = await db_fetch(
        "SELECT * FROM logs WHERE uid=? ORDER BY id DESC LIMIT 20",
        m.from_user.id)
    if not rows:
        await m.answer("Chưa có log.")
        return
    txt = "📜 **20 log gần nhất**\n\n"
    for r in rows:
        emoji = "✅" if r["result"] == "ok" else "❌"
        txt += (f"{emoji} `{r['ts'][11:19]}` "
                f"acc#{r['account_id']} {r['action']}\n")
    await m.answer(txt, parse_mode="Markdown")


# ==================== ADMIN ====================
@dp.message(Command("admin"))
async def cmd_admin(m: Message):
    if m.from_user.id != ADMIN_ID:
        return
    u = await db_fetchone("SELECT COUNT(DISTINCT uid) c FROM accounts")
    a = await db_fetchone("SELECT COUNT(*) c FROM accounts")
    alive = await db_fetchone(
        "SELECT COUNT(*) c FROM accounts WHERE status!='dead'")
    await m.answer(
        f"👑 **ADMIN**\n"
        f"• Users: {u['c']}\n"
        f"• Tổng acc: {a['c']} | Sống: {alive['c']}\n"
        f"• Engine: `{ENGINE}`",
        parse_mode="Markdown")


# ==================== MAIN ====================
async def main():
    global active_bot
    if not BOT_TOKEN:
        print("❌ export BOT_TOKEN='...' trước khi chạy")
        return

    await init_db()
    active_bot = Bot(token=BOT_TOKEN)
    await active_bot.delete_webhook(drop_pending_updates=True)

    # Khôi phục worker
    rows = await db_fetch("SELECT uid FROM tasks WHERE running=1")
    for r in rows:
        await start_worker(r["uid"])
        log.info(f"resume worker uid={r['uid']}")

    log.info(f"🤖 FarmBot online | Engine: {ENGINE}")
    await dp.start_polling(active_bot)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        log.info("Shutdown.")
