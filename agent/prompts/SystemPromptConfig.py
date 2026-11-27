from hashlib import md5
from pathlib import Path
from threading import Event, Thread

from common import (
    NACOS_CONFIG_GROUP,
    PROMPT_BUY_CHAT_DATA_ID,
    PROMPT_CONSULT_CHAT_DATA_ID,
    PROMPT_KNOWLEDGE_CHAT_DATA_ID,
    PROMPT_RECOMMEND_CHAT_DATA_ID,
    PROMPT_ROUTE_CHAT_DATA_ID,
    PROMPT_TEXT_CHAT_DATA_ID,
    PROMPT_UNKNOWN_CHAT_DATA_ID,
)
from config import config_manager, logger, nacos_config


class SystemPromptConfig:
    """读取 Nacos 系统提示词并每 60 秒检查更新。"""

    _config_map = {
        PROMPT_ROUTE_CHAT_DATA_ID: ("chat_route_message", "route-agent-system-message.txt"),
        PROMPT_RECOMMEND_CHAT_DATA_ID: ("chat_recommend_message", "recommend-agent-system-message.txt"),
        PROMPT_BUY_CHAT_DATA_ID: ("chat_buy_message", "buy-agent-system-message.txt"),
        PROMPT_CONSULT_CHAT_DATA_ID: ("chat_consult_message", "consult-agent-system-message.txt"),
        PROMPT_KNOWLEDGE_CHAT_DATA_ID: ("chat_knowledge_message", "knowledge-agent-system-message.txt"),
        PROMPT_UNKNOWN_CHAT_DATA_ID: ("chat_unknown_message", "unknown-agent-system-message.txt"),
        PROMPT_TEXT_CHAT_DATA_ID: ("chat_text_message", "text-system-chat-message.txt"),
    }

    def __init__(self):
        self._client = nacos_config.get_config_client()
        self._group = config_manager.get(NACOS_CONFIG_GROUP, "DEFAULT_GROUP")
        self._stop_event = Event()
        self._thread = None
        self._snapshots = {}
        self._data_ids = {}

        prompt_dir = Path(__file__).parent
        for key, (attribute, filename) in self._config_map.items():
            data_id = config_manager.get(key, filename)
            content = (prompt_dir / filename).read_text(encoding="utf-8")
            setattr(self, attribute, content)
            self._snapshots[attribute] = md5(content.encode("utf-8"), usedforsecurity=False).digest()
            self._data_ids[attribute] = data_id

    def start(self):
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._check_updates()
        self._thread = Thread(target=self._watch_loop, name="nacos-system-prompts", daemon=True)
        self._thread.start()

    def stop(self):
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=6)

    def _watch_loop(self):
        while not self._stop_event.wait(60):
            self._check_updates()

    def _check_updates(self):
        for attribute, data_id in self._data_ids.items():
            if self._stop_event.is_set():
                return
            try:
                content = self._client.get_config(data_id, self._group, timeout=5)
            except Exception as error:
                logger.warning(f"读取 Nacos 提示词 {data_id} 失败 ({type(error).__name__})")
                continue

            if not isinstance(content, str) or not content.strip():
                continue
            snapshot = md5(content.encode("utf-8"), usedforsecurity=False).digest()
            if self._snapshots[attribute] != snapshot:
                setattr(self, attribute, content)
                self._snapshots[attribute] = snapshot
                logger.info(f"Nacos 提示词已更新: {data_id}")
