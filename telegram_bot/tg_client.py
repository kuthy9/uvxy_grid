# telegram_bot/tg_client.py
"""Raw Telegram Bot API client over requests. No third-party telegram framework."""
from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Optional

import requests

logger = logging.getLogger("telegram_bot.tg_client")


class TGClient:
    API_BASE = "https://api.telegram.org"
    BACKOFF_LADDER_SEC = [5, 30, 120, 300]  # 429 / network error backoff

    def __init__(self, token: str, long_poll_timeout: int = 30) -> None:
        self._token = token
        self._timeout = long_poll_timeout
        # Holds the offset for the next getUpdates call so updates are not
        # re-delivered. Caller is responsible for advancing this.
        self.update_offset: Optional[int] = None

    # ─── URL builder ───
    def _url(self, method: str) -> str:
        return f"{self.API_BASE}/bot{self._token}/{method}"

    # ─── Long-poll updates ───
    def get_updates(self, offset: Optional[int] = None, timeout: Optional[int] = None) -> list[dict]:
        url = self._url("getUpdates")
        params: dict = {"timeout": timeout if timeout is not None else self._timeout}
        if offset is not None:
            params["offset"] = offset
        for delay in [0] + self.BACKOFF_LADDER_SEC:
            if delay:
                time.sleep(delay)
            try:
                r = requests.get(url, params=params, timeout=self._timeout + 5)
            except requests.RequestException as e:
                logger.warning("getUpdates network error (will retry): %s", type(e).__name__)
                continue
            if r.status_code == 200:
                data = r.json()
                if data.get("ok"):
                    return data.get("result", [])
                logger.warning("getUpdates ok=false: %s", data)
            elif r.status_code == 429:
                retry = int(r.json().get("parameters", {}).get("retry_after", 0))
                logger.warning("getUpdates 429 retry_after=%s", retry)
                time.sleep(retry)
                continue
            else:
                logger.warning("getUpdates HTTP %s", r.status_code)
        logger.error("getUpdates exhausted backoff ladder")
        return []

    # ─── Send message ───
    def send_message(self, chat_id: int, text: str, parse_mode: Optional[str] = None) -> bool:
        url = self._url("sendMessage")
        body = {"chat_id": chat_id, "text": text, "parse_mode": parse_mode}
        return self._post_with_backoff(url, json_body=body)

    # ─── Send document ───
    def send_document(self, chat_id: int, file_path: Path, caption: str = "") -> bool:
        url = self._url("sendDocument")
        for delay in [0] + self.BACKOFF_LADDER_SEC:
            if delay:
                time.sleep(delay)
            try:
                with open(file_path, "rb") as fh:
                    r = requests.post(
                        url,
                        data={"chat_id": chat_id, "caption": caption},
                        files={"document": (file_path.name, fh)},
                        timeout=self._timeout + 5,
                    )
            except requests.RequestException as e:
                logger.warning("sendDocument network error: %s", type(e).__name__)
                continue
            if r.status_code == 200 and r.json().get("ok"):
                return True
            if r.status_code == 429:
                retry = int(r.json().get("parameters", {}).get("retry_after", 0))
                time.sleep(retry)
                continue
            logger.warning("sendDocument HTTP %s", r.status_code)
        logger.error("sendDocument failed for %s", file_path.name)
        return False

    def _post_with_backoff(self, url: str, json_body: dict) -> bool:
        for delay in [0] + self.BACKOFF_LADDER_SEC:
            if delay:
                time.sleep(delay)
            try:
                r = requests.post(url, json=json_body, timeout=self._timeout + 5)
            except requests.RequestException as e:
                logger.warning("post network error: %s", type(e).__name__)
                continue
            if r.status_code == 200 and r.json().get("ok"):
                return True
            if r.status_code == 429:
                retry = int(r.json().get("parameters", {}).get("retry_after", 0))
                time.sleep(retry)
                continue
            logger.warning("post HTTP %s body_ok=%s",
                           r.status_code, r.json().get("ok"))
        return False
