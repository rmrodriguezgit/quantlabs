from api import ocean


def test_buffered_ocean_completion_accumulates_chunks(monkeypatch):
    calls = []
    chunks = [
        {"content": "Primera parte", "finish_reason": "length", "usage": {"completion_tokens": 55}},
        {"content": "segunda parte completa", "finish_reason": "stop", "usage": {"completion_tokens": 12}},
    ]

    def fake_call(provider, messages, token="", model="", temperature=0.4, max_tokens=500):
        calls.append(messages)
        return chunks[len(calls) - 1]

    monkeypatch.setattr(ocean, "call_ocean_llm", fake_call)

    result = ocean.buffered_ocean_completion(
        "local",
        "Sistema",
        "Pregunta original",
        "",
        "",
        0.45,
        55,
        buffered=True,
    )

    assert result["content"] == "Primera parte segunda parte completa"
    assert result["chunks"] == 2
    assert result["usage"]["completion_tokens"] == 67
    assert "Continua exactamente" in calls[1][-1]["content"]


def test_format_buffered_chunks_avoids_blank_space_between_requests():
    assert ocean._format_buffered_chunks(["Idea incompleta", "continua aqui"]) == "Idea incompleta continua aqui"
    assert ocean._format_buffered_chunks(["Idea completa.", "Siguiente punto"]) == "Idea completa.\nSiguiente punto"


def test_unbuffered_ocean_completion_returns_first_chunk(monkeypatch):
    monkeypatch.setattr(
        ocean,
        "call_ocean_llm",
        lambda *args, **kwargs: {"content": "Solo primer bloque", "finish_reason": "length", "usage": {"completion_tokens": 55}},
    )

    result = ocean.buffered_ocean_completion(
        "local",
        "Sistema",
        "Pregunta original",
        "",
        "",
        0.45,
        55,
        buffered=False,
    )

    assert result["content"] == "Solo primer bloque"
    assert result["chunks"] == 1
    assert result["buffer_limit_reached"] is True


def test_local_token_budget_allows_fuller_blocks():
    assert ocean._bounded_max_tokens("local", None) == 160
    assert ocean._bounded_max_tokens("local", 999) == 220
    assert ocean.LOCAL_BUFFER_MAX_CHUNKS == 4
