import json
from datetime import timezone
from typing import Annotated, Literal
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator

COUNTRIES = frozenset("AD AE AF AG AI AL AM AO AQ AR AS AT AU AW AX AZ BA BB BD BE BF BG BH BI BJ BL BM BN BO BQ BR BS BT BV BW BY BZ CA CC CD CF CG CH CI CK CL CM CN CO CR CU CV CW CX CY CZ DE DJ DK DM DO DZ EC EE EG EH ER ES ET FI FJ FK FM FO FR GA GB GD GE GF GG GH GI GL GM GN GP GQ GR GS GT GU GW GY HK HM HN HR HT HU ID IE IL IM IN IO IQ IR IS IT JE JM JO JP KE KG KH KI KM KN KP KR KW KY KZ LA LB LC LI LK LR LS LT LU LV LY MA MC MD ME MF MG MH MK ML MM MN MO MP MQ MR MS MT MU MV MW MX MY MZ NA NC NE NF NG NI NL NO NP NR NU NZ OM PA PE PF PG PH PK PL PM PN PR PS PT PW PY QA RE RO RS RU RW SA SB SC SD SE SG SH SI SJ SK SL SM SN SO SR SS ST SV SX SY SZ TC TD TF TG TH TJ TK TL TM TN TO TR TT TV TW TZ UA UG UM US UY UZ VA VC VE VG VI VN VU WF WS YE YT ZA ZM ZW".split())


class PaymentRequestV1(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    transaction_id: UUID
    account_id: Annotated[str, Field(pattern=r"^acct_[A-Za-z0-9_-]{1,59}$")]
    merchant_id: Annotated[str, Field(pattern=r"^mer_[A-Za-z0-9_-]{1,60}$")]
    device_id: Annotated[str, Field(pattern=r"^dev_[A-Za-z0-9_-]{1,60}$")] | None = None
    event_time: AwareDatetime
    amount_minor: Annotated[int, Field(strict=True, gt=0, le=100_000_000)]
    currency: Literal["INR"]
    channel: Literal["web", "app", "pos"]
    account_country: str
    merchant_country: str
    run_id: UUID

    @field_validator("event_time", mode="before")
    @classmethod
    def iso_timestamp(cls, value):
        if isinstance(value, (int, float)) or (isinstance(value, str) and "T" not in value.upper()):
            raise ValueError("Expected a timezone-aware ISO timestamp")
        return value

    @field_validator("event_time")
    @classmethod
    def utc(cls, value):
        return value.astimezone(timezone.utc)

    @field_validator("account_country", "merchant_country")
    @classmethod
    def country(cls, value):
        if value not in COUNTRIES:
            raise ValueError("Unknown country code")
        return value

    def canonical(self):
        return json.dumps(self.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))


def validate_run(payment, registered_runs):
    if payment.run_id not in registered_runs:
        raise ValueError("Unregistered run")
    return payment
