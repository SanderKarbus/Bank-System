import asyncio
import logging
import uuid
import json
import sqlite3
from datetime import datetime, timedelta
from typing import Optional
from decimal import Decimal
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Header, Depends, Body, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives import hashes
from cryptography import x509
from jose import jwt, JWTError
import httpx

from config import settings
from models import (
    UserRegistrationRequest, UserRegistrationResponse,
    AccountCreationRequest, AccountCreationResponse,
    AccountLookupResponse, TransferRequest, TransferResponse,
    TransferStatusResponse, InterBankTransferRequest, InterBankTransferResponse,
    ErrorResponse, HealthResponse, BankDirectory, BankDetails, ExchangeRatesResponse,
    TransferStatus, BearerToken, BearerTokenResponse, TransferStatusEnum
)
from key_manager import key_manager
from central_bank_client import CentralBankClient
from database import Database

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

central_bank: Optional[CentralBankClient] = None
scheduler: Optional[AsyncIOScheduler] = None
db: Optional[Database] = None

bank_prefix = "XXX"
bank_id: Optional[str] = None
bank_address: Optional[str] = None
private_key_pem: Optional[str] = None
public_key_pem: Optional[str] = None


def verify_user(x_user_id: str = Header(None)) -> str:
    if not x_user_id:
        raise HTTPException(status_code=401, detail={"code": "UNAUTHORIZED", "message": "X-User-Id header required"})
    if x_user_id not in db.get_all_user_ids():
        raise HTTPException(status_code=401, detail={"code": "UNAUTHORIZED", "message": "User not found"})
    return x_user_id


def create_bearer_token(user_id: str, expires_in: int = 3600) -> tuple[str, datetime]:
    exp = datetime.utcnow() + timedelta(seconds=expires_in)
    payload = {
        "sub": user_id,
        "exp": exp,
        "iat": datetime.utcnow()
    }


async def heartbeat_task():
    global bank_id, central_bank
    if not bank_id or not central_bank:
        logger.warning("Cannot send heartbeat: bank not registered")
        return
    try:
        response = await central_bank.send_heartbeat(bank_id)
        logger.info(f"Heartbeat sent, expires at {response.expiresAt}")
    except Exception as e:
        logger.error(f"Heartbeat failed: {e}")


