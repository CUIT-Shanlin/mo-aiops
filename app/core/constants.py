"""全局常量单点声明：错误码、服务名、Redis key（后缀 + 命名空间拼接）。

错误码分段约定（后续里程碑按域扩展，避免拍脑袋编号）：
  40xx = 鉴权 / 项目上下文     41xx = 告警域
  42xx = 自愈域 / 请求校验      43xx = Agent 域
  5xxx = 系统内部错误
"""
from enum import IntEnum

SERVICE_NAME = "mo-chat-aiops"


class ErrorCode(IntEnum):
    """统一业务错误码（body 内 code 字段）。"""

    SUCCESS = 0
    # 40xx 鉴权 / 上下文
    INVALID_PROJECT = 4001
    UNAUTHORIZED = 4010
    FORBIDDEN = 4030
    # 42xx 请求校验
    VALIDATION_ERROR = 4220
    # 5xxx 系统
    INTERNAL = 5000


class RedisKey:
    """Redis key 后缀单点声明 + 项目命名空间拼接，禁止散落字面量。

    后续里程碑用到的 key 后缀在此登记，调用点引用常量，不写字符串。
    """

    AGENT_STATUS = "agent:status"     # M3
    INGEST = "ingest"                 # M4 入站处理队列
    ALERTS = "alerts"                 # M4 出站前端推送通道
    TOPOLOGY_CACHE = "topology:cache" # M5
    CONFIG = "config"                 # 运行时热更新配置 Hash
    WS_AGENT = "ws:agent"             # M5 PubSub
    WS_HEAL = "ws:heal"               # M5 PubSub
    RECENT_ERRORS = "recent_errors"   # M2 日志采集

    @staticmethod
    def of(project_id: str, suffix: str) -> str:
        """拼接项目命名空间 key：aiops:{project_id}:{suffix}。"""
        return f"aiops:{project_id}:{suffix}"
