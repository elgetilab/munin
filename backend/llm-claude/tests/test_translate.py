"""Each case here is a request vLLM accepts and the Messages API rejects."""

import translate


def build(body, model="claude-haiku-5-5"):
    return translate.build_request(body, model=model, effort="medium", max_output_tokens=16384)


def tool_call(call_id, name="search", arguments='{"q": "x"}'):
    return {"id": call_id, "type": "function", "function": {"name": name, "arguments": arguments}}


TOOL = {"type": "function", "function": {"name": "search", "description": "d", "parameters": {
    "type": "object", "properties": {"q": {"type": "string"}}}, "strict": True}}
USER = {"role": "user", "content": "hi"}


def test_vllm_only_fields_never_reach_the_api():
    params = build({
        "messages": [USER], "temperature": 0.1, "top_p": 0.95, "top_k": 20, "min_p": 0.0,
        "presence_penalty": 1.5, "chat_template_kwargs": {"enable_thinking": False},
        "stream_options": {"include_usage": True},
    })
    assert set(params) == {"model", "max_tokens", "messages", "thinking", "output_config"}


def test_request_cannot_choose_the_model():
    assert build({"model": "claude-fable-5-1", "messages": [USER]})["model"] == "claude-haiku-5-5"


def test_max_tokens_is_clamped_but_small_values_survive():
    assert build({"messages": [USER], "max_tokens": 999_999})["max_tokens"] == 16384
    assert build({"messages": [USER], "max_tokens": 8})["max_tokens"] == 8
    assert build({"messages": [USER], "max_completion_tokens": 50})["max_tokens"] == 50
    assert build({"messages": [USER]})["max_tokens"] == 16384


def test_thinking_is_on_unless_a_sub_task_or_a_tiny_budget():
    on = {"type": "adaptive", "display": "summarized"}
    off = {"type": "disabled"}
    assert build({"messages": [USER]})["thinking"] == on
    assert build({"messages": [USER], "max_tokens": 1024})["thinking"] == on
    assert build({"messages": [USER], "max_tokens": 40})["thinking"] == off
    sub_task = {"messages": [USER], "chat_template_kwargs": {"enable_thinking": False}}
    assert build(sub_task)["thinking"] == off
    # Only an explicit false counts: reasoning_effort goes through the same field.
    effort = {"messages": [USER], "chat_template_kwargs": {"reasoning_effort": "medium"}}
    assert build(effort)["thinking"] == on


def test_operator_can_turn_thinking_off():
    params = translate.build_request(
        {"messages": [USER], "tools": [TOOL]}, model="claude-haiku-5-5", effort="medium",
        max_output_tokens=16384, thinking=False,
    )
    assert params["thinking"] == {"type": "disabled"}
    assert translate.TOOL_LOOP_INSTRUCTION in params["system"][0]["text"]


def test_system_messages_are_hoisted_wherever_they_appear():
    params = build({"messages": [
        {"role": "system", "content": "one"}, USER,
        {"role": "system", "content": "two"}, {"role": "assistant", "content": "ok"}, USER,
    ]})
    assert params["system"][0]["text"] == "one\ntwo"
    assert all(m["role"] != "system" for m in params["messages"])


