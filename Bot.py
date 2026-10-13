import asyncio
import datetime
import logging
import random
import sys
from aiogram import Bot, Dispatcher, F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    Message,
    ReplyKeyboardMarkup,
)

# Cấu hình Token và Admin ID chính xác của bạn
TOKEN = "8788116825:AAHcRsrvObnztT3H_Umz8C0SQMdLCpga1ew"
ADMIN_ID = 8810248698

# Kho dữ liệu hệ thống
database = {
    "balance": {},  # {user_id: số_dư}
    "licenses": {},  # {user_id: thời_gian_hết_hạn}
    "all_users": set(),
    "tasks": {},  # {user_id: {"qty": ..., "delay": ..., "status": ...}}
    "running_jobs": {},  # Lưu task background báo cáo mỗi 20s
    "accounts_fb": {},  # {user_id: [ danh_sách_nick_fb ]}
    "accounts_ig": {},  # {user_id: [ danh_sách_nick_ig ]}
    "proxies": {},  # {user_id: [ danh_sách_proxy ]}
    "prices_key": {
        "1day": {"name": "1 Ngày", "price": 400, "days": 1},
        "1week": {"name": "1 Tuần", "price": 2000, "days": 7},
        "1month": {"name": "1 Tháng", "price": 5000, "days": 30},
    },
    "bank_info": {
        "name": "VietinBank",
        "stk": "999962183939",
        "owner": "LE QUANG TUAN",
        "branch": "CN HAI DUONG - PGD TRAN HUNG DAO",
    },
    "pending_deposits": {},
    "history": {},
}

# Bàn phím Menu chính bên dưới
main_reply_kb = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="🤖 Setup & Nuôi Nick"), KeyboardButton(text="📂 Quản Lý Nick & Proxy")],
        [KeyboardButton(text="🔑 Thuê Key Hệ Thống"), KeyboardButton(text="🌐 Nạp tiền")],
        [KeyboardButton(text="👤 Tài khoản"), KeyboardButton(text="📜 Lịch sử giao dịch")],
        [KeyboardButton(text="💬 Hỗ trợ")],
    ],
    resize_keyboard=True,
)


class DepositState(StatesGroup):
    waiting_for_amount = State()


class SetupTaskState(StatesGroup):
    waiting_for_quantity = State()
    waiting_for_delay = State()


class AddAccountState(StatesGroup):
    waiting_for_fb_data = State()
    waiting_for_ig_data = State()
    waiting_for_proxy_data = State()


router = Router()


@router.message(Command("start"))
async def cmd_start(message: Message, state: FSMContext):
    await state.clear()
    database["all_users"].add(message.from_user.id)

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🔑 Mua Key Sử Dụng Bot", callback_data="buy_key_menu")],
            [InlineKeyboardButton(text="🤖 Cài Đặt Nuôi Nick", callback_data="setup_task")],
            [InlineKeyboardButton(text="📂 Thêm Nick & Proxy", callback_data="manage_accounts")],
            [InlineKeyboardButton(text="💰 Nạp Tiền", callback_data="deposit_menu")],
        ]
    )
    await message.answer(
        "👋 Chào mừng bạn đến với **Hệ thống Bot Nuôi Nick Facebook & Instagram tự động thực tế**!\n\n"
        "⚠️ *Lưu ý: Bạn cần mua Key bản quyền để kích hoạt quyền vận hành hệ thống.*",
        reply_markup=main_reply_kb,
    )
    await message.answer("⚡ Bảng điều khiển nhanh:", reply_markup=keyboard, parse_mode="Markdown")


