from pydantic import BaseModel, Field, EmailStr
from typing import Optional, List
from datetime import datetime
from enum import Enum
import uuid


class TransferStatus(str, Enum):
    COMPLETED = "completed"
    FAILED = "failed"
    PENDING = "pending"
    FAILED_TIMEOUT = "failed_timeout"


class TransferStatusEnum(str, Enum):
    COMPLETED = "completed"
    FAILED = "failed"
    PENDING = "pending"
    FAILED_TIMEOUT = "failed_timeout"


class Currency(str, Enum):
    EUR = "EUR"
    USD = "USD"
    GBP = "GBP"
    SEK = "SEK"
    LVL = "LVL"


class BearerToken(BaseModel):
    accessToken: str
    tokenType: str = "Bearer"
    expiresAt: datetime


class BearerTokenResponse(BaseModel):
    accessToken: str
    tokenType: str = "Bearer"
    expiresAt: datetime


class UserRegistrationRequest(BaseModel):
    fullName: str = Field(..., min_length=2, max_length=200)
    email: Optional[EmailStr] = None


class UserRegistrationResponse(BaseModel):
    userId: str
    fullName: str
    email: Optional[str] = None
    createdAt: datetime
    token: Optional[dict] = None


class AccountCreationRequest(BaseModel):
    currency: str = Field(..., pattern="^[A-Z]{3}$")


class AccountCreationResponse(BaseModel):
    accountNumber: str
    ownerId: str
    currency: str
    balance: str = "0.00"
    createdAt: datetime


class AccountLookupResponse(BaseModel):
    accountNumber: str
    ownerName: str
    currency: str


class TransferRequest(BaseModel):
    transferId: str = Field(..., description="UUID for idempotency")
    sourceAccount: str = Field(..., pattern="^[A-Z0-9]{8}$")
    destinationAccount: str = Field(..., pattern="^[A-Z0-9]{8}$")
    amount: str = Field(..., pattern="^\\d+\\.\\d{2}$")


class TransferResponse(BaseModel):
    transferId: str
    status: TransferStatus
    sourceAccount: str
    destinationAccount: str
    amount: str
    convertedAmount: Optional[str] = None
    exchangeRate: Optional[str] = None
    rateCapturedAt: Optional[datetime] = None
    timestamp: datetime
    errorMessage: Optional[str] = None


class TransferStatusResponse(BaseModel):
    transferId: str
    status: TransferStatus
    sourceAccount: str
    destinationAccount: str
    amount: str
    convertedAmount: Optional[str] = None
    exchangeRate: Optional[str] = None
    rateCapturedAt: Optional[datetime] = None
    timestamp: datetime
    pendingSince: Optional[datetime] = None
    nextRetryAt: Optional[datetime] = None
    retryCount: Optional[int] = None
    errorMessage: Optional[str] = None


class InterBankTransferRequest(BaseModel):
    jwt: str


class InterBankTransferResponse(BaseModel):
    transferId: str
    status: TransferStatus
    destinationAccount: str
    amount: str
    timestamp: datetime


class ErrorResponse(BaseModel):
    code: str
    message: str


class BankRegistrationRequest(BaseModel):
    name: str = Field(..., min_length=3, max_length=100)
    address: str = Field(..., description="Public URL of this bank")
    publicKey: str = Field(..., min_length=100)


class BankRegistrationResponse(BaseModel):
    bankId: str
    expiresAt: datetime


class BankEntry(BaseModel):
    bankId: str
    name: str
    address: str
    publicKey: str
    lastHeartbeat: datetime
    status: str = "active"


class BankDirectory(BaseModel):
    banks: List[BankEntry]
    lastSyncedAt: datetime


class BankDetails(BaseModel):
    bankId: str
    name: str
    address: str
    publicKey: str
    lastHeartbeat: datetime
    status: str


class HeartbeatRequest(BaseModel):
    timestamp: datetime


class HeartbeatResponse(BaseModel):
    bankId: str
    receivedAt: datetime
    expiresAt: datetime
    status: str = "active"


class ExchangeRatesResponse(BaseModel):
    baseCurrency: str
    rates: dict
    timestamp: datetime


class HealthResponse(BaseModel):
    status: str = "ok"
    timestamp: datetime
    bankId: Optional[str] = None