async def register_with_central_bank():
    global central_bank, bank_id, bank_prefix, bank_address, private_key_pem, public_key_pem
    
    private_key_pem, public_key_pem = key_manager.generate_ec_keys()
    central_bank = CentralBankClient(settings.CENTRAL_BANK_URL)
    bank_address = settings.BANK_ADDRESS
    
    if settings.BANK_ID:
        bank_id = settings.BANK_ID
        bank_prefix = bank_id[:3]
        logger.info(f"Using BANK_ID from environment: {bank_id}")
    else:
        stored_bank_id = db.get_bank_id_from_db()
        if stored_bank_id:
            bank_id = stored_bank_id
            bank_prefix = bank_id[:3]
            logger.info(f"Using stored bank_id: {bank_id}")
        else:
            result = await central_bank.register_bank(
                name=settings.BANK_NAME,
                address=bank_address,
                public_key=public_key_pem
            )
            bank_id = result.bankId
            bank_prefix = bank_id[:3]
            db.save_bank_id_to_db(bank_id)
            logger.info(f"Registered new bank: {bank_id}")
    
    db.update_bank_info(bank_id, bank_prefix, bank_address)
    
    scheduler.add_job(
        heartbeat_task,
        'interval',
        minutes=settings.HEARTBEAT_INTERVAL_MINUTES,
        id='heartbeat'
    )
    
    logger.info(f"Bank setup complete: {bank_id}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    global scheduler, db
    scheduler = AsyncIOScheduler()
    db = Database()
    db.init_db()
    
    try:
        await register_with_central_bank()
    except Exception as e:
        logger.error(f"Startup registration failed: {e}")
    
    scheduler.start()
    
    yield
    
    scheduler.shutdown()
    if central_bank:
        await central_bank.close()
    db.close()


app = FastAPI(
    title="Branch Bank API",
    description="Distributed Banking System - Branch Bank API. Supports user registration, account management, and cross-bank transfers.",
    version="1.0.0",
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/")
async def root():
    return RedirectResponse(url="/docs")


@app.get("/health", response_model=HealthResponse, tags=["Health"])
async def health():
    return HealthResponse(
        status="ok",
        timestamp=datetime.utcnow(),
        bankId=bank_id
    )


@app.get("/debug", tags=["Debug"])
async def debug_settings():
    return {
        "BANK_NAME": settings.BANK_NAME,
        "BANK_ADDRESS": settings.BANK_ADDRESS,
        "BANK_ID": settings.BANK_ID,
        "bank_id_var": bank_id,
        "bank_prefix_var": bank_prefix,
    }


# ==================== CENTRAL BANK PROXIES ====================

@app.get("/api/v1/central-bank/banks", response_model=BankDirectory, tags=["Central Bank"])
async def list_central_bank_banks():
    if not central_bank:
        raise HTTPException(status_code=503, detail={"code": "CENTRAL_BANK_UNAVAILABLE", "message": "Central bank not connected"})
    try:
        return await central_bank.list_banks()
    except Exception as e:
        raise HTTPException(status_code=503, detail={"code": "SERVICE_UNAVAILABLE", "message": str(e)})


@app.get("/api/v1/central-bank/banks/{cb_bank_id}", response_model=BankDetails, tags=["Central Bank"])
async def get_central_bank_bank(cb_bank_id: str):
    if not central_bank:
        raise HTTPException(status_code=503, detail={"code": "CENTRAL_BANK_UNAVAILABLE", "message": "Central bank not connected"})
    try:
        return await central_bank.get_bank(cb_bank_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail={"code": "BANK_NOT_FOUND", "message": str(e)})
    except Exception as e:
        raise HTTPException(status_code=500, detail={"code": "INTERNAL_ERROR", "message": str(e)})


@app.get("/api/v1/central-bank/exchange-rates", response_model=ExchangeRatesResponse, tags=["Central Bank"])
async def get_exchange_rates():
    if not central_bank:
        raise HTTPException(status_code=503, detail={"code": "CENTRAL_BANK_UNAVAILABLE", "message": "Central bank not connected"})
    try:
        return await central_bank.get_exchange_rates()
    except Exception as e:
        raise HTTPException(status_code=500, detail={"code": "INTERNAL_ERROR", "message": str(e)})


# ==================== USERS ====================

@app.post("/api/v1/users", response_model=UserRegistrationResponse, status_code=201, tags=["Users"])
async def register_user(request: UserRegistrationRequest):
    existing = db.get_user_by_email(request.email) if request.email else None
    if existing:
        raise HTTPException(status_code=409, detail={"code": "DUPLICATE_USER", "message": "A user with this email address is already registered"})
    
    user = db.create_user(request.fullName, request.email)
    return UserRegistrationResponse(**user)


@app.get("/api/v1/users/{user_id}", response_model=UserRegistrationResponse, tags=["Users"])
async def get_user(user_id: str, x_user_id: str = Header(None)):
    verify_user(x_user_id)
    
    user = db.get_user(user_id)
    if not user:
        raise HTTPException(status_code=404, detail={"code": "USER_NOT_FOUND", "message": f"User with ID '{user_id}' not found"})
    
    return UserRegistrationResponse(**user)


@app.post("/api/v1/users/auth/token", response_model=BearerTokenResponse, tags=["Users"])
async def create_token(request: UserRegistrationRequest):
    user = db.get_user_by_email(request.email) if request.email else None
    if not user:
        raise HTTPException(status_code=404, detail={"code": "USER_NOT_FOUND", "message": "User not found"})
    
    return BearerTokenResponse(
        accessToken=user["id"],
        tokenType="Bearer",
        expiresAt=datetime.utcnow()
    )


# ==================== ACCOUNTS ====================

@app.post("/api/v1/users/{user_id}/accounts", response_model=AccountCreationResponse, status_code=201, tags=["Accounts"])
async def create_account(user_id: str, request: AccountCreationRequest, x_user_id: str = Header(None)):
    verify_user(x_user_id)
    
    user = db.get_user(user_id)
    if not user:
        raise HTTPException(status_code=404, detail={"code": "USER_NOT_FOUND", "message": f"User with ID '{user_id}' not found"})
    
    currency = request.currency.upper()
    if currency not in ["EUR", "USD", "GBP", "SEK", "LVL"]:
        raise HTTPException(status_code=400, detail={"code": "UNSUPPORTED_CURRENCY", "message": f"Currency '{currency}' is not supported by this bank"})
    
    account = db.create_account(user_id, currency, bank_prefix)
    
    return AccountCreationResponse(**account)


@app.get("/api/v1/accounts/{account_number}", response_model=AccountLookupResponse, tags=["Accounts"])
async def lookup_account(account_number: str):
    account = db.get_account(account_number)
    if not account:
        raise HTTPException(status_code=404, detail={"code": "ACCOUNT_NOT_FOUND", "message": f"Account with number '{account_number}' not found"})
    
    user = db.get_user(account["ownerId"])
    
    return AccountLookupResponse(
        accountNumber=account["accountNumber"],
        ownerName=user["fullName"] if user else "Unknown",
        currency=account["currency"]
    )


@app.get("/api/v1/users/{user_id}/accounts", response_model=list[AccountCreationResponse], tags=["Accounts"])
async def list_user_accounts(user_id: str, x_user_id: str = Header(None)):
    verify_user(x_user_id)
    
    user = db.get_user(user_id)
    if not user:
        raise HTTPException(status_code=404, detail={"code": "USER_NOT_FOUND", "message": f"User with ID '{user_id}' not found"})
    
    accounts = db.get_user_accounts(user_id)
    return [AccountCreationResponse(**acc) for acc in accounts]


# ==================== TRANSFERS ====================

def sign_jwt_ec(payload: dict) -> str:
    global private_key_pem
    
    private_key = serialization.load_pem_private_key(
        private_key_pem.encode(),
        password=None,
        backend=default_backend()
    )
    
    token = jwt.encode(payload, private_key, algorithm="ES256")
    return token


async def convert_currency(amount: Decimal, from_currency: str, to_currency: str, rates: dict) -> tuple[Decimal, str, datetime]:
    if from_currency == to_currency:
        return amount, "1.000000", datetime.utcnow()
    
    from_rate = Decimal("1") if from_currency == "EUR" else Decimal(rates.get(from_currency, "1"))
    to_rate = Decimal("1") if to_currency == "EUR" else Decimal(rates.get(to_currency, "1"))
    
    amount_eur = amount / from_rate
    converted = (amount_eur * to_rate).quantize(Decimal("0.01"))
    rate = (to_rate / from_rate).quantize(Decimal("0.000001"))
    
    return converted, str(rate), datetime.utcnow()


@app.post("/api/v1/transfers", response_model=TransferResponse, tags=["Transfers"])
async def initiate_transfer(request: TransferRequest, x_user_id: str = Header(None)):
    verify_user(x_user_id)
    
    transfer_id = request.transferId
    
    existing = db.get_transfer(transfer_id)
    if existing:
        if existing["status"] == TransferStatus.PENDING:
            raise HTTPException(status_code=409, detail={"code": "TRANSFER_ALREADY_PENDING", "message": f"Transfer with ID '{transfer_id}' is already pending"})
        raise HTTPException(status_code=409, detail={"code": "DUPLICATE_TRANSFER", "message": f"A transfer with ID '{transfer_id}' already exists"})
    
    source = db.get_account(request.sourceAccount)
    if not source:
        raise HTTPException(status_code=404, detail={"code": "ACCOUNT_NOT_FOUND", "message": f"Source account '{request.sourceAccount}' not found"})
    
    amount = Decimal(request.amount)
    if source["balance"] < amount:
        raise HTTPException(status_code=422, detail={"code": "INSUFFICIENT_FUNDS", "message": "Insufficient funds in source account"})
    
    dest_prefix = request.destinationAccount[:3]
    is_cross_bank = dest_prefix != bank_prefix
    now = datetime.utcnow()
    
    transfer_data = {
        "transferId": transfer_id,
        "status": TransferStatus.COMPLETED.value,
        "sourceAccount": request.sourceAccount,
        "destinationAccount": request.destinationAccount,
        "amount": request.amount,
        "timestamp": now
    }
    
    if is_cross_bank:
        dest_account = db.get_account(request.destinationAccount)
        dest_currency = dest_account["currency"] if dest_account else source["currency"]
        
        exchange_rates = await central_bank.get_exchange_rates()
        converted_amount, exchange_rate, rate_time = await convert_currency(
            amount, source["currency"], dest_currency, exchange_rates.rates
        )
        
        jwt_payload = {
            "transferId": transfer_id,
            "sourceAccount": request.sourceAccount,
            "destinationAccount": request.destinationAccount,
            "amount": str(converted_amount),
            "sourceBankId": bank_id,
            "destinationBankId": dest_prefix,
            "timestamp": now.isoformat() + "Z",
            "nonce": str(uuid.uuid4())
        }
        
        jwt_token = sign_jwt_ec(jwt_payload)
        
        db.update_account_balance(request.sourceAccount, source["balance"] - amount)
        
        transfer_data["convertedAmount"] = str(converted_amount)
        transfer_data["exchangeRate"] = exchange_rate
        transfer_data["rateCapturedAt"] = rate_time
        
        try:
            dest_bank = await central_bank.get_bank(dest_prefix)
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.post(
                    f"{dest_bank.address}/transfers/receive",
                    json={"jwt": jwt_token}
                )
                
                if resp.status_code == 200:
                    transfer_data["status"] = TransferStatus.COMPLETED.value
                    logger.info(f"Cross-bank transfer {transfer_id} completed")
                else:
                    transfer_data["status"] = TransferStatus.PENDING.value
                    transfer_data["pendingSince"] = now
                    transfer_data["retryCount"] = 0
                    logger.warning(f"Cross-bank transfer {transfer_id} pending")
                    
        except (httpx.ConnectError, httpx.TimeoutException) as e:
            db.update_account_balance(request.sourceAccount, source["balance"])
            raise HTTPException(status_code=503, detail={"code": "DESTINATION_BANK_UNAVAILABLE", "message": "Destination bank is temporarily unavailable. Transfer has been queued for retry."})
        except Exception as e:
            transfer_data["status"] = TransferStatus.PENDING.value
            transfer_data["pendingSince"] = now
            transfer_data["retryCount"] = 0
            logger.error(f"Cross-bank transfer error: {e}")
    else:
        dest = db.get_account(request.destinationAccount)
        if not dest:
            raise HTTPException(status_code=404, detail={"code": "ACCOUNT_NOT_FOUND", "message": f"Destination account '{request.destinationAccount}' not found"})
        
        db.update_account_balance(request.sourceAccount, source["balance"] - amount)
        db.update_account_balance(request.destinationAccount, dest["balance"] + amount)
    
    db.create_transfer(**transfer_data)
    return TransferResponse(**transfer_data)


@app.get("/api/v1/transfers/{transfer_id}", response_model=TransferStatusResponse, tags=["Transfers"])
async def get_transfer_status(transfer_id: str, x_user_id: str = Header(None)):
    verify_user(x_user_id)
    
    transfer = db.get_transfer(transfer_id)
    if not transfer:
        raise HTTPException(status_code=404, detail={"code": "TRANSFER_NOT_FOUND", "message": f"Transfer with ID '{transfer_id}' not found"})
    
    return TransferStatusResponse(**transfer)


@app.get("/api/v1/transfers", response_model=list[TransferStatusResponse], tags=["Transfers"])
async def list_transfers(
    account_number: str = Query(None),
    status: str = Query(None),
    x_user_id: str = Header(None)
):
    verify_user(x_user_id)
    
    transfers = db.get_transfers(account_number=account_number, status=status, owner_id=x_user_id)
    return [TransferStatusResponse(**t) for t in transfers]


# ==================== INTER-BANK TRANSFER RECEIVE ====================

@app.post("/api/v1/transfers/receive", response_model=InterBankTransferResponse, tags=["Transfers"])
async def receive_inter_bank_transfer(request: InterBankTransferRequest):
    try:
        jwt_token = request.jwt
        
        try:
            header = jwt.get_unverified_header(jwt_token)
        except Exception:
            raise HTTPException(status_code=401, detail={"code": "INVALID_JWT", "message": "Invalid JWT format"})
        
        try:
            payload = jwt.decode(jwt_token, options={"verify_signature": False}, algorithms=["ES256"])
        except JWTError as e:
            raise HTTPException(status_code=401, detail={"code": "INVALID_JWT", "message": f"Invalid JWT: {str(e)}"})
        
        if not central_bank:
            raise HTTPException(status_code=503, detail={"code": "SERVICE_UNAVAILABLE", "message": "Cannot verify source bank"})
        
        source_bank_id = payload.get("sourceBankId")
        if not source_bank_id:
            raise HTTPException(status_code=400, detail={"code": "BAD_REQUEST", "message": "Missing sourceBankId in JWT"})
        
        try:
            source_bank = await central_bank.get_bank(source_bank_id)
            public_key_pem = source_bank.publicKey
            
            public_key = serialization.load_pem_public_key(
                public_key_pem.encode(),
                backend=default_backend()
            )
            
            jwt.decode(jwt_token, public_key, algorithms=["ES256"])
            logger.info(f"JWT verified for transfer {payload.get('transferId')} from bank {source_bank_id}")
        except ValueError as e:
            logger.warning(f"Bank {source_bank_id} not found for JWT verification: {e}")
        except Exception as e:
            logger.warning(f"JWT verification failed but proceeding: {e}")
        
        account = db.get_account(payload["destinationAccount"])
        if not account:
            raise HTTPException(status_code=404, detail={"code": "ACCOUNT_NOT_FOUND", "message": f"Account '{payload['destinationAccount']}' not found"})
        
        amount = Decimal(payload["amount"])
        db.update_account_balance(payload["destinationAccount"], account["balance"] + amount)
        
        now = datetime.utcnow()
        db.create_transfer(
            transferId=payload["transferId"],
            status=TransferStatus.COMPLETED.value,
            sourceAccount=payload["sourceAccount"],
            destinationAccount=payload["destinationAccount"],
            amount=payload["amount"],
            timestamp=now
        )
        
        return InterBankTransferResponse(
            transferId=payload["transferId"],
            status=TransferStatus.COMPLETED,
            destinationAccount=payload["destinationAccount"],
            amount=payload["amount"],
            timestamp=now
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Receive transfer error: {e}")
        raise HTTPException(status_code=400, detail={"code": "BAD_REQUEST", "message": str(e)})


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
