from types import SimpleNamespace


class _FakeStream:
    def __init__(self, chunks):
        self._chunks = chunks
        self.closed = False

    def __iter__(self):
        return iter(self._chunks)

    def close(self):
        self.closed = True


def test_stream_chat_skips_empty_choice_chunks(monkeypatch):
    """OpenAI-compatible providers may emit usage-only chunks with choices=[]."""
    import core.llm as llm

    stream = _FakeStream([
        SimpleNamespace(choices=[]),
        SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content=None))]),
        SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content="hello"))]),
        SimpleNamespace(choices=[]),
        SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content=" world"))]),
    ])

    fake_client = SimpleNamespace(
        chat=SimpleNamespace(
            completions=SimpleNamespace(
                create=lambda **_: stream,
            ),
        ),
    )
    monkeypatch.setattr(llm, "get_client", lambda api_key=None, base_url=None: fake_client)

    assert list(llm.stream_chat([{"role": "user", "content": "ping"}])) == ["hello", " world"]
    assert stream.closed is True
