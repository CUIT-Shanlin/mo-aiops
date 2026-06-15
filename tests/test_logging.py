import json
import logging

from app.core.logging import set_trace_id, setup_logging


def test_setup_logging_json(capsys):
    setup_logging(log_format="json")
    set_trace_id("trace-123")
    logging.getLogger("test").info("hello")
    captured = capsys.readouterr()
    out = captured.out + captured.err
    # 至少一条 JSON 行包含 service 与 trace_id
    lines = [ln for ln in out.splitlines() if ln.strip().startswith("{")]
    assert lines, "no json log emitted"
    rec = json.loads(lines[-1])
    assert rec["service"] == "mo-chat-aiops"
    assert rec.get("trace_id") == "trace-123"
