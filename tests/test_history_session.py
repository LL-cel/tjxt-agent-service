"""
练习：完善历史对话功能 —— 单元测试

覆盖：
1. ChatSessionDAO.update_title：首次提问写入标题、后续提问只更新 update_time
2. ChatSessionDAO.query_history_session：按 当天/最近30天/最近1年/1年以上 分组并排序
3. ChatSessionDAO.update_history_session：更新标题（截断 100 字符）
4. ChatSessionDAO.delete_history_session：物理删除
5. SessionRouter：GET/DELETE/PUT /session/history 路由注册与行为
6. RouterAgent.delete_session：清理 LangGraph checkpointer 会话数据

运行方式（在 agent_center 目录下）：
    python -m unittest tests.test_history_session -v
"""
import asyncio
import os
import unittest
from datetime import datetime, timedelta

# 导入 Agent 时会初始化 LLM 客户端，这里提供占位 key（测试不会真正发起模型调用）
os.environ.setdefault("OPENAI_API_KEY", "test-key")

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import dao.BaseDAO as base_dao
from config import Base
from dao import chat_session_dao
from dao.ChatSessionDAO import (
    SUCCESS_MESSAGE, NO_CHANGE_MESSAGE,
    TODAY, LAST_30_DAYS, LAST_YEAR, MORE_THAN_YEAR,
)
from dao.pojo import ChatSession


def _make_engine():
    """内存 SQLite，多个 session 共享同一个连接"""
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return engine


