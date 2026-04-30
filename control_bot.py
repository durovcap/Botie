"""
Control Bot - Telegram Bot interface to manage ad campaigns
"""

import asyncio
import json
import logging
import os
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler,
    MessageHandler, filters, ContextTypes, ConversationHandler
)
from userbot import AdUserBot

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Conversation states
(
    MAIN_MENU, AWAITING_PHONE, AWAITING_CODE, AWAITING_2FA,
    AWAITING_AD_TEXT, AWAITING_FORWARD_LINK, AWAITING_INTERVAL,
    AWAITING_TARGETS, MANAGE_ADS
) = range(9)

with open("config.json") as f:
    CONFIG = json.load(f)

BOT_TOKEN = CONFIG["control_bot_token"]
OWNER_IDS = CONFIG["owner_ids"]

userbot = AdUserBot()


def main_menu_keyboard(logged_in: bool):
    if not logged_in:
        return InlineKeyboardMarkup([
            [InlineKeyboardButton("🔑 Login Telegram Account", callback_data="login")]
        ])
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📋 My Campaigns", callback_data="list_ads")],
        [
            InlineKeyboardButton("✏️ New Text Ad", callback_data="new_text_ad"),
            InlineKeyboardButton("🔗 New Forward Ad", callback_data="new_forward_ad"),
        ],
        [InlineKeyboardButton("🎯 Set Target Chats", callback_data="set_targets")],
        [
            InlineKeyboardButton("🚀 Start All", callback_data="start_all"),
            InlineKeyboardButton("⏹ Stop All", callback_data="stop_all"),
        ],
        [InlineKeyboardButton("🔓 Logout", callback_data="logout")],
    ])


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in OWNER_IDS:
        await update.message.reply_text("⛔ Unauthorized.")
        return ConversationHandler.END

    logged_in = await userbot.is_logged_in()
    status = "✅ Account connected." if logged_in else "❌ No account connected."
    await update.message.reply_text(
        f"🤖 *Ad Bot Control Panel*\n\n{status}",
        parse_mode="Markdown",
        reply_markup=main_menu_keyboard(logged_in)
    )
    return MAIN_MENU


# ─── LOGIN FLOW ───────────────────────────────────────────────────────────────

async def login_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text(
        "📱 Enter your phone number in international format:\n`+1234567890`",
        parse_mode="Markdown"
    )
    return AWAITING_PHONE


async def receive_phone(update: Update, context: ContextTypes.DEFAULT_TYPE):
    phone = update.message.text.strip()
    context.user_data["phone"] = phone
    msg = await update.message.reply_text("⏳ Sending OTP...")
    try:
        await userbot.send_code(phone)
        await msg.edit_text(
            "📨 Code sent! Enter it below *(add spaces between digits)*:\n`1 2 3 4 5`",
            parse_mode="Markdown"
        )
        return AWAITING_CODE
    except Exception as e:
        await msg.edit_text(f"❌ Error: `{e}`", parse_mode="Markdown")
        return ConversationHandler.END


async def receive_code(update: Update, context: ContextTypes.DEFAULT_TYPE):
    code = update.message.text.replace(" ", "").strip()
    phone = context.user_data.get("phone")
    msg = await update.message.reply_text("⏳ Signing in...")
    try:
        result = await userbot.sign_in(phone, code)
        if result == "2fa":
            await msg.edit_text("🔐 Two-step verification required. Send your password:")
            return AWAITING_2FA
        me = await userbot.client.get_me()
        premium = "⭐ Premium" if me.premium else "Standard"
        await msg.edit_text(
            f"✅ *Logged in!*\n👤 {me.first_name} | {premium}",
            parse_mode="Markdown",
            reply_markup=main_menu_keyboard(True)
        )
        return MAIN_MENU
    except Exception as e:
        await msg.edit_text(f"❌ Login failed: `{e}`", parse_mode="Markdown")
        return ConversationHandler.END


