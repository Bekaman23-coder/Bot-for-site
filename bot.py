import asyncio
import logging
import sqlite3
import os
from datetime import datetime
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.enums import ChatAction
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from openai import AsyncOpenAI

BOT_TOKEN = ""
DEEPSEEK_API_KEY = ""
ADMIN_ID = 8001201773 

client = AsyncOpenAI(api_key=DEEPSEEK_API_KEY, base_url="https://api.deepseek.com")
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

# --- МАШИНА СОСТОЯНИЙ (FSM) ДЛЯ ПОДДЕРЖКИ ---
class SupportState(StatesGroup):
    waiting_for_message = State()

# --- БАЗА ДАННЫХ ---
def init_db():
    conn = sqlite3.connect("bot_database.db")
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            prompt TEXT,
            tier INTEGER,
            sub_basic INTEGER DEFAULT 0,
            sub_standard INTEGER DEFAULT 0,
            sub_business INTEGER DEFAULT 0
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            site_code TEXT,
            created_at TEXT
        )
    """)
    conn.commit()
    conn.close()
init_db()

def update_user_data(user_id, prompt=None, tier=None):
    conn = sqlite3.connect("bot_database.db")
    cursor = conn.cursor()
    if prompt:
        cursor.execute("UPDATE users SET prompt = ? WHERE user_id = ?", (prompt, user_id))
    if tier:
        cursor.execute("UPDATE users SET tier = ? WHERE user_id = ?", (tier, user_id))
    conn.commit()
    conn.close()

def get_user_data(user_id):
    conn = sqlite3.connect("bot_database.db")
    cursor = conn.cursor()
    cursor.execute("SELECT prompt, tier, sub_basic, sub_standard, sub_business FROM users WHERE user_id = ?", (user_id,))
    result = cursor.fetchone()
    if not result:
        cursor.execute("INSERT INTO users (user_id) VALUES (?)", (user_id,))
        conn.commit()
        result = ("Create a simple website", 15, 0, 0, 0)
    conn.close()
    return result

def add_subscription(user_id, tier):
    conn = sqlite3.connect("bot_database.db")
    cursor = conn.cursor()
    if tier == 15:
        cursor.execute("UPDATE users SET sub_basic = sub_basic + 5 WHERE user_id = ?", (user_id,))
    elif tier == 35:
        cursor.execute("UPDATE users SET sub_standard = sub_standard + 5 WHERE user_id = ?", (user_id,))
    elif tier == 75:
        cursor.execute("UPDATE users SET sub_business = sub_business + 3 WHERE user_id = ?", (user_id,))
    conn.commit()
    conn.close()

def use_subscription(user_id, tier):
    conn = sqlite3.connect("bot_database.db")
    cursor = conn.cursor()
    cursor.execute("SELECT sub_basic, sub_standard, sub_business FROM users WHERE user_id = ?", (user_id,))
    basic, standard, business = cursor.fetchone()
    success = False
    if tier == 15 and basic > 0:
        cursor.execute("UPDATE users SET sub_basic = sub_basic - 1 WHERE user_id = ?", (user_id,))
        success = True
    elif tier == 35 and standard > 0:
        cursor.execute("UPDATE users SET sub_standard = sub_standard - 1 WHERE user_id = ?", (user_id,))
        success = True
    elif tier == 75 and business > 0:
        cursor.execute("UPDATE users SET sub_business = sub_business - 1 WHERE user_id = ?", (user_id,))
        success = True
    conn.commit()
    conn.close()
    return success

def save_order(user_id, site_code):
    conn = sqlite3.connect("bot_database.db")
    cursor = conn.cursor()
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    cursor.execute("INSERT INTO orders (user_id, site_code, created_at) VALUES (?, ?, ?)", (user_id, site_code, now))
    conn.commit()
    conn.close()

# --- КЛАВИАТУРЫ ---
def get_main_menu():
    builder = InlineKeyboardBuilder()
    builder.button(text="🚀 Создать сайт", callback_data="create_site")
    builder.button(text="👤 Мой профиль", callback_data="profile")
    builder.button(text="🆘 Поддержка", callback_data="support")
    builder.adjust(1)
    return builder.as_markup()

def get_site_templates():
    builder = InlineKeyboardBuilder()
    builder.button(text="☕ Кофейня", callback_data="site_coffee")
    builder.button(text="💼 Портфолио", callback_data="site_portfolio")
    builder.button(text="🛒 Магазин", callback_data="site_shop")
    builder.button(text="🚀 IT-Стартап", callback_data="site_startup")
    builder.button(text="🍔 Ресторан", callback_data="site_restaurant")
    builder.button(text="🏋️ Фитнес", callback_data="site_fitness")
    builder.button(text="🎓 Курсы", callback_data="site_courses")
    builder.button(text="💍 Свадьба", callback_data="site_wedding")
    builder.button(text="📝 Блог", callback_data="site_blog")
    builder.button(text="✍️ Свой вариант", callback_data="site_custom")
    builder.button(text="⬅️ Назад в меню", callback_data="back_to_main")
    builder.adjust(2, 2, 2, 2, 2, 1)
    return builder.as_markup()

def get_tier_keyboard():
    builder = InlineKeyboardBuilder()
    builder.button(text="Разовый: Базовый (15 ⭐)", callback_data="tier_15")
    builder.button(text="Разовый: Стандарт (35 ⭐)", callback_data="tier_35")
    builder.button(text="Разовый: Бизнес (75 ⭐)", callback_data="tier_75")
    builder.button(text="🔥 Подписка Старт (5 базовых за 60 ⭐)", callback_data="sub_15")
    builder.button(text="🔥 Подписка Про (5 стандартных за 140 ⭐)", callback_data="sub_35")
    builder.button(text="🔥 Подписка Бизнес (3 бизнеса за 180 ⭐)", callback_data="sub_75")
    builder.button(text="⬅️ Назад", callback_data="back_to_templates")
    builder.adjust(1)
    return builder.as_markup()

def get_back_keyboard():
    builder = InlineKeyboardBuilder()
    builder.button(text="⬅️ Назад", callback_data="back_to_main")
    return builder.as_markup()

# --- ВСПОМОГАТЕЛЬНАЯ ФУНКЦИЯ ---
async def safe_edit_or_send(message: types.Message, text: str, reply_markup=None, parse_mode="HTML"):
    try:
        await message.edit_text(text, reply_markup=reply_markup, parse_mode=parse_mode)
    except Exception:
        try:
            await message.delete()
        except Exception:
            pass
        await message.answer(text, reply_markup=reply_markup, parse_mode=parse_mode)

# --- ГЛАВНЫЙ ЭКРАН ---
@dp.message(Command("start"))
async def cmd_start(message: types.Message, state: FSMContext):
    await state.clear() # Сбрасываем состояния при старте
    get_user_data(message.from_user.id) 
    text = (
        "👋 <b>Привет! Я — WebCraft AI.</b>\n\n"
        "Создаю сайты за секунды. У меня есть <b>выгодные подписки</b>!\n\n"
        "👇 <b>Выбери действие в меню:</b>"
    )
    try:
        photo = types.FSInputFile('images/welcome.jpg') 
        await message.answer_photo(photo=photo, caption=text, reply_markup=get_main_menu(), parse_mode="HTML")
    except Exception:
        await message.answer(text, reply_markup=get_main_menu(), parse_mode="HTML")

# --- АДМИН-ПАНЕЛЬ ---
@dp.message(Command("admin"))
async def cmd_admin(message: types.Message):
    print(f"DEBUG: Кто-то ввел /admin. Его ID: {message.from_user.id}, ADMIN_ID в коде: {ADMIN_ID}")
    
    if message.from_user.id != ADMIN_ID:
        return await message.answer(f"⛔ У вас нет доступа. Ваш ID: {message.from_user.id}, а нужен: {ADMIN_ID}")
    
    conn = sqlite3.connect("bot_database.db")
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM users")
    users_count = cursor.fetchone()[0]
    cursor.execute("SELECT COUNT(*) FROM orders")
    orders_count = cursor.fetchone()[0]
    conn.close()

    await message.answer(
        f"📊 <b>Статистика бота:</b>\n\n"
        f"👥 Пользователей: {users_count}\n"
        f"🌐 Сгенерировано сайтов: {orders_count}",
        parse_mode="HTML"
    )

# --- НАВИГАЦИЯ ---
@dp.callback_query(F.data == "back_to_main")
async def back_to_main(callback: types.CallbackQuery, state: FSMContext):
    await state.clear() # Если вышли в меню, сбрасываем поддержку
    await callback.answer()
    await safe_edit_or_send(callback.message, "👋 Главное меню. Выбери действие:", reply_markup=get_main_menu())

@dp.callback_query(F.data == "create_site")
async def show_templates(callback: types.CallbackQuery):
    await callback.answer()
    await safe_edit_or_send(callback.message, "🎨 <b>Выбери шаблон для твоего сайта:</b>", reply_markup=get_site_templates())

@dp.callback_query(F.data == "back_to_templates")
async def back_to_templates(callback: types.CallbackQuery):
    await callback.answer()
    await safe_edit_or_send(callback.message, "🎨 <b>Выбери шаблон для твоего сайта:</b>", reply_markup=get_site_templates())

# --- ПРОФИЛЬ ---
@dp.callback_query(F.data == "profile")
async def show_profile(callback: types.CallbackQuery):
    await callback.answer()
    _, _, basic, standard, business = get_user_data(callback.from_user.id)
    
    text = (
        f"👤 <b>Ваш профиль</b>\n\n"
        f"🆔 ID: <code>{callback.from_user.id}</code>\n"
        f"👤 Имя: {callback.from_user.first_name}\n\n"
        f"💎 <b>Ваши подписки (осталось):</b>\n"
        f"🔹 Базовых: {basic}\n"
        f"🔸 Стандартных: {standard}\n"
        f"🔺 Бизнес: {business}\n\n"
        f"📁 <b>Последние сайты:</b>\n"
    )
    
    conn = sqlite3.connect("bot_database.db")
    cursor = conn.cursor()
    cursor.execute("SELECT id, created_at FROM orders WHERE user_id = ? ORDER BY id DESC LIMIT 5", (callback.from_user.id,))
    orders = cursor.fetchall()
    conn.close()

    builder = InlineKeyboardBuilder()
    if not orders:
        text += "<i>У вас пока нет созданных сайтов.</i>"
    else:
        for order_id, date in orders:
            text += f"🌐 Сайт от {date} (ID: {order_id})\n"
            builder.button(text=f"Скачать сайт #{order_id}", callback_data=f"download_{order_id}")
            
    builder.button(text="⬅️ В главное меню", callback_data="back_to_main")
    builder.adjust(1)
    
    await safe_edit_or_send(callback.message, text, reply_markup=builder.as_markup())

@dp.callback_query(F.data.startswith("download_"))
async def download_order(callback: types.CallbackQuery):
    await callback.answer()
    order_id = int(callback.data.split("_")[1])
    conn = sqlite3.connect("bot_database.db")
    cursor = conn.cursor()
    cursor.execute("SELECT site_code FROM orders WHERE id = ? AND user_id = ?", (order_id, callback.from_user.id))
    result = cursor.fetchone()
    conn.close()

    if result:
        filename = f"site_{order_id}.html"
        with open(filename, "w", encoding="utf-8") as f:
            f.write(result[0])
        await callback.message.answer_document(types.FSInputFile(filename), caption="Ваш сайт готов!")
    else:
        await callback.message.answer("Сайт не найден.")

# --- ПОДДЕРЖКА (С ИСПОЛЬЗОВАНИЕМ FSM) ---
@dp.callback_query(F.data == "support")
async def support_start(callback: types.CallbackQuery, state: FSMContext):
    await callback.answer()
    # Устанавливаем состояние "ждем сообщение"
    await state.set_state(SupportState.waiting_for_message)
    
    await safe_edit_or_send(
        callback.message, 
        "🆘 <b>Служба поддержки</b>\n\n"
        "Напишите ваш вопрос прямо сейчас (не нужно писать слово 'Вопрос', просто текст). "
        "Я передам его администратору, а ответ придет сюда же.",
        reply_markup=get_back_keyboard()
    )

@dp.message(SupportState.waiting_for_message)
async def handle_support_message(message: types.Message, state: FSMContext):
    # Этот обработчик сработает, ТОЛЬКО если пользователь нажал кнопку "Поддержка"
    user_id = message.from_user.id
    username = message.from_user.username or "нет username"
    
    admin_text = (
        f"🆘 <b>Новое сообщение в поддержку!</b>\n\n"
        f"👤 От: {message.from_user.first_name} (@{username})\n"
        f"🆔 ID: {user_id}\n\n"
        f"💬 <b>Текст:</b>\n{message.text}\n\n"
        f"<i>(Чтобы ответить, нажмите 'Ответить' на это сообщение)</i>"
    )
    
    # Отправляем вам
    await bot.send_message(ADMIN_ID, admin_text, parse_mode="HTML")
    # Подтверждаем пользователю
    await message.answer("✅ Ваше сообщение отправлено в поддержку. Ожидайте ответа администратора.")
    # Сбрасываем состояние, чтобы следующие сообщения не улетали в поддержку
    await state.clear()

# --- ОТВЕТ АДМИНА ПОЛЬЗОВАТЕЛЮ ---
@dp.message(F.reply_to_message & (F.from_user.id == ADMIN_ID))
async def admin_reply(message: types.Message):
    try:
        # Извлекаем ID пользователя из текста пересланного сообщения
        # Ищем строку "ID: 123456" и берем только цифры
        text_parts = message.reply_to_message.text.split("ID: ")
        if len(text_parts) < 2:
            return await message.answer("❌ Не удалось найти ID пользователя в сообщении.")
        
        user_id_str = text_parts[1].split("\n")[0].strip()
        user_id = int(user_id_str)
        
        # Отправляем ответ
        await bot.send_message(user_id, f"📩 <b>Ответ поддержки:</b>\n\n{message.text}", parse_mode="HTML")
        await message.answer("✅ Ответ успешно отправлен пользователю!")
        
    except Exception as e:
        await message.answer(f"❌ Ошибка при отправке: {e}")

# --- ВЫБОР ШАБЛОНА ---
@dp.callback_query(F.data.startswith("site_"))
async def process_callback(callback: types.CallbackQuery, state: FSMContext):
    await state.clear() # Сбрасываем поддержку, если начали делать сайт
    await callback.answer()
    prompts = {
        "site_coffee": "Create a beautiful landing page for a coffee shop.",
        "site_portfolio": "Create a modern personal portfolio website.",
        "site_shop": "Create a minimalistic online store landing page.",
        "site_startup": "Create a landing page for an AI IT startup.",
        "site_restaurant": "Create a landing page for a restaurant with a menu and booking form.",
        "site_fitness": "Create a landing page for a fitness trainer with pricing and schedule.",
        "site_courses": "Create a landing page for online courses with a curriculum.",
        "site_wedding": "Create a beautiful, romantic wedding website with RSVP form.",
        "site_blog": "Create a minimalistic personal blog about technology."
    }
    
    action = callback.data

    if action == "site_custom":
        await safe_edit_or_send(callback.message, "✍️ <b>Напиши в чат, какой сайт ты хочешь создать.</b>\n(Лучше на английском)")
        return

    user_prompt = prompts.get(action)
    update_user_data(callback.from_user.id, prompt=user_prompt)
    
    await safe_edit_or_send(callback.message, "✅ Отлично! Теперь выбери тариф или подписку:", reply_markup=get_tier_keyboard())

# --- ВЫБОР ТАРИФА ---
@dp.callback_query(F.data.startswith("tier_") | F.data.startswith("sub_"))
async def process_tier(callback: types.CallbackQuery):
    await callback.answer()
    action = callback.data
    
    if action.startswith("tier_"):
        tier = int(action.split("_")[1])
        update_user_data(callback.from_user.id, tier=tier)
        price = tier
        title = f"Разовый заказ ({tier} ⭐)"
    else:
        tier = int(action.split("_")[1])
        update_user_data(callback.from_user.id, tier=tier)
        if tier == 15:
            price = 60
            title = "Подписка Старт (5 сайтов)"
        elif tier == 35:
            price = 140
            title = "Подписка Про (5 сайтов)"
        elif tier == 75:
            price = 180
            title = "Подписка Бизнес (3 сайта)"

    await bot.send_invoice(
        chat_id=callback.message.chat.id,
        title=title,
        description=f"Оплата доступа к генерации.",
        payload=f"pay_{action}",
        provider_token="",
        currency="XTR",
        prices=[types.LabeledPrice(label="Оплата", amount=price)],
        start_parameter="create_site"
    )

# --- ОБРАБОТКА ТЕКСТА (ДЛЯ СВОЕГО ВАРИАНТА) ---
# Важно: этот обработчик стоит ПОСЛЕДНИМ, чтобы не перехватывать другие сообщения
@dp.message()
async def handle_text(message: types.Message, state: FSMContext):
    # Игнорируем команды и ответы админа
    if message.text and not message.text.startswith("/"):
        if message.reply_to_message and message.from_user.id == ADMIN_ID:
            return
            
        # Если пользователь не в состоянии поддержки, считаем это промптом
        update_user_data(message.from_user.id, prompt=message.text)
        await message.answer(
            "✅ Запрос принят! Теперь выбери тариф или подписку:",
            reply_markup=get_tier_keyboard()
        )

# --- ОПЛАТА ---
@dp.pre_checkout_query()
async def process_pre_checkout(pre_checkout_query: types.PreCheckoutQuery):
    await bot.answer_pre_checkout_query(pre_checkout_query.id, ok=True)

@dp.message(F.successful_payment)
async def process_successful_payment(message: types.Message):
    payload = message.successful_payment.invoice_payload
    await message.answer("💳 Оплата получена! Обрабатываю... ⏳")
    
    if payload.startswith("pay_tier_"):
        await bot.send_chat_action(chat_id=message.chat.id, action=ChatAction.UPLOAD_PHOTO)
        prompt, tier = get_user_data(message.from_user.id)[:2]
        await generate_website(message, prompt, tier, message.from_user.id)

    elif payload.startswith("pay_sub_"):
        tier = int(payload.split("_")[2])
        add_subscription(message.from_user.id, tier)
        await message.answer("🎉 Подписка успешно активирована! Теперь вы можете генерировать сайты без доплаты.")

# --- ГЕНЕРАЦИЯ ---
async def generate_website(status_message: types.Message, prompt: str, tier: int, user_id: int):
    used_sub = use_subscription(user_id, tier)
    if tier <= 15:
        sys_prompt = "You are a web developer. Generate ONLY basic HTML code with inline CSS. Keep it very simple."
    elif tier <= 35:
        sys_prompt = "You are a professional web developer. Generate complete HTML code with embedded CSS and JS. Make it look modern and responsive."
    else:
        sys_prompt = "You are an expert web developer. Generate a high-quality HTML page. Use embedded CSS and JS. Include smooth animations, a modern layout, interactive elements, and well-structured sections. No markdown."

    try:
        response = await client.chat.completions.create(
            model="deepseek-flash",
            messages=[
                {"role": "system", "content": sys_prompt},
                {"role": "user", "content": prompt}
            ]
        )
        site_code = response.choices[0].message.content
        site_code = site_code.replace("```html", "").replace("```", "").strip()

        save_order(user_id, site_code)

        filename = "generated_site.html"
        with open(filename, "w", encoding="utf-8") as f:
            f.write(site_code)

        status = "🎁 <i>Использована подписка (бесплатно).</i>\n\n" if used_sub else ""
        
        await status_message.answer_document(
            types.FSInputFile(filename),
            caption=f"🎉 <b>Твой сайт готов!</b>\n\n{status}📥 Скачай файл <code>generated_site.html</code> и открой его в браузере.",
            parse_mode="HTML"
        )
        await status_message.delete()
    except Exception as e:
        await status_message.edit_text(f"❌ Ошибка: {str(e)}")

async def main():
    logging.basicConfig(level=logging.INFO)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
