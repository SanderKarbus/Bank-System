import asyncio
import logging
import uuid
from datetime import datetime, timedelta
from typing import Optional, Dict
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Header, Depends
from fastapi.middleware.cors import CORSMiddleware
from apscheduler.schedulers.asyncio import AsyncIOScheduler

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
    global central_bank, bank_id, bank_prefix
    
    try:
        _, public_key = key_manager.generate_rsa_keys()
        central_bank = CentralBankClient(settings.CENTRAL_BANK_URL)
        
        result = await central_bank.register_bank(
            name=settings.BANK_NAME,
            address=settings.BANK_ADDRESS,
            public_key=public_key
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
        raise


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


@app.get("/central-bank/banks", response_model=BankDirectory)
async def list_central_bank_banks():
    if not central_bank:
        raise HTTPException(status_code=503, detail={"code": "CENTRAL_BANK_UNAVAILABLE", "message": "Central bank not connected"})
    try:
        return await central_bank.list_banks()
    except Exception as e:
        raise HTTPException(status_code=503, detail={"code": "SERVICE_UNAVAILABLE", "message": str(e)})


@app.get("/central-bank/banks/{bank_id_param}", response_model=BankDetails)
async def get_central_bank_bank(bank_id_param: str):
    if not central_bank:
        raise HTTPException(status_code=503, detail={"code": "CENTRAL_BANK_UNAVAILABLE", "message": "Central bank not connected"})
    try:
        return await central_bank.get_bank(bank_id_param)
    except ValueError as e:
        raise HTTPException(status_code=404, detail={"code": "BANK_NOT_FOUND", "message": str(e)})
    except Exception as e:
        raise HTTPException(status_code=500, detail={"code": "INTERNAL_ERROR", "message": str(e)})


@app.get("/central-bank/exchange-rates", response_model=ExchangeRatesResponse)
async def get_exchange_rates():
    if not central_bank:
        raise HTTPException(status_code=503, detail={"code": "CENTRAL_BANK_UNAVAILABLE", "message": "Central bank not connected"})
    try:
        return await central_bank.get_exchange_rates()
    except Exception as e:
        raise HTTPException(status_code=500, detail={"code": "INTERNAL_ERROR", "message": str(e)})


@app.post("/api/v1/users", response_model=UserRegistrationResponse, status_code=201)
async def register_user(request: UserRegistrationRequest):
    user_id = str(uuid.uuid4())
    now = datetime.utcnow()
    
    for user in users_db.values():
        if user.get("email") == request.email and request.email:
            raise HTTPException(status_code=409, detail={"code": "DUPLICATE_USER", "message": "User with this email already exists"})
    
    user_data = {
        "userId": f"user-{user_id}",
        "fullName": request.fullName,
        "email": request.email,
        "createdAt": now
    }
    users_db[user_id] = user_data
    
    return UserRegistrationResponse(**user_data)


@app.post("/api/v1/users/{user_id}/accounts", response_model=AccountCreationResponse, status_code=201)
async def create_account(user_id: str, request: AccountCreationRequest, user_id_header: str = Depends(get_current_user_id)):
    if user_id != user_id_header:
        raise HTTPException(status_code=404, detail={"code": "USER_NOT_FOUND", "message": f"User with ID '{user_id}' not found"})
    
    if user_id not in users_db:
        raise HTTPException(status_code=404, detail={"code": "USER_NOT_FOUND", "message": f"User with ID '{user_id}' not found"})
    
    account_suffix = str(uuid.uuid4())[:5].upper()
    account_number = f"{bank_prefix}{account_suffix}"
    
    now = datetime.utcnow()
    account_data = {
        "accountNumber": account_number,
        "ownerId": user_id,
        "currency": request.currency,
        "balance": "0.00",
        "createdAt": now
    }
    accounts_db[account_number] = account_data
    
    return AccountCreationResponse(**account_data)


@app.get("/api/v1/accounts/{account_number}", response_model=AccountLookupResponse)
async def lookup_account(account_number: str):
    account = accounts_db.get(account_number.upper())
    if not account:
        raise HTTPException(status_code=404, detail={"code": "ACCOUNT_NOT_FOUND", "message": f"Account with number '{account_number}' not found"})
    
    owner = users_db.get(account["ownerId"], {})
    return AccountLookupResponse(
        accountNumber=account["accountNumber"],
        ownerName=owner.get("fullName", "Unknown"),
        currency=account["currency"]
    )


@app.post("/api/v1/transfers", response_model=TransferResponse)
async def initiate_transfer(request: TransferRequest, user_id: str = Depends(get_current_user_id)):
    if request.transferId in transfers_db:
        existing = transfers_db[request.transferId]
        if existing["status"] == TransferStatus.PENDING:
            raise HTTPException(status_code=409, detail={"code": "TRANSFER_ALREADY_PENDING", "message": f"Transfer with ID '{request.transferId}' is already pending"})
        raise HTTPException(status_code=409, detail={"code": "DUPLICATE_TRANSFER", "message": f"A transfer with ID '{request.transferId}' already exists"})
    
    source = accounts_db.get(request.sourceAccount.upper())
    if not source:
        raise HTTPException(status_code=404, detail={"code": "ACCOUNT_NOT_FOUND", "message": f"Source account '{request.sourceAccount}' not found"})
    
    dest = accounts_db.get(request.destinationAccount.upper())
    if not dest:
        if request.destinationAccount[:3] == bank_prefix:
            raise HTTPException(status_code=404, detail={"code": "ACCOUNT_NOT_FOUND", "message": f"Destination account '{request.destinationAccount}' not found"})
        raise HTTPException(status_code=404, detail={"code": "ACCOUNT_NOT_FOUND", "message": f"Destination account '{request.destinationAccount}' not found"})
    
    amount = float(request.amount)
    balance = float(source["balance"])
    
    if balance < amount:
        raise HTTPException(status_code=422, detail={"code": "INSUFFICIENT_FUNDS", "message": "Insufficient funds in source account"})
    
    source["balance"] = f"{balance - amount:.2f}"
    
    if dest:
        dest_balance = float(dest["balance"])
        dest["balance"] = f"{dest_balance + amount:.2f}"
    
    now = datetime.utcnow()
    transfer_data = {
        "transferId": request.transferId,
        "status": TransferStatus.COMPLETED,
        "sourceAccount": request.sourceAccount,
        "destinationAccount": request.destinationAccount,
        "amount": request.amount,
        "timestamp": now
    }
    transfers_db[request.transferId] = transfer_data
    
    return TransferResponse(**transfer_data)


@app.get("/api/v1/transfers/{transfer_id}", response_model=TransferStatusResponse)
async def get_transfer_status(transfer_id: str, user_id: str = Depends(get_current_user_id)):
    transfer = transfers_db.get(transfer_id)
    if not transfer:
        raise HTTPException(status_code=404, detail={"code": "TRANSFER_NOT_FOUND", "message": f"Transfer with ID '{transfer_id}' not found"})
    
    return TransferStatusResponse(**transfer)


@app.post("/api/v1/transfers/receive", response_model=InterBankTransferResponse)
async def receive_inter_bank_transfer(request: InterBankTransferRequest):
    raise HTTPException(status_code=501, detail={"code": "NOT_IMPLEMENTED", "message": "Cross-bank transfer receiving not yet implemented"})


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
