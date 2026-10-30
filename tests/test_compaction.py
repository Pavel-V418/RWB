from specforge.executor.compaction import LONG_ARG_CHARS, HistoryEntry, compact, estimate_tokens
from specforge.llm import ChatMessage, ToolCall


def _history() -> list[HistoryEntry]:
    entries = [
        HistoryEntry(ChatMessage(role="system", content="S" * 5000), 0),
        HistoryEntry(ChatMessage(role="user", content="задача"), 0),
    ]
    for step in range(1, 5):
        big = "x" * (LONG_ARG_CHARS + 1)
        call = ToolCall(
            id=f"c{step}", name="write_file", arguments={"path": f"{step}.txt", "content": big}
        )
        entries.append(HistoryEntry(ChatMessage(role="assistant", tool_calls=[call]), step))
        entries.append(
            HistoryEntry(
                ChatMessage.tool_result(f"c{step}", "вывод " * 500),
                step,
                stub=f"[run: exit 0, 340 строк, лог: runs/r/logs/{step}.log]",
            )
        )
    return entries


def test_old_steps_compacted_last_kept() -> None:
    entries = _history()
    before = estimate_tokens([e.message for e in entries])
    changed = compact(entries, current_step=4, keep_last=2)

    assert changed == 4  # шаги 1–2: по ассистенту и результату
    assert entries[0].message.content == "S" * 5000  # префикс не трогается
    assert entries[3].message.content == "[run: exit 0, 340 строк, лог: runs/r/logs/1.log]"
    shortened = entries[2].message.tool_calls[0]
    assert shortened.arguments["content"] == f"[опущено {LONG_ARG_CHARS + 1} символов]"
    assert shortened.arguments["path"] == "1.txt"
    assert shortened.id == "c1"
    assert entries[-1].message.content.startswith("вывод ")  # шаг 4 целиком
    assert entries[-3].message.content.startswith("вывод ")  # шаг 3 целиком
    assert estimate_tokens([e.message for e in entries]) < before


def test_compaction_is_idempotent_and_sticky() -> None:
    entries = _history()
    compact(entries, current_step=4, keep_last=2)
    snapshot = [e.message for e in entries]
    assert compact(entries, current_step=4, keep_last=2) == 0
    assert [e.message for e in entries] == snapshot
    # на следующем шаге сжимается только шаг 3, ранее сжатое не меняется
    assert compact(entries, current_step=5, keep_last=2) == 2
    assert [e.message for e in entries][:6] == snapshot[:6]


def test_invalid_calls_are_left_as_is() -> None:
    bad = ToolCall(id="b", name="run", raw_arguments="{" + "x" * 2000, error="не JSON")
    entries = [HistoryEntry(ChatMessage(role="assistant", tool_calls=[bad]), 1)]
    assert compact(entries, current_step=5, keep_last=1) == 0
