class AppError(Exception):
    status_code = 400

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


class NotFound(AppError):
    status_code = 404


class Forbidden(AppError):
    status_code = 403


class Conflict(AppError):
    status_code = 409


class Unprocessable(AppError):
    status_code = 422


class PayloadTooLarge(AppError):
    status_code = 413


class ServiceUnavailable(AppError):
    status_code = 503
