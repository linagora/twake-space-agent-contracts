"""RFC 9457 problem details, the one error format of every contract."""

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse


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


def _invalid_request(error: RequestValidationError) -> Problem:
    # Each location starts with where the value came from (query, path...), then its name
    details = "; ".join(
        f"{'.'.join(str(part) for part in issue['loc'][1:])}: {issue['msg']}"
        for issue in error.errors()
    )
    return Problem(status=400, code="invalid_request", title="Invalid request", detail=details)


def install(app: FastAPI) -> None:
    async def handle_problem(_: Request, problem: Exception) -> JSONResponse:
        assert isinstance(problem, Problem)
        return problem.response()

    async def handle_invalid_request(_: Request, error: Exception) -> JSONResponse:
        assert isinstance(error, RequestValidationError)
        return _invalid_request(error).response()

    app.add_exception_handler(Problem, handle_problem)
    app.add_exception_handler(RequestValidationError, handle_invalid_request)
