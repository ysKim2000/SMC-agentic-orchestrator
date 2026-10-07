import logging
import re


SIGNATURE_PATTERN = re.compile(r"([?&]sig=)[^&\s]+", re.IGNORECASE)


def redact_signed_url(value: str) -> str:
    return SIGNATURE_PATTERN.sub(r"\1[REDACTED]", value)


class SignedUrlRedactionFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = redact_signed_url(record.msg)
        if isinstance(record.args, tuple):
            record.args = tuple(
                redact_signed_url(str(value))
                if "sig=" in str(value).lower()
                else value
                for value in record.args
            )
        elif isinstance(record.args, dict):
            record.args = {
                key: (
                    redact_signed_url(str(value))
                    if "sig=" in str(value).lower()
                    else value
                )
                for key, value in record.args.items()
            }
        return True


def install_signed_url_redaction() -> None:
    redaction_filter = SignedUrlRedactionFilter()
    for logger_name in ("uvicorn.access", "httpx", "maple"):
        logging.getLogger(logger_name).addFilter(redaction_filter)
