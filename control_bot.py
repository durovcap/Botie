"""
Control Bot — flat handler architecture.
Every button works from any state because callbacks are registered at the
Application level, not locked inside ConversationHandler states.
Only the text-input flows (login, campaign name, interval, slot text/link)
use a lightweight ConversationHandler.
"""

import json
import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler,
    MessageHandler, ConversationHandler, filters, ContextTypes,
)
from userbot import AdUserBot

logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

# ── text-input conversation states ───────────────────────────────────────────
(
    IDLE,
    AWAIT_PHONE, AWAIT_CODE, AWAIT_2FA,
    AWAIT_CAMP_NAME, AWAIT_INTERVAL,
    AWAIT_SLOT_TEXT, AWAIT_SLOT_LINK,
) = range(8)

with open("config.json") as f:
    CFG = json.load(f)

BOT_TOKEN = CFG["control_bot_token"]
OWNER_IDS = set(CFG["owner_ids"])

bot = AdUserBot()


# ── auth guard ────────────────────────────────────────────────────────────────

def is_owner(update: Update) -> bool:
    return update.effective_user.id in OWNER_IDS


# ── keyboards ─────────────────────────────────────────────────────────────────

def kb_main(logged_in: bool) -> InlineKeyboardMarkup:
    if not logged_in:
        return InlineKeyboardMarkup([[
            InlineKeyboardButton("🔑 Login Account", callback_data="login"),
        ]])
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📋 Campaigns",    callback_data="list_campaigns"),
         InlineKeyboardButton("➕ New Campaign", callback_data="new_campaign")],
        [InlineKeyboardButton("🚀 Start All",    callback_data="start_all"),
         InlineKeyboardButton("⏹ Stop All",     callback_data="stop_all")],
        [InlineKeyboardButton("👥 Joined Groups",callback_data="view_groups"),
         InlineKeyboardButton("🔓 Logout",       callback_data="logout")],
    ])


def kb_campaign(cid: int, active: bool, n_slots: int) -> InlineKeyboardMarkup:
    toggle = "⏹ Stop" if active else "▶️ Start"
    rows = [
        [InlineKeyboardButton(f"{toggle} Campaign", callback_data=f"ctoggle:{cid}")],
        [InlineKeyboardButton("✏️ Add Text Ad",     callback_data=f"addtext:{cid}"),
         InlineKeyboardButton("🔗 Add Forward Ad",  callback_data=f"addfwd:{cid}")],
    ]
    if n_slots:
        rows.append([InlineKeyboardButton(
            f"🗂 Manage Slots ({n_slots})", callback_data=f"slots:{cid}"
        )])
    rows.append([InlineKeyboardButton("🗑 Delete Campaign", callback_data=f"delcamp:{cid}")])
    rows.append([InlineKeyboardButton("« Back",             callback_data="list_campaigns")])
    return InlineKeyboardMarkup(rows)


def kb_slots(cid: int, slots: list, cur_idx: int) -> InlineKeyboardMarkup:
    rows = []
    for i, s in enumerate(slots):
        marker = " ▶" if i == cur_idx else ""
        label  = s.get("text") or s.get("forward_link", "")
        label  = label[:28] + "…" if len(label) > 28 else label
        icon   = "✏️" if s["type"] == "text" else "🔗"
        rows.append([
            InlineKeyboardButton(f"{icon} {label}{marker}", callback_data="noop"),
            InlineKeyboardButton("🗑",                      callback_data=f"delslot:{cid}:{s['slot_id']}"),
        ])
    rows.append([InlineKeyboardButton("« Back", callback_data=f"camp:{cid}")])
    return InlineKeyboardMarkup(rows)


def kb_back_main() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton("« Main Menu", callback_data="main")]])


# ── shared render helpers ─────────────────────────────────────────────────────