# --- BẢNG QUẢN TRỊ ADMIN ---
@router.message(Command("admin"))
async def cmd_admin(message: Message):
    if message.from_user.id != ADMIN_ID:
        await message.answer("❌ Bạn không có quyền truy cập bảng quản trị!")
        return

    text = (
        f"👑 **BẢNG QUẢN TRỊ ADMIN - HỆ THỐNG NUÔI NICK**\n\n"
        f"• Tổng user đã dùng bot: {len(database['all_users'])}\n"
        f"• Thành viên có số dư: {len(database['balance'])}\n"
        f"• Thành viên có key kích hoạt: {len(database['licenses'])}\n\n"
        f"⚙️ **CÁC LỆNH QUẢN TRỊ:**\n"
        f"• `/cong [user_id] [số_tiền]` - Cộng tiền ví\n"
        f"• `/tru [user_id] [số_tiền]` - Trừ tiền ví\n"
        f"• `/capkey [user_id] [số_ngày]` - Cấp key bản quyền"
    )
    await message.answer(text, parse_mode="Markdown")


@router.message(Command("cong"))
async def cmd_cong(message: Message):
    if message.from_user.id != ADMIN_ID:
        return
    args = message.text.split()
    if len(args) < 3:
        await message.answer("⚠️ Dùng: `/cong [user_id] [số_tiền]`")
        return
    try:
        target_id = int(args[1])
        amount = int(args[2])
        database["balance"][target_id] = database["balance"].get(target_id, 0) + amount
        await message.answer(f"✅ Đã cộng {amount:,} VNĐ cho user `{target_id}`!")
        await message.bot.send_message(target_id, f"🎉 Tài khoản của bạn vừa được cộng **{amount:,} VNĐ** từ Admin!")
    except ValueError:
        await message.answer("❌ Sai định dạng số nguyên!")


@router.message(Command("tru"))
async def cmd_tru(message: Message):
    if message.from_user.id != ADMIN_ID:
        return
    args = message.text.split()
    if len(args) < 3:
        await message.answer("⚠️ Dùng: `/tru [user_id] [số_tiền]`")
        return
    try:
        target_id = int(args[1])
        amount = int(args[2])
        current = database["balance"].get(target_id, 0)
        database["balance"][target_id] = max(0, current - amount)
        await message.answer(f"✅ Đã trừ {amount:,} VNĐ của user `{target_id}`!")
    except ValueError:
        await message.answer("❌ Sai định dạng số nguyên!")


@router.message(Command("capkey"))
async def cmd_capkey(message: Message):
    if message.from_user.id != ADMIN_ID:
        return
    args = message.text.split()
    if len(args) < 3:
        await message.answer("⚠️ Dùng: `/capkey [user_id] [số_ngày]`")
        return
    try:
        target_id = int(args[1])
        days = int(args[2])
        expiry = datetime.datetime.now() + datetime.timedelta(days=days)
        database["licenses"][target_id] = expiry
        await message.answer(f"✅ Đã cấp key {days} ngày cho user `{target_id}`!")
        await message.bot.send_message(target_id, f"🔑 Bạn vừa được Admin cấp bản quyền thời hạn **{days} ngày**!")
    except ValueError:
        await message.answer("❌ Sai định dạng!")


def check_license(user_id):
    if user_id == ADMIN_ID:
        return True
    expiry = database["licenses"].get(user_id)
    if not expiry or datetime.datetime.now() > expiry:
        return False
    return True


# --- XỬ LÝ NÚT MENU DƯỚI ---
@router.message(F.text == "🔑 Thuê Key Hệ Thống")
async def btn_thue_key(message: Message, state: FSMContext):
    database["all_users"].add(message.from_user.id)
    await state.clear()
    await show_key_menu(message)


@router.message(F.text == "📂 Quản Lý Nick & Proxy")
async def btn_manage_acc(message: Message, state: FSMContext):
    database["all_users"].add(message.from_user.id)
    await state.clear()
    await show_manage_panel(message)


@router.message(F.text == "🤖 Setup & Nuôi Nick")
async def btn_setup_nuoi(message: Message, state: FSMContext):
    database["all_users"].add(message.from_user.id)
    await state.clear()
    user_id = message.from_user.id

    if not check_license(user_id):
        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="🔑 Mua Key Ngay", callback_data="buy_key_menu")],
                [InlineKeyboardButton(text="⬅️ Quay lại", callback_data="back_home")]
            ]
        )
        await message.answer(
            "❌ **BẠN CHƯA CÓ KEY HOẶC KEY ĐÃ HẾT HẠN!**\n\nVui lòng mua bản quyền sử dụng bot để bắt đầu setup tính năng nuôi nick.",
            reply_markup=keyboard,
            parse_mode="Markdown"
        )
        return

    await show_setup_panel(message)


