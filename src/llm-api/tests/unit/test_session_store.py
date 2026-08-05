import sys
from pathlib import Path

import pytest
from langchain_core.messages import AIMessage, HumanMessage

package_root = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(package_root))

from api.session_store import SessionStore  # noqa: E402


class FakeClock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


@pytest.fixture()
def clock(monkeypatch):
    fake_clock = FakeClock()
    monkeypatch.setattr("api.session_store.time.monotonic", fake_clock)
    return fake_clock


def test_create_and_get(clock):
    store = SessionStore(ttl_seconds=60)
    messages = [HumanMessage("hi")]
    conversation_id = store.create(messages=messages, citations=[{"id": "1"}])

    session = store.get(conversation_id)
    assert session is not None
    assert session.messages == messages
    assert session.citations == [{"id": "1"}]


def test_get_returns_none_for_unknown_id(clock):
    store = SessionStore(ttl_seconds=60)
    assert store.get("does-not-exist") is None


def test_get_expires_after_ttl(clock):
    store = SessionStore(ttl_seconds=10)
    conversation_id = store.create(messages=[], citations=[])

    clock.advance(11)

    assert store.get(conversation_id) is None
    assert len(store) == 0  # expired session should be evicted on access


def test_get_still_valid_within_ttl(clock):
    store = SessionStore(ttl_seconds=10)
    conversation_id = store.create(messages=[], citations=[])

    clock.advance(9)

    assert store.get(conversation_id) is not None


def test_append_messages_extends_ttl(clock):
    store = SessionStore(ttl_seconds=10)
    conversation_id = store.create(messages=[], citations=[])

    clock.advance(9)
    store.append_messages(conversation_id, [HumanMessage("follow up"), AIMessage("answer")])

    clock.advance(9)  # 18s since creation, but only 9s since the append refreshed it
    session = store.get(conversation_id)
    assert session is not None
    assert len(session.messages) == 2


def test_append_messages_replaces_citations_when_given(clock):
    store = SessionStore(ttl_seconds=60)
    conversation_id = store.create(messages=[], citations=[{"id": "old"}])

    store.append_messages(conversation_id, [HumanMessage("follow up")], citations=[{"id": "new"}])
    assert store.get(conversation_id).citations == [{"id": "new"}]

    store.append_messages(conversation_id, [HumanMessage("another")])  # citations omitted -- left as-is
    assert store.get(conversation_id).citations == [{"id": "new"}]


def test_append_messages_raises_for_unknown_conversation(clock):
    store = SessionStore(ttl_seconds=60)
    with pytest.raises(KeyError):
        store.append_messages("nope", [HumanMessage("hi"), AIMessage("hello")])


def test_sweep_expired_removes_only_stale_sessions(clock):
    store = SessionStore(ttl_seconds=10)
    stale_id = store.create(messages=[], citations=[])
    clock.advance(5)
    fresh_id = store.create(messages=[], citations=[])
    clock.advance(6)  # stale_id is now 11s old, fresh_id is 6s old

    removed = store.sweep_expired()

    assert removed == 1
    assert len(store) == 1
    assert store.get(fresh_id) is not None
    assert store.get(stale_id) is None