async def render_main(query_or_msg, logged_in: bool, text: str = None):
    txt = text or ("✅ Account connected." if logged_in else "❌ No account connected.")
    body = f"🤖 *Ad Bot Control Panel*\n\n{txt}"
    kb   = kb_main(logged_in)
    if hasattr(query_or_msg, "edit_message_text"):
        await query_or_msg.edit_message_text(body, parse_mode="Markdown", reply_markup=kb)
    else:
        await query_or_msg.reply_text(body, parse_mode="Markdown", reply_markup=kb)


async def render_campaign(query, cid: int):
    c = bot.get_campaign(cid)
    if not c:
        await query.edit_message_text("❌ Campaign not found.", reply_markup=kb_back_main())
        return
    slots = c.get("rotation", [])
    idx   = c.get("current_index", 0)
    n     = len(slots)

    lines = ""
    for i, s in enumerate(slots):
        marker = " ▶️ *next*" if i == idx else f"{i+1}."
        prev   = (s.get("text") or s.get("forward_link", ""))[:36]
        icon   = "✏️" if s["type"] == "text" else "🔗"
        lines += f"{marker} {icon} `{prev}`\n"

    body = (
        f"📁 *{c['name']}* (#{cid})\n\n"
        f"⏱ Interval: every `{c['interval']}` min\n"
        f"🗂 Slots: `{n}`\n"
        f"Status: {'🟢 Running' if c['active'] else '🔴 Stopped'}\n\n"
        + (f"*Rotation:*\n{lines}" if lines else "_No slots yet — add some below._")
    )
    await query.edit_message_text(
        body, parse_mode="Markdown",
        reply_markup=kb_campaign(cid, c["active"], n)
    )


async def render_campaigns(query):
    camps = bot.get_campaigns()
    if not camps:
        await query.edit_message_text(
            "📋 *No campaigns yet.*\n\nCreate one to get started!",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("➕ New Campaign", callback_data="new_campaign")],
                [InlineKeyboardButton("« Back",          callback_data="main")],
            ])
        )
        return
    rows = []
    txt  = "📋 *Your Campaigns:*\n\n"
    for c in camps:
        st   = "🟢" if c["active"] else "🔴"
        n    = len(c.get("rotation", []))
        txt += f"{st} *#{c['id']} — {c['name']}* | {n} slot(s) | {c['interval']}min\n"
        rows.append([InlineKeyboardButton(
            f"{st} #{c['id']} — {c['name']} ({n} slots)",
            callback_data=f"camp:{c['id']}"
        )])
    rows.append([InlineKeyboardButton("➕ New Campaign", callback_data="new_campaign")])
    rows.append([InlineKeyboardButton("« Back",          callback_data="main")])
    await query.edit_message_text(txt, parse_mode="Markdown",
                                  reply_markup=InlineKeyboardMarkup(rows))


async def render_slots(query, cid: int):
    c = bot.get_campaign(cid)
    if not c:
        await query.edit_message_text("❌ Campaign not found.", reply_markup=kb_back_main())
        return
    slots = c.get("rotation", [])
    idx   = c.get("current_index", 0)
    if not slots:
        await query.edit_message_text(
            "🗂 No slots yet. Go back and add some!",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("« Back", callback_data=f"camp:{cid}")
            ]])
        )
        return
    txt = f"🗂 *Slots — {c['name']}*\n\n"
    for i, s in enumerate(slots):
        marker = " ▶️ *next*" if i == idx else f"`{i+1}.`"
        prev   = (s.get("text") or s.get("forward_link", ""))[:40]
        icon   = "✏️" if s["type"] == "text" else "🔗"
        txt   += f"{marker} {icon} `{prev}`\n"
    await query.edit_message_text(
        txt, parse_mode="Markdown",
        reply_markup=kb_slots(cid, slots, idx)
    )


# ── entity serialiser ─────────────────────────────────────────────────────────

