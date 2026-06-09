import pytest

from app.main import RecallRequest, ai_engine, recall_message


@pytest.mark.asyncio
async def test_recall_request_does_not_require_message():
    user_id = "recall_test_user"
    ai_engine.memory.clear(user_id)
    ai_engine.memory.add(user_id, "user", "hello")
    ai_engine.memory.add(user_id, "assistant", "hi")

    result = await recall_message(RecallRequest(user_id=user_id))

    assert result["success"] is True
    assert ai_engine.memory.get(user_id) == []
