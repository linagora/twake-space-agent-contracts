"""RFC 9457 problem details, the one error format of every contract."""

from http import HTTPStatus

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException


class Problem(Exception):
    def __init__(self, *, status: int, code: str, title: str, detail: str) -> None:
        super().__init__(detail)
        self.status = status
        self.code = code
        self.title = title
        self.detail = detail

    def response(self) -> JSONResponse:
        return JSONResponse(
            status_code=self.status,
            media_type="application/problem+json",
            content={
                "type": f"urn:twake:problem:{self.code}",
                "title": self.title,
                "status": self.status,
                "detail": self.detail,
                "code": self.code,
            },
        )


def invalid_request(detail: str) -> Problem:
    return Problem(status=400, code="invalid_request", title="Invalid request", detail=detail)


def _invalid_request(error: RequestValidationError) -> Problem:
    # Each location starts with where the value came from (query, path...), then its name
    return invalid_request(
        "; ".join(
            f"{'.'.join(str(part) for part in issue['loc'][1:])}: {issue['msg']}"
            for issue in error.errors()
        )
    )


def _http_error(error: StarletteHTTPException) -> Problem:
    """Routing errors, such as an unknown path, named after their HTTP status."""
    phrase = HTTPStatus(error.status_code).phrase
    return Problem(
        status=error.status_code,
        code=phrase.lower().replace(" ", "_"),
        title=phrase,
        detail=str(error.detail),
    )


def install(app: FastAPI) -> None:
    async def handle_problem(_: Request, problem: Exception) -> JSONResponse:
        assert isinstance(problem, Problem)
        return problem.response()

    async def handle_invalid_request(_: Request, error: Exception) -> JSONResponse:
        assert isinstance(error, RequestValidationError)
        return _invalid_request(error).response()

    async def handle_http_error(_: Request, error: Exception) -> JSONResponse:
        assert isinstance(error, StarletteHTTPException)
        return _http_error(error).response()

    app.add_exception_handler(Problem, handle_problem)
    app.add_exception_handler(RequestValidationError, handle_invalid_request)
    app.add_exception_handler(StarletteHTTPException, handle_http_error)
