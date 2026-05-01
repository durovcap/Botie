"""
Control Bot - Telegram Bot interface to manage ad rotation campaigns
"""

import json
import logging
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

# ─── Conversation states ──────────────────────────────────────────────────────
(
    MAIN_MENU,
    AWAITING_PHONE, AWAITING_CODE, AWAITING_2FA,
    AWAITING_CAMPAIGN_NAME, AWAITING_INTERVAL,
    CAMPAIGN_MENU,
    AWAITING_SLOT_TEXT, AWAITING_SLOT_LINK,
    MANAGE_SLOTS,
) = range(10)

with open("config.json") as f:
    CONFIG = json.load(f)

BOT_TOKEN = CONFIG["control_bot_token"]
OWNER_IDS = CONFIG["owner_ids"]

userbot = AdUserBot()


# ─── Keyboards ────────────────────────────────────────────────────────────────

def main_menu_keyboard(logged_in: bool) -> InlineKeyboardMarkup:
    if not logged_in:
        return InlineKeyboardMarkup([[
            InlineKeyboardButton("🔑 Login Telegram Account", callback_data="login")
        ]])
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📋 My Campaigns", callback_data="list_campaigns")],
        [InlineKeyboardButton("➕ New Campaign", callback_data="new_campaign")],
        [
            InlineKeyboardButton("🚀 Start All", callback_data="start_all"),
            InlineKeyboardButton("⏹ Stop All",  callback_data="stop_all"),
        ],
        [
            InlineKeyboardButton("👥 Joined Groups", callback_data="view_groups"),
            InlineKeyboardButton("🔓 Logout",        callback_data="logout"),
        ],
    ])


def back_btn(target: str = "back_main") -> list:
    return [InlineKeyboardButton("« Back", callback_data=target)]


def campaign_menu_keyboard(cid: int, active: bool, slot_count: int) -> InlineKeyboardMarkup:
    toggle = "⏹ Stop" if active else "▶️ Start"
    buttons = [
        [InlineKeyboardButton(f"{toggle} Campaign", callback_data=f"ctoggle_{cid}")],
        [
            InlineKeyboardButton("✏️ Add Text Ad",    callback_data=f"addtext_{cid}"),
            InlineKeyboardButton("🔗 Add Forward Ad", callback_data=f"addfwd_{cid}"),
        ],
    ]
    if slot_count > 0:
        buttons.append([InlineKeyboardButton(
            f"🗂 Manage Slots ({slot_count})", callback_data=f"slots_{cid}"
        )])
    buttons.append([InlineKeyboardButton("🗑 Delete Campaign", callback_data=f"delcamp_{cid}")])
    buttons.append(back_btn("list_campaigns"))
    return InlineKeyboardMarkup(buttons)


# ─── Helpers ──────────────────────────────────────────────────────────────────

def serialize_entities(entities) -> list:
    """Convert python-telegram-bot MessageEntity list → Telethon-compatible dicts."""
    out = []
    for e in (entities or []):
        entry = {"offset": e.offset, "length": e.length}
        t = e.type.name
        if   t == "CUSTOM_EMOJI":   entry["_"] = "MessageEntityCustomEmoji"; entry["document_id"] = int(e.custom_emoji_id)
        elif t == "TEXT_LINK":      entry["_"] = "MessageEntityTextUrl";     entry["url"] = e.url
        elif t == "BOLD":           entry["_"] = "MessageEntityBold"
        elif t == "ITALIC":         entry["_"] = "MessageEntityItalic"
        elif t == "CODE":           entry["_"] = "MessageEntityCode"
        elif t == "UNDERLINE":      entry["_"] = "MessageEntityUnderline"
        elif t == "STRIKETHROUGH":  entry["_"] = "MessageEntityStrike"
        elif t == "SPOILER":        entry["_"] = "MessageEntitySpoiler"
        else: continue
        out.append(entry)
    return out


def slot_preview(slot: dict, max_len: int = 38) -> str:
    if slot["type"] == "text":
        icon = "✏️"
        txt  = slot.get("text", "")
    else:
        icon = "🔗"
        txt  = slot.get("forward_link", "")
    txt = txt[:max_len] + ("…" if len(txt) > max_len else "")
    return f"{icon} `{txt}`"


# ─── /start ───────────────────────────────────────────────────────────────────

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


# ─── LOGIN ────────────────────────────────────────────────────────────────────

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
            "📨 Code sent! Enter it *(with spaces)*:\n`1 2 3 4 5`",
            parse_mode="Markdown"
        )
        return AWAITING_CODE
    except Exception as e:
        await msg.edit_text(f"❌ Error: `{e}`", parse_mode="Markdown")
        return ConversationHandler.END


