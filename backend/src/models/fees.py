from sqlalchemy import String, BigInteger, Boolean, Double, ForeignKey, Integer, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from src.config.database import Base


class FeePool(Base):
    """A monthly shared operating-fee pool funded in cents.

    ``used_cents`` only contains confirmed allocations.  ``amount_cents`` may
    be increased or reduced (never below ``used_cents``) and each financial
    change bumps ``version`` so old trials can be rejected at confirmation.
    """
    __tablename__ = 'fee_pools'
    __table_args__ = (UniqueConstraint('month', 'name', name='uq_fee_pool_month_name'),)
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(160))
    month: Mapped[str] = mapped_column(String(7))
    dataset_id: Mapped[int] = mapped_column(ForeignKey('datasets.id'))
    basis: Mapped[str] = mapped_column(String(10), default='revenue')
    amount_cents: Mapped[int] = mapped_column(BigInteger)
    used_cents: Mapped[int] = mapped_column(BigInteger, default=0)
    version: Mapped[int] = mapped_column(Integer, default=1)
    created_by: Mapped[int] = mapped_column(ForeignKey('users.id'))
    created_at: Mapped[float] = mapped_column(Double)
    updated_at: Mapped[float] = mapped_column(Double)


class AllocationSheet(Base):
    __tablename__ = 'allocation_sheets'
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(160))
    note: Mapped[str] = mapped_column(String(500), default='')
    status: Mapped[str] = mapped_column(String(20), default='draft')
    operation: Mapped[str] = mapped_column(String(20), default='')
    total_fee_cents: Mapped[int] = mapped_column(BigInteger, default=0)
    created_by: Mapped[int] = mapped_column(ForeignKey('users.id'))
    confirmed_by: Mapped[int | None] = mapped_column(ForeignKey('users.id'), nullable=True)
    reversed_by: Mapped[int | None] = mapped_column(ForeignKey('users.id'), nullable=True)
    created_at: Mapped[float] = mapped_column(Double)
    updated_at: Mapped[float] = mapped_column(Double)
    trialed_at: Mapped[float | None] = mapped_column(Double, nullable=True)
    confirmed_at: Mapped[float | None] = mapped_column(Double, nullable=True)
    reversed_at: Mapped[float | None] = mapped_column(Double, nullable=True)


class AllocationLine(Base):
    __tablename__ = 'allocation_lines'
    id: Mapped[int] = mapped_column(primary_key=True)
    sheet_id: Mapped[int] = mapped_column(ForeignKey('allocation_sheets.id'))
    pool_id: Mapped[int] = mapped_column(ForeignKey('fee_pools.id'))
    requested_amount_cents: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    amount_cents: Mapped[int] = mapped_column(BigInteger, default=0)
    pool_version: Mapped[int] = mapped_column(Integer, default=0)
    pool_amount_cents_snapshot: Mapped[int] = mapped_column(BigInteger, default=0)
    dataset_id: Mapped[int] = mapped_column(Integer, default=0)
    dataset_checksum: Mapped[str] = mapped_column(String(64), default='')
    basis: Mapped[str] = mapped_column(String(10), default='revenue')
    weight_total: Mapped[int] = mapped_column(BigInteger, default=0)
    zero_weight: Mapped[bool] = mapped_column(Boolean, default=False)
    reserved: Mapped[bool] = mapped_column(Boolean, default=False)
    returned: Mapped[bool] = mapped_column(Boolean, default=False)
    line_order: Mapped[int] = mapped_column(Integer, default=0)


class AllocationDetail(Base):
    __tablename__ = 'allocation_details'
    id: Mapped[int] = mapped_column(primary_key=True)
    line_id: Mapped[int] = mapped_column(ForeignKey('allocation_lines.id'))
    dimension: Mapped[str] = mapped_column(String(120))
    weight_units: Mapped[int] = mapped_column(BigInteger)
    weight_kind: Mapped[str] = mapped_column(String(10))
    fee_cents: Mapped[int] = mapped_column(BigInteger)
    revenue_cents: Mapped[int] = mapped_column(BigInteger)
    visits: Mapped[int] = mapped_column(BigInteger)


class FeePoolLedger(Base):
    """Append-only evidence for every quota movement.

    The unique (line_id, kind) key makes a retried confirm/revoke physically
    unable to move the same line twice, even if an operator retries after an
    interrupted request.
    """
    __tablename__ = 'fee_pool_ledger'
    __table_args__ = (UniqueConstraint('line_id', 'kind', name='uq_fee_pool_ledger_line_kind'),)
    id: Mapped[int] = mapped_column(primary_key=True)
    pool_id: Mapped[int] = mapped_column(ForeignKey('fee_pools.id'))
    sheet_id: Mapped[int] = mapped_column(ForeignKey('allocation_sheets.id'))
    line_id: Mapped[int] = mapped_column(ForeignKey('allocation_lines.id'))
    kind: Mapped[str] = mapped_column(String(10))
    amount_cents: Mapped[int] = mapped_column(BigInteger)
    balance_after: Mapped[int] = mapped_column(BigInteger)
    actor: Mapped[str] = mapped_column(String(150), default='system')
    created_at: Mapped[float] = mapped_column(Double)
