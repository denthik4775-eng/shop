import asyncio
import aiosqlite
import logging
import os
from aiogram import Bot, Dispatcher, types, F, Router
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    LabeledPrice, PreCheckoutQuery, InlineKeyboardMarkup, 
    InlineKeyboardButton, CallbackQuery, SuccessfulPayment
)
from aiogram.utils.keyboard import InlineKeyboardBuilder

# Настройки
BOT_TOKEN = "8216114774:AAHvmxCht79fVCFMnM14WqO2FOkBF5QxLx4"  # ← ЗАМЕНИТЕ
ADMIN_ID = 640876100  # ← ВАШ ID

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(storage=MemoryStorage())
router = Router()
dp.include_router(router)

# Состояния FSM
class AddProductStates(StatesGroup):
    waiting_name = State()
    waiting_desc = State()
    waiting_price = State()
    waiting_category = State()
    waiting_item_count = State()
    waiting_item_data = State()

class AddItemStates(StatesGroup):
    waiting_product_id = State()
    waiting_item_count = State()
    waiting_item_data = State()

async def init_db():
    async with aiosqlite.connect('shop.db') as db:
        await db.execute('''
            CREATE TABLE IF NOT EXISTS products (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT,
                description TEXT,
                price INTEGER DEFAULT 0,
                stars INTEGER DEFAULT 0,
                category TEXT
            )
        ''')
        await db.execute('''
            CREATE TABLE IF NOT EXISTS items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                product_id INTEGER,
                data TEXT,
                sold INTEGER DEFAULT 0
            )
        ''')
        await db.execute('''
            CREATE TABLE IF NOT EXISTS payments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                product_id INTEGER,
                stars INTEGER,
                data TEXT,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        # Добавляем колонку stars если нет
        try:
            await db.execute("ALTER TABLE products ADD COLUMN stars INTEGER DEFAULT 0")
        except:
            pass
        await db.execute("UPDATE products SET stars = price WHERE stars = 0 AND price > 0")
        await db.commit()
    logging.info("✅ База данных готова (Stars)")

async def clear_database(state: FSMContext):
    if os.path.exists('shop.db'):
        os.remove('shop.db')
    await init_db()
    await state.clear()
    return "🗑️ База данных очищена!"

async def get_available_count(product_id: int) -> int:
    async with aiosqlite.connect('shop.db') as db:
        async with db.execute("SELECT COUNT(*) FROM items WHERE product_id=? AND sold=0", (product_id,)) as cursor:
            return (await cursor.fetchone())[0]

def get_main_keyboard():
    builder = InlineKeyboardBuilder()
    builder.button(text="🛒 Каталог", callback_data="catalog")
    builder.button(text="🔧 Админ", callback_data="admin")
    builder.adjust(1)
    return builder.as_markup()

def get_catalog_keyboard():
    builder = InlineKeyboardBuilder()
    builder.button(text="👤 Аккаунты", callback_data="cat:Аккаунты")
    builder.button(text="🔑 Ключи", callback_data="cat:Ключи")
    builder.button(text="💳 Коды", callback_data="cat:Коды")
    builder.button(text="🔙 Главное", callback_data="main").adjust(1)
    return builder.as_markup()

def get_admin_keyboard():
    builder = InlineKeyboardBuilder()
    builder.button(text="➕ Добавить товар", callback_data="admin_add_product")
    builder.button(text="📦 Добавить единицу", callback_data="admin_add_item")
    builder.button(text="📊 Статистика", callback_data="admin_stats")
    builder.button(text="🗑️ Очистить БД", callback_data="admin_clear").row()
    builder.button(text="🔙 Главное", callback_data="main")
    return builder.as_markup()

@router.message(CommandStart())
async def start_handler(message: types.Message, state: FSMContext):
    await state.clear()
    await message.answer(
        "👋 Добро пожаловать!\n💫 Оплата Telegram Stars ⭐\n🛒 Выберите:",
        reply_markup=get_main_keyboard()
    )

@router.callback_query(F.data == "main")
async def main_menu(callback: CallbackQuery):
    try:
        await callback.message.edit_text("🏪 Главное меню", reply_markup=get_main_keyboard())
    except: pass
    await callback.answer()

@router.callback_query(F.data == "catalog")
async def catalog_menu(callback: CallbackQuery):
    try:
        await callback.message.edit_text("🛒 Каталог. Выберите категорию:", reply_markup=get_catalog_keyboard())
    except: pass
    await callback.answer()

@router.callback_query(F.data.startswith("cat:"))
async def category_handler(callback: CallbackQuery):
    category = callback.data.split(":", 1)[1]
    
    async with aiosqlite.connect('shop.db') as db:
        async with db.execute("""
            SELECT p.id, p.name, COALESCE(p.stars, p.price) as cost, p.description 
            FROM products p WHERE p.category=? AND EXISTS(
                SELECT 1 FROM items i WHERE i.product_id=p.id AND i.sold=0
            )
        """, (category,)) as cursor:
            products = await cursor.fetchall()
    
    if not products:
        builder = InlineKeyboardBuilder()
        builder.button(text="🔙 Каталог", callback_data="catalog")
        builder.button(text="🔙 Главное", callback_data="main").adjust(1)
        try:
            await callback.message.edit_text(f"❌ В {category} нет товаров", reply_markup=builder.as_markup())
        except: pass
    else:
        builder = InlineKeyboardBuilder()
        text = f"📦 {category}:\n\n"
        for pid, name, cost, desc in products:
            count = await get_available_count(pid)
            builder.button(text=f"{name} ({cost}⭐)[{count}]", callback_data=f"buy:{pid}")
            text += f"• {name} — {cost}⭐ [{count}]\n{desc}\n\n"
        builder.button(text="🔙 Каталог", callback_data="catalog").adjust(1)
        builder.button(text="🔙 Главное", callback_data="main")
        try:
            await callback.message.edit_text(text, reply_markup=builder.as_markup())
        except: pass
    await callback.answer()

@router.callback_query(F.data.startswith("buy:"))
async def buy_handler(callback: CallbackQuery):
    product_id = int(callback.data.split(":", 1)[1])
    
    async with aiosqlite.connect('shop.db') as db:
        async with db.execute("SELECT name, description, COALESCE(stars, price) as cost FROM products WHERE id=?", (product_id,)) as cursor:
            product = await cursor.fetchone()
    
    if not product:
        await callback.answer("❌ Товар не найден!")
        return
    
    name, desc, stars = product
    count = await get_available_count(product_id)
    
    if count == 0:
        await callback.answer("❌ Нет в наличии!")
        return
    
    prices = [LabeledPrice(label=name, amount=stars)]
    
    try:
        await callback.message.answer_invoice(
            title=f"💫 {name}",
            description=f"{desc}\n📦 В наличии: {count}",
            payload=f"product_{product_id}",
            provider_token="",
            currency="XTR",
            prices=prices
        )
    except Exception as e:
        await callback.answer(f"❌ Ошибка: {str(e)[:30]}")
    await callback.answer()

@router.pre_checkout_query()
async def pre_checkout_handler(pre_checkout_q: PreCheckoutQuery):
    await bot.answer_pre_checkout_query(pre_checkout_q.id, ok=True)

@router.message(F.successful_payment)
async def successful_payment_handler(message: types.Message, state: FSMContext):
    payment: SuccessfulPayment = message.successful_payment
    product_id = int(payment.invoice_payload.split("_")[1])
    
    async with aiosqlite.connect('shop.db') as db:
        await db.execute("INSERT INTO payments (user_id, product_id, stars) VALUES (?, ?, ?)", 
                        (message.from_user.id, product_id, payment.total_amount))
        
        async with db.execute("SELECT id, data FROM items WHERE product_id=? AND sold=0 LIMIT 1", (product_id,)) as cursor:
            item = await cursor.fetchone()
        
        if item:
            item_id, data = item
            await db.execute("UPDATE items SET sold=1 WHERE id=?", (item_id,))
            await db.execute("UPDATE payments SET data=? WHERE id=(SELECT MAX(id) FROM payments)", (data,))
            await db.commit()
            
            async with db.execute("SELECT name FROM products WHERE id=?", (product_id,)) as cursor:
                name = (await cursor.fetchone())[0]
            
            # Уведомление админу
            await bot.send_message(ADMIN_ID, 
                f"🔔 НОВЫЙ ПЛАТЕЖ!\n"
                f"👤 {message.from_user.first_name} (ID: {message.from_user.id})\n"
                f"🛒 {name}\n💫 {payment.total_amount}⭐\n"
                f"📄 `{data}`",
                parse_mode="Markdown"
            )
            
            await message.answer(
                f"✅ Успешно! ⭐{payment.total_amount}\n\n🛒 {name}\n📄 `{data}`",
                parse_mode="Markdown",
                reply_markup=InlineKeyboardBuilder()
                .button(text="🛒 Еще", callback_data="catalog")
                .button(text="🏪 Главное", callback_data="main").adjust(1).as_markup()
            )

# АДМИН ПАНЕЛЬ
@router.callback_query(F.data == "admin")
async def admin_panel(callback: CallbackQuery):
    if callback.from_user.id != ADMIN_ID:
        await callback.answer("❌ Нет доступа!")
        return
    try:
        await callback.message.edit_text("🔧 Админ-панель ⭐", reply_markup=get_admin_keyboard())
    except: pass
    await callback.answer()

@router.callback_query(F.data == "admin_stats")
async def admin_stats(callback: CallbackQuery):
    if callback.from_user.id != ADMIN_ID: return
    
    async with aiosqlite.connect('shop.db') as db:
        async with db.execute("SELECT COUNT(*), SUM(COALESCE(stars, price)) FROM products") as cursor:
            prods, _ = await cursor.fetchone()
        async with db.execute("SELECT COUNT(*) FROM items WHERE sold=1") as cursor:
            sales = (await cursor.fetchone())[0]
        async with db.execute("SELECT SUM(stars) FROM payments") as cursor:
            stars = (await cursor.fetchone())[0] or 0
    
    text = f"📊 Статистика:\n\nТоваров: {prods}\nПродано: {sales}\n⭐ Stars: {stars}"
    
    builder = InlineKeyboardBuilder()
    builder.button(text="🔄 Обновить", callback_data="admin_stats")
    builder.button(text="🗑️ Очистить", callback_data="admin_clear").row()
    builder.button(text="🔙 Админ", callback_data="admin")
    
    try:
        await callback.message.edit_text(text, reply_markup=builder.as_markup())
    except:
        await callback.message.answer(text, reply_markup=builder.as_markup())
    await callback.answer()

# ✅ ДОБАВЛЕНИЕ ТОВАРА
@router.callback_query(F.data == "admin_add_product")
async def admin_add_product(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id != ADMIN_ID: 
        await callback.answer("❌ Нет доступа!")
        return
    await state.clear()
    await state.set_state(AddProductStates.waiting_name)
    await callback.message.answer("➕ Название товара:")
    await callback.answer()

@router.message(AddProductStates.waiting_name)
async def add_product_name(message: types.Message, state: FSMContext):
    await state.update_data(name=message.text.strip())
    await state.set_state(AddProductStates.waiting_desc)
    await message.answer("📝 Описание:")

@router.message(AddProductStates.waiting_desc)
async def add_product_desc(message: types.Message, state: FSMContext):
    await state.update_data(description=message.text.strip())
    await state.set_state(AddProductStates.waiting_price)
    await message.answer("⭐ Цена в Stars:")

@router.message(AddProductStates.waiting_price)
async def add_product_price(message: types.Message, state: FSMContext):
    try:
        stars = int(message.text)
        await state.update_data(stars=stars)
        await state.set_state(AddProductStates.waiting_category)
        await message.answer("🏷️ Категория (Аккаунты/Ключи/Коды):")
    except:
        await message.answer("❌ Число Stars:")

@router.message(AddProductStates.waiting_category)
async def add_product_category(message: types.Message, state: FSMContext):
    data = await state.get_data()
    async with aiosqlite.connect('shop.db') as db:
        cursor = await db.execute(
            "INSERT INTO products (name, description, stars, category) VALUES (?, ?, ?, ?)",
            (data['name'], data['description'], data['stars'], message.text.strip())
        )
        product_id = cursor.lastrowid
        await db.commit()
    
    await state.update_data(product_id=product_id)
    await state.set_state(AddProductStates.waiting_item_count)
    await message.answer(f"✅ '{data['name']}' (ID: {product_id})\n⭐ {data['stars']}⭐\n\n📦 Сколько единиц? (0=позже)")

@router.message(AddProductStates.waiting_item_count)
async def add_product_item_count(message: types.Message, state: FSMContext):
    try:
        count = int(message.text)
        data = await state.get_data()
        if count == 0:
            await message.answer("✅ Товар готов!", reply_markup=get_admin_keyboard())
            await state.clear()
            return
        await state.update_data(item_count=count, current_item=1)
        await state.set_state(AddProductStates.waiting_item_data)
        await message.answer(f"📝 1/{count} единицы (login:pass):")
    except:
        await message.answer("❌ Число:")

@router.message(AddProductStates.waiting_item_data)
async def add_product_item_data(message: types.Message, state: FSMContext):
    data = await state.get_data()
    async with aiosqlite.connect('shop.db') as db:
        await db.execute("INSERT INTO items (product_id, data) VALUES (?, ?)", (data['product_id'], message.text.strip()))
        await db.commit()
    
    data['current_item'] += 1
    if data['current_item'] <= data['item_count']:
        await state.update_data(current_item=data['current_item'])
        await message.answer(f"✅ {data['item_count'] - data['current_item'] + 1} осталось:")
    else:
        await message.answer("✅ Все добавлено!", reply_markup=get_admin_keyboard())
        await state.clear()

# ✅ ДОБАВЛЕНИЕ ЕДИНИЦ (ИСПРАВЛЕНО!)
@router.callback_query(F.data == "admin_add_item")
async def admin_add_item(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id != ADMIN_ID: 
        await callback.answer("❌ Нет доступа!")
        return
    
    async with aiosqlite.connect('shop.db') as db:
        async with db.execute("SELECT id, name, COALESCE(stars, price), category FROM products") as cursor:
            products = await cursor.fetchall()
    
    if not products:
        await callback.message.edit_text("❌ Нет товаров!")
        await callback.answer()
        return
    
    builder = InlineKeyboardBuilder()
    text = "📦 Выберите товар:\n\n"
    for pid, name, stars, cat in products[:10]:  # Макс 10 кнопок
        builder.button(text=f"{name} ({stars}⭐)", callback_data=f"additem:{pid}")
        text += f"• {name} ({stars}⭐) - {cat}\n"
    
    builder.button(text="🔙 Админ", callback_data="admin").adjust(1)
    await callback.message.edit_text(text, reply_markup=builder.as_markup())
    await callback.answer()

@router.callback_query(F.data.startswith("additem:"))
async def select_product_for_items(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id != ADMIN_ID: return
    
    product_id = int(callback.data.split(":", 1)[1])
    
    async with aiosqlite.connect('shop.db') as db:
        async with db.execute("SELECT name, COALESCE(stars, price) FROM products WHERE id=?", (product_id,)) as cursor:
            product = await cursor.fetchone()
    
    if product:
        name, stars = product
        await state.update_data(product_id=product_id, product_name=name)
        await state.set_state(AddItemStates.waiting_item_count)
        builder = InlineKeyboardBuilder()
        builder.button(text="🔙 Админ", callback_data="admin")
        await callback.message.edit_text(
            f"📦 *{name}* ({stars}⭐)\n\nСколько единиц добавить?",
            parse_mode="Markdown",
            reply_markup=builder.as_markup()
        )
    await callback.answer()

@router.message(AddItemStates.waiting_item_count)
async def add_item_count(message: types.Message, state: FSMContext):
    try:
        count = int(message.text)
        if count <= 0:
            await message.answer("❌ Минимум 1!")
            return
        data = await state.get_data()
        await state.update_data(item_count=count, current_item=1)
        await state.set_state(AddItemStates.waiting_item_data)
        await message.answer(f"📝 *{data['product_name']}*\n\n1/{count} единицы:", parse_mode="Markdown")
    except:
        await message.answer("❌ Число единиц:")

@router.message(AddItemStates.waiting_item_data)
async def add_item_data(message: types.Message, state: FSMContext):
    data = await state.get_data()
    async with aiosqlite.connect('shop.db') as db:
        await db.execute("INSERT INTO items (product_id, data) VALUES (?, ?)", 
                        (data['product_id'], message.text.strip()))
        await db.commit()
    
    data['current_item'] += 1
    if data['current_item'] <= data['item_count']:
        await state.update_data(current_item=data['current_item'])
        await message.answer(f"✅ Осталось {data['item_count'] - data['current_item'] + 1}/{data['item_count']}:")
    else:
        await message.answer("✅ Все единицы добавлены!", reply_markup=get_admin_keyboard())
        await state.clear()

# Очистка БД
@router.callback_query(F.data == "admin_clear")
async def admin_clear_db(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id != ADMIN_ID: return
    builder = InlineKeyboardBuilder()
    builder.button(text="💥 ОЧИСТИТЬ", callback_data="clear_all")
    builder.button(text="❌ Отмена", callback_data="admin").adjust(1)
    await callback.message.edit_text("⚠️ ОЧИСТИТЬ ВСЮ БД?", reply_markup=builder.as_markup())
    await callback.answer()

@router.callback_query(F.data == "clear_all")
async def clear_all_handler(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id != ADMIN_ID: return
    result = await clear_database(state)
    await callback.message.edit_text(result, reply_markup=get_admin_keyboard())
    await callback.answer()

async def main():
    logging.basicConfig(level=logging.INFO)
    await init_db()
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
