import os
import asyncio
import logging
import sqlite3
import random
from datetime import datetime, timedelta
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, filters, ContextTypes

# Import vocabulary
from dailywords import VOCABULARY_BY_DAY, ALL_VOCABULARY

BOT_TOKEN = "8514559658:AAGlVxrUPHqOuALmIFucZBb6Ur-HV14hX3c"
ADMIN_CHAT_ID = 7211030078
DB_NAME = "vocabulary_bot.db"

logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)

class UserState:
    def __init__(self):
        self.current_word = None
        self.correct_answer = None
        self.options = []
        self.score = 0
        self.total_questions = 0
        self.difficulty_level = 1
        self.selected_day = None
        self.consecutive_correct = 0
        self.learned_words = set()

user_sessions = {}
admin_actions = {}

# ==================== DATABASE HELPERS ====================
def init_database():
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute('''CREATE TABLE IF NOT EXISTS users (user_id INTEGER PRIMARY KEY, username TEXT, first_name TEXT, last_name TEXT, joined_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP, total_correct INTEGER DEFAULT 0, total_wrong INTEGER DEFAULT 0, consecutive_correct INTEGER DEFAULT 0, learned_words INTEGER DEFAULT 0, last_active TIMESTAMP DEFAULT CURRENT_TIMESTAMP, banned INTEGER DEFAULT 0)''')
    cursor.execute('''CREATE TABLE IF NOT EXISTS user_stats (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, date TIMESTAMP DEFAULT CURRENT_TIMESTAMP, score INTEGER, total_questions INTEGER, day_number INTEGER, FOREIGN KEY (user_id) REFERENCES users (user_id))''')
    cursor.execute('''CREATE TABLE IF NOT EXISTS admins (user_id INTEGER PRIMARY KEY, username TEXT)''')
    cursor.execute('''CREATE TABLE IF NOT EXISTS channels (channel_id INTEGER PRIMARY KEY, channel_username TEXT, channel_link TEXT)''')
    conn.commit()
    conn.close()

def add_user(user_id, username, first_name, last_name):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute('INSERT OR REPLACE INTO users (user_id, username, first_name, last_name) VALUES (?, ?, ?, ?)', (int(user_id), username, first_name, last_name))
    conn.commit()
    conn.close()

def update_user_stats(user_id, correct_answers, wrong_answers, consecutive_correct, learned_words):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute('UPDATE users SET total_correct = total_correct + ?, total_wrong = total_wrong + ?, consecutive_correct = ?, learned_words = ?, last_active = CURRENT_TIMESTAMP WHERE user_id = ?', (correct_answers, wrong_answers, consecutive_correct, learned_words, int(user_id)))
    conn.commit()
    conn.close()

def save_user_stats(user_id, score, total_questions, day_number):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute('INSERT INTO user_stats (user_id, score, total_questions, day_number) VALUES (?, ?, ?, ?)', (int(user_id), score, total_questions, day_number))
    conn.commit()
    conn.close()

def get_user_stats(user_id):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute('SELECT u.total_correct, u.total_wrong, u.consecutive_correct, u.learned_words, u.last_active, u.joined_date, COUNT(us.id) as tests_taken FROM users u LEFT JOIN user_stats us ON u.user_id = us.user_id WHERE u.user_id = ? GROUP BY u.user_id', (int(user_id),))
    result = cursor.fetchone()
    conn.close()
    return result

def get_all_users_stats():
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute('SELECT u.user_id, u.username, u.first_name, u.total_correct, u.total_wrong, u.learned_words, u.last_active, u.joined_date FROM users u ORDER BY u.total_correct DESC')
    result = cursor.fetchall()
    conn.close()
    return result

# ==================== ADMIN HELPERS ====================
def is_admin(user_id):
    try:
        uid = int(user_id)
    except:
        return False
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute("SELECT 1 FROM admins WHERE user_id = ?", (uid,))
    res = cur.fetchone()
    conn.close()
    return bool(res) or uid == ADMIN_CHAT_ID

def add_admin_db(user_id):
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute("INSERT OR REPLACE INTO admins (user_id) VALUES (?)", (int(user_id),))
    conn.commit()
    conn.close()