async def receive_2fa(update: Update, context: ContextTypes.DEFAULT_TYPE):
    password = update.message.text.strip()
    msg = await update.message.reply_text("⏳ Verifying...")
    try:
        await userbot.sign_in_2fa(password)
        me = await userbot.client.get_me()
        await msg.edit_text(
            f"✅ *Logged in with 2FA!*\n👤 {me.first_name}",
            parse_mode="Markdown",
            reply_markup=main_menu_keyboard(True)
        )
        return MAIN_MENU
    except Exception as e:
        await msg.edit_text(f"❌ 2FA failed: `{e}`", parse_mode="Markdown")
        return ConversationHandler.END


# ─── AD CREATION ──────────────────────────────────────────────────────────────

async def new_text_ad(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    context.user_data["ad_type"] = "text"
    await query.edit_message_text(
        "✏️ *New Text Ad*\n\n"
        "Send your ad message text now.\n\n"
        "💡 Tips:\n"
        "• Paste Premium custom emojis directly — they'll be preserved\n"
        "• Use Markdown: *bold*, _italic_, `code`\n"
        "• Or just send plain text",
        parse_mode="Markdown"
    )
    return AWAITING_AD_TEXT


async def new_forward_ad(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    context.user_data["ad_type"] = "forward"
    await query.edit_message_text(
        "🔗 *New Forward Ad*\n\n"
        "Send the link to the message you want to forward:\n"
        "`https://t.me/channelname/123`\n\n"
        "The message will be forwarded as-is, preserving all emojis and formatting.",
        parse_mode="Markdown"
    )
    return AWAITING_FORWARD_LINK


async def receive_ad_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["ad_text"] = update.message.text
    # Serialize entities into Telethon-compatible dicts
    entities = []
    for e in (update.message.entities or []):
        entry = {"offset": e.offset, "length": e.length}
        t = e.type.name
        if t == "CUSTOM_EMOJI":
            entry["_"] = "MessageEntityCustomEmoji"
            entry["document_id"] = int(e.custom_emoji_id)
        elif t == "TEXT_LINK":
            entry["_"] = "MessageEntityTextUrl"
            entry["url"] = e.url
        elif t == "BOLD":
            entry["_"] = "MessageEntityBold"
        elif t == "ITALIC":
            entry["_"] = "MessageEntityItalic"
        elif t == "CODE":
            entry["_"] = "MessageEntityCode"
        elif t == "UNDERLINE":
            entry["_"] = "MessageEntityUnderline"
        elif t == "STRIKETHROUGH":
            entry["_"] = "MessageEntityStrike"
        elif t == "SPOILER":
            entry["_"] = "MessageEntitySpoiler"
        else:
            continue
        entities.append(entry)
    context.user_data["ad_entities"] = entities
    await update.message.reply_text(
        "⏱ *Set broadcast interval* (in minutes):\n"
        "e.g. `60` = post every hour | `120` = every 2 hours\n\n"
        "_Tip: 60–180 minutes is a healthy interval for marketplace groups._",
        parse_mode="Markdown"
    )
    return AWAITING_INTERVAL


async def receive_forward_link(update: Update, context: ContextTypes.DEFAULT_TYPE):
    link = update.message.text.strip()
    try:
        clean = link.replace("https://t.me/", "").replace("http://t.me/", "")
        parts = clean.split("/")
        context.user_data["forward_chat"] = parts[0]
        context.user_data["forward_msg_id"] = int(parts[1])
        context.user_data["forward_link"] = link
    except:
        await update.message.reply_text(
            "❌ Invalid format. Use `https://t.me/channelname/123`",
            parse_mode="Markdown"
        )
        return AWAITING_FORWARD_LINK

    await update.message.reply_text(
        "⏱ *Set broadcast interval* (in minutes):\n`60` = every hour | `120` = every 2 hours",
        parse_mode="Markdown"
    )
    return AWAITING_INTERVAL


async def receive_interval(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        interval = int(update.message.text.strip())
        if interval < 1:
            raise ValueError
    except ValueError:
        await update.message.reply_text("❌ Enter a valid number (minimum 1 minute).")
        return AWAITING_INTERVAL

    ad_type = context.user_data.get("ad_type")
    campaign = {
        "id": userbot.next_campaign_id(),
        "type": ad_type,
        "interval": interval,
        "active": False,
    }

    if ad_type == "text":
        campaign["text"] = context.user_data.get("ad_text", "")
        campaign["source_msg_id"] = context.user_data.get("ad_source_msg_id")
        campaign["source_chat_id"] = context.user_data.get("ad_source_chat_id")
        campaign["entities"] = context.user_data.get("ad_entities", [])
        preview = campaign["text"][:50]
    else:
        campaign["forward_chat"] = context.user_data.get("forward_chat")
        campaign["forward_msg_id"] = context.user_data.get("forward_msg_id")
        campaign["forward_link"] = context.user_data.get("forward_link")
        preview = campaign["forward_link"]

    userbot.save_campaign(campaign)

    await update.message.reply_text(
        f"✅ *Campaign #{campaign['id']} created!*\n\n"
        f"📌 Type: `{ad_type}`\n"
        f"⏱ Interval: every `{interval}` minutes\n"
        f"📝 Preview: _{preview}_\n\n"
        f"Go to *My Campaigns* to start it.",
        parse_mode="Markdown",
        reply_markup=main_menu_keyboard(True)
    )
    return MAIN_MENU


# ─── CAMPAIGN MANAGEMENT ──────────────────────────────────────────────────────

async def list_ads(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    campaigns = userbot.get_campaigns()

    if not campaigns:
        await query.edit_message_text(
            "📋 No campaigns yet.\nCreate one with *New Text Ad* or *New Forward Ad*.",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("« Back", callback_data="back_main")
            ]])
        )
        return MAIN_MENU

    text = "📋 *Your Ad Campaigns:*\n\n"
    buttons = []
    for c in campaigns:
        status = "🟢 Running" if c["active"] else "🔴 Stopped"
        icon = "✏️" if c["type"] == "text" else "🔗"
        preview = (c.get("text") or c.get("forward_link", ""))[:35]
        text += f"{icon} *#{c['id']}* | {status} | Every {c['interval']}min\n`{preview}...`\n\n"

        toggle_label = "⏹ Stop" if c["active"] else "▶️ Start"
        buttons.append([
            InlineKeyboardButton(f"{toggle_label} #{c['id']}", callback_data=f"toggle_{c['id']}"),
            InlineKeyboardButton(f"🗑 Delete #{c['id']}", callback_data=f"delete_{c['id']}"),
        ])

    buttons.append([InlineKeyboardButton("« Back", callback_data="back_main")])
    await query.edit_message_text(
        text, parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(buttons)
    )
    return MANAGE_ADS


async def toggle_campaign(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    campaign_id = int(query.data.split("_")[1])
    campaign = userbot.get_campaign(campaign_id)

    if not campaign:
        await query.answer("Campaign not found!", show_alert=True)
        return MANAGE_ADS

    if not campaign.get("active"):
        targets = userbot.get_targets()
        if not targets:
            await query.answer("⚠️ Set target chats first!", show_alert=True)
            return MANAGE_ADS
        await userbot.start_campaign(campaign_id)
        await query.answer(f"🟢 Campaign #{campaign_id} started!")
    else:
        await userbot.stop_campaign(campaign_id)
        await query.answer(f"⏹ Campaign #{campaign_id} stopped.")

    return await list_ads(update, context)


async def delete_campaign(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    campaign_id = int(query.data.split("_")[1])
    await userbot.stop_campaign(campaign_id)
    userbot.delete_campaign(campaign_id)
    await query.answer(f"🗑 Campaign #{campaign_id} deleted.")
    return await list_ads(update, context)


async def start_all(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if not userbot.get_targets():
        await query.edit_message_text(
            "⚠️ No target chats set! Use *Set Target Chats* first.",
            parse_mode="Markdown",
            reply_markup=main_menu_keyboard(True)
        )
        return MAIN_MENU
    count = await userbot.start_all_campaigns()
    await query.edit_message_text(
        f"🚀 *{count} campaign(s) started!*",
        parse_mode="Markdown",
        reply_markup=main_menu_keyboard(True)
    )
    return MAIN_MENU


async def stop_all(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    count = await userbot.stop_all_campaigns()
    await query.edit_message_text(
        f"⏹ *{count} campaign(s) stopped.*",
        parse_mode="Markdown",
        reply_markup=main_menu_keyboard(True)
    )
    return MAIN_MENU


# ─── TARGETS ─────────────────────────────────────────────────────────────────

async def set_targets(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    current = userbot.get_targets()
    current_str = "\n".join(f"• `{t}`" for t in current) if current else "_None set_"
    await query.edit_message_text(
        f"🎯 *Target Chats*\n\nCurrently set:\n{current_str}\n\n"
        "Send a list of chat usernames or IDs *(one per line)*:\n"
        "`@mymarketplace\n-1001234567890`\n\n"
        "⚠️ You must already be a member of these chats.",
        parse_mode="Markdown"
    )
    return AWAITING_TARGETS


async def receive_targets(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lines = [l.strip() for l in update.message.text.strip().split("\n") if l.strip()]
    userbot.save_targets(lines)
    await update.message.reply_text(
        f"✅ *{len(lines)} target chat(s) saved!*",
        parse_mode="Markdown",
        reply_markup=main_menu_keyboard(True)
    )
    return MAIN_MENU


# ─── MISC ─────────────────────────────────────────────────────────────────────

async def logout(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await userbot.stop_all_campaigns()
    await userbot.logout()
    await query.edit_message_text("🔓 Logged out.", reply_markup=main_menu_keyboard(False))
    return MAIN_MENU


async def back_main(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    logged_in = await userbot.is_logged_in()
    await query.edit_message_text(
        "🤖 *Ad Bot Control Panel*",
        parse_mode="Markdown",
        reply_markup=main_menu_keyboard(logged_in)
    )
    return MAIN_MENU


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logged_in = await userbot.is_logged_in()
    await update.message.reply_text("❌ Cancelled.", reply_markup=main_menu_keyboard(logged_in))
    return MAIN_MENU


# ─── MAIN ─────────────────────────────────────────────────────────────────────

def main():
    app = Application.builder().token(BOT_TOKEN).build()

    conv = ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={
            MAIN_MENU: [
                CallbackQueryHandler(login_start, pattern="^login$"),
                CallbackQueryHandler(new_text_ad, pattern="^new_text_ad$"),
                CallbackQueryHandler(new_forward_ad, pattern="^new_forward_ad$"),
                CallbackQueryHandler(list_ads, pattern="^list_ads$"),
                CallbackQueryHandler(set_targets, pattern="^set_targets$"),
                CallbackQueryHandler(start_all, pattern="^start_all$"),
                CallbackQueryHandler(stop_all, pattern="^stop_all$"),
                CallbackQueryHandler(logout, pattern="^logout$"),
            ],
            AWAITING_PHONE: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_phone)],
            AWAITING_CODE: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_code)],
            AWAITING_2FA: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_2fa)],
            AWAITING_AD_TEXT: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_ad_text)],
            AWAITING_FORWARD_LINK: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_forward_link)],
            AWAITING_INTERVAL: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_interval)],
            AWAITING_TARGETS: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_targets)],
            MANAGE_ADS: [
                CallbackQueryHandler(toggle_campaign, pattern="^toggle_"),
                CallbackQueryHandler(delete_campaign, pattern="^delete_"),
                CallbackQueryHandler(back_main, pattern="^back_main$"),
            ],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        per_message=False,
        allow_reentry=True,
    )

    app.add_handler(conv)
    logger.info("✅ Control bot polling...")
    app.run_polling()


if __name__ == "__main__":
    main()
