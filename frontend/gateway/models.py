from pydantic import BaseModel


class CreateKeyRequest(BaseModel):
    name: str = ""


class CreateKeyResponse(BaseModel):
    id: str
    key: str
    key_prefix: str
    name: str
    created_at: str
    warning: str = "Save this key now. It cannot be shown again."


class KeyInfo(BaseModel):
    id: str
    key_prefix: str
    name: str
    created_at: str
    last_used_at: str | None
    revoked: bool


class UsageStats(BaseModel):
    current_month: dict
    api_keys: list[dict]


class RateLimitError(BaseModel):
    error: dict
