from app.core.constants import ErrorCode, RedisKey, SERVICE_NAME


def test_error_codes():
    assert ErrorCode.SUCCESS == 0
    assert ErrorCode.INVALID_PROJECT == 4001
    assert ErrorCode.UNAUTHORIZED == 4010
    assert ErrorCode.FORBIDDEN == 4030


def test_service_name():
    assert SERVICE_NAME == "mo-chat-aiops"


def test_redis_key_suffix_single_source():
    # 后缀是单点常量，不在调用点写字面量
    assert RedisKey.AGENT_STATUS == "agent:status"
    assert RedisKey.of("p1", RedisKey.AGENT_STATUS) == "aiops:p1:agent:status"
    assert RedisKey.of("p1", RedisKey.INGEST) == "aiops:p1:ingest"