def test_one_shot_calls_are_not_cached_but_tool_loops_are():
    plain = build({"messages": [{"role": "system", "content": "s"}, USER]})
    assert "cache_control" not in plain and "cache_control" not in plain["system"][0]
    loop = build({"messages": [{"role": "system", "content": "s"}, USER], "tools": [TOOL]})
    assert loop["cache_control"] == {"type": "ephemeral"}
    assert loop["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert loop["tools"][-1]["cache_control"] == {"type": "ephemeral"}
    assert translate.TOOL_LOOP_INSTRUCTION not in loop["system"][0]["text"]


def test_tools_convert_and_strict_is_dropped():
    tools = build({"messages": [USER], "tools": [TOOL, TOOL]})["tools"]
    assert tools == [{"name": "search", "description": "d", "cache_control": {"type": "ephemeral"},
                       "input_schema": {"type": "object", "properties": {"q": {"type": "string"}}}}]


def test_tool_choice_forms():
    def choice(value, model="claude-haiku-5-5", **extra):
        body = {"messages": [USER], "tools": [TOOL], "tool_choice": value, **extra}
        return build(body, model=model)["tool_choice"]

    named = {"type": "function", "function": {"name": "search"}}
    assert choice("auto") == {"type": "auto"}
    assert choice("none") == {"type": "none"}
    assert choice("required") == {"type": "any"}
    assert choice(named) == {"type": "tool", "name": "search"}
    assert choice("auto", parallel_tool_calls=False) == {"type": "auto", "disable_parallel_tool_use": True}
    # Sonnet 5.5 returns a 400 on forced tool use.
    assert choice("required", model="claude-sonnet-5-5") == {"type": "auto"}
    assert choice(named, model="claude-sonnet-5-5") == {"type": "auto"}


def test_no_tool_choice_without_tools():
    assert "tool_choice" not in build({"messages": [USER], "tool_choice": "auto"})


def test_parallel_results_share_one_user_turn():
    _, messages = translate.convert_messages([
        USER,
        {"role": "assistant", "content": "", "tool_calls": [tool_call("a"), tool_call("b")]},
        {"role": "tool", "tool_call_id": "a", "content": "ra"},
        {"role": "tool", "tool_call_id": "b", "content": "rb"},
    ])
    assert [m["role"] for m in messages] == ["user", "assistant", "user"]
    assert [b["type"] for b in messages[1]["content"]] == ["tool_use", "tool_use"]
    assert [b["tool_use_id"] for b in messages[2]["content"]] == ["a", "b"]


def test_call_without_a_saved_result_gets_an_error_result():
    _, messages = translate.convert_messages([
        USER,
        {"role": "assistant", "tool_calls": [tool_call("a")]},
        {"role": "user", "content": "next question"},
    ])
    result, text = messages[2]["content"]
    assert result["tool_use_id"] == "a" and result["is_error"] is True
    assert text == {"type": "text", "text": "next question"}


def test_result_whose_call_was_trimmed_becomes_text():
    _, messages = translate.convert_messages([
        {"role": "tool", "tool_call_id": "gone", "content": "old"}, USER,
    ])
    assert not any(b["type"] == "tool_result" for m in messages for b in m["content"])
    assert "old" in messages[0]["content"][0]["text"]


def test_reused_tool_ids_stay_unique_and_paired():
    _, messages = translate.convert_messages([
        USER,
        {"role": "assistant", "tool_calls": [tool_call("a")]},
        {"role": "tool", "tool_call_id": "a", "content": "first"},
        {"role": "assistant", "tool_calls": [tool_call("a")]},
        {"role": "tool", "tool_call_id": "a", "content": "second"},
    ])
    assert messages[1]["content"][0]["id"] == "a"
    assert messages[3]["content"][0]["id"] == "a_2"
    assert messages[4]["content"][0]["tool_use_id"] == "a_2"


def test_invalid_id_characters_are_replaced_on_both_sides():
    _, messages = translate.convert_messages([
        USER,
        {"role": "assistant", "tool_calls": [tool_call("call:1.2")]},
        {"role": "tool", "tool_call_id": "call:1.2", "content": "r"},
    ])
    assert messages[1]["content"][0]["id"] == "call_1_2"
    assert messages[2]["content"][0]["tool_use_id"] == "call_1_2"


def test_bad_saved_arguments_still_make_an_object():
    for raw, expected in [
        ("{broken", {"raw_arguments": "{broken"}),
        ("[1]", {"raw_arguments": [1]}),
        ("", {}),
        (None, {}),
        ({"q": 1}, {"q": 1}),
    ]:
        assert translate._parse_arguments(raw) == expected


def test_saved_tool_call_shape_is_accepted():
    _, messages = translate.convert_messages([
        USER,
        {"role": "assistant", "tool_calls": [{"id": "a", "name": "search", "arguments": {"q": "x"}}]},
        {"role": "tool", "tool_call_id": "a", "content": "r"},
    ])
    assert messages[1]["content"][0]["name"] == "search"
    assert messages[1]["content"][0]["input"] == {"q": "x"}


def test_empty_text_is_removed():
    _, messages = translate.convert_messages([
        {"role": "user", "content": ""},
        {"role": "assistant", "content": "   ", "tool_calls": [tool_call("a")]},
        {"role": "tool", "tool_call_id": "a", "content": ""},
    ])
    assert messages[0]["content"][0]["text"] == "(empty message)"
    assert [b["type"] for b in messages[1]["content"]] == ["tool_use"]
    assert messages[2]["content"][0]["content"] == "(no output)"


def test_conversation_starts_and_ends_on_a_user_turn():
    _, messages = translate.convert_messages([{"role": "assistant", "content": "partial"}])
    assert [m["role"] for m in messages] == ["user", "assistant", "user"]


def test_images():
    _, messages = translate.convert_messages([{"role": "user", "content": [
        {"type": "text", "text": "look"},
        {"type": "image_url", "image_url": {"url": "data:image/png;base64,QUJD"}},
        {"type": "image_url", "image_url": {"url": "https://e.org/a.png"}},
    ]}])
    _, inline, remote = messages[0]["content"]
    assert inline["source"] == {"type": "base64", "media_type": "image/png", "data": "QUJD"}
    assert remote["source"] == {"type": "url", "url": "https://e.org/a.png"}


def test_stop_sequences_skip_whitespace():
    assert build({"messages": [USER], "stop": ["END", "\n"]})["stop_sequences"] == ["END"]
    assert "stop_sequences" not in build({"messages": [USER], "stop": "\n"})


def test_prompt_tokens_count_the_whole_prompt():
    usage = translate.usage_dict(input_tokens=10, output_tokens=5, cache_read=100, cache_write=20)
    assert usage["prompt_tokens"] == 130
    assert usage["total_tokens"] == 135
    assert usage["prompt_tokens_details"]["cached_tokens"] == 100


BLOCK = {"type": "thinking", "thinking": "hmm", "signature": "sig"}


def loop_request(first_result="r1"):
    return build({"tools": [TOOL], "messages": [
        USER,
        {"role": "assistant", "tool_calls": [tool_call("t1")]},
        {"role": "tool", "tool_call_id": "t1", "content": first_result},
        {"role": "assistant", "tool_calls": [tool_call("t2")]},
        {"role": "tool", "tool_call_id": "t2", "content": "r2"},
    ]})


def stored_store():
    """A store holding the blocks each round of loop_request() produced."""
    store = translate.ThinkingStore()
    before, _ = translate._prefix_hashes(loop_request())
    store.put("t1", before[1], [BLOCK])
    store.put("t2", before[3], [{**BLOCK, "signature": "sig2"}])
    return store


def test_thinking_blocks_return_on_an_unchanged_history():
    params = loop_request()
    translate.attach_thinking(params, stored_store())
    assert params["messages"][1]["content"][0] == BLOCK
    assert params["messages"][3]["content"][0]["signature"] == "sig2"
    assert params["messages"][3]["content"][1]["type"] == "tool_use"


def test_edited_history_drops_later_blocks_but_not_earlier_ones():
    # Eliding t1's result changes what preceded t2, so t2's block would be a 400.
    # The walk stops there, and t1's block is then a removable leading one too.
    params = loop_request(first_result="[elided]")
    translate.attach_thinking(params, stored_store())
    assert all(b["type"] != "thinking" for m in params["messages"] for b in m["content"])


def test_a_changed_system_prompt_drops_every_block():
    params = loop_request()
    params["system"] = [{"type": "text", "text": "new minute"}]
    translate.attach_thinking(params, stored_store())
    assert all(b["type"] != "thinking" for m in params["messages"] for b in m["content"])


def test_blocks_are_only_missing_from_the_start():
    store = translate.ThinkingStore()
    before, _ = translate._prefix_hashes(loop_request())
    store.put("t1", before[1], [BLOCK])  # t2 unknown: restoring t1 alone is a gap
    params = loop_request()
    translate.attach_thinking(params, store)
    assert all(b["type"] != "thinking" for m in params["messages"] for b in m["content"])


def test_a_round_without_thinking_does_not_stop_the_walk():
    store = translate.ThinkingStore()
    before, _ = translate._prefix_hashes(loop_request())
    store.put("t1", before[1], [BLOCK])
    store.put("t2", before[3], [])
    params = loop_request()
    translate.attach_thinking(params, store)
    assert params["messages"][1]["content"][0] == BLOCK


def test_blocks_from_another_model_are_not_replayed():
    params = translate.build_request(
        {"tools": [TOOL], "messages": [
            USER,
            {"role": "assistant", "tool_calls": [tool_call("t1")]},
            {"role": "tool", "tool_call_id": "t1", "content": "r1"},
            {"role": "assistant", "tool_calls": [tool_call("t2")]},
            {"role": "tool", "tool_call_id": "t2", "content": "r2"},
        ]}, model="claude-sonnet-5", effort="medium", max_output_tokens=16384,
    )
    translate.attach_thinking(params, stored_store())
    assert all(b["type"] != "thinking" for m in params["messages"] for b in m["content"])


def test_store_is_bounded():
    store = translate.ThinkingStore(max_entries=2)
    for name in "abc":
        store.put(name, "p", [])
    assert store.get("a") is None and store.get("c") is not None
