from pydantic import BaseModel, Field


class PoolInput(BaseModel):
    name: str = Field(min_length=2, max_length=160)
    month: str = Field(min_length=7, max_length=7)
    dataset_id: int
    basis: str = Field(default='revenue', pattern='^(revenue|visits)$')
    amount: float = Field(gt=0, le=1_000_000_000)


class PoolUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=2, max_length=160)
    amount: float | None = Field(default=None, gt=0, le=1_000_000_000)
    dataset_id: int | None = None
    basis: str | None = Field(default=None, pattern='^(revenue|visits)$')


class SheetLineInput(BaseModel):
    pool_id: int
    amount: float | None = Field(default=None, gt=0, le=1_000_000_000)


class SheetInput(BaseModel):
    name: str = Field(min_length=2, max_length=160)
    note: str = Field(default='', max_length=500)
    lines: list[SheetLineInput] = Field(min_length=1, max_length=20)