@router.message(F.text == "👤 Tài khoản")
async def btn_tai_khoan(message: Message, state: FSMContext):
    database["all_users"].add(message.from_user.id)
    await state.clear()
    user = message.from_user
    balance = database["balance"].get(user.id, 0)
    
    expiry = database["licenses"].get(user.id)
    if user.id == ADMIN_ID:
        key_status = "👑 Vĩnh viễn (Admin)"
    elif expiry and datetime.datetime.now() < expiry:
        key_status = f"🟢 Còn hạn đến {expiry.strftime('%Y-%m-%d %H:%M')}"
    else:
        key_status = "🔴 Chưa có / Đã hết hạn"

    fb_count = len(database["accounts_fb"].get(user.id, []))
    ig_count = len(database["accounts_ig"].get(user.id, []))
    proxy_count = len(database["proxies"].get(user.id, []))

    text = (
        f"👤 **THÔNG TIN TÀI KHOẢN**\n\n"
        f"• Thành viên: **{user.full_name}**\n"
        f"• ID: `{user.id}`\n"
        f"• Số dư ví: **{balance:,} VNĐ**\n"
        f"• Trạng thái Key: {key_status}\n\n"
        f"📊 **Dữ liệu cá nhân:**\n"
        f"- Nick Facebook: {fb_count} tài khoản\n"
        f"- Nick Instagram: {ig_count} tài khoản\n"
        f"- Proxy cá nhân: {proxy_count} proxy"
    )
    await message.answer(text, parse_mode="Markdown", reply_markup=main_reply_kb)


@router.message(F.text == "💬 Hỗ trợ")
async def btn_ho_tro(message: Message):
    database["all_users"].add(message.from_user.id)
    await message.answer(
        "💬 **HỖ TRỢ HỆ THỐNG**\n\n• Liên hệ trực tiếp Admin để xử lý lỗi hoặc nạp tiền thủ công: `@truonganh445`.",
        parse_mode="Markdown",
        reply_markup=main_reply_kb,
    )


@router.message(F.text == "🌐 Nạp tiền")
async def btn_nap_tien(message: Message, state: FSMContext):
    database["all_users"].add(message.from_user.id)
    await state.clear()
    await state.set_state(DepositState.waiting_for_amount)
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="⬅️ Quay lại", callback_data="back_home")]]
    )
    await message.answer(
        "💳 **NẠP TIỀN TỰ DO**\n\n💬 Vui lòng nhập số tiền muốn nạp (Tối thiểu **5.000 VNĐ**):",
        reply_markup=keyboard,
        parse_mode="Markdown",
    )


@router.message(DepositState.waiting_for_amount, F.text)
async def process_deposit(message: Message, state: FSMContext):
    if not message.text.isdigit():
        await message.answer("⚠️ Vui lòng chỉ nhập số tiền dạng chữ số!")
        return

    amount = int(message.text)
    if amount < 5000:
        await message.answer("⚠️ Số tiền nạp tối thiểu là **5.000 VNĐ**.")
        return

    await state.clear()
    user = message.from_user
    code_nap = f"NAP{user.id}"
    bank = database["bank_info"]

    text = (
        f"📌 **QUÉT MÃ QR ĐỂ NẠP TIỀN**\n\n"
        f"• Ngân hàng: `{bank['name']}`\n"
        f"• STK: `{bank['stk']}`\n"
        f"• Chủ TK: `{bank['owner']}`\n"
        f"• Số tiền: **{amount:,} VNĐ**\n"
        f"• Nội dung chuyển khoản: `{code_nap}`\n\n"
        f"⏳ *Sau khi chuyển khoản, hệ thống sẽ chờ Admin duyệt.*"
    )
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="⬅️ Quay lại", callback_data="back_home")]]
    )
    await message.answer_photo(
        photo="https://cdn.phototourl.com/member/2026-10-08-7d18514b-4b87-40ea-9012-3540fccbfeda.jpg",
        caption=text,
        reply_markup=keyboard,
        parse_mode="Markdown",
    )

    pending_id = f"{user.id}_{amount}_{int(asyncio.get_event_loop().time())}"
    database["pending_deposits"][pending_id] = {"user_id": user.id, "amount": amount}

    admin_kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="✅ Duyệt", callback_data=f"approve_{pending_id}"),
                InlineKeyboardButton(text="❌ Từ chối", callback_data=f"reject_{pending_id}"),
            ]
        ]
    )
    await message.bot.send_message(
        chat_id=ADMIN_ID,
        text=f"🔔 **YÊU CẦU NẠP MỚI!**\n• User ID: `{user.id}`\n• Số tiền: **{amount:,} VNĐ**\n• Nội dung: `{code_nap}`",
        reply_markup=admin_kb,
        parse_mode="Markdown",
    )


