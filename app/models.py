from pydantic import BaseModel, Field

class PaymentRequest(BaseModel):
    account_id: str
    merchant_id: str
    amount: float = Field(..., gt=0)
    currency: str = "USD"

class PaymentResponse(BaseModel):
    transaction_id: str
    status: str
    message: str