def serialise_entities(entities) -> list:
    out = []
    for e in (entities or []):
        d = {"offset": e.offset, "length": e.length}
        t = e.type.name
        if   t == "CUSTOM_EMOJI":  d["_"] = "MessageEntityCustomEmoji"; d["document_id"] = int(e.custom_emoji_id)
        elif t == "TEXT_LINK":     d["_"] = "MessageEntityTextUrl";     d["url"] = e.url
        elif t == "BOLD":          d["_"] = "MessageEntityBold"
        elif t == "ITALIC":        d["_"] = "MessageEntityItalic"
        elif t == "CODE":          d["_"] = "MessageEntityCode"
        elif t == "UNDERLINE":     d["_"] = "MessageEntityUnderline"
        elif t == "STRIKETHROUGH": d["_"] = "MessageEntityStrike"
        elif t == "SPOILER":       d["_"] = "MessageEntitySpoiler"
        else: continue
        out.append(d)
    return out


# ── /start command ────────────────────────────────────────────────────────────

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update):
        await update.message.reply_text("⛔ Unauthorized.")
        return IDLE
    logged_in = await bot.is_logged_in()
    await render_main(update.message, logged_in)
    return IDLE


# ── global callback router ────────────────────────────────────────────────────

async def on_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()

    if not is_owner(update):
        await q.answer("⛔ Unauthorized", show_alert=True)
        return

    d = q.data

    # ── main menu ─────────────────────────────────────────────────────────────
    if d == "main":
        logged_in = await bot.is_logged_in()
        await render_main(q, logged_in)

    elif d == "login":
        await q.edit_message_text(
            "📱 Enter your phone number:\n`+1234567890`",
            parse_mode="Markdown"
        )
        context.user_data["conv"] = "phone"

    elif d == "logout":
        await bot.stop_all_campaigns()
        await bot.logout()
        await render_main(q, False, "🔓 Logged out successfully.")

    elif d == "view_groups":
        await q.edit_message_text("⏳ Fetching joined groups…")
        try:
            groups = await bot.get_joined_groups()
            if not groups:
                body = "👥 *Joined Groups*\n\n_No groups found._"
            else:
                lines = "\n".join(f"• {g['title']} (`{g['id']}`)" for g in groups)
                body  = f"👥 *Joined Groups* ({len(groups)})\n\n{lines}\n\n_Ads go to all of these._"
        except Exception as e:
            body = f"❌ Error: `{e}`"
        await q.edit_message_text(body, parse_mode="Markdown",
                                  reply_markup=InlineKeyboardMarkup([[
                                      InlineKeyboardButton("« Back", callback_data="main")
                                  ]]))

    elif d == "start_all":
        count = await bot.start_all_campaigns()
        await render_main(q, True, f"🚀 {count} campaign(s) started!")

    elif d == "stop_all":
        count = await bot.stop_all_campaigns()
        await render_main(q, True, f"⏹ {count} campaign(s) stopped.")

    # ── campaign list ─────────────────────────────────────────────────────────
    elif d == "list_campaigns":
        await render_campaigns(q)

    elif d == "new_campaign":
        await q.edit_message_text(
            "📛 *New Campaign*\n\nSend a name for this campaign:",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("✖ Cancel", callback_data="list_campaigns")
            ]])
        )
        context.user_data["conv"] = "camp_name"

    # ── open a campaign ───────────────────────────────────────────────────────
    elif d.startswith("camp:"):
        cid = int(d.split(":")[1])
        await render_campaign(q, cid)

    # ── toggle start/stop ─────────────────────────────────────────────────────
    elif d.startswith("ctoggle:"):
        cid = int(d.split(":")[1])
        c   = bot.get_campaign(cid)
        if not c:
            await q.answer("Campaign not found!", show_alert=True)
            return
        if c["active"]:
            await bot.stop_campaign(cid)
        else:
            if not c.get("rotation"):
                await q.answer("⚠️ Add at least one slot first!", show_alert=True)
                return
            await bot.start_campaign(cid)
        await render_campaign(q, cid)

    # ── delete campaign ───────────────────────────────────────────────────────
    elif d.startswith("delcamp:"):
        cid = int(d.split(":")[1])
        await bot.stop_campaign(cid)
        bot.delete_campaign(cid)
        await render_campaigns(q)

    # ── add text slot ─────────────────────────────────────────────────────────
    elif d.startswith("addtext:"):
        cid = int(d.split(":")[1])
        context.user_data["conv"]     = "slot_text"
        context.user_data["conv_cid"] = cid
        await q.edit_message_text(
            "✏️ *Add Text Ad*\n\nSend your ad text now.\n"
            "Premium emojis ⭐, *bold*, _italic_, `code` all supported.",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("✖ Cancel", callback_data=f"camp:{cid}")
            ]])
        )

    # ── add forward slot ──────────────────────────────────────────────────────
    elif d.startswith("addfwd:"):
        cid = int(d.split(":")[1])
        context.user_data["conv"]     = "slot_link"
        context.user_data["conv_cid"] = cid
        await q.edit_message_text(
            "🔗 *Add Forward Ad*\n\nSend the message link:\n`https://t.me/channel/123`",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("✖ Cancel", callback_data=f"camp:{cid}")
            ]])
        )

    # ── manage slots ──────────────────────────────────────────────────────────
    elif d.startswith("slots:"):
        cid = int(d.split(":")[1])
        await render_slots(q, cid)

    # ── delete a slot ─────────────────────────────────────────────────────────
    elif d.startswith("delslot:"):
        _, cid_s, sid_s = d.split(":")
        cid = int(cid_s)
        sid = int(sid_s)
        bot.delete_slot(cid, sid)
        await render_slots(q, cid)

    elif d == "noop":
        pass  # slot label button — do nothing


