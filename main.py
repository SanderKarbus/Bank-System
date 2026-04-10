import asyncio
import logging
import uuid
import json
from datetime import datetime, timedelta
from typing import Optional, Dict
from decimal import Decimal
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Header, Depends, Body
from fastapi.middleware.cors import CORSMiddleware
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from jose import jwt, JWTError
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.backends import default_backend
import httpx

from config import settings
from models import (
    UserRegistrationRequest, UserRegistrationResponse,
    AccountCreationRequest, AccountCreationResponse,
    AccountLookupResponse, TransferRequest, TransferResponse,
    TransferStatusResponse, InterBankTransferRequest, InterBankTransferResponse,
    ErrorResponse, HealthResponse, BankDirectory, BankDetails, ExchangeRatesResponse,
    TransferStatus
)
from key_manager import key_manager
from central_bank_client import CentralBankClient

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

central_bank: Optional[CentralBankClient] = None
scheduler: Optional[AsyncIOScheduler] = None

users_db: Dict[str, dict] = {}
accounts_db: Dict[str, dict] = {}
transfers_db: Dict[str, dict] = {}
bank_prefix = "XXX"
bank_id: Optional[str] = None
private_key = None


def get_current_user_id(authorization: str = Header(None)) -> str:
    if not authorization:
        raise HTTPException(status_code=401, detail={"code": "UNAUTHORIZED", "message": "Authentication required"})
    if not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail={"code": "UNAUTHORIZED", "message": "Invalid authorization header"})
    token = authorization[7:]
    if token not in users_db:
        raise HTTPException(status_code=401, detail={"code": "UNAUTHORIZED", "message": "Invalid token"})
    return token


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
    global central_bank, bank_id, bank_prefix, private_key, public_key_pem
    
    try:
        private_key, public_key_pem = key_manager.generate_rsa_keys()
        central_bank = CentralBankClient(settings.CENTRAL_BANK_URL)
        
        result = await central_bank.register_bank(
            name=settings.BANK_NAME,
            address=settings.BANK_ADDRESS,
            public_key=public_key_pem
        )
        
        bank_id = result.bankId
        bank_prefix = bank_id[:3]
        
        scheduler.add_job(
            heartbeat_task,
            'interval',
            minutes=settings.HEARTBEAT_INTERVAL_MINUTES,
            id='heartbeat'
        )
        
        logger.info(f"Successfully registered with central bank: {bank_id}")
        
    except Exception as e:
        logger.error(f"Failed to register with central bank: {e}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    global scheduler
    scheduler = AsyncIOScheduler()
    
    try:
        await register_with_central_bank()
    except Exception as e:
        logger.error(f"Startup registration failed: {e}")
    
    scheduler.start()
    
    yield
    
    scheduler.shutdown()
    if central_bank:
        await central_bank.close()


app = FastAPI(
    title="Branch Bank API",
    description="Distributed Banking System - Branch Bank",
    version="1.0.0",
    lifespan=lifespan
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health", response_model=HealthResponse)
async def health():
    return HealthResponse(
        status="ok",
        timestamp=datetime.utcnow(),
        bankId=bank_id
    )


@app.get("/api/v1/central-bank/banks", response_model=BankDirectory)
async def list_central_bank_banks():
    if not central_bank:
        raise HTTPException(status_code=503, detail={"code": "CENTRAL_BANK_UNAVAILABLE", "message": "Central bank not connected"})
    try:
        return await central_bank.list_banks()
    except Exception as e:
        raise HTTPException(status_code=503, detail={"code": "SERVICE_UNAVAILABLE", "message": str(e)})


@app.get("/api/v1/central-bank/banks/{bank_id_param}", response_model=BankDetails)
async def get_central_bank_bank(bank_id_param: str):
    if not central_bank:
        raise HTTPException(status_code=503, detail={"code": "CENTRAL_BANK_UNAVAILABLE", "message": "Central bank not connected"})
    try:
        return await central_bank.get_bank(bank_id_param)
    except ValueError as e:
        raise HTTPException(status_code=404, detail={"code": "BANK_NOT_FOUND", "message": str(e)})
    except Exception as e:
        raise HTTPException(status_code=500, detail={"code": "INTERNAL_ERROR", "message": str(e)})


@app.get("/api/v1/central-bank/exchange-rates", response_model=ExchangeRatesResponse)
async def get_exchange_rates():
    if not central_bank:
        raise HTTPException(status_code=503, detail={"code": "CENTRAL_BANK_UNAVAILABLE", "message": "Central bank not connected"})
    try:
        return await central_bank.get_exchange_rates()
    except Exception as e:
        raise HTTPException(status_code=500, detail={"code": "INTERNAL_ERROR", "message": str(e)})


@app.post("/api/v1/users", response_model=UserRegistrationResponse, status_code=201)
async def register_user(request: UserRegistrationRequest):
    for user in users_db.values():
        if user.get("email") == request.email and request.email:
            raise HTTPException(status_code=409, detail={"code": "DUPLICATE_USER", "message": "User with this email already exists"})
    
    user_id = str(uuid.uuid4())
    now = datetime.utcnow()
    
    user_data = {
        "userId": f"user-{user_id}",
        "fullName": request.fullName,
        "email": request.email,
        "createdAt": now
    }
    users_db[user_id] = user_data
    
    return UserRegistrationResponse(**user_data)


@app.post("/api/v1/users/{user_id}/accounts", response_model=AccountCreationResponse, status_code=201)
async def create_account(user_id: str, request: AccountCreationRequest, authorization: str = Header(None)):
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail={"code": "UNAUTHORIZED", "message": "Authentication required"})
    
    token = authorization[7:]
    if token != user_id and f"user-{token}" != user_id:
        raise HTTPException(status_code=404, detail={"code": "USER_NOT_FOUND", "message": f"User with ID '{user_id}' not found"})
    
    if user_id not in users_db and f"user-{user_id}" not in users_db:
        raise HTTPException(status_code=404, detail={"code": "USER_NOT_FOUND", "message": f"User with ID '{user_id}' not found"})
    
    account_suffix = str(uuid.uuid4())[:5].upper().replace("-", "")[:5]
    account_number = f"{bank_prefix}{account_suffix}"
    
    while account_number in accounts_db:
        account_suffix = str(uuid.uuid4())[:5].upper().replace("-", "")[:5]
        account_number = f"{bank_prefix}{account_suffix}"
    
    now = datetime.utcnow()
    account_data = {
        "accountNumber": account_number,
        "ownerId": user_id,
        "currency": request.currency.upper(),
        "balance": Decimal("0.00"),
        "createdAt": now
    }
    accounts_db[account_number] = account_data
    
    return AccountCreationResponse(
        accountNumber=account_number,
        ownerId=user_id,
        currency=request.currency.upper(),
        balance="0.00",
        createdAt=now
    )


@app.get("/api/v1/accounts/{account_number}", response_model=AccountLookupResponse)
async def lookup_account(account_number: str):
    account = accounts_db.get(account_number.upper())
    if not account:
        raise HTTPException(status_code=404, detail={"code": "ACCOUNT_NOT_FOUND", "message": f"Account with number '{account_number}' not found"})
    
    owner_id = account["ownerId"]
    owner = users_db.get(owner_id, {})
    
    return AccountLookupResponse(
        accountNumber=account["accountNumber"],
        ownerName=owner.get("fullName", "Unknown"),
        currency=account["currency"]
    )


def create_jwt_transfer(source_account: str, destination_account: str, amount: str, 
                        dest_bank_id: str, transfer_id: str) -> str:
    global private_key
    
    payload = {
        "transferId": transfer_id,
        "sourceAccount": source_account,
        "destinationAccount": destination_account,
        "amount": amount,
        "sourceBankId": bank_id,
        "destinationBankId": dest_bank_id,
        "timestamp": datetime.utcnow().isoformat() + "Z",
        "nonce": str(uuid.uuid4())
    }
    
    private_key_obj = serialization.load_pem_private_key(
        private_key.encode(),
        password=None,
        backend=default_backend()
    )
    
    token = jwt.encode(payload, private_key_obj, algorithm="RS256")
    return token


async def convert_currency(amount: str, from_currency: str, to_currency: str, rates: dict) -> tuple[str, str, datetime]:
    if from_currency == to_currency:
        return amount, "1.000000", datetime.utcnow()
    
    if from_currency == "EUR":
        from_rate = Decimal("1.0")
    else:
        from_rate = Decimal(rates.get(from_currency, "1.0"))
    
    if to_currency == "EUR":
        to_rate = Decimal("1.0")
    else:
        to_rate = Decimal(rates.get(to_currency, "1.0"))
    
    amount_dec = Decimal(amount)
    from_to_eur = amount_dec / from_rate
    to_amount = (from_to_eur * to_rate).quantize(Decimal("0.01"))
    
    exchange_rate = (to_rate / from_rate).quantize(Decimal("0.000001"))
    
    return str(to_amount), str(exchange_rate), datetime.utcnow()


@app.post("/api/v1/transfers", response_model=TransferResponse)
async def initiate_transfer(request: TransferRequest, authorization: str = Header(None)):
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail={"code": "UNAUTHORIZED", "message": "Authentication required"})
    
    transfer_id = request.transferId
    if transfer_id in transfers_db:
        existing = transfers_db[transfer_id]
        if existing["status"] == TransferStatus.PENDING:
            raise HTTPException(status_code=409, detail={"code": "TRANSFER_ALREADY_PENDING", "message": f"Transfer with ID '{transfer_id}' is already pending"})
        raise HTTPException(status_code=409, detail={"code": "DUPLICATE_TRANSFER", "message": f"A transfer with ID '{transfer_id}' already exists"})
    
    source = accounts_db.get(request.sourceAccount.upper())
    if not source:
        raise HTTPException(status_code=404, detail={"code": "ACCOUNT_NOT_FOUND", "message": f"Source account '{request.sourceAccount}' not found"})
    
    amount = Decimal(request.amount)
    balance = source["balance"]
    
    if balance < amount:
        raise HTTPException(status_code=422, detail={"code": "INSUFFICIENT_FUNDS", "message": "Insufficient funds in source account"})
    
    dest_prefix = request.destinationAccount[:3]
    is_cross_bank = dest_prefix != bank_prefix
    
    now = datetime.utcnow()
    transfer_data = {
        "transferId": transfer_id,
        "status": TransferStatus.COMPLETED,
        "sourceAccount": request.sourceAccount,
        "destinationAccount": request.destinationAccount,
        "amount": request.amount,
        "timestamp": now
    }
    
    if is_cross_bank:
        if not central_bank:
            raise HTTPException(status_code=503, detail={"code": "CENTRAL_BANK_UNAVAILABLE", "message": "Central bank not available"})
        
        try:
            dest_bank = await central_bank.get_bank(dest_prefix)
        except ValueError:
            raise HTTPException(status_code=404, detail={"code": "DESTINATION_BANK_NOT_FOUND", "message": f"Destination bank '{dest_prefix}' not found"})
        
        source_currency = source["currency"]
        dest_account = accounts_db.get(request.destinationAccount.upper())
        dest_currency = "EUR"
        if dest_account:
            dest_currency = dest_account["currency"]
        
        exchange_rates = await central_bank.get_exchange_rates()
        converted_amount, exchange_rate, rate_time = await convert_currency(
            request.amount, source_currency, dest_currency, exchange_rates.rates
        )
        
        jwt_token = create_jwt_transfer(
            request.sourceAccount,
            request.destinationAccount,
            converted_amount,
            dest_prefix,
            transfer_id
        )
        
        source["balance"] = balance - amount
        transfer_data["convertedAmount"] = converted_amount
        transfer_data["exchangeRate"] = exchange_rate
        transfer_data["rateCapturedAt"] = rate_time
        
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(
                    f"{dest_bank.address}/transfers/receive",
                    json={"jwt": jwt_token}
                )
                
                if response.status_code == 200:
                    transfer_data["status"] = TransferStatus.COMPLETED
                else:
                    transfer_data["status"] = TransferStatus.PENDING
                    transfer_data["retryCount"] = 0
                    transfer_data["pendingSince"] = now
                    
        except httpx.ConnectError:
            transfer_data["status"] = TransferStatus.PENDING
            transfer_data["retryCount"] = 0
            transfer_data["pendingSince"] = now
            source["balance"] = balance
        except Exception as e:
            logger.error(f"Cross-bank transfer failed: {e}")
            transfer_data["status"] = TransferStatus.PENDING
            transfer_data["retryCount"] = 0
            transfer_data["pendingSince"] = now
    else:
        dest = accounts_db.get(request.destinationAccount.upper())
        if not dest:
            raise HTTPException(status_code=404, detail={"code": "ACCOUNT_NOT_FOUND", "message": f"Destination account '{request.destinationAccount}' not found"})
        
        source["balance"] = balance - amount
        dest["balance"] = dest["balance"] + amount
    
    transfers_db[transfer_id] = transfer_data
    
    return TransferResponse(**transfer_data)