def remove_admin_db(user_id):
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute("DELETE FROM admins WHERE user_id = ?", (int(user_id),))
    conn.commit()
    conn.close()

def ban_user_db(user_id):
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute("UPDATE users SET banned = 1 WHERE user_id = ?", (int(user_id),))
    conn.commit()
    conn.close()

def unban_user_db(user_id):
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute("UPDATE users SET banned = 0 WHERE user_id = ?", (int(user_id),))
    conn.commit()
    conn.close()

def add_channel_db(channel_id, channel_username):
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    link = f"https://t.me/{channel_username}" if channel_username else None
    cur.execute("INSERT OR REPLACE INTO channels (channel_id, channel_username, channel_link) VALUES (?, ?, ?)", (int(channel_id), channel_username, link))
    conn.commit()
    conn.close()

def remove_channel_db(channel_id):
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute("DELETE FROM channels WHERE channel_id = ?", (int(channel_id),))
    conn.commit()
    conn.close()

# ==================== SUBSCRIPTION ====================
async def check_subscription(user_id, bot):
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute('SELECT channel_id, channel_username FROM channels')
    channels = cur.fetchall()
    conn.close()
    if not channels:
        return True, []
    not_subscribed = []
    for channel_id, channel_username in channels:
        try:
            member = await bot.get_chat_member(channel_id, user_id)
            if getattr(member, "status", "") in ("left", "kicked"):
                not_subscribed.append((channel_id, channel_username))
        except:
            not_subscribed.append((channel_id, channel_username))
    return (len(not_subscribed) == 0), not_subscribed

async def send_subscription_message_chat(chat_id, bot, not_subscribed):
    text = "Kechirasiz, botdan foydalanish uchun quyidagi kanallarga obuna bo'ling:\n\n"
    keyboard = []
    for channel_id, channel_username in not_subscribed:
        if channel_username:
            url = f"https://t.me/{channel_username}"
            keyboard.append([InlineKeyboardButton(f"📍 @{channel_username}", url=url)])
            text += f"@{channel_username}\n"
        else:
            text += f"Kanal ID: {channel_id}\n"
    keyboard.append([InlineKeyboardButton("✅ Tekshirish", callback_data="check_subscription")])
    await bot.send_message(chat_id=chat_id, text=text, reply_markup=InlineKeyboardMarkup(keyboard))

# ==================== QUIZ LOGIC ====================
def generate_fallback_options(correct_meaning):
    all_meanings = [m for day_words in VOCABULARY_BY_DAY.values() for m in day_words.values()]
    other_meanings = [m for m in all_meanings if m != correct_meaning]
    wrong_options = random.sample(other_meanings, 2) if len(other_meanings) >= 2 else random.choices(other_meanings, k=2)
    options = [correct_meaning] + wrong_options
    random.shuffle(options)
    return options

async def generate_quality_options(word, correct_meaning, difficulty_level=1):
    return generate_fallback_options(correct_meaning)

async def get_random_word(day_number):
    if day_number == "mixed":
        random_day = random.choice(list(VOCABULARY_BY_DAY.keys()))
        words_dict = VOCABULARY_BY_DAY[random_day]
    else:
        words_dict = VOCABULARY_BY_DAY.get(day_number, VOCABULARY_BY_DAY[random.choice(list(VOCABULARY_BY_DAY.keys()))])
    word = random.choice(list(words_dict.keys()))
    return word, words_dict[word]

