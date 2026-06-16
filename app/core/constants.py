"""全局常量单点声明：错误码、服务名、Redis key（后缀 + 命名空间拼接）。

错误码分段约定（对齐 AIOps_API_文档 §1.3）：
  40001 = 未登录/Token 失效    40003 = 权限不足
  40004 = 资源不存在            40022 = 参数校验失败
  5xxxx = 系统内部错误
"""
from enum import IntEnum

SERVICE_NAME = "mo-chat-aiops"


class ErrorCode(IntEnum):
    """统一业务错误码（body 内 code 字段），对齐 AIOps_API_文档 §1.3。"""

    SUCCESS = 0
    # 认证 / 上下文
    UNAUTHORIZED = 40001       # 未登录 / Token 失效
    FORBIDDEN = 40003          # 权限不足
    NOT_FOUND = 40004          # 资源不存在
    VALIDATION_ERROR = 40022   # 参数校验失败
    # 系统
    INTERNAL = 50000           # 服务器内部错误
    K8S_ERROR = 50001          # Kubernetes API 调用失败
    EXTERNAL_SERVICE = 50002   # 外部服务连接失败


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