@app.get("/api/v1/transfers/{transfer_id}", response_model=TransferStatusResponse)
async def get_transfer_status(transfer_id: str, authorization: str = Header(None)):
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail={"code": "UNAUTHORIZED", "message": "Authentication required"})
    
    transfer = transfers_db.get(transfer_id)
    if not transfer:
        raise HTTPException(status_code=404, detail={"code": "TRANSFER_NOT_FOUND", "message": f"Transfer with ID '{transfer_id}' not found"})
    
    return TransferStatusResponse(**transfer)


@app.post("/api/v1/transfers/receive", response_model=InterBankTransferResponse)
async def receive_inter_bank_transfer(request: InterBankTransferRequest):
    try:
        jwt_token = request.jwt
        
        header = jwt.get_unverified_header(jwt_token)
        payload = jwt.decode(jwt_token, options={"verify_signature": False})
        
        account = accounts_db.get(payload["destinationAccount"])
        if not account:
            raise HTTPException(status_code=404, detail={"code": "ACCOUNT_NOT_FOUND", "message": f"Account '{payload['destinationAccount']}' not found"})
        
        source_bank_id = payload["sourceBankId"]
        if not central_bank:
            raise HTTPException(status_code=503, detail={"code": "SERVICE_UNAVAILABLE", "message": "Cannot verify source bank"})
        
        try:
            source_bank = await central_bank.get_bank(source_bank_id)
            public_key_pem = source_bank.publicKey
            
            jwt.decode(jwt_token, public_key_pem, algorithms=["RS256"])
        except Exception as e:
            logger.warning(f"JWT verification failed: {e}")
            raise HTTPException(status_code=401, detail={"code": "UNAUTHORIZED", "message": "Invalid JWT signature"})
        
        amount = Decimal(payload["amount"])
        account["balance"] = account["balance"] + amount
        
        now = datetime.utcnow()
        return InterBankTransferResponse(
            transferId=payload["transferId"],
            status=TransferStatus.COMPLETED,
            destinationAccount=payload["destinationAccount"],
            amount=str(amount),
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


from fastapi.responses import RedirectResponse

@app.get("/")
async def root():
    return RedirectResponse(url="/docs")


@app.get("/openapi.yaml")
async def openapi_yaml():
    return app.openapi()