async def receive_code(update: Update, context: ContextTypes.DEFAULT_TYPE):
    code  = update.message.text.replace(" ", "").strip()
    phone = context.user_data.get("phone")
    msg   = await update.message.reply_text("⏳ Signing in...")
    try:
        result = await userbot.sign_in(phone, code)
        if result == "2fa":
            await msg.edit_text("🔐 Two-step verification required. Send your password:")
            return AWAITING_2FA
        me      = await userbot.client.get_me()
        premium = "⭐ Premium" if me.premium else "Standard"
        await msg.edit_text("⏳ Setting up logs channel...")
        await userbot.setup_logs_channel()
        await msg.edit_text(
            f"✅ *Logged in!*\n👤 {me.first_name} | {premium}\n📋 Logs channel ready.",
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
        await msg.edit_text("⏳ Setting up logs channel...")
        await userbot.setup_logs_channel()
        await msg.edit_text(
            f"✅ *Logged in with 2FA!*\n👤 {me.first_name}\n📋 Logs channel ready.",
            parse_mode="Markdown",
            reply_markup=main_menu_keyboard(True)
        )
        return MAIN_MENU
    except Exception as e:
        await msg.edit_text(f"❌ 2FA failed: `{e}`", parse_mode="Markdown")
        return ConversationHandler.END


# ─── CAMPAIGN CREATION ────────────────────────────────────────────────────────

async def new_campaign(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text(
        "📛 *New Campaign*\n\nGive this campaign a name:\n_(e.g. \"Market Rotation\", \"Phone Ads\")_",
        parse_mode="Markdown"
    )
    return AWAITING_CAMPAIGN_NAME


async def receive_campaign_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["campaign_name"] = update.message.text.strip()
    await update.message.reply_text(
        "⏱ Set the *interval between each ad* (in minutes):\n"
        "`60` = 1 hour | `120` = 2 hours\n\n"
        "_Each slot in the rotation will fire once per interval, in order._",
        parse_mode="Markdown"
    )
    return AWAITING_INTERVAL


async def receive_interval(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        interval = int(update.message.text.strip())
        if interval < 1:
            raise ValueError
    except ValueError:
        await update.message.reply_text("❌ Enter a valid number (minimum 1).")
        return AWAITING_INTERVAL

    name     = context.user_data.get("campaign_name", "Untitled")
    campaign = userbot.create_campaign(name, interval)

    context.user_data["active_campaign_id"] = campaign["id"]
    await update.message.reply_text(
        f"✅ *Campaign \"{name}\" created!*\n"
        f"⏱ Interval: every `{interval}` min\n\n"
        f"Now add ad slots to this campaign:",
        parse_mode="Markdown",
        reply_markup=campaign_menu_keyboard(campaign["id"], False, 0)
    )
    return CAMPAIGN_MENU


# ─── CAMPAIGN LIST ────────────────────────────────────────────────────────────

async def list_campaigns(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    campaigns = userbot.get_campaigns()

    if not campaigns:
        await query.edit_message_text(
            "📋 No campaigns yet. Create one first!",
            reply_markup=InlineKeyboardMarkup([back_btn()])
        )
        return MAIN_MENU

    text    = "📋 *Your Campaigns:*\n\n"
    buttons = []
    for c in campaigns:
        status     = "🟢" if c["active"] else "🔴"
        slot_count = len(c.get("rotation", []))
        idx        = c.get("current_index", 0)
        text += (
            f"{status} *#{c['id']} {c['name']}*\n"
            f"  ⏱ {c['interval']}min | 🗂 {slot_count} slot(s)"
            + (f" | next: slot {idx+1}" if slot_count else "") + "\n\n"
        )
        buttons.append([InlineKeyboardButton(
            f"{status} #{c['id']} — {c['name']} ({slot_count} slots)",
            callback_data=f"opencampaign_{c['id']}"
        )])

    buttons.append(back_btn())
    await query.edit_message_text(
        text, parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(buttons)
    )
    return CAMPAIGN_MENU


async def open_campaign(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    cid      = int(query.data.split("_")[1])
    campaign = userbot.get_campaign(cid)
    if not campaign:
        await query.answer("Campaign not found!", show_alert=True)
        return CAMPAIGN_MENU

    context.user_data["active_campaign_id"] = cid
    slots     = campaign.get("rotation", [])
    idx       = campaign.get("current_index", 0)
    slot_count = len(slots)

    text = (
        f"📁 *Campaign #{cid}: {campaign['name']}*\n\n"
        f"⏱ Interval: every `{campaign['interval']}` min\n"
        f"🗂 Slots: `{slot_count}`\n"
        f"🔄 Next slot: `{idx + 1 if slot_count else '—'}`\n"
        f"Status: {'🟢 Running' if campaign['active'] else '🔴 Stopped'}\n\n"
    )

    if slots:
        text += "*Rotation order:*\n"
        for i, s in enumerate(slots):
            marker = "▶️" if i == idx else f"`{i+1}.`"
            text += f"{marker} {slot_preview(s)}\n"

    await query.edit_message_text(
        text, parse_mode="Markdown",
        reply_markup=campaign_menu_keyboard(cid, campaign["active"], slot_count)
    )
    return CAMPAIGN_MENU


# ─── SLOT ADDING ──────────────────────────────────────────────────────────────

async def add_text_slot(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    cid = int(query.data.split("_")[1])
    context.user_data["active_campaign_id"] = cid
    context.user_data["slot_type"] = "text"
    await query.edit_message_text(
        "✏️ *Add Text Ad Slot*\n\n"
        "Send the ad message text now.\n"
        "• Premium custom emojis are preserved ⭐\n"
        "• *bold*, _italic_, `code`, [links](url) all work",
        parse_mode="Markdown"
    )
    return AWAITING_SLOT_TEXT


async def add_forward_slot(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    cid = int(query.data.split("_")[1])
    context.user_data["active_campaign_id"] = cid
    context.user_data["slot_type"] = "forward"
    await query.edit_message_text(
        "🔗 *Add Forward Ad Slot*\n\n"
        "Send the message link to forward:\n"
        "`https://t.me/channelname/123`",
        parse_mode="Markdown"
    )
    return AWAITING_SLOT_LINK


async def receive_slot_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cid = context.user_data.get("active_campaign_id")
    slot = {
        "type":     "text",
        "text":     update.message.text,
        "entities": serialize_entities(update.message.entities),
    }
    slot_id = userbot.add_slot(cid, slot)
    campaign = userbot.get_campaign(cid)
    slots    = campaign.get("rotation", [])

    await update.message.reply_text(
        f"✅ *Text slot #{slot_id} added!*\n"
        f"Campaign now has *{len(slots)}* slot(s) in rotation.\n\n"
        f"Add more or manage slots below:",
        parse_mode="Markdown",
        reply_markup=campaign_menu_keyboard(cid, campaign["active"], len(slots))
    )
    return CAMPAIGN_MENU


async def receive_slot_link(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cid  = context.user_data.get("active_campaign_id")
    link = update.message.text.strip()
    try:
        clean = link.replace("https://t.me/", "").replace("http://t.me/", "")
        parts = clean.split("/")
        forward_chat   = parts[0]
        forward_msg_id = int(parts[1])
    except Exception:
        await update.message.reply_text(
            "❌ Invalid format. Use `https://t.me/channelname/123`",
            parse_mode="Markdown"
        )
        return AWAITING_SLOT_LINK

    slot = {
        "type":           "forward",
        "forward_chat":   forward_chat,
        "forward_msg_id": forward_msg_id,
        "forward_link":   link,
    }
    slot_id  = userbot.add_slot(cid, slot)
    campaign = userbot.get_campaign(cid)
    slots    = campaign.get("rotation", [])

    await update.message.reply_text(
        f"✅ *Forward slot #{slot_id} added!*\n"
        f"Campaign now has *{len(slots)}* slot(s) in rotation.",
        parse_mode="Markdown",
        reply_markup=campaign_menu_keyboard(cid, campaign["active"], len(slots))
    )
    return CAMPAIGN_MENU


# ─── SLOT MANAGEMENT ─────────────────────────────────────────────────────────

async def manage_slots(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    cid      = int(query.data.split("_")[1])
    campaign = userbot.get_campaign(cid)
    slots    = campaign.get("rotation", []) if campaign else []
    idx      = campaign.get("current_index", 0) if campaign else 0

    context.user_data["active_campaign_id"] = cid

    if not slots:
        await query.edit_message_text(
            "🗂 No slots yet — go back and add some!",
            reply_markup=InlineKeyboardMarkup([back_btn(f"opencampaign_{cid}")])
        )
        return MANAGE_SLOTS

    text    = f"🗂 *Slots for Campaign #{cid}: {campaign['name']}*\n\n"
    buttons = []
    for i, s in enumerate(slots):
        marker  = " ▶️ *next*" if i == idx else ""
        text   += f"*Slot #{s['slot_id']}*{marker}\n{slot_preview(s)}\n\n"
        buttons.append([InlineKeyboardButton(
            f"🗑 Delete slot #{s['slot_id']}", callback_data=f"delslot_{cid}_{s['slot_id']}"
        )])

    buttons.append(back_btn(f"opencampaign_{cid}"))
    await query.edit_message_text(
        text, parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(buttons)
    )
    return MANAGE_SLOTS


async def delete_slot(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query    = update.callback_query
    await query.answer()
    parts    = query.data.split("_")   # delslot_{cid}_{slot_id}
    cid      = int(parts[1])
    slot_id  = int(parts[2])
    userbot.delete_slot(cid, slot_id)
    await query.answer(f"🗑 Slot #{slot_id} deleted.", show_alert=False)
    # Refresh the slots view
    query.data = f"slots_{cid}"
    return await manage_slots(update, context)


# ─── CAMPAIGN TOGGLE / DELETE ─────────────────────────────────────────────────

async def toggle_campaign(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query    = update.callback_query
    await query.answer()
    cid      = int(query.data.split("_")[1])
    campaign = userbot.get_campaign(cid)
    if not campaign:
        await query.answer("Campaign not found!", show_alert=True)
        return CAMPAIGN_MENU

    if campaign["active"]:
        await userbot.stop_campaign(cid)
        await query.answer(f"⏹ Stopped.", show_alert=False)
    else:
        if not campaign.get("rotation"):
            await query.answer("⚠️ Add at least one ad slot first!", show_alert=True)
            return CAMPAIGN_MENU
        await userbot.start_campaign(cid)
        await query.answer(f"🟢 Started!", show_alert=False)

    # Refresh campaign view
    query.data = f"opencampaign_{cid}"
    return await open_campaign(update, context)


async def delete_campaign_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    cid   = int(query.data.split("_")[1])
    await userbot.stop_campaign(cid)
    userbot.delete_campaign(cid)
    await query.answer("🗑 Campaign deleted.", show_alert=False)
    query.data = "list_campaigns"
    return await list_campaigns(update, context)


# ─── START ALL / STOP ALL ─────────────────────────────────────────────────────

async def start_all(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
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


# ─── VIEW JOINED GROUPS ───────────────────────────────────────────────────────

async def view_groups(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text("⏳ Fetching joined groups...")
    try:
        groups = await userbot.get_joined_groups()
        if not groups:
            text = "👥 *Joined Groups*\n\n_No groups found._"
        else:
            lines = "\n".join(f"• {g['title']} (`{g['id']}`)" for g in groups)
            text  = f"👥 *Joined Groups* ({len(groups)} total)\n\n{lines}\n\n_Ads are sent to all of these._"
        await query.edit_message_text(
            text, parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([back_btn()])
        )
    except Exception as e:
        await query.edit_message_text(
            f"❌ Error: `{e}`", parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([back_btn()])
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
    query     = update.callback_query
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
                CallbackQueryHandler(login_start,     pattern="^login$"),
                CallbackQueryHandler(new_campaign,    pattern="^new_campaign$"),
                CallbackQueryHandler(list_campaigns,  pattern="^list_campaigns$"),
                CallbackQueryHandler(start_all,       pattern="^start_all$"),
                CallbackQueryHandler(stop_all,        pattern="^stop_all$"),
                CallbackQueryHandler(view_groups,     pattern="^view_groups$"),
                CallbackQueryHandler(logout,          pattern="^logout$"),
                CallbackQueryHandler(back_main,       pattern="^back_main$"),
            ],
            AWAITING_PHONE:  [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_phone)],
            AWAITING_CODE:   [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_code)],
            AWAITING_2FA:    [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_2fa)],

            AWAITING_CAMPAIGN_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_campaign_name)],
            AWAITING_INTERVAL:      [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_interval)],

            CAMPAIGN_MENU: [
                CallbackQueryHandler(list_campaigns,         pattern="^list_campaigns$"),
                CallbackQueryHandler(open_campaign,          pattern="^opencampaign_"),
                CallbackQueryHandler(add_text_slot,          pattern="^addtext_"),
                CallbackQueryHandler(add_forward_slot,       pattern="^addfwd_"),
                CallbackQueryHandler(manage_slots,           pattern="^slots_"),
                CallbackQueryHandler(toggle_campaign,        pattern="^ctoggle_"),
                CallbackQueryHandler(delete_campaign_handler,pattern="^delcamp_"),
                CallbackQueryHandler(back_main,              pattern="^back_main$"),
            ],
            AWAITING_SLOT_TEXT: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_slot_text)],
            AWAITING_SLOT_LINK: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_slot_link)],

            MANAGE_SLOTS: [
                CallbackQueryHandler(delete_slot,    pattern="^delslot_"),
                CallbackQueryHandler(manage_slots,   pattern="^slots_"),
                CallbackQueryHandler(open_campaign,  pattern="^opencampaign_"),
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