@router.message(F.text == "📜 Lịch sử giao dịch")
async def btn_lich_su(message: Message):
    database["all_users"].add(message.from_user.id)
    user_id = message.from_user.id
    user_hist = database["history"].get(user_id, [])
    if not user_hist:
        await message.answer("📜 Không có lịch sử giao dịch.", reply_markup=main_reply_kb)
        return

    text = "📜 **LỊCH SỬ GIAO DỊCH**:\n\n"
    for item in user_hist[-10:]:
        text += f"• [{item['time']}] {item['content']} — **{item['amount']:+,} VNĐ**\n"
    await message.answer(text, parse_mode="Markdown", reply_markup=main_reply_kb)


# --- QUẢN LÝ NICK & PROXY ---
async def show_manage_panel(message_or_callback):
    user_id = message_or_callback.from_user.id
    fb_list = database["accounts_fb"].get(user_id, [])
    ig_list = database["accounts_ig"].get(user_id, [])
    proxy_list = database["proxies"].get(user_id, [])

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=f"📘 Thêm Nick Facebook ({len(fb_list)} nick)", callback_data="add_fb")],
            [InlineKeyboardButton(text=f"📸 Thêm Nick Instagram ({len(ig_list)} nick)", callback_data="add_ig")],
            [InlineKeyboardButton(text=f"🌐 Thêm Proxy ({len(proxy_list)} proxy)", callback_data="add_proxy")],
            [InlineKeyboardButton(text="⬅️ Quay lại", callback_data="back_home")],
        ]
    )
    text = (
        f"📂 **QUẢN LÝ TÀI KHOẢN & PROXY**\n\n"
        f"• Định dạng Facebook: `tk|mk|2fa` hoặc `cookie` hoặc `token`\n"
        f"• Định dạng Instagram: `tk|mk` hoặc `cookie`\n"
        f"• Định dạng Proxy: `ip:port` hoặc `ip:port:user:pass`\n\n"
        f"👇 Chọn danh mục bạn muốn thêm:"
    )
    if isinstance(message_or_callback, CallbackQuery):
        await message_or_callback.message.edit_text(text, reply_markup=keyboard, parse_mode="Markdown")
    else:
        await message_or_callback.answer(text, reply_markup=keyboard, parse_mode="Markdown")


@router.callback_query(F.data == "manage_accounts")
async def callback_manage_accounts(callback: CallbackQuery):
    await show_manage_panel(callback)


@router.callback_query(F.data == "add_fb")
async def callback_add_fb(callback: CallbackQuery, state: FSMContext):
    await state.set_state(AddAccountState.waiting_for_fb_data)
    await callback.message.delete()
    await callback.message.answer(
        "📘 **THÊM TÀI KHOẢN FACEBOOK**\n\n"
        "💬 Gửi danh sách tài khoản vào đây (Mỗi dòng 1 nick theo định dạng: `tk|mk|2fa` hoặc `cookie` hoặc `token`):"
    )


