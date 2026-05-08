"""Phase 2: ingestion pipeline schema.

Replaces the Phase 1 placeholder schema (which was never used in production).
This is a single fresh-start migration; safe because Phase 1 introduced no real data.
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "002_phase2_pipeline"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:  # noqa: PLR0915
    bind = op.get_bind()

    # ----------------------------------------- Drop legacy Phase 1 tables --
    for tbl in ("chat_sessions", "processing_jobs", "document_records"):
        op.execute(f'DROP TABLE IF EXISTS "{tbl}" CASCADE;')
    for enum_name in (
        "processing_status_enum",
        "job_status_enum",
        "job_type_enum",
    ):
        op.execute(f'DROP TYPE IF EXISTS "{enum_name}" CASCADE;')

    # ------------------------------------------------------ Enum types -----
    for nm, vals in [
        ("doc_type_enum", "'contract','nda','filing','general','unknown'"),
        ("doc_status_enum", "'uploaded','validating','ocr','chunking','embedding','indexing','ready','failed'"),
        ("version_kind_enum", "'original','ocr_text','normalized'"),
        ("job_state_enum", "'queued','running','succeeded','failed','cancelled'"),
    ]:
        op.execute(
            f"DO $$ BEGIN CREATE TYPE {nm} AS ENUM ({vals}); "
            "EXCEPTION WHEN duplicate_object THEN null; END $$;"
        )
    doc_type = postgresql.ENUM(name="doc_type_enum", create_type=False)
    doc_status = postgresql.ENUM(name="doc_status_enum", create_type=False)
    version_kind = postgresql.ENUM(name="version_kind_enum", create_type=False)
    job_state = postgresql.ENUM(name="job_state_enum", create_type=False)

    # ----------------------------------------------------- documents -------
    op.create_table(
        "documents",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("owner_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("original_filename", sa.String(500), nullable=False),
        sa.Column("stored_path", sa.String(1000), nullable=False),
        sa.Column("mime_type", sa.String(255), nullable=False),
        sa.Column("size_bytes", sa.BigInteger, nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("doc_type", doc_type, nullable=False, server_default="unknown"),
        sa.Column("status", doc_status, nullable=False, server_default="uploaded"),
        sa.Column("error_message", sa.Text, nullable=True),
        sa.Column("page_count", sa.Integer, nullable=True),
        sa.Column("language", sa.String(16), nullable=True),
        sa.Column(
            "extra", postgresql.JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint("owner_user_id", "sha256", name="uq_documents_owner_sha256"),
    )
    op.create_index("ix_documents_owner_user_id", "documents", ["owner_user_id"])
    op.create_index("ix_documents_workspace_id", "documents", ["workspace_id"])
    op.create_index("ix_documents_status", "documents", ["status"])
    op.create_index("ix_documents_doc_type", "documents", ["doc_type"])
    op.create_index("ix_documents_created_at", "documents", ["created_at"])

    # ----------------------------------------- document_versions -----------
    op.create_table(
        "document_versions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "document_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("documents.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("kind", version_kind, nullable=False),
        sa.Column("storage_path", sa.String(1000), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint("document_id", "kind", name="uq_document_versions_doc_kind"),
    )
    op.create_index(
        "ix_document_versions_document_id", "document_versions", ["document_id"]
    )

    # ----------------------------------------------------- chunks ----------
    op.create_table(
        "chunks",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "document_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("documents.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("ordinal", sa.Integer, nullable=False),
        sa.Column("page_start", sa.Integer, nullable=True),
        sa.Column("page_end", sa.Integer, nullable=True),
        sa.Column("char_start", sa.Integer, nullable=False, server_default="0"),
        sa.Column("char_end", sa.Integer, nullable=False, server_default="0"),
        sa.Column("token_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("text", sa.Text, nullable=False),
        sa.Column("section_path", sa.Text, nullable=True),
        sa.Column("hash", sa.String(64), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint("document_id", "ordinal", name="uq_chunks_doc_ordinal"),
        sa.UniqueConstraint("document_id", "hash", name="uq_chunks_doc_hash"),
    )
    op.create_index("ix_chunks_document_id", "chunks", ["document_id"])

    # ----------------------------------------- embeddings_meta -------------
    op.create_table(
        "embeddings_meta",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "document_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("documents.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("model_name", sa.String(255), nullable=False),
        sa.Column("dim", sa.Integer, nullable=False),
        sa.Column("faiss_index_path", sa.String(1000), nullable=False),
        sa.Column("vector_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint(
            "document_id", "model_name", name="uq_embeddings_meta_doc_model"
        ),
    )
    op.create_index(
        "ix_embeddings_meta_document_id", "embeddings_meta", ["document_id"]
    )

    # ----------------------------------------------------- ingest_jobs -----
    op.create_table(
        "ingest_jobs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "document_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("documents.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("celery_task_id", sa.String(255), nullable=True),
        sa.Column("state", job_state, nullable=False, server_default="queued"),
        sa.Column("progress_pct", sa.Integer, nullable=False, server_default="0"),
        sa.Column("stage", sa.String(64), nullable=True),
        sa.Column("message", sa.Text, nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index("ix_ingest_jobs_document_id", "ingest_jobs", ["document_id"])
    op.create_index("ix_ingest_jobs_state", "ingest_jobs", ["state"])


def downgrade() -> None:
    op.drop_index("ix_ingest_jobs_state", table_name="ingest_jobs")
    op.drop_index("ix_ingest_jobs_document_id", table_name="ingest_jobs")
    op.drop_table("ingest_jobs")

    op.drop_index("ix_embeddings_meta_document_id", table_name="embeddings_meta")
    op.drop_table("embeddings_meta")

    op.drop_index("ix_chunks_document_id", table_name="chunks")
    op.drop_table("chunks")

    op.drop_index("ix_document_versions_document_id", table_name="document_versions")
    op.drop_table("document_versions")

    op.drop_index("ix_documents_created_at", table_name="documents")
    op.drop_index("ix_documents_doc_type", table_name="documents")
    op.drop_index("ix_documents_status", table_name="documents")
    op.drop_index("ix_documents_workspace_id", table_name="documents")
    op.drop_index("ix_documents_owner_user_id", table_name="documents")
    op.drop_table("documents")

    bind = op.get_bind()
    for enum_name in ("job_state_enum", "version_kind_enum", "doc_status_enum", "doc_type_enum"):
        sa.Enum(name=enum_name).drop(bind, checkfirst=True)
