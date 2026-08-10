"""
Per-instrument precision/pip metadata, fetched live from OANDA rather than
hardcoded -- the prior attempt's `get_price_step()` guessed these values
per instrument-name-prefix; fetching from OANDA's own /instruments
endpoint means we're never out of sync with what the broker actually
requires, and new instruments need no code change.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP


@dataclass(frozen=True)
class InstrumentMeta:
    name: str
    display_precision: int
    pip_location: int
    margin_rate: float

    @property
    def price_step(self) -> Decimal:
        return Decimal(1).scaleb(-self.display_precision)

    @property
    def pip_size(self) -> Decimal:
        return Decimal(1).scaleb(self.pip_location)

    @property
    def base_currency(self) -> str:
        return self.name.split("_")[0]

    @property
    def quote_currency(self) -> str:
        return self.name.split("_")[1]


def fetch_instrument_metadata(client, instruments: list[str]) -> dict[str, InstrumentMeta]:
    """`client` is an OandaClient (or anything with a matching
    get_instruments(list[str]) -> list[dict])."""
    raw = client.get_instruments(instruments)
    return {
        ins["name"]: InstrumentMeta(
            name=ins["name"],
            display_precision=ins["displayPrecision"],
            pip_location=ins["pipLocation"],
            margin_rate=float(ins.get("marginRate", 0.05)),
        )
        for ins in raw
    }


def round_price(meta: InstrumentMeta, price: float) -> Decimal:
    step = meta.price_step
    d = Decimal(str(price))
    return (d / step).quantize(Decimal("1"), rounding=ROUND_HALF_UP) * step
