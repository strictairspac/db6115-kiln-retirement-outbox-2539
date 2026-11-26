from time import time

from sqlalchemy import (
    BigInteger, Column, ForeignKey, Integer, JSON, String, Table, UniqueConstraint,
)

from .base import metadata


# 产线：放行判定的并发串行化边界（对产线行加 FOR UPDATE 行锁）。
production_lines = Table(
    "production_lines", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("code", String(48), unique=True, nullable=False),
    Column("name", String(120), nullable=False),
    Column("note", String(420), nullable=False, server_default=""),
    Column("created_by", String(48), nullable=False, server_default="system"),
    Column("created_at", BigInteger, nullable=False, default=lambda: int(time())),
)


# 烘箱/炉台账。旧炉与替换炉都在此表，用 kind 区分；state 决定是否计入可用数。
ovens = Table(
    "ovens", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("code", String(48), unique=True, nullable=False),
    Column("line_id", ForeignKey("production_lines.id"), nullable=False, index=True),
    Column("kind", String(16), nullable=False, server_default="old"),  # old / replacement
    Column("state", String(24), nullable=False, index=True),
    Column("note", String(420), nullable=False, server_default=""),
    Column("accepted_by", String(48), nullable=True),
    Column("accepted_at", BigInteger, nullable=True),
    Column("restored_by", String(48), nullable=True),
    Column("restored_at", BigInteger, nullable=True),
    Column("created_at", BigInteger, nullable=False, default=lambda: int(time())),
)


# 炉体退役批次（放行台账）。
retirement_batches = Table(
    "retirement_release_batches", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("code", String(48), unique=True, nullable=False),
    Column("line_id", ForeignKey("production_lines.id"), nullable=False, index=True),
    Column("title", String(160), nullable=False, server_default=""),
    Column("status", String(16), nullable=False, index=True),
    # 最近一次判定快照
    Column("release_verdict", String(16), nullable=False, server_default="pending"),
    Column("shortage", Integer, nullable=False, server_default="0"),
    Column("usable_count", Integer, nullable=False, server_default="0"),
    Column("retire_count", Integer, nullable=False, server_default="0"),
    Column("remaining_count", Integer, nullable=False, server_default="0"),
    Column("verdict_text", String(520), nullable=False, server_default=""),
    Column("release_count", Integer, nullable=False, server_default="0"),
    Column("submit_error", String(320), nullable=True),
    Column("extra", JSON, nullable=True),
    Column("created_by", String(48), nullable=False, server_default="system"),
    Column("created_at", BigInteger, nullable=False, default=lambda: int(time())),
    Column("decided_at", BigInteger, nullable=True),
    Column("released_at", BigInteger, nullable=True),
    Column("submitted_at", BigInteger, nullable=True),
    Column("restored_at", BigInteger, nullable=True),
)


retirement_items = Table(
    "retirement_release_items", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("batch_id", ForeignKey("retirement_release_batches.id"), nullable=False, index=True),
    Column("oven_id", ForeignKey("ovens.id"), nullable=False, index=True),
    UniqueConstraint("batch_id", "oven_id", name="uq_release_batch_oven"),
)


# 验收收据：每台替换炉至多一行（oven_id 唯一），重试只更新 attempts，不新增行、不重复计数。
oven_acceptances = Table(
    "oven_acceptances", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("oven_id", ForeignKey("ovens.id"), nullable=False, unique=True),
    Column("idempotency_key", String(80), nullable=True, unique=True),
    Column("attempts", Integer, nullable=False, server_default="1"),
    Column("accepted_by", String(48), nullable=False),
    Column("accepted_at", BigInteger, nullable=False),
)