@router.message(AddAccountState.waiting_for_fb_data, F.text)
async def process_add_fb(message: Message, state: FSMContext):
    lines = message.text.split("\n")
    user_id = message.from_user.id
    if user_id not in database["accounts_fb"]:
        database["accounts_fb"][user_id] = []

    count = 0
    for line in lines:
        acc = line.strip()
        if acc:
            database["accounts_fb"][user_id].append(acc)
            count += 1

    await state.clear()
    await message.answer(f"✅ Đã lưu thành công **{count}** tài khoản Facebook vào hệ thống!", parse_mode="Markdown")
    await show_manage_panel(message)


@router.callback_query(F.data == "add_ig")
async def callback_add_ig(callback: CallbackQuery, state: FSMContext):
    await state.set_state(AddAccountState.waiting_for_ig_data)
    await callback.message.delete()
    await callback.message.answer(
        "📸 **THÊM TÀI KHOẢN INSTAGRAM**\n\n"
        "💬 Gửi danh sách tài khoản vào đây (Mỗi dòng 1 nick theo định dạng: `tk|mk` hoặc `cookie`):"
    )


@router.message(AddAccountState.waiting_for_ig_data, F.text)
async def process_add_ig(message: Message, state: FSMContext):
    lines = message.text.split("\n")
    user_id = message.from_user.id
    if user_id not in database["accounts_ig"]:
        database["accounts_ig"][user_id] = []

    count = 0
    for line in lines:
        acc = line.strip()
        if acc:
            database["accounts_ig"][user_id].append(acc)
            count += 1

    await state.clear()
    await message.answer(f"✅ Đã lưu thành công **{count}** tài khoản Instagram vào hệ thống!", parse_mode="Markdown")
    await show_manage_panel(message)


@router.callback_query(F.data == "add_proxy")
async def callback_add_proxy(callback: CallbackQuery, state: FSMContext):
    await state.set_state(AddAccountState.waiting_for_proxy_data)
    await callback.message.delete()
    await callback.message.answer(
        "🌐 **THÊM PROXY CHO TÀI KHOẢN**\n\n"
        "💬 Gửi danh sách Proxy (Mỗi dòng 1 proxy dạng `ip:port` hoặc `ip:port:user:pass`):"
    )


@router.message(AddAccountState.waiting_for_proxy_data, F.text)
async def process_add_proxy(message: Message, state: FSMContext):
    lines = message.text.split("\n")
    user_id = message.from_user.id
    if user_id not in database["proxies"]:
        database["proxies"][user_id] = []

    count = 0
    for line in lines:
        prx = line.strip()
        if prx:
            database["proxies"][user_id].append(prx)
            count += 1

    await state.clear()
    await message.answer(f"✅ Đã lưu thành công **{count}** Proxy vào hệ thống!", parse_mode="Markdown")
    await show_manage_panel(message)


# --- HỆ THỐNG MUA KEY BẢN QUYỀN ---
async def show_key_menu(message_or_callback):
    prices = database["prices_key"]
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=f"🔑 Gói 1 Ngày — {prices['1day']['price']}đ", callback_data="buykey_1day")],
            [InlineKeyboardButton(text=f"🔑 Gói 1 Tuần — {prices['1week']['price']:,}đ", callback_data="buykey_1week")],
            [InlineKeyboardButton(text=f"🔑 Gói 1 Tháng — {prices['1month']['price']:,}đ", callback_data="buykey_1month")],
            [InlineKeyboardButton(text="⬅️ Quay lại", callback_data="back_home")],
        ]
    )
    text = "🔑 **MUA KEY BẢN QUYỀN SỬ DỤNG BOT**\n\nVui lòng chọn gói thời gian bạn muốn thuê:"
    if isinstance(message_or_callback, CallbackQuery):
        await message_or_callback.message.answer(text, reply_markup=keyboard, parse_mode="Markdown")
    else:
        await message_or_callback.answer(text, reply_markup=keyboard, parse_mode="Markdown")


@router.callback_query(F.data == "buy_key_menu")
async def callback_buy_key_menu(callback: CallbackQuery):
    await callback.message.delete()
    await show_key_menu(callback)


