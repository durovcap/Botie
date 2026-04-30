"""
Ad Userbot - Telethon-based user account client
Handles login, session, and scheduled ad broadcasting.
Supports Telegram Premium custom emojis.
"""

import asyncio
import json
import logging
import os
from typing import Optional
from telethon import TelegramClient, errors
from telethon.tl.types import (
    InputMessageEntityMentionName,
    MessageEntityCustomEmoji,
)
from telethon.tl.functions.messages import ForwardMessagesRequest, GetMessagesRequest
from telethon.sessions import StringSession

logger = logging.getLogger(__name__)

DATA_FILE = "data.json"
SESSION_FILE = "userbot.session"


def load_data() -> dict:
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE) as f:
            return json.load(f)
    return {"campaigns": [], "targets": [], "next_id": 1}


def save_data(data: dict):
    with open(DATA_FILE, "w") as f:
        json.dump(data, f, indent=2)


class AdUserBot:
    def __init__(self):
        with open("config.json") as f:
            cfg = json.load(f)
        self.api_id = cfg["api_id"]
        self.api_hash = cfg["api_hash"]
        self.client: Optional[TelegramClient] = None
        self._tasks: dict[int, asyncio.Task] = {}
        self._phone_hash = None
        self._init_client()

    def _init_client(self):
        self.client = TelegramClient(SESSION_FILE, self.api_id, self.api_hash)

    async def is_logged_in(self) -> bool:
        try:
            if not self.client.is_connected():
                await self.client.connect()
            return await self.client.is_user_authorized()
        except:
            return False

    async def send_code(self, phone: str):
        if not self.client.is_connected():
            await self.client.connect()
        result = await self.client.send_code_request(phone)
        self._phone_hash = result.phone_code_hash
        self._phone = phone

    async def sign_in(self, phone: str, code: str) -> str:
        try:
            await self.client.sign_in(phone, code, phone_code_hash=self._phone_hash)
            return "ok"
        except errors.SessionPasswordNeededError:
            return "2fa"

    async def sign_in_2fa(self, password: str):
        await self.client.sign_in(password=password)

    async def logout(self):
        for task in self._tasks.values():
            task.cancel()
        self._tasks.clear()
        try:
            await self.client.log_out()
        except:
            pass
        if os.path.exists(SESSION_FILE):
            os.remove(SESSION_FILE)
        self._init_client()

    # ─── Campaign Data ────────────────────────────────────────────────────────

    def next_campaign_id(self) -> int:
        data = load_data()
        cid = data["next_id"]
        data["next_id"] += 1
        save_data(data)
        return cid

    def save_campaign(self, campaign: dict):
        data = load_data()
        data["campaigns"].append(campaign)
        save_data(data)

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

    def save_targets(self, targets: list):
        data = load_data()
        data["targets"] = targets
        save_data(data)

    def get_targets(self) -> list:
        return load_data()["targets"]

    # ─── Broadcasting ─────────────────────────────────────────────────────────

    async def _send_ad(self, campaign: dict):
        """Send one round of the ad to all target chats."""
        targets = self.get_targets()
        if not targets:
            logger.warning("No targets set, skipping.")
            return

        for target in targets:
            try:
                if campaign["type"] == "text":
                    from telethon.tl.types import (
                        MessageEntityCustomEmoji, MessageEntityBold,
                        MessageEntityItalic, MessageEntityCode,
                        MessageEntityUnderline, MessageEntityStrike,
                        MessageEntitySpoiler, MessageEntityTextUrl,
                    )

                    text = campaign.get("text", "")
                    raw_entities = campaign.get("entities", [])

                    # Rebuild Telethon MessageEntity objects from stored dicts
                    entities = []
                    for e in raw_entities:
                        kind = e.get("_", "")
                        o, l = e["offset"], e["length"]
                        if kind == "MessageEntityCustomEmoji":
                            entities.append(MessageEntityCustomEmoji(o, l, e["document_id"]))
                        elif kind == "MessageEntityBold":
                            entities.append(MessageEntityBold(o, l))
                        elif kind == "MessageEntityItalic":
                            entities.append(MessageEntityItalic(o, l))
                        elif kind == "MessageEntityCode":
                            entities.append(MessageEntityCode(o, l))
                        elif kind == "MessageEntityUnderline":
                            entities.append(MessageEntityUnderline(o, l))
                        elif kind == "MessageEntityStrike":
                            entities.append(MessageEntityStrike(o, l))
                        elif kind == "MessageEntitySpoiler":
                            entities.append(MessageEntitySpoiler(o, l))
                        elif kind == "MessageEntityTextUrl":
                            entities.append(MessageEntityTextUrl(o, l, e["url"]))

                    await self.client.send_message(
                        entity=target,
                        message=text,
                        formatting_entities=entities if entities else None,
                    )

                elif campaign["type"] == "forward":
                    src = campaign["forward_chat"]
                    msg_id = campaign["forward_msg_id"]
                    await self.client.forward_messages(
                        entity=target,
                        messages=msg_id,
                        from_peer=src,
                    )

                logger.info(f"Sent campaign #{campaign['id']} to {target}")
                await asyncio.sleep(2)  # Small delay between targets

            except errors.FloodWaitError as e:
                logger.warning(f"FloodWait {e.seconds}s, sleeping...")
                await asyncio.sleep(e.seconds)
            except Exception as e:
                logger.error(f"Failed to send to {target}: {e}")

    async def _campaign_loop(self, campaign_id: int):
        """Loop that sends the ad at the configured interval."""
        while True:
            campaign = self.get_campaign(campaign_id)
            if not campaign or not campaign.get("active"):
                break
            await self._send_ad(campaign)
            interval_minutes = campaign.get("interval", 60)
            logger.info(f"Campaign #{campaign_id} sent. Next in {interval_minutes}min.")
            await asyncio.sleep(interval_minutes * 60)

    async def start_campaign(self, campaign_id: int):
        if campaign_id in self._tasks:
            task = self._tasks[campaign_id]
            if not task.done():
                return  # Already running

        self.update_campaign(campaign_id, {"active": True})
        loop = asyncio.get_event_loop()
        task = loop.create_task(self._campaign_loop(campaign_id))
        self._tasks[campaign_id] = task
        logger.info(f"Campaign #{campaign_id} started.")

    async def stop_campaign(self, campaign_id: int):
        self.update_campaign(campaign_id, {"active": False})
        if campaign_id in self._tasks:
            self._tasks[campaign_id].cancel()
            del self._tasks[campaign_id]
        logger.info(f"Campaign #{campaign_id} stopped.")

    async def start_all_campaigns(self) -> int:
        count = 0
        for c in self.get_campaigns():
            await self.start_campaign(c["id"])
            count += 1
        return count

    async def stop_all_campaigns(self) -> int:
        count = len(self._tasks)
        for cid in list(self._tasks.keys()):
            await self.stop_campaign(cid)
        return count

    async def resume_active_campaigns(self):
        """On restart, resume any campaigns that were marked active."""
        for c in self.get_campaigns():
            if c.get("active"):
                await self.start_campaign(c["id"])
                logger.info(f"Resumed campaign #{c['id']}")
