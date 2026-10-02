"""
封签谱系业务异常
"""
from apps.core.exceptions import BusinessException


class LineageException(BusinessException):
    """谱系操作异常基类"""


class ConservationError(LineageException):
    """数量守恒校验失败：投入与产出不一致，整次操作回滚"""


class NodeStateError(LineageException):
    """节点状态不允许参与本次操作（已领用/已冻结/已转化等）"""


class ImmutableLineageError(LineageException):
    """谱系事实记录不可变：禁止修改或删除已提交的操作与边"""