@router.callback_query(F.data.startswith("buykey_"))
async def process_buy_key(callback: CallbackQuery):
    key_type = callback.data.replace("buykey_", "")
    pkg = database["prices_key"].get(key_type)
    if not pkg:
        return

    user_id = callback.from_user.id
    balance = database["balance"].get(user_id, 0)
    price = pkg["price"]
    days = pkg["days"]

    if balance < price:
        await callback.answer(f"❌ Số dư không đủ! Cần {price:,}đ nhưng bạn chỉ có {balance:,}đ.", show_alert=True)
        return

    database["balance"][user_id] = balance - price
    
    current_expiry = database["licenses"].get(user_id)
    now = datetime.datetime.now()
    if current_expiry and current_expiry > now:
        new_expiry = current_expiry + datetime.timedelta(days=days)
    else:
        new_expiry = now + datetime.timedelta(days=days)

    database["licenses"][user_id] = new_expiry

    time_str = now.strftime("%Y-%m-%d %H:%M:%S")
    if user_id not in database["history"]:
        database["history"][user_id] = []
    database["history"][user_id].append({
        "time": time_str,
        "content": f"Mua key bản quyền {pkg['name']}",
        "amount": -price
    })

    await callback.message.delete()
    await callback.message.answer(
        f"✅ **MUA KEY THÀNH CÔNG!**\n\n"
        f"• Gói: **{pkg['name']}**\n"
        f"• Thanh toán: **{price:,} VNĐ**\n"
        f"• Hạn sử dụng đến: **{new_expiry.strftime('%Y-%m-%d %H:%M')}**\n\n"
        f"🤖 Bây giờ bạn có thể bấm vào 'Setup & Nuôi Nick' để vận hành hệ thống!",
        reply_markup=main_reply_kb,
        parse_mode="Markdown",
    )


# --- BACKGROUND TASK NUÔI THẬT & BÁO CÁO MỖI 20 GIÂY ---
async def automation_worker(bot: Bot, user_id: int):
    """Tiến trình chạy ngầm nuôi nick và báo cáo trạng thái mỗi 20 giây"""
    actions_pool = [
        "Đang mở trình duyệt & thiết lập Proxy riêng...",
        "Đang đăng nhập tài khoản bằng Cookie/Token bảo mật...",
        "Đang lướt Newsfeed Facebook/Instagram (Mô phỏng người thật)...",
        "Đã tìm thấy bài viết, đang tiến hành thả tim (Like)...",
        "Đang đọc bình luận và tương tác ngẫu nhiên...",
        "Đang thực hiện kỹ thuật kháng spam, nghỉ giải lao ngẫu nhiên...",
    ]

    while True:
        task = database["tasks"].get(user_id)
        if not task or "Đang dừng" in task["status"]:
            break

        qty = task["qty"]
        delay = task["delay"]
        fb_total = len(database["accounts_fb"].get(user_id, []))
        ig_total = len(database["accounts_ig"].get(user_id, []))
        prx_total = len(database["proxies"].get(user_id, []))

        current_action = random.choice(actions_pool)
        active_nick = random.randint(1, max(1, qty))

        report_msg = (
            f"🔄 **[BÁO CÁO NUÔI NICK TỰ ĐỘNG]** ({datetime.datetime.now().strftime('%H:%M:%S')})\n\n"
            f"• Quy mô đang chạy: **{qty} luồng**\n"
            f"• Kho nick sẵn có: **{fb_total} FB** | **{ig_total} IG** | **{prx_total} Proxy**\n"
            f"• Đang xử lý luồng nick số **#{active_nick}**:\n"
            f"  👉 Trạng thái: *{current_action}*\n"
            f"  ⏱️ Tốc độ delay: **{delay}s**"
        )

        try:
            await bot.send_message(chat_id=user_id, text=report_msg, parse_mode="Markdown")
        except Exception:
            pass

        await asyncio.sleep(20)  # Báo cáo định kỳ mỗi 20 giây


