"""phase_4_approvals_and_config"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

from mas_ops_api.db.types import UtcDateTime

revision = "4f9a1d7d6b2e"
down_revision = "2c0c77d9c6a1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "config_validation_runs",
        sa.Column(
            "requested_by_user_id",
            sa.String(length=36),
            nullable=False,
            server_default="",
        ),
    )
    op.add_column(
        "config_validation_runs",
        sa.Column(
            "requested_at",
            UtcDateTime(),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
    )
    op.add_column(
        "config_apply_runs",
        sa.Column("approval_id", sa.String(length=36), nullable=True),
    )
    op.create_foreign_key(
        "fk_config_apply_runs_approval_id",
        "config_apply_runs",
        "approval_requests",
        ["approval_id"],
        ["approval_id"],
        ondelete="SET NULL",
    )
    op.create_index(
        op.f("ix_config_apply_runs_approval_id"),
        "config_apply_runs",
        ["approval_id"],
        unique=False,
    )
    op.create_table(
        "config_apply_steps",
        sa.Column("config_apply_step_id", sa.Integer(), nullable=False),
        sa.Column("config_apply_run_id", sa.String(length=36), nullable=False),
        sa.Column("client_id", sa.String(length=36), nullable=False),
        sa.Column("step_index", sa.Integer(), nullable=False),
        sa.Column("step_name", sa.String(length=64), nullable=False),
        sa.Column("outcome", sa.String(length=32), nullable=False),
        sa.Column("details", sa.JSON(), nullable=False),
        sa.Column("occurred_at", UtcDateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["config_apply_run_id"],
            ["config_apply_runs.config_apply_run_id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("config_apply_step_id"),
        sa.UniqueConstraint("config_apply_run_id", "step_index"),
    )
    op.create_index(
        op.f("ix_config_apply_steps_client_id"),
        "config_apply_steps",
        ["client_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_config_apply_steps_config_apply_run_id"),
        "config_apply_steps",
        ["config_apply_run_id"],
        unique=False,
    )
    op.create_index(
        "ix_config_apply_steps_run_order",
        "config_apply_steps",
        ["config_apply_run_id", "step_index"],
        unique=False,
    )
    op.create_table(
        "ops_audit_entries",
        sa.Column("audit_id", sa.String(length=36), nullable=False),
        sa.Column("client_id", sa.String(length=36), nullable=False),
        sa.Column("incident_id", sa.String(length=36), nullable=True),
        sa.Column("approval_id", sa.String(length=36), nullable=True),
        sa.Column("config_apply_run_id", sa.String(length=36), nullable=True),
        sa.Column("actor_type", sa.String(length=32), nullable=False),
        sa.Column("actor_id", sa.String(length=64), nullable=False),
        sa.Column("target_type", sa.String(length=64), nullable=False),
        sa.Column("target_id", sa.String(length=64), nullable=False),
        sa.Column("action", sa.String(length=64), nullable=False),
        sa.Column("outcome", sa.String(length=32), nullable=False),
        sa.Column("details", sa.JSON(), nullable=False),
        sa.Column("occurred_at", UtcDateTime(), nullable=False),
        sa.PrimaryKeyConstraint("audit_id"),
    )
    op.create_index(
        op.f("ix_ops_audit_entries_action"),
        "ops_audit_entries",
        ["action"],
        unique=False,
    )
    op.create_index(
        op.f("ix_ops_audit_entries_approval_id"),
        "ops_audit_entries",
        ["approval_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_ops_audit_entries_client_id"),
        "ops_audit_entries",
        ["client_id"],
        unique=False,
    )
    op.create_index(
        "ix_ops_audit_entries_client_id_occurred_at",
        "ops_audit_entries",
        ["client_id", "occurred_at"],
        unique=False,
    )
    op.create_index(
        op.f("ix_ops_audit_entries_config_apply_run_id"),
        "ops_audit_entries",
        ["config_apply_run_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_ops_audit_entries_incident_id"),
        "ops_audit_entries",
        ["incident_id"],
        unique=False,
    )
    op.create_index(
        "ix_ops_audit_entries_incident_id_occurred_at",
        "ops_audit_entries",
        ["incident_id", "occurred_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_ops_audit_entries_incident_id_occurred_at",
        table_name="ops_audit_entries",
    )
    op.drop_index(op.f("ix_ops_audit_entries_incident_id"), table_name="ops_audit_entries")
    op.drop_index(
        op.f("ix_ops_audit_entries_config_apply_run_id"),
        table_name="ops_audit_entries",
    )
    op.drop_index(
        "ix_ops_audit_entries_client_id_occurred_at",
        table_name="ops_audit_entries",
    )
    op.drop_index(op.f("ix_ops_audit_entries_client_id"), table_name="ops_audit_entries")
    op.drop_index(op.f("ix_ops_audit_entries_approval_id"), table_name="ops_audit_entries")
    op.drop_index(op.f("ix_ops_audit_entries_action"), table_name="ops_audit_entries")
    op.drop_table("ops_audit_entries")
    op.drop_index(
        "ix_config_apply_steps_run_order",
        table_name="config_apply_steps",
    )
    op.drop_index(
        op.f("ix_config_apply_steps_config_apply_run_id"),
        table_name="config_apply_steps",
    )
    op.drop_index(op.f("ix_config_apply_steps_client_id"), table_name="config_apply_steps")
    op.drop_table("config_apply_steps")
    op.drop_index(
        op.f("ix_config_apply_runs_approval_id"),
        table_name="config_apply_runs",
    )
    op.drop_constraint(
        "fk_config_apply_runs_approval_id",
        "config_apply_runs",
        type_="foreignkey",
    )
    op.drop_column("config_apply_runs", "approval_id")
    op.drop_column("config_validation_runs", "requested_at")
    op.drop_column("config_validation_runs", "requested_by_user_id")
