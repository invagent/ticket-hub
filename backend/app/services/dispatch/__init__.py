"""处理人分派引擎（Operation 运营 + 研发类共用）。"""

from app.services.dispatch.engine import DispatchResult, dispatch_handler

__all__ = ["DispatchResult", "dispatch_handler"]