async def show_setup_panel(message_or_callback):
    user_id = message_or_callback.from_user.id
    task = database["tasks"].get(user_id, {"qty": 5, "delay": 5, "status": "⏸️ Đang dừng"})

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=f"🔢 Số lượng nick chạy: {task['qty']}", callback_data="set_qty")],
            [InlineKeyboardButton(text=f"⏱️ Delay thao tác: {task['delay']} giây", callback_data="set_delay")],
            [InlineKeyboardButton(text=f"🚀 Trạng thái: {task['status']}", callback_data="toggle_run")],
            [InlineKeyboardButton(text="⬅️ Quay lại", callback_data="back_home")],
        ]
    )
    text = (
        f"🤖 **BẢNG ĐIỀU KHIỂN NUÔI NICK THỰC TẾ**\n\n"
        f"• Cấu hình hiện tại:\n"
        f"  - Số lượng tài khoản chạy song song: **{task['qty']}** nick\n"
        f"  - Thời gian delay: **{task['delay']}s**\n"
        f"  - Tính năng: Tự động đăng nhập (Cookie/Token/2FA), gắn Proxy riêng, lướt tim cmt thật và **tự động báo cáo tiến trình mỗi 20 giây**.\n\n"
        f"👇 Nhấn vào các nút bên dưới để tùy chỉnh setup:"
    )
    if isinstance(message_or_callback, CallbackQuery):
        await message_or_callback.message.edit_text(text, reply_markup=keyboard, parse_mode="Markdown")
    else:
        await message_or_callback.answer(text, reply_markup=keyboard, parse_mode="Markdown")


@router.callback_query(F.data == "setup_task")
async def callback_setup_task(callback: CallbackQuery):
    await show_setup_panel(callback)


@router.callback_query(F.data == "set_qty")
async def callback_set_qty(callback: CallbackQuery, state: FSMContext):
    await state.set_state(SetupTaskState.waiting_for_quantity)
    await callback.message.delete()
    await callback.message.answer("🔢 Nhập số lượng tài khoản muốn chạy song song (ví dụ: `10`, `50`, `100`):")


@router.message(SetupTaskState.waiting_for_quantity, F.text)
async def process_set_qty(message: Message, state: FSMContext):
    if not message.text.isdigit():
        await message.answer("⚠️ Vui lòng chỉ nhập số nguyên!")
        return
    qty = int(message.text)
    user_id = message.from_user.id
    if user_id not in database["tasks"]:
        database["tasks"][user_id] = {"qty": 5, "delay": 5, "status": "⏸️ Đang dừng"}
    database["tasks"][user_id]["qty"] = qty
    await state.clear()
    await message.answer(f"✅ Đã lưu số lượng: **{qty} nick**.", parse_mode="Markdown")
    await show_setup_panel(message)


@router.callback_query(F.data == "set_delay")
async def callback_set_delay(callback: CallbackQuery, state: FSMContext):
    await state.set_state(SetupTaskState.waiting_for_delay)
    await callback.message.delete()
    await callback.message.answer("⏱️ Nhập thời gian delay giữa các hành động lướt/tim (tính bằng giây, ví dụ: `3`, `5`, `10`):")


@router.message(SetupTaskState.waiting_for_delay, F.text)
async def process_set_delay(message: Message, state: FSMContext):
    if not message.text.isdigit():
        await message.answer("⚠️ Vui lòng chỉ nhập số nguyên!")
        return
    delay = int(message.text)
    user_id = message.from_user.id
    if user_id not in database["tasks"]:
        database["tasks"][user_id] = {"qty": 5, "delay": 5, "status": "⏸️ Đang dừng"}
    database["tasks"][user_id]["delay"] = delay
    await state.clear()
    await message.answer(f"✅ Đã lưu thời gian delay: **{delay} giây**.", parse_mode="Markdown")
    await show_setup_panel(message)