# ── text message router ───────────────────────────────────────────────────────

async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update):
        return

    conv = context.user_data.get("conv")
    text = update.message.text.strip()

    # ── login: phone ──────────────────────────────────────────────────────────
    if conv == "phone":
        context.user_data["phone"] = text
        context.user_data["conv"]  = None
        msg = await update.message.reply_text("⏳ Sending OTP…")
        try:
            await bot.send_code(text)
            await msg.edit_text(
                "📨 Code sent! Enter it with spaces:\n`1 2 3 4 5`",
                parse_mode="Markdown"
            )
            context.user_data["conv"] = "code"
        except Exception as e:
            await msg.edit_text(f"❌ Failed: `{e}`", parse_mode="Markdown",
                                reply_markup=kb_back_main())

    # ── login: OTP code ───────────────────────────────────────────────────────
    elif conv == "code":
        code = text.replace(" ", "")
        context.user_data["conv"] = None
        msg = await update.message.reply_text("⏳ Signing in…")
        try:
            result = await bot.sign_in(context.user_data.get("phone", ""), code)
            if result == "2fa":
                await msg.edit_text("🔐 Enter your 2FA password:")
                context.user_data["conv"] = "2fa"
            else:
                me = await bot.client.get_me()
                premium = "⭐ Premium" if me.premium else "Standard"
                await msg.edit_text("⏳ Setting up logs channel…")
                await bot.setup_logs_channel()
                await msg.edit_text(
                    f"✅ *Logged in!*\n👤 {me.first_name} | {premium}\n📋 Logs channel ready.",
                    parse_mode="Markdown",
                    reply_markup=kb_main(True)
                )
        except Exception as e:
            await msg.edit_text(f"❌ Login failed: `{e}`", parse_mode="Markdown",
                                reply_markup=kb_back_main())

    # ── login: 2FA ────────────────────────────────────────────────────────────
    elif conv == "2fa":
        context.user_data["conv"] = None
        msg = await update.message.reply_text("⏳ Verifying…")
        try:
            await bot.sign_in_2fa(text)
            me = await bot.client.get_me()
            await msg.edit_text("⏳ Setting up logs channel…")
            await bot.setup_logs_channel()
            await msg.edit_text(
                f"✅ *Logged in with 2FA!*\n👤 {me.first_name}\n📋 Logs channel ready.",
                parse_mode="Markdown",
                reply_markup=kb_main(True)
            )
        except Exception as e:
            await msg.edit_text(f"❌ 2FA failed: `{e}`", parse_mode="Markdown",
                                reply_markup=kb_back_main())

    # ── new campaign: name ────────────────────────────────────────────────────
    elif conv == "camp_name":
        context.user_data["camp_name"] = text
        context.user_data["conv"]      = "interval"
        await update.message.reply_text(
            "⏱ Set the *interval* in minutes between each ad:\n"
            "`60` = 1 hour  |  `120` = 2 hours",
            parse_mode="Markdown"
        )

    # ── new campaign: interval ────────────────────────────────────────────────
    elif conv == "interval":
        try:
            mins = int(text)
            if mins < 1:
                raise ValueError
        except ValueError:
            await update.message.reply_text("❌ Enter a whole number ≥ 1.")
            return
        context.user_data["conv"] = None
        name     = context.user_data.pop("camp_name", "Untitled")
        campaign = bot.create_campaign(name, mins)
        await update.message.reply_text(
            f"✅ *Campaign \"{name}\" created!*\n"
            f"⏱ {mins} min interval\n\nNow add ad slots:",
            parse_mode="Markdown",
            reply_markup=kb_campaign(campaign["id"], False, 0)
        )

    # ── add text slot ─────────────────────────────────────────────────────────
    elif conv == "slot_text":
        context.user_data["conv"] = None
        cid  = context.user_data.pop("conv_cid", None)
        slot = {
            "type":     "text",
            "text":     text,
            "entities": serialise_entities(update.message.entities),
        }
        bot.add_slot(cid, slot)
        c = bot.get_campaign(cid)
        n = len(c.get("rotation", [])) if c else 0
        await update.message.reply_text(
            f"✅ *Text slot added!* Campaign now has *{n}* slot(s).",
            parse_mode="Markdown",
            reply_markup=kb_campaign(cid, c["active"] if c else False, n)
        )

    # ── add forward slot ──────────────────────────────────────────────────────
    elif conv == "slot_link":
        cid = context.user_data.get("conv_cid")
        try:
            clean  = text.replace("https://t.me/", "").replace("http://t.me/", "")
            parts  = clean.split("/")
            f_chat = parts[0]
            f_mid  = int(parts[1])
        except Exception:
            await update.message.reply_text(
                "❌ Invalid link. Use `https://t.me/channelname/123`",
                parse_mode="Markdown"
            )
            return
        context.user_data["conv"] = None
        context.user_data.pop("conv_cid", None)
        slot = {
            "type":           "forward",
            "forward_chat":   f_chat,
            "forward_msg_id": f_mid,
            "forward_link":   text,
        }
        bot.add_slot(cid, slot)
        c = bot.get_campaign(cid)
        n = len(c.get("rotation", [])) if c else 0
        await update.message.reply_text(
            f"✅ *Forward slot added!* Campaign now has *{n}* slot(s).",
            parse_mode="Markdown",
            reply_markup=kb_campaign(cid, c["active"] if c else False, n)
        )

    # ── no active conv ────────────────────────────────────────────────────────
    else:
        logged_in = await bot.is_logged_in()
        await update.message.reply_text(
            "Use /start to open the control panel.",
            reply_markup=kb_main(logged_in)
        )


# ── main ──────────────────────────────────────────────────────────────────────

def main():
    app = Application.builder().token(BOT_TOKEN).build()

    # /start — just shows the menu, no state machine needed
    app.add_handler(CommandHandler("start", cmd_start))

    # All button presses — registered globally, always work
    app.add_handler(CallbackQueryHandler(on_callback))

    # All text — routed by context.user_data["conv"] key
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))

    logger.info("✅ Bot is running…")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
