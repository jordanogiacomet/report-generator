from pydantic import BaseModel
from datetime import datetime

class TemplateSchema(BaseModel):
    id: int
    key: str
    version: int
    body: str
    created_at: datetime
    updated_at: datetime
    is_active: bool
    