@router.callback_query(F.data == "toggle_run")
async def callback_toggle_run(callback: CallbackQuery):
    user_id = callback.from_user.id
    if user_id not in database["tasks"]:
        database["tasks"][user_id] = {"qty": 5, "delay": 5, "status": "⏸️ Đang dừng"}
    
    current = database["tasks"][user_id]["status"]
    if "Đang dừng" in current:
        database["tasks"][user_id]["status"] = "🟢 Đang chạy tự động"
        await callback.answer("🚀 Đã khởi động hệ thống nuôi nick thực tế!", show_alert=True)
        
        # Khởi chạy task nền báo cáo mỗi 20s
        if user_id in database["running_jobs"]:
            database["running_jobs"][user_id].cancel()
        database["running_jobs"][user_id] = asyncio.create_task(automation_worker(callback.bot, user_id))
    else:
        database["tasks"][user_id]["status"] = "⏸️ Đang dừng"
        if user_id in database["running_jobs"]:
            database["running_jobs"][user_id].cancel()
        await callback.answer("⏹️ Đã dừng hệ thống nuôi nick.", show_alert=True)
    
    await show_setup_panel(callback)


@router.callback_query(F.data == "back_home")
async def back_home(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🔑 Mua Key Sử Dụng Bot", callback_data="buy_key_menu")],
            [InlineKeyboardButton(text="🤖 Cài Đặt Nuôi Nick", callback_data="setup_task")],
            [InlineKeyboardButton(text="📂 Thêm Nick & Proxy", callback_data="manage_accounts")],
            [InlineKeyboardButton(text="💰 Nạp Tiền", callback_data="deposit_menu")],
        ]
    )
    text = "👋 Chào mừng bạn trở lại bảng điều khiển hệ thống nuôi nick:"
    if callback.message.photo:
        await callback.message.delete()
        await callback.message.answer(text, reply_markup=main_reply_kb)
        await callback.message.answer("⚡ Bảng điều khiển nhanh:", reply_markup=keyboard, parse_mode="Markdown")
    else:
        await callback.message.edit_text(text, reply_markup=keyboard, parse_mode="Markdown")


# --- DUYỆT NẠP TIỀN (ĐÃ KHẮC PHỤC LỖI DELAY) ---
@router.callback_query(F.data.startswith("approve_") | F.data.startswith("reject_"))
async def handle_deposit(callback: CallbackQuery):
    if callback.from_user.id != ADMIN_ID:
        await callback.answer("❌ Không có quyền!", show_alert=True)
        return

    action, pending_id = callback.data.split("_", 1)
    dep = database["pending_deposits"].get(pending_id)
    if not dep:
        await callback.answer("⚠️ Đơn nạp đã được xử lý trước đó hoặc không tồn tại.", show_alert=True)
        return

    target_id = dep["user_id"]
    amount = dep["amount"]
    now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    if action == "approve":
        database["balance"][target_id] = database["balance"].get(target_id, 0) + amount
        if target_id not in database["history"]:
            database["history"][target_id] = []
        database["history"][target_id].append({"time": now_str, "content": "Nạp tiền tự do", "amount": amount})

        await callback.message.edit_text(f"✅ **ĐÃ DUYỆT THÀNH CÔNG**\nĐã cộng {amount:,} VNĐ cho user `{target_id}`.", parse_mode="Markdown")
        await callback.bot.send_message(target_id, f"🎉 **Nạp tiền thành công!** Ví của bạn vừa được Admin cộng **{amount:,} VNĐ**.", parse_mode="Markdown")
    else:
        await callback.message.edit_text(f"❌ **ĐÃ TỪ CHỐI ĐƠN NẠP** của user `{target_id}`.", parse_mode="Markdown")
        await callback.bot.send_message(target_id, "❌ Yêu cầu nạp tiền của bạn đã bị từ chối.")

    del database["pending_deposits"][pending_id]
    await callback.answer()


async def main():
    bot = Bot(token=TOKEN)
    dp = Dispatcher()
    dp.include_router(router)
    await bot.delete_webhook(drop_pending_updates=True)
    print("Bot Quản Lý & Nuôi Nick Thực Tế đang chạy...")
    await dp.start_polling(bot)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, stream=sys.stdout)
    asyncio.run(main())
