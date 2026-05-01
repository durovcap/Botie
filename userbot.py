"""
Ad Userbot - Telethon-based user account client
- Auto-detects all groups the account has joined
- Creates a private "Ad Bot Logs" channel for live logging
- Supports ad rotation: cycles through a list of ads one per interval
- Supports Telegram Premium custom emojis
"""

import asyncio
import json
import logging
import os
from datetime import datetime
from typing import Optional

from telethon import TelegramClient, errors
from telethon.tl.functions.channels import CreateChannelRequest
from telethon.tl.types import (
    Channel, Chat,
    MessageEntityBold, MessageEntityItalic, MessageEntityCode,
    MessageEntityUnderline, MessageEntityStrike, MessageEntitySpoiler,
    MessageEntityTextUrl, MessageEntityCustomEmoji,
)

logger = logging.getLogger(__name__)

DATA_FILE    = "data.json"
SESSION_FILE = "userbot.session"


# ─── Persistence ──────────────────────────────────────────────────────────────

def load_data() -> dict:
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE) as f:
            return json.load(f)
    return {"campaigns": [], "next_id": 1, "logs_channel_id": None}


def save_data(data: dict):
    with open(DATA_FILE, "w") as f:
        json.dump(data, f, indent=2)


# ─── Data model for a campaign ────────────────────────────────────────────────
#
# campaign = {
#   "id":            int,
#   "name":          str,          # friendly label e.g. "Market rotation"
#   "interval":      int,          # minutes between each send
#   "active":        bool,
#   "current_index": int,          # which rotation slot fires next (0-based)
#   "rotation": [                  # list of ad slots — any mix of text/forward
#     {
#       "slot_id":   int,          # unique within this campaign
#       "type":      "text" | "forward",
#       # if text:
#       "text":      str,
#       "entities":  [...],        # Telethon-compatible entity dicts
#       # if forward:
#       "forward_chat":   str,
#       "forward_msg_id": int,
#       "forward_link":   str,
#     },
#     ...
#   ]
# }


