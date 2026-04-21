"""initial schema with FTS

Revision ID: 0001_initial
Revises:
Create Date: 2026-04-21
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS unaccent")
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")

    # Custom text-search config: unaccent + russian snowball stemmer.
    # Russian snowball handles most Cyrillic tokens reasonably for ukr/rus MVP.
    # Admins can swap in hunspell_uk later by replacing the dictionary mapping.
    op.execute(
        """
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_ts_config WHERE cfgname = 'vkstat_ru_uk') THEN
                CREATE TEXT SEARCH CONFIGURATION vkstat_ru_uk (COPY = russian);
                ALTER TEXT SEARCH CONFIGURATION vkstat_ru_uk
                    ALTER MAPPING FOR hword, hword_part, word
                    WITH unaccent, russian_stem;
            END IF;
        END$$;
        """
    )

    op.create_table(
        "groups",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column("vk_id", sa.BigInteger, nullable=False, unique=True),
        sa.Column("screen_name", sa.Text, nullable=True),
        sa.Column("name", sa.Text, nullable=True),
        sa.Column("type", sa.Text, nullable=True),
        sa.Column("members_count", sa.Integer, nullable=True),
        sa.Column("is_closed", sa.Integer, nullable=True),
        sa.Column("url", sa.Text, nullable=True),
        sa.Column("added_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("last_scraped_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_post_id", sa.BigInteger, nullable=True),
        sa.Column("active", sa.Boolean, server_default=sa.text("true"), nullable=False),
    )
    op.create_index("ix_groups_active", "groups", ["active"])

    op.create_table(
        "posts",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("group_id", sa.BigInteger, sa.ForeignKey("groups.id", ondelete="CASCADE"), nullable=False),
        sa.Column("vk_post_id", sa.BigInteger, nullable=False),
        sa.Column("owner_id", sa.BigInteger, nullable=False),
        sa.Column("text", sa.Text, nullable=False, server_default=""),
        sa.Column("text_hash", sa.String(40), nullable=True),
        sa.Column("date", sa.DateTime(timezone=True), nullable=False),
        sa.Column("views", sa.Integer, server_default="0", nullable=False),
        sa.Column("likes", sa.Integer, server_default="0", nullable=False),
        sa.Column("reposts", sa.Integer, server_default="0", nullable=False),
        sa.Column("comments", sa.Integer, server_default="0", nullable=False),
        sa.Column("url", sa.Text, nullable=True),
        sa.Column("raw", sa.JSON, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column(
            "search_vector",
            sa.dialects.postgresql.TSVECTOR,
            sa.Computed(
                "to_tsvector('vkstat_ru_uk', coalesce(text, ''))",
                persisted=True,
            ),
        ),
        sa.UniqueConstraint("owner_id", "vk_post_id", name="uq_posts_owner_post"),
    )
    op.create_index("ix_posts_group_id", "posts", ["group_id"])
    op.create_index("ix_posts_date", "posts", ["date"])
    op.create_index("ix_posts_text_hash", "posts", ["text_hash"])
    op.create_index(
        "ix_posts_search_vector",
        "posts",
        ["search_vector"],
        postgresql_using="gin",
    )
    op.create_index(
        "ix_posts_text_trgm",
        "posts",
        ["text"],
        postgresql_using="gin",
        postgresql_ops={"text": "gin_trgm_ops"},
    )

    op.create_table(
        "post_metrics_history",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("post_id", sa.BigInteger, sa.ForeignKey("posts.id", ondelete="CASCADE"), nullable=False),
        sa.Column("captured_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("views", sa.Integer, nullable=False),
        sa.Column("likes", sa.Integer, nullable=False),
        sa.Column("reposts", sa.Integer, nullable=False),
        sa.Column("comments", sa.Integer, nullable=False),
    )
    op.create_index("ix_metrics_post_id", "post_metrics_history", ["post_id"])
    op.create_index("ix_metrics_captured_at", "post_metrics_history", ["captured_at"])


def downgrade() -> None:
    op.drop_index("ix_metrics_captured_at", table_name="post_metrics_history")
    op.drop_index("ix_metrics_post_id", table_name="post_metrics_history")
    op.drop_table("post_metrics_history")
    op.drop_index("ix_posts_text_trgm", table_name="posts")
    op.drop_index("ix_posts_search_vector", table_name="posts")
    op.drop_index("ix_posts_text_hash", table_name="posts")
    op.drop_index("ix_posts_date", table_name="posts")
    op.drop_index("ix_posts_group_id", table_name="posts")
    op.drop_table("posts")
    op.drop_index("ix_groups_active", table_name="groups")
    op.drop_table("groups")
    op.execute("DROP TEXT SEARCH CONFIGURATION IF EXISTS vkstat_ru_uk")
