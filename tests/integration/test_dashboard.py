from __future__ import annotations

from fastapi.testclient import TestClient

from muxdev.api import create_app


def test_dashboard_is_conversation_first_and_exposes_trusted_delivery(workspace) -> None:
    response = TestClient(create_app(workspace)).get("/")

    assert response.status_code == 200
    assert "一个会话，完成一次可信开发交付" in response.text
    assert "本次任务与交付标准" in response.text
    assert "接受并写入项目" in response.text
    assert "/api/v1/conversations" in response.text
    assert "new EventSource" in response.text
    assert "mock 只验证流程" in response.text
    assert "Standard/Strict 需要独立 Reviewer" in response.text
    assert "主要实现 Agent" in response.text
    assert "Agent 团队" in response.text
    assert "aria-live=\"polite\"" in response.text
    assert "recovery?.next_actions" in response.text
    assert "payload.next_actions" in response.text
    assert "可信交付阶段进度" in response.text
    assert "交付什么" in response.text
    assert "怎样算完成" in response.text
    assert "由什么证明" in response.text
    assert "respond_interaction" in response.text
    assert "1 分钟未回复" in response.text
    assert "添加本次会话标准" in response.text
    assert "保存为新契约" in response.text
    assert "普通聊天不会修改这些规则" in response.text
    assert "查看完整文件与逐项标准结果" in response.text
    assert "执行与证据详情" in response.text
    assert 'item.action==="start_work"' in response.text
    assert "run.orphaned" in response.text
    assert "Run 已建立，正在预检 Agent 团队" in response.text
    assert "catch{}" not in response.text


def test_dashboard_uses_safe_dom_text_rendering(workspace) -> None:
    response = TestClient(create_app(workspace)).get("/")

    assert "item.textContent=text" in response.text
    assert "insertAdjacentHTML" not in response.text
    assert "innerHTML" not in response.text
