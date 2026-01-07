from datetime import datetime

from sqlalchemy import Column, DateTime, Text, UniqueConstraint
from sqlmodel import Field, SQLModel


def utcnow() -> datetime:
    return datetime.utcnow()


class Template(SQLModel, table=True):
    __tablename__ = "templates"
    __table_args__ = (
        UniqueConstraint("key", "version", name="uq_template_key_version"),
    )

    id: int | None = Field(default=None, primary_key=True)
    key: str = Field(index=True, min_length=1)
    version: int = Field(default=1, ge=1)
    body: str = Field(sa_column=Column(Text, nullable=False))
    created_at: datetime = Field(
        default_factory=utcnow,
        sa_column=Column(DateTime, nullable=False, default=utcnow),
    )
    updated_at: datetime = Field(
        default_factory=utcnow,
        sa_column=Column(DateTime, nullable=False, default=utcnow, onupdate=utcnow),
    )
    is_active: bool = Field(default=True, index=True)


class Report(SQLModel, table=True):
    __tablename__ = "reports"

    id: int | None = Field(default=None, primary_key=True)
    template_id: int | None = Field(
        default=None, foreign_key="templates.id", index=True
    )
    template_key: str | None = Field(default=None, index=True)
    template_version: int | None = Field(default=None)
    template: str = Field(sa_column=Column(Text, nullable=False))
    data_json: str = Field(sa_column=Column(Text, nullable=False))
    markdown: str = Field(sa_column=Column(Text, nullable=False))
    created_at: datetime = Field(
        default_factory=utcnow,
        sa_column=Column(DateTime, nullable=False, default=utcnow),
    )
