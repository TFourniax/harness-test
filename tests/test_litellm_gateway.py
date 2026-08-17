from adaptive_harness.providers.litellm_provider import LiteLLMProvider


def test_text_tool_protocol_parses_tool_call():
    turn = LiteLLMProvider._parse_text_tool_turn(
        '{"type":"tool_call","name":"fs_read","arguments":{"path":"README.md"}}',
        {"cost_usd": 0.01},
        raw=None,
    )
    assert turn.protocol_error is None
    assert turn.content is None
    assert len(turn.tool_calls) == 1
    assert turn.tool_calls[0].name == "fs_read"
    assert turn.tool_calls[0].arguments == {"path": "README.md"}


def test_text_tool_protocol_invalid_json_is_not_a_final_answer():
    turn = LiteLLMProvider._parse_text_tool_turn("not-json", {}, raw=None)
    assert turn.protocol_error
    assert turn.content is None
    assert turn.tool_calls == []
