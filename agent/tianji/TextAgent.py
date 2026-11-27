from typing import AsyncIterable

from langchain.agents import create_agent
from langchain.chat_models import init_chat_model
from langchain_core.messages import HumanMessage, SystemMessage

from agent.BaseAgent import BaseAgent, STOP_EVENT, format_sse_data, make_sse_event
from agent.prompts import system_prompt_config
from config import config_manager, logger


class TextAgent(BaseAgent):
    """通用文本生成智能体，按请求生成内容，不保存会话状态。"""

    def __init__(self, provider: str = "openai"):
        self.provider = provider
        self.llm = None
        self.agent = None

    async def init(self):
        prefix = f"ai.{self.provider}"
        self.llm = init_chat_model(
            model=config_manager.get(f"{prefix}.model"),
            model_provider="openai",
            api_key=config_manager.get(f"{prefix}.api-key"),
            base_url=config_manager.get(f"{prefix}.base-url"),
            temperature=float(config_manager.get(f"{prefix}.temperature", 0.7)),
            timeout=int(config_manager.get(f"{prefix}.timeout", 60)),
        )
        self.agent = create_agent(model=self.llm)

    async def execute(self, question: str, session_id: str, user_token: str) -> AsyncIterable[str]:
        prompts = [
            SystemMessage(system_prompt_config.chat_text_message),
            HumanMessage(question),
        ]
        try:
            async for token, _ in self.agent.astream(
                input={"messages": prompts}, stream_mode="messages"
            ):
                if token.content:
                    yield make_sse_event(1001, token.content)
        except Exception as exc:
            logger.error("TextAgent 流式生成失败: %s", type(exc).__name__)
            yield make_sse_event(2001, "文本生成失败")

        yield format_sse_data(STOP_EVENT)

    def id(self) -> int:
        return 1002

    async def session_detail(self, user_id: int, session_id: str) -> list:
        raise NotImplementedError("文本智能体不保存会话详情")

    async def delete_session(self, session_id: str):
        raise NotImplementedError("文本智能体不保存会话")


text_agent = TextAgent()
