from fastapi import HTTPException

from packages.contracts.payment import PaymentRequestV1, validate_run


def registered_payment(registered_runs):
    """FastAPI body dependency for a caller supplying a run-registry lookup."""
    def parse(payment: PaymentRequestV1):
        try:
            return validate_run(payment, registered_runs)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
    return parse
