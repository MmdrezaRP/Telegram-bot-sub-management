from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup
)
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)
import sqlite3
from datetime import datetime, timedelta

TOKEN = "8585092411:AAGGYi56M-ftlHMuNxMXq5-QL0JYIVGdHQQ"
ADMIN_ID = 169941775

# ================= DATABASE =================
conn = sqlite3.connect("bot.db", check_same_thread=False)
cursor = conn.cursor()

cursor.execute("""
CREATE TABLE IF NOT EXISTS users (
    user_id INTEGER PRIMARY KEY,
    username TEXT,
    uuid TEXT,
    subscribed_until TEXT,
    status TEXT
)
""")

cursor.execute("""
CREATE TABLE IF NOT EXISTS config_template (
    id INTEGER PRIMARY KEY,
    content TEXT,
    updated_at TEXT
)
""")

cursor.execute("""
CREATE TABLE IF NOT EXISTS relay (
    user_id INTEGER PRIMARY KEY
)
""")

conn.commit()

# ================= HELPERS =================
def is_admin(user_id):
    return user_id == ADMIN_ID


def get_template():
    cursor.execute("SELECT content FROM config_template ORDER BY id DESC LIMIT 1")
    row = cursor.fetchone()
    return row[0] if row else None


def days_left(expiry):
    return max((datetime.fromisoformat(expiry) - datetime.utcnow()).days, 0)


def user_menu():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔐 Get Config", callback_data="user_config")],
        [InlineKeyboardButton("📊 Status", callback_data="user_status")],
        [InlineKeyboardButton("💬 Chat with Admin", callback_data="user_chat")]
    ])

# ================= USER START =================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user

    cursor.execute("SELECT status FROM users WHERE user_id = ?", (user.id,))
    row = cursor.fetchone()

    if not row:
        cursor.execute("""
        INSERT INTO users (user_id, username, status)
        VALUES (?, ?, 'PENDING')
        """, (user.id, user.username))
        conn.commit()

        await update.message.reply_text("⏳ Access request sent to admin.")

        await context.bot.send_message(
            ADMIN_ID,
            f"New access request\n\nID: {user.id}\nUsername: @{user.username}",
            reply_markup=InlineKeyboardMarkup([
                [
                    InlineKeyboardButton("✅ Approve", callback_data=f"approve:{user.id}"),
                    InlineKeyboardButton("❌ Deny", callback_data=f"deny:{user.id}")
                ]
            ])
        )
        return

    if row[0] == "ACTIVE":
        await update.message.reply_text("✅ Welcome back!", reply_markup=user_menu())
    else:
        await update.message.reply_text("ℹ️ Your request is being processed.")

# ================= APPROVE / DENY =================
async def approval_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    action, uid = query.data.split(":")
    uid = int(uid)

    if not is_admin(query.from_user.id):
        return

    if action == "deny":
        cursor.execute("UPDATE users SET status='DENIED' WHERE user_id=?", (uid,))
        conn.commit()
        await query.edit_message_text(f"❌ User {uid} denied.")
        return

    cursor.execute("UPDATE users SET status='WAITING_UUID' WHERE user_id=?", (uid,))
    conn.commit()

    context.user_data["awaiting_uuid"] = uid
    await query.edit_message_text(f"Send UUID for user {uid}")

# ================= UUID INPUT =================
async def uuid_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return

    if "awaiting_uuid" not in context.user_data:
        return

    uid = context.user_data.pop("awaiting_uuid")
    uuid = update.message.text.strip()
    expiry = datetime.utcnow() + timedelta(days=30)

    cursor.execute("""
    UPDATE users
    SET uuid=?, subscribed_until=?, status='ACTIVE'
    WHERE user_id=?
    """, (uuid, expiry.isoformat(), uid))
    conn.commit()

    await update.message.delete()
    await update.message.reply_text("✅ UUID saved and user activated.")

    template = get_template()
    if template:
        config = template.replace("UUID", uuid)
        await context.bot.send_message(uid, f"🔐 Your Config:\n\n{config}")
    else:
        await context.bot.send_message(uid, "⚠️ No config template set yet.")

# ================= USER CALLBACKS =================
async def user_actions(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    uid = query.from_user.id
    cursor.execute("SELECT uuid, subscribed_until, status FROM users WHERE user_id=?", (uid,))
    row = cursor.fetchone()

    if not row or row[2] != "ACTIVE":
        await query.message.reply_text("❌ Access denied.")
        return

    if query.data == "user_status":
        await query.message.reply_text(
            f"📊 Days left: {days_left(row[1])}"
        )

    elif query.data == "user_config":
        template = get_template()
        if not template:
            await query.message.reply_text("⚠️ Config not available.")
            return
        await query.message.reply_text(template.replace("UUID", row[0]))

    elif query.data == "user_chat":
        cursor.execute("INSERT OR IGNORE INTO relay (user_id) VALUES (?)", (uid,))
        conn.commit()
        await query.message.reply_text("💬 Send your message to admin.")

# ================= CHAT RELAY =================
async def relay_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id

    cursor.execute("SELECT user_id FROM relay WHERE user_id=?", (user_id,))
    if cursor.fetchone():
        await context.bot.send_message(
            ADMIN_ID,
            f"📩 Message from {user_id}:\n\n{update.message.text}"
        )

    elif is_admin(user_id):
        cursor.execute("SELECT user_id FROM relay LIMIT 1")
        row = cursor.fetchone()
        if row:
            await context.bot.send_message(row[0], update.message.text)

# ================= ADMIN COMMANDS =================
async def setconfig(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return

    content = update.message.text.replace("/setconfig", "").strip()
    cursor.execute("""
    INSERT INTO config_template (content, updated_at)
    VALUES (?, ?)
    """, (content, datetime.utcnow().isoformat()))
    conn.commit()

    await update.message.delete()
    await update.message.reply_text("✅ Config template updated.")


async def remove(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return

    uid = int(context.args[0])
    cursor.execute("""
    UPDATE users SET status='DENIED', uuid=NULL, subscribed_until=NULL
    WHERE user_id=?
    """, (uid,))
    conn.commit()
    await update.message.reply_text(f"❌ User {uid} removed.")


async def configfor(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return

    uid = int(context.args[0])
    cursor.execute("SELECT uuid FROM users WHERE user_id=?", (uid,))
    row = cursor.fetchone()
    if not row:
        return

    template = get_template()
    await update.message.reply_text(template.replace("UUID", row[0]))

# ================= MAIN =================
def main():
    app = ApplicationBuilder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("setconfig", setconfig))
    app.add_handler(CommandHandler("remove", remove))
    app.add_handler(CommandHandler("configfor", configfor))

    app.add_handler(CallbackQueryHandler(approval_handler, pattern="approve|deny"))
    app.add_handler(CallbackQueryHandler(user_actions, pattern="user_"))

    app.add_handler(MessageHandler(filters.TEXT & filters.User(ADMIN_ID), uuid_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, relay_handler))

    print("🔥 Bot running")
    app.run_polling()

if __name__ == "__main__":
    main()
