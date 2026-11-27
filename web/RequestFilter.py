from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import PlainTextResponse

from common import JWT_PUBLIC_KEY
from config import config_manager, logger
from util import JWTUtil


class RequestFilter(BaseHTTPMiddleware):
    """校验受保护接口的网关来源和 Agent Center JWT。"""

    public_paths = {"/auth/token", "/docs", "/openapi.json"}

    def __init__(self, app):
        super().__init__(app)
        self.public_key = config_manager.get(JWT_PUBLIC_KEY)

    def verify_token(self, token: str) -> bool:
        try:
            return JWTUtil.verify_token(token, self.public_key) is not None
        except Exception as error:
            logger.warning(f"Agent Center JWT 校验失败 ({type(error).__name__})")
            return False

    async def dispatch(self, request: Request, call_next):
        if request.url.path in self.public_paths:
            return await call_next(request)

        if request.headers.get("request-from") != "agent-center-gateway":
            return PlainTextResponse("请求只能通过网关转发，不能直接访问！", status_code=401)

        token = request.headers.get("token")
        if not token:
            return PlainTextResponse("请求中缺少 token ！", status_code=401)

        if not self.verify_token(token):
            return PlainTextResponse("token 已失效！", status_code=401)

        return await call_next(request)