class HistorySessionTest(unittest.TestCase):
    """历史会话功能测试"""

    def setUp(self):
        self.engine = _make_engine()
        self.Session = sessionmaker(bind=self.engine)
        # DAO 通过 dao.BaseDAO.SessionLocal 获取数据库会话，这里替换为 SQLite
        self._origin_session_local = base_dao.SessionLocal
        base_dao.SessionLocal = self.Session
        self._origin_session_scope = base_dao.BaseDAO._session_scope

        # 直接插入测试数据
        now = datetime.now()
        self.rows = [
            # session_id, agent_id, user_id, title, update_time
            ("s_today", 1001, 1, "今天的会话", now),
            ("s_30days", 1001, 1, "30天内的会话", now - timedelta(days=5)),
            ("s_1year", 1001, 1, "一年内的会话", now - timedelta(days=100)),
            ("s_old", 1001, 1, "一年以上的会话", now - timedelta(days=400)),
            ("s_no_title", 1001, 1, None, now),
            ("s_other_user", 1001, 2, "别人的会话", now),
            ("s_other_agent", 1002, 1, "别的智能体会话", now),
        ]
        with self.Session() as session:
            for i, (sid, aid, uid, title, ut) in enumerate(self.rows):
                session.add(ChatSession(
                    id=i + 1,
                    session_id=sid,
                    agent_id=aid,
                    user_id=uid,
                    title=title,
                    update_time=ut,
                ))
            session.commit()

    def tearDown(self):
        base_dao.SessionLocal = self._origin_session_local
        self.engine.dispose()

    # ---------------- 1. 设置标题 ----------------
    def test_update_title_first_time(self):
        """首次提问：写入标题，超长截断为 100 字符"""
        long_question = "问" * 150
        result = chat_session_dao.update_title("s_no_title", 1001, long_question)
        self.assertEqual(result, SUCCESS_MESSAGE)

        with self.Session() as session:
            row = session.execute(
                select(ChatSession).where(ChatSession.session_id == "s_no_title")
            ).scalars().first()
            self.assertEqual(row.title, "问" * 100)

    def test_update_title_keeps_existing_title_and_touches_time(self):
        """后续提问：不覆盖已有标题，但刷新 update_time"""
        with self.Session() as session:
            old_time = session.execute(
                select(ChatSession).where(ChatSession.session_id == "s_today")
            ).scalars().first().update_time

        result = chat_session_dao.update_title("s_today", 1001, "新的问题")
        self.assertEqual(result, SUCCESS_MESSAGE)

        with self.Session() as session:
            row = session.execute(
                select(ChatSession).where(ChatSession.session_id == "s_today")
            ).scalars().first()
            self.assertEqual(row.title, "今天的会话")
            self.assertIsNotNone(row.update_time)
            self.assertGreaterEqual(row.update_time, old_time)

    def test_update_title_session_not_found(self):
        self.assertIsNone(chat_session_dao.update_title("not_exists", 1001, "问题"))

    # ---------------- 2. 查询历史会话 ----------------
    def test_query_history_session_grouping_and_order(self):
        result = chat_session_dao.query_history_session(1001, 1)

        # 分组顺序：1年以上 → 最近1年 → 最近30天 → 当天
        self.assertEqual(
            list(result.keys()),
            [MORE_THAN_YEAR, LAST_YEAR, LAST_30_DAYS, TODAY]
        )
        self.assertEqual([vo.session_id for vo in result[TODAY]], ["s_today"])
        self.assertEqual([vo.session_id for vo in result[LAST_30_DAYS]], ["s_30days"])
        self.assertEqual([vo.session_id for vo in result[LAST_YEAR]], ["s_1year"])
        self.assertEqual([vo.session_id for vo in result[MORE_THAN_YEAR]], ["s_old"])

    def test_query_history_session_filters_title_and_user(self):
        """无标题、其他用户、其他智能体的会话不返回"""
        result = chat_session_dao.query_history_session(1001, 1)
        all_ids = [vo.session_id for vos in result.values() for vo in vos]
        self.assertNotIn("s_no_title", all_ids)
        self.assertNotIn("s_other_user", all_ids)
        self.assertNotIn("s_other_agent", all_ids)

    def test_query_history_session_empty(self):
        self.assertEqual(chat_session_dao.query_history_session(2000, 2000), {})

    def test_query_history_session_returns_vo_fields(self):
        result = chat_session_dao.query_history_session(1001, 1)
        vo = result[TODAY][0]
        self.assertEqual(vo.title, "今天的会话")
        self.assertIsInstance(vo.update_time, datetime)

    # ---------------- 3. 更新历史会话标题 ----------------
    def test_update_history_session(self):
        result = chat_session_dao.update_history_session(1001, 1, "s_today", "改后的标题")
        self.assertEqual(result, SUCCESS_MESSAGE)

        with self.Session() as session:
            row = session.execute(
                select(ChatSession).where(ChatSession.session_id == "s_today")
            ).scalars().first()
            self.assertEqual(row.title, "改后的标题")

    def test_update_history_session_title_truncated(self):
        result = chat_session_dao.update_history_session(1001, 1, "s_today", "长" * 120)
        self.assertEqual(result, SUCCESS_MESSAGE)

        with self.Session() as session:
            row = session.execute(
                select(ChatSession).where(ChatSession.session_id == "s_today")
            ).scalars().first()
            self.assertEqual(row.title, "长" * 100)

    def test_update_history_session_not_exists(self):
        result = chat_session_dao.update_history_session(1001, 1, "not_exists", "标题")
        self.assertEqual(result, NO_CHANGE_MESSAGE)

    # ---------------- 4. 删除历史会话 ----------------
    def test_delete_history_session(self):
        result = chat_session_dao.delete_history_session(1001, 1, "s_today")
        self.assertEqual(result, SUCCESS_MESSAGE)

        with self.Session() as session:
            row = session.execute(
                select(ChatSession).where(ChatSession.session_id == "s_today")
            ).scalars().first()
            self.assertIsNone(row)

    def test_delete_history_session_not_exists(self):
        result = chat_session_dao.delete_history_session(1001, 1, "not_exists")
        self.assertEqual(result, NO_CHANGE_MESSAGE)

    # ---------------- 5. 路由测试 ----------------
    def test_routes_registered(self):
        from web.WebApp import app
        paths = {(route.path, tuple(sorted(route.methods))) for route in app.routes}
        self.assertIn(("/session/history", ("GET",)), paths)
        self.assertIn(("/session/history", ("DELETE",)), paths)
        self.assertIn(("/session/history", ("PUT",)), paths)

    def gateway_client(self):
        from unittest.mock import patch
        from web.RequestFilter import RequestFilter
        from web.WebApp import app

        verifier = patch.object(RequestFilter, "verify_token", autospec=True,
                                side_effect=lambda _, token: token == "unit-test-token")
        verifier.start()
        self.addCleanup(verifier.stop)
        return TestClient(app, headers={
            "request-from": "agent-center-gateway", "token": "unit-test-token"
        })

    def test_router_rejects_direct_access(self):
        from web.WebApp import app
        response = TestClient(app).get("/session/history", params={"agent_id": 1001, "user_id": 1})
        self.assertEqual(response.status_code, 401)

    def test_router_query_history(self):
        client = self.gateway_client()
        resp = client.get("/session/history", params={"agent_id": 1001, "user_id": 1})
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(list(data.keys()), [MORE_THAN_YEAR, LAST_YEAR, LAST_30_DAYS, TODAY])

    def test_router_update_history(self):
        client = self.gateway_client()
        resp = client.put(
            "/session/history",
            params={"agent_id": 1001, "user_id": 1, "session_id": "s_30days", "title": "路由改标题"},
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json(), SUCCESS_MESSAGE)

    def test_router_delete_history(self):
        from agent import AGENTS

        class _FakeAgent:
            def __init__(self):
                self.deleted = []

            async def delete_session(self, session_id):
                self.deleted.append(session_id)

        fake = _FakeAgent()
        AGENTS[1001] = fake
        try:
            client = self.gateway_client()
            resp = client.delete(
                "/session/history",
                params={"agent_id": 1001, "user_id": 1, "session_id": "s_today"},
            )
            self.assertEqual(resp.status_code, 200)
            self.assertEqual(resp.json(), SUCCESS_MESSAGE)
            self.assertEqual(fake.deleted, ["s_today"])
        finally:
            from agent.tianji import router_agent
            AGENTS[1001] = router_agent

    def test_router_delete_history_agent_not_found(self):
        client = self.gateway_client()
        resp = client.delete(
            "/session/history",
            params={"agent_id": 9999, "user_id": 1, "session_id": "s_today"},
        )
        self.assertEqual(resp.status_code, 200)
        self.assertIn("Agent not found", resp.json())

    # ---------------- 6. RouterAgent.delete_session ----------------
    def test_router_agent_delete_session(self):
        from agent.tianji.RouterAgent import RouterAgent

        class _FakeCheckpointer:
            def __init__(self):
                self.threads = []

            async def adelete_thread(self, thread_id):
                self.threads.append(thread_id)

        agent = RouterAgent.__new__(RouterAgent)
        agent.checkpointer = _FakeCheckpointer()

        asyncio.run(agent.delete_session("s_today"))
        self.assertEqual(agent.checkpointer.threads, ["s_today"])


if __name__ == "__main__":
    unittest.main()
