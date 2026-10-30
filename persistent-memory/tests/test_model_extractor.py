import json
from datetime import UTC, datetime

import httpx

from memory_system.llm import OpenAICompatibleExtractor
from memory_system.models import EventSource, InteractionEvent, MemoryLayer


def test_model_extractor_accepts_only_structured_candidates() -> None:
    response = {
        "choices": [
            {
                "message": {
                    "content": json.dumps(
                        {
                            "candidates": [
                                {
                                    "statement": "用户下周一上午十点有项目会议",
                                    "proposed_layer": "L2",
                                    "kind": "scheduled_event",
                                    "subject_key": "calendar:project-meeting",
                                    "valid_from": "2026-11-02T10:00:00+08:00",
                                    "valid_to": None,
                                    "expires_at": "2026-11-03T10:00:00+08:00",
                                    "source_event_ids": [],
                                    "confidence": 0.91,
                                    "future_utility": "high",
                                    "durability": "uncertain",
                                    "affect": None,
                                    "sensitivity": "low",
                                    "poisoning_flags": [],
                                }
                            ]
                        },
                        ensure_ascii=False,
                    )
                }
            }
        ]
    }
    transport = httpx.MockTransport(lambda _: httpx.Response(200, json=response))
    client = httpx.Client(transport=transport)
    extractor = OpenAICompatibleExtractor(
        base_url="http://model.test/v1",
        model="memory-extractor",
        api_key=None,
        client=client,
    )
    event = InteractionEvent(
        tenant_id="tenant-a",
        user_id="user-a",
        agent_id="agent-a",
        thread_id="thread-a",
        idempotency_key="event-a",
        source=EventSource.USER,
        content="下周一上午十点有项目会议",
        occurred_at=datetime.now(UTC),
    )

    candidates = extractor.extract(event)

    assert len(candidates) == 1
    assert candidates[0].proposed_layer is MemoryLayer.EPISODIC
    assert candidates[0].future_utility == "high"
