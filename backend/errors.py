from fastapi import Request
from fastapi.responses import JSONResponse


class ApiError(Exception):
    """Raise this from any route to return {"error": {"code", "message"}}."""

    def __init__(self, status_code: int, code: str, message: str):
        self.status_code = status_code
        self.code = code
        self.message = message
        super().__init__(message)


def error_response(status_code: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={"error": {"code": code, "message": message}},
    )


async def api_error_handler(request: Request, exc: ApiError) -> JSONResponse:
    return error_response(exc.status_code, exc.code, exc.message)


# Shortcuts for the 7 error codes in the spec
def not_found(what: str = "Resource") -> ApiError:
    return ApiError(404, "not_found", f"{what} not found")


def bad_weights(message: str = "Each weight must be between 1 and 3") -> ApiError:
    return ApiError(400, "bad_weights", message)


def bad_lens(message: str = "Unknown lens") -> ApiError:
    return ApiError(400, "bad_lens", message)


def place_full(message: str = "This place has no slots left") -> ApiError:
    return ApiError(409, "place_full", message)


def nafdac_required(message: str = "A NAFDAC number is required") -> ApiError:
    return ApiError(400, "nafdac_required", message)


def bad_capacity(message: str = "Capacity must be at least 1") -> ApiError:
    return ApiError(400, "bad_capacity", message)


def bad_value(message: str = "Invalid value") -> ApiError:
    return ApiError(400, "bad_value", message)