async def ask_question(user_id: int, context: ContextTypes.DEFAULT_TYPE, message=None):
    user_session = user_sessions.get(user_id)
    if not user_session or not user_session.selected_day:
        return
    word, correct_meaning = await get_random_word(user_session.selected_day)
    user_session.current_word = word
    user_session.correct_answer = correct_meaning
    user_session.options = await generate_quality_options(word, correct_meaning, user_session.difficulty_level)
    keyboard = [[InlineKeyboardButton(opt, callback_data=f"answer_{i}")] for i, opt in enumerate(user_session.options)]
    keyboard.append([InlineKeyboardButton("🚫 Testni yakunlash", callback_data="finish_quiz")])
    question_text = f"🎯 **So'z: {word}**\n\nBu so'zning to'g'ri ma'nosi qaysi?"
    if message:
        await message.edit_text(question_text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')
    else:
        await context.bot.send_message(chat_id=user_id, text=question_text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')

# ==================== HANDLERS ====================
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.callback_query:
        query = update.callback_query
        await query.answer()
        user = query.from_user
        chat = query.message.chat_id
    else:
        user = update.effective_user
        chat = update.effective_chat.id
    user_id = user.id
    add_user(user_id, user.username, user.first_name, user.last_name)
    ok, not_sub = await check_subscription(user_id, context.bot)
    if not ok:
        await send_subscription_message_chat(chat, context.bot, not_sub)
        return
    if user_id not in user_sessions:
        user_sessions[user_id] = UserState()
    keyboard = [
        [InlineKeyboardButton("🎯 Testni boshlash", callback_data="select_day")],
        [InlineKeyboardButton("📊 Mening statistikam", callback_data="my_stats"), InlineKeyboardButton("🏆 TOP-10", callback_data="top10")],
        [InlineKeyboardButton("ℹ️ Yordam", callback_data="help")]
    ]
    if is_admin(user_id):
        keyboard.append([InlineKeyboardButton("👑 Admin panel", callback_data="admin_panel")])
    reply_markup = InlineKeyboardMarkup(keyboard)
    text = (
        f"🧠 Assalomu alaykum, {user.first_name}!\n\n"
        f"📚 So'z yodlatuvchi botga xush kelibsiz!\n"
        f"Bu bot orqali har kuni qisqa testlar bilan so'z boyligingizni oshirasiz.\n\n"
        f"🔢 Bazada hozirda {len(ALL_VOCABULARY)} ta so'z mavjud.\n"
        f"✅ Omad tilaymiz!"
    )
    if update.callback_query:
        await query.message.edit_text(text, reply_markup=reply_markup)
    else:
        await context.bot.send_message(chat_id=chat, text=text, reply_markup=reply_markup)

async def select_day(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    if user_id not in user_sessions:
        user_sessions[user_id] = UserState()
    keyboard = []
    row = []
    for day in range(1, 31):
        row.append(InlineKeyboardButton(f"📅 Day {day}", callback_data=f"select_day_{day}"))
        if len(row) == 3:
            keyboard.append(row)
            row = []
    if row:
        keyboard.append(row)
    keyboard.append([InlineKeyboardButton("🔀 Mixed", callback_data="mixed")])
    keyboard.append([InlineKeyboardButton("🔙 Bosh menyu", callback_data="main_menu")])
    await query.message.edit_text("📚 **Qaysi kundan test ishlamoqchisiz?**", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')

async def start_quiz_with_day(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    if user_id not in user_sessions:
        user_sessions[user_id] = UserState()
    user_session = user_sessions[user_id]
    day_number = int(query.data.split('_')[-1])
    user_session.selected_day = day_number
    user_session.score = 0
    user_session.total_questions = 0
    user_session.consecutive_correct = 0
    await ask_question(user_id, context, query.message)

async def start_quiz_mixed(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    if user_id not in user_sessions:
        user_sessions[user_id] = UserState()
    user_session = user_sessions[user_id]
    user_session.selected_day = "mixed"
    user_session.score = 0
    user_session.total_questions = 0
    user_session.consecutive_correct = 0
    await ask_question(user_id, context, query.message)

async def handle_answer(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    if user_id not in user_sessions:
        await query.message.reply_text("Iltimos, /start ni bosing.")
        return
    user_session = user_sessions[user_id]
    answer_index = int(query.data.split('_')[1])
    selected_answer = user_session.options[answer_index]
    user_session.total_questions += 1
    if selected_answer == user_session.correct_answer:
        user_session.score += 1
        user_session.consecutive_correct += 1
        user_session.learned_words.add(user_session.current_word)
        result_text = "✅ **To'g'ri javob!** 🎉"
    else:
        user_session.consecutive_correct = 0
        result_text = f"❌ **Noto'g'ri javob!**\n\nTo'g'ri javob: **{user_session.correct_answer}**"
    await query.message.edit_text(f"{result_text}\n\n📊 Joriy natija: {user_session.score}/{user_session.total_questions}\n🔥 Ketma-ket to'g'ri: {user_session.consecutive_correct}", parse_mode='Markdown')
    await asyncio.sleep(1)
    await ask_question(user_id, context, query.message)

async def finish_quiz(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    if user_id not in user_sessions:
        await query.message.reply_text("Iltimos, /start ni bosing.")
        return
    user_session = user_sessions[user_id]
    if user_session.total_questions > 0:
        wrong_answers = user_session.total_questions - user_session.score
        save_user_stats(user_id, user_session.score, user_session.total_questions, user_session.selected_day)
        update_user_stats(user_id, user_session.score, wrong_answers, user_session.consecutive_correct, len(user_session.learned_words))
        percentage = (user_session.score / user_session.total_questions) * 100
        grade = "A'lo 🌟" if percentage >= 90 else ("Yaxshi 👍" if percentage >= 70 else ("Qoniqarli ✅" if percentage >= 50 else "Yomon 👎"))
        result_text = f"📊 **Test natijalari:**\n\n📅 Kun: Day {user_session.selected_day}\n✅ To'g'ri javoblar: {user_session.score}\n❌ Xato javoblar: {wrong_answers}\n📝 Jami savollar: {user_session.total_questions}\n📈 Foiz: {percentage:.1f}%\n🏆 Baho: {grade}\n🔥 Ketma-ket to'g'ri: {user_session.consecutive_correct}"
    else:
        result_text = "ℹ️ Siz hech qanday savolga javob bermagansiz."
    keyboard = [[InlineKeyboardButton("🎯 Yangi test", callback_data="select_day")], [InlineKeyboardButton("📊 Statistika", callback_data="my_stats")], [InlineKeyboardButton("🏠 Bosh menyu", callback_data="main_menu")]]
    await query.message.edit_text(result_text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')

async def show_my_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    stats = get_user_stats(user_id)
    if stats and stats[0] is not None:
        total_correct, total_wrong, consecutive_correct, learned_words, last_active, joined_date, tests_taken = stats
        total_answers = total_correct + total_wrong
        accuracy = (total_correct / total_answers * 100) if total_answers > 0 else 0
        level = "Gold Master 🥇" if total_correct >= 300 else ("Silver Brain 🥈" if total_correct >= 100 else ("Bronze Mind 🥉" if total_correct >= 50 else "Beginner 📘"))
        medals_text = "├─🏆 Medal va Mukofotlar:\n│   ├─"
        if total_correct >= 50:
            medals_text += "🥉 Bronze Mind – 50+ to'g'ri javob\n│   ├─"
        if total_correct >= 100:
            medals_text += "🥈 Silver Brain – 100+ to'g'ri javob\n│   ├─"
        if total_correct >= 300:
            medals_text += "🥇 Gold Master – 300+ to'g'ri javob"
        else:
            medals_text += "🔒 Gold Master – 300+ to'g'ri javob (hali ochilmagan)"
        stats_text = f"🗄 Lug'at kabinetingizga xush kelibsiz!\n\n├─🆔 ID: {user_id}\n├─🏅 Daraja: {level}\n├─🎯 To'g'ri javoblar: {total_correct} ta\n├─❌ Xato javoblar: {total_wrong} ta\n├─📈 Aniqlik: {accuracy:.0f}%\n├─🔥 Ketma-ket to'g'ri javoblar: {consecutive_correct} ta\n{medals_text}\n├─📚 O'rganilgan) so'zlar: {learned_words} ta\n├─⏱ Oxirgi faol: {last_active[:10]}\n└─📅 Ro'yxatdan o'tgan sanasi: {joined_date[:10]}"
    else:
        stats_text = "📊 Siz hali test ishlamagansiz. Test boshlang!"
    keyboard = [[InlineKeyboardButton("🎯 Test boshlash", callback_data="select_day")], [InlineKeyboardButton("🏠 Bosh menyu", callback_data="main_menu")]]
    await query.message.edit_text(stats_text, reply_markup=InlineKeyboardMarkup(keyboard))

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "ℹ️ Foydalanish yo'riqnomasi:\n\n"
        "1) 🎯 Testni boshlash tugmasini bosing\n"
        "2) Kun (Day 1–30) yoki 🔀 Mixed rejimini tanlang\n"
        "3) Har savolda so'zning ma'nosini tanlang\n"
        "4) Natijada umumiy foiz va bahoni ko'rasiz\n\n"
        "Qo'shimcha:\n"
        "• 📊 Mening statistikam — shaxsiy natijalar\n"
        "• 🏆 TOP-10 — eng yaxshi foydalanuvchilar\n\n"
        "Savollar uchun admin: @bekzzod_00"
    )
    keyboard = [
        [InlineKeyboardButton("🎯 Testni boshlash", callback_data="select_day")],
        [InlineKeyboardButton("📊 Mening statistikam", callback_data="my_stats"), InlineKeyboardButton("🏆 TOP-10", callback_data="top10")],
        [InlineKeyboardButton("🔙 Bosh menyu", callback_data="main_menu")]
    ]
    if update.callback_query:
        query = update.callback_query
        await query.answer()
        await query.message.edit_text(text, reply_markup=InlineKeyboardMarkup(keyboard))
    else:
        await context.bot.send_message(chat_id=update.effective_chat.id, text=text, reply_markup=InlineKeyboardMarkup(keyboard))

async def main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user = query.from_user
    keyboard = [
        [InlineKeyboardButton("🎯 Testni boshlash", callback_data="select_day")],
        [InlineKeyboardButton("📊 Mening statistikam", callback_data="my_stats"), InlineKeyboardButton("🏆 TOP-10", callback_data="top10")],
        [InlineKeyboardButton("ℹ️ Yordam", callback_data="help")]
    ]
    if is_admin(user.id):
        keyboard.append([InlineKeyboardButton("👑 Admin Panel", callback_data="admin_panel")])
    text = (
        f"🏠 Bosh menyu\n\n"
        f"👋 Salom, {user.first_name}! Pastdagi tugmalardan foydalaning."
    )
    await query.message.edit_text(text, reply_markup=InlineKeyboardMarkup(keyboard))

async def check_subscription_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    ok, not_sub = await check_subscription(user_id, context.bot)
    if ok:
        await query.message.edit_text("🎉 Siz barcha kanallarga obuna bo'lgansiz. /start ni qayta bosing.")
    else:
        await send_subscription_message_chat(user_id, context.bot, not_sub)

async def bot_stats_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM users")
    total_users = cur.fetchone()[0] or 0
    cur.execute("SELECT COUNT(*) FROM users WHERE banned = 1")
    banned = cur.fetchone()[0] or 0
    conn.close()
    total_words = len(ALL_VOCABULARY)
    five_min = (datetime.utcnow() - timedelta(minutes=5)).isoformat()
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM users WHERE last_active >= ?", (five_min,))
    online = cur.fetchone()[0] or 0
    conn.close()
    active_tests = sum(1 for s in user_sessions.values() if s.selected_day)
    text = f"📊 Bot Statistikasi:\n\n👥 Jami foydalanuvchilar: {total_users}\n📚 Lug'at so'zlari: {total_words}\n⛔ Bloklanganlar: {banned}\n🟢 Hozir onlayn (5m): {online}\n🧪 Hozir test ishlayotganlar: {active_tests}\n"
    await query.message.edit_text(text, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Orqaga", callback_data="main_menu")]]))

async def top10_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    rows = get_all_users_stats()
    if not rows:
        await query.message.edit_text("Hech qanday foydalanuvchi yo'q")
        return
    text = "🏆 TOP-10 foydalanuvchilar:\n\n"
    for i, r in enumerate(rows[:10], 1):
        user_id, username, first_name, total_correct, *_ = r
        if is_admin(query.from_user.id):
            name = f"@{username}" if username else (first_name or f"User#{user_id}")
        else:
            name = first_name or f"User #{i}"
        medal = "🥇" if i == 1 else ("🥈" if i == 2 else ("🥉" if i == 3 else f"{i}."))
        text += f"{medal} {name} — ✅ {total_correct}\n"
    await query.message.edit_text(text, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Orqaga", callback_data="main_menu")]]))

async def admin_panel_inline(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user = query.from_user
    if not is_admin(user.id):
        await query.message.edit_text("❌ Siz admin emassiz")
        return
    keyboard = [[InlineKeyboardButton("📊 Barcha foydalanuvchilar", callback_data="all_stats")], [InlineKeyboardButton("➕ Admin qo'shish", callback_data="admin_add"), InlineKeyboardButton("➖ Admin olib tashlash", callback_data="admin_remove")], [InlineKeyboardButton("➕ Kanal qo'shish", callback_data="add_channel"), InlineKeyboardButton("➖ Kanal o'chirish", callback_data="remove_channel")], [InlineKeyboardButton("📢 E'lon yuborish", callback_data="admin_broadcast")], [InlineKeyboardButton("🔙 Bosh menyu", callback_data="main_menu")]]
    await query.message.edit_text("👑 Admin panel — tanlang:", reply_markup=InlineKeyboardMarkup(keyboard))

async def show_all_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if not is_admin(query.from_user.id):
        await query.message.edit_text("❌ Siz admin emassiz")
        return
    rows = get_all_users_stats()
    if not rows:
        await query.message.edit_text("Hech qanday foydalanuvchi yo'q")
        return
    out = "📋 Barcha foydalanuvchilar (top 50):\n\n"
    for r in rows[:50]:
        uid, uname, fname, total_correct, total_wrong, learned, last_active, joined = r
        name = f"@{uname}" if uname else (fname or f"User#{uid}")
        out += f"{name} — ✅ {total_correct} | ❌ {total_wrong} | learned: {learned}\n"
    await query.message.edit_text(out, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Orqaga", callback_data="admin_panel")]]))

async def handle_admin_simple_callbacks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user = query.from_user
    if not is_admin(user.id):
        await query.message.edit_text("❌ Siz admin emassiz")
        return
    data = query.data
    if data == "admin_add":
        admin_actions[user.id] = {"action": "add_admin"}
        await query.message.edit_text("Qo'shmoqchi bo'lgan adminning Telegram ID sini yuboring:")
    elif data == "admin_remove":
        admin_actions[user.id] = {"action": "remove_admin"}
        await query.message.edit_text("Olib tashlamoqchi bo'lgan adminning Telegram ID sini yuboring:")
    elif data == "add_channel":
        admin_actions[user.id] = {"action": "add_channel_username"}
        await query.message.edit_text("Kanal username ni @ bilan yuboring (masalan @mychannel):")
    elif data == "remove_channel":
        admin_actions[user.id] = {"action": "remove_channel"}
        await query.message.edit_text("O'chirmoqchi bo'lgan kanal ID sini yuboring:")

async def admin_broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if not is_admin(query.from_user.id):
        await query.message.edit_text("❌ Siz admin emassiz")
        return
    admin_actions[query.from_user.id] = {"action": "broadcast"}
    await query.message.edit_text(
        "📢 Hammaga xabar yuborish:\n\n"
        "Xabar matnini yuboring (matn yetarli). \n"
        "Yuborish davomida limitdan qochish uchun sekin yuboriladi."
    )

async def broadcast_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if not is_admin(query.from_user.id):
        await query.message.edit_text("❌ Siz admin emassiz")
        return
    admin_actions[query.from_user.id] = {"action": "broadcast"}
    await query.message.edit_text("📢 Iltimos, yubormoqchi bo'lgan matningizni shu chatga yozing. Bekor qilish uchun /cancel.")

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    if not msg or not msg.text:
        return
    user = msg.from_user
    text = msg.text.strip()
    if is_admin(user.id) and user.id in admin_actions:
        pending = admin_actions[user.id]
        action = pending.get("action")
        if action == "add_admin":
            try:
                new_id = int(text)
                add_admin_db(new_id)
                admin_actions.pop(user.id, None)
                await msg.reply_text(f"✅ {new_id} admin sifatida qo'shildi.")
            except:
                await msg.reply_text("Iltimos to'g'ri Telegram ID kiriting (raqam).")
        elif action == "remove_admin":
            try:
                rem_id = int(text)
                remove_admin_db(rem_id)
                admin_actions.pop(user.id, None)
                await msg.reply_text(f"✅ {rem_id} adminlikdan olib tashlandi.")
            except:
                await msg.reply_text("Iltimos to'g'ri Telegram ID kiriting (raqam).")
        elif action == "add_channel_username":
            uname = text.lstrip()
            if not uname.startswith("@"):
                await msg.reply_text("Iltimos kanal username ni @ bilan yuboring (masalan @mychannel).")
                return
            admin_actions[user.id] = {"action": "add_channel_id", "channel_username": uname.lstrip("@")}
            await msg.reply_text("Endi kanal ID yuboring (masalan -1001234567890).")
        elif action == "add_channel_id":
            try:
                cid = int(text)
                ch_info = pending.get("channel_username")
                add_channel_db(cid, ch_info)
                admin_actions.pop(user.id, None)
                await msg.reply_text(f"✅ Kanal @{ch_info} (ID: {cid}) majburiy kanallar ro'yxatiga qo'shildi.")
            except:
                await msg.reply_text("Iltimos kanal ID sini tog'ri yuboring (raqam, masalan -1001234567890).")
        elif action == "remove_channel":
            try:
                cid = int(text)
                remove_channel_db(cid)
                admin_actions.pop(user.id, None)
                await msg.reply_text(f"🗑 Kanal ID {cid} o'chirildi.")
            except:
                await msg.reply_text("Iltimos to'g'ri kanal ID kiriting.")
        elif action == "broadcast":
            payload = text
            admin_actions.pop(user.id, None)
            conn = sqlite3.connect(DB_NAME)
            cur = conn.cursor()
            cur.execute("SELECT user_id FROM users")
            users = [r[0] for r in cur.fetchall()]
            conn.close()
            sent = 0
            failed = 0
            for uid in users:
                try:
                    await context.bot.send_message(chat_id=uid, text=payload)
                    sent += 1
                    await asyncio.sleep(0.04)
                except Exception:
                    failed += 1
                    continue
            await msg.reply_text(f"📢 Yuborildi: {sent} ta\n❌ Yuborilmadi: {failed} ta")
        return
    if text.startswith("/broadcast") and is_admin(user.id):
        payload = text.partition(" ")[2].strip()
        if not payload:
            await msg.reply_text("Iltimos: /broadcast <matn>")
            return
        conn = sqlite3.connect(DB_NAME)
        cur = conn.cursor()
        cur.execute("SELECT user_id FROM users")
        users = [r[0] for r in cur.fetchall()]
        conn.close()
        sent = 0
        failed = 0
        for uid in users:
            try:
                await context.bot.send_message(chat_id=uid, text=payload)
                sent += 1
                await asyncio.sleep(0.04)
            except Exception:
                failed += 1
                continue
        await msg.reply_text(f"📢 Yuborildi: {sent} ta\n❌ Yuborilmadi: {failed} ta")
        return
    await msg.reply_text("🔎 /start bilan boshlang yoki yordam uchun /help yozing.")

def register_handlers(application):
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CallbackQueryHandler(help_command, pattern="^help$"))
    application.add_handler(CallbackQueryHandler(select_day, pattern="^select_day$"))
    application.add_handler(CallbackQueryHandler(start_quiz_with_day, pattern=r"^select_day_\d+$"))
    application.add_handler(CallbackQueryHandler(start_quiz_mixed, pattern="^mixed$"))
    application.add_handler(CallbackQueryHandler(handle_answer, pattern="^answer_"))
    application.add_handler(CallbackQueryHandler(finish_quiz, pattern="^finish_quiz$"))
    application.add_handler(CallbackQueryHandler(show_my_stats, pattern="^my_stats$"))
    application.add_handler(CallbackQueryHandler(main_menu, pattern="^main_menu$"))
    application.add_handler(CallbackQueryHandler(admin_panel_inline, pattern="^admin_panel$"))
    application.add_handler(CallbackQueryHandler(handle_admin_simple_callbacks, pattern="^(admin_add|admin_remove|add_channel|remove_channel)$"))
    application.add_handler(CallbackQueryHandler(admin_broadcast, pattern="^admin_broadcast$"))
    application.add_handler(CallbackQueryHandler(check_subscription_button, pattern="^check_subscription$"))
    application.add_handler(CallbackQueryHandler(top10_handler, pattern="^top10$"))
    application.add_handler(CallbackQueryHandler(show_all_stats, pattern="^all_stats$"))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

def main():
    init_database()
    application = Application.builder().token(BOT_TOKEN).build()
    register_handlers(application)
    print("🤖 Bot ishga tushdi...")
    application.run_polling()

if __name__ == "__main__":
    main()