class AdUserBot:
    def __init__(self):
        with open("config.json") as f:
            cfg = json.load(f)
        self.api_id   = cfg["api_id"]
        self.api_hash = cfg["api_hash"]
        self.client: Optional[TelegramClient] = None
        self._tasks: dict[int, asyncio.Task] = {}
        self._phone_hash = None
        self._logs_channel_id: Optional[int] = load_data().get("logs_channel_id")
        self._init_client()

    def _init_client(self):
        self.client = TelegramClient(SESSION_FILE, self.api_id, self.api_hash)

    # ─── Auth ─────────────────────────────────────────────────────────────────

    async def is_logged_in(self) -> bool:
        try:
            if not self.client.is_connected():
                await self.client.connect()
            return await self.client.is_user_authorized()
        except Exception:
            return False

    async def send_code(self, phone: str):
        if not self.client.is_connected():
            await self.client.connect()
        result = await self.client.send_code_request(phone)
        self._phone_hash = result.phone_code_hash

    async def sign_in(self, phone: str, code: str) -> str:
        try:
            await self.client.sign_in(phone, code, phone_code_hash=self._phone_hash)
            return "ok"
        except errors.SessionPasswordNeededError:
            return "2fa"

    async def sign_in_2fa(self, password: str):
        await self.client.sign_in(password=password)

    async def logout(self):
        await self.stop_all_campaigns()
        try:
            await self.client.log_out()
        except Exception:
            pass
        if os.path.exists(SESSION_FILE):
            os.remove(SESSION_FILE)
        data = load_data()
        data["logs_channel_id"] = None
        save_data(data)
        self._logs_channel_id = None
        self._init_client()

    # ─── Logs Channel ─────────────────────────────────────────────────────────

    async def setup_logs_channel(self) -> int:
        data = load_data()
        existing_id = data.get("logs_channel_id")
        if existing_id:
            try:
                await self.client.get_entity(existing_id)
                self._logs_channel_id = existing_id
                return existing_id
            except Exception:
                pass

        result = await self.client(CreateChannelRequest(
            title="📋 Ad Bot Logs",
            about="Live logs for the Ad Bot. Auto-generated.",
            megagroup=False,
        ))
        channel_id = result.chats[0].id
        full_id = int(f"-100{channel_id}")
        data["logs_channel_id"] = full_id
        save_data(data)
        self._logs_channel_id = full_id
        await self._raw_log("✅ *Ad Bot Logs* channel ready.")
        return full_id

    async def _raw_log(self, text: str):
        if not self._logs_channel_id:
            return
        try:
            await self.client.send_message(self._logs_channel_id, text, parse_mode="md")
        except Exception as e:
            logger.error(f"Logs channel write failed: {e}")

    async def tg_log(self, text: str, level: str = "INFO"):
        icons = {"INFO": "ℹ️", "OK": "✅", "WARN": "⚠️", "ERROR": "❌", "SEND": "📤"}
        icon  = icons.get(level, "•")
        ts    = datetime.now().strftime("%H:%M:%S")
        logger.info(text)
        await self._raw_log(f"{icon} `[{ts}]` {text}")

    # ─── Group Discovery ──────────────────────────────────────────────────────

    async def get_joined_groups(self) -> list[dict]:
        groups = []
        async for dialog in self.client.iter_dialogs():
            entity = dialog.entity
            is_group      = isinstance(entity, Chat)
            is_supergroup = isinstance(entity, Channel) and entity.megagroup
            if not (is_group or is_supergroup):
                continue
            if self._logs_channel_id and dialog.id == self._logs_channel_id:
                continue
            groups.append({"id": dialog.id, "title": dialog.title})
        return groups

    # ─── Campaign CRUD ────────────────────────────────────────────────────────

    def next_campaign_id(self) -> int:
        data = load_data()
        cid  = data["next_id"]
        data["next_id"] += 1
        save_data(data)
        return cid

    def next_slot_id(self, campaign_id: int) -> int:
        """Return next available slot_id within a campaign."""
        c = self.get_campaign(campaign_id)
        if not c or not c["rotation"]:
            return 1
        return max(s["slot_id"] for s in c["rotation"]) + 1

    def create_campaign(self, name: str, interval: int) -> dict:
        campaign = {
            "id":            self.next_campaign_id(),
            "name":          name,
            "interval":      interval,
            "active":        False,
            "current_index": 0,
            "rotation":      [],
        }
        data = load_data()
        data["campaigns"].append(campaign)
        save_data(data)
        return campaign

    def get_campaigns(self) -> list:
        return load_data()["campaigns"]

    def get_campaign(self, cid: int) -> Optional[dict]:
        for c in load_data()["campaigns"]:
            if c["id"] == cid:
                return c
        return None

    def update_campaign(self, cid: int, updates: dict):
        data = load_data()
        for c in data["campaigns"]:
            if c["id"] == cid:
                c.update(updates)
        save_data(data)

    def delete_campaign(self, cid: int):
        data = load_data()
        data["campaigns"] = [c for c in data["campaigns"] if c["id"] != cid]
        save_data(data)

    # ─── Rotation Slot CRUD ───────────────────────────────────────────────────

    def add_slot(self, campaign_id: int, slot: dict) -> int:
        """Add an ad slot to a campaign's rotation. Returns the slot_id."""
        data = load_data()
        for c in data["campaigns"]:
            if c["id"] == campaign_id:
                slot_id = (max((s["slot_id"] for s in c["rotation"]), default=0) + 1)
                slot["slot_id"] = slot_id
                c["rotation"].append(slot)
                save_data(data)
                return slot_id
        return -1

    def delete_slot(self, campaign_id: int, slot_id: int):
        """Remove a slot from a campaign's rotation."""
        data = load_data()
        for c in data["campaigns"]:
            if c["id"] == campaign_id:
                c["rotation"] = [s for s in c["rotation"] if s["slot_id"] != slot_id]
                # Reset index if it's now out of bounds
                if c["rotation"]:
                    c["current_index"] = c["current_index"] % len(c["rotation"])
                else:
                    c["current_index"] = 0
                save_data(data)
                return

    def get_slots(self, campaign_id: int) -> list:
        c = self.get_campaign(campaign_id)
        return c["rotation"] if c else []

    # ─── Entity Rebuilding ────────────────────────────────────────────────────

    def _rebuild_entities(self, raw: list) -> list:
        out = []
        for e in raw:
            kind = e.get("_", "")
            o, l = e["offset"], e["length"]
            if   kind == "MessageEntityCustomEmoji":
                out.append(MessageEntityCustomEmoji(o, l, e["document_id"]))
            elif kind == "MessageEntityBold":
                out.append(MessageEntityBold(o, l))
            elif kind == "MessageEntityItalic":
                out.append(MessageEntityItalic(o, l))
            elif kind == "MessageEntityCode":
                out.append(MessageEntityCode(o, l))
            elif kind == "MessageEntityUnderline":
                out.append(MessageEntityUnderline(o, l))
            elif kind == "MessageEntityStrike":
                out.append(MessageEntityStrike(o, l))
            elif kind == "MessageEntitySpoiler":
                out.append(MessageEntitySpoiler(o, l))
            elif kind == "MessageEntityTextUrl":
                out.append(MessageEntityTextUrl(o, l, e["url"]))
        return out

    # ─── Broadcasting ─────────────────────────────────────────────────────────

    async def _send_slot_to_group(self, slot: dict, group: dict):
        target = group["id"]
        if slot["type"] == "text":
            entities = self._rebuild_entities(slot.get("entities", []))
            await self.client.send_message(
                entity=target,
                message=slot.get("text", ""),
                formatting_entities=entities if entities else None,
            )
        elif slot["type"] == "forward":
            await self.client.forward_messages(
                entity=target,
                messages=slot["forward_msg_id"],
                from_peer=slot["forward_chat"],
            )

    async def _fire_rotation(self, campaign: dict):
        """Send the current rotation slot to all groups, then advance index."""
        rotation = campaign.get("rotation", [])
        if not rotation:
            await self.tg_log(
                f"Campaign **#{campaign['id']} {campaign['name']}** has no ads — skipping.", "WARN"
            )
            return

        idx  = campaign.get("current_index", 0) % len(rotation)
        slot = rotation[idx]
        next_idx = (idx + 1) % len(rotation)

        slot_label = f"slot #{slot['slot_id']} ({slot['type']})"
        preview = slot.get("text", slot.get("forward_link", ""))[:40]

        groups = await self.get_joined_groups()
        if not groups:
            await self.tg_log("No joined groups found — nothing to send.", "WARN")
            return

        await self.tg_log(
            f"🔄 Campaign **#{campaign['id']} {campaign['name']}** — "
            f"firing {slot_label} [{idx+1}/{len(rotation)}] → {len(groups)} group(s)\n"
            f"📝 `{preview}`",
            "SEND"
        )

        sent = failed = 0
        for g in groups:
            try:
                await self._send_slot_to_group(slot, g)
                await self.tg_log(f"Sent to **{g['title']}**", "OK")
                sent += 1
                await asyncio.sleep(3)

            except errors.FloodWaitError as e:
                await self.tg_log(f"FloodWait {e.seconds}s — pausing...", "WARN")
                await asyncio.sleep(e.seconds + 2)
                try:
                    await self._send_slot_to_group(slot, g)
                    await self.tg_log(f"Retry OK → **{g['title']}**", "OK")
                    sent += 1
                except Exception as retry_err:
                    await self.tg_log(f"Retry failed **{g['title']}**: `{retry_err}`", "ERROR")
                    failed += 1

            except errors.ChatWriteForbiddenError:
                await self.tg_log(f"No write permission in **{g['title']}** — skipped.", "WARN")
                failed += 1

            except errors.UserBannedInChannelError:
                await self.tg_log(f"Banned in **{g['title']}** — skipped.", "WARN")
                failed += 1

            except Exception as e:
                await self.tg_log(f"Error in **{g['title']}**: `{e}`", "ERROR")
                failed += 1

        # Advance the rotation index and persist it
        self.update_campaign(campaign["id"], {"current_index": next_idx})

        next_slot = rotation[next_idx]
        next_preview = next_slot.get("text", next_slot.get("forward_link", ""))[:30]
        await self.tg_log(
            f"Campaign **#{campaign['id']}** done — ✅ {sent} sent  ❌ {failed} failed\n"
            f"⏭ Next: slot #{next_slot['slot_id']} `{next_preview}` in "
            f"**{campaign['interval']} min**"
        )

    async def _campaign_loop(self, campaign_id: int):
        while True:
            campaign = self.get_campaign(campaign_id)
            if not campaign or not campaign.get("active"):
                break
            await self._fire_rotation(campaign)
            interval = campaign.get("interval", 60)
            await asyncio.sleep(interval * 60)

    # ─── Control ──────────────────────────────────────────────────────────────

    async def start_campaign(self, campaign_id: int):
        if campaign_id in self._tasks and not self._tasks[campaign_id].done():
            return
        c = self.get_campaign(campaign_id)
        if not c or not c.get("rotation"):
            await self.tg_log(
                f"Campaign #{campaign_id} has no ad slots — add some before starting.", "WARN"
            )
            return
        self.update_campaign(campaign_id, {"active": True})
        task = asyncio.get_event_loop().create_task(self._campaign_loop(campaign_id))
        self._tasks[campaign_id] = task
        await self.tg_log(
            f"Campaign **#{campaign_id} {c['name']}** started — "
            f"{len(c['rotation'])} slot(s) in rotation.", "OK"
        )

    async def stop_campaign(self, campaign_id: int):
        self.update_campaign(campaign_id, {"active": False})
        if campaign_id in self._tasks:
            self._tasks[campaign_id].cancel()
            del self._tasks[campaign_id]
        c = self.get_campaign(campaign_id)
        name = c["name"] if c else str(campaign_id)
        await self.tg_log(f"Campaign **#{campaign_id} {name}** stopped.", "WARN")

    async def start_all_campaigns(self) -> int:
        count = 0
        for c in self.get_campaigns():
            if c.get("rotation"):
                await self.start_campaign(c["id"])
                count += 1
        return count

    async def stop_all_campaigns(self) -> int:
        count = len(self._tasks)
        for cid in list(self._tasks.keys()):
            await self.stop_campaign(cid)
        return count

    async def resume_active_campaigns(self):
        for c in self.get_campaigns():
            if c.get("active") and c.get("rotation"):
                await self.start_campaign(c["id"])
