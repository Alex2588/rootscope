from fastapi import Header, HTTPException, status

# Matches the idempotency_key column width on every table that stores one.
_IDEMPOTENCY_KEY_MAX_LENGTH = 255


def require_idempotency_key(
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> str:
    if not idempotency_key or not idempotency_key.strip():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Idempotency-Key header is required",
        )
    if len(idempotency_key) > _IDEMPOTENCY_KEY_MAX_LENGTH:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Idempotency-Key must be at most {_IDEMPOTENCY_KEY_MAX_LENGTH} characters",
        )
    return idempotency_key
