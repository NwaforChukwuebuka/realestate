from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class NormalizedProperty:
    """MunRoll row mapped to pipeline fields (plan step 2)."""

    parcel_id: str
    property_address: str | None
    city: str | None
    state: str | None
    zip: str | None
    owner_name: str | None
    mailing_address: str | None
    property_type: str | None
    year_built: int | None
    last_sale_date: str | None
    """Most recent sale date as ISO ``YYYY-MM-DD`` when parseable; else ``None``."""

    assessed_value: int | None

    def to_dict(self) -> dict[str, object]:
        return asdict(self)
