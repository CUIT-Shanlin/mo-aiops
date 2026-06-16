from app.core.constants import ErrorCode, RedisKey, SERVICE_NAME


def test_error_codes():
    assert ErrorCode.SUCCESS == 0
    assert ErrorCode.UNAUTHORIZED == 40001
    assert ErrorCode.FORBIDDEN == 40003
    assert ErrorCode.NOT_FOUND == 40004
    assert ErrorCode.VALIDATION_ERROR == 40022
    assert ErrorCode.INTERNAL == 50000


def test_service_name():
    assert SERVICE_NAME == "mo-chat-aiops"


def test_redis_key_suffix_single_source():
    assert RedisKey.AGENT_STATUS == "agent:status"
    assert RedisKey.of("p1", RedisKey.AGENT_STATUS) == "aiops:p1:agent:status"
    assert RedisKey.of("p1", RedisKey.INGEST) == "aiops:p1:ingest"
