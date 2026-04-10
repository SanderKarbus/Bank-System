import asyncio
import logging
import uuid
from datetime import datetime, timedelta
from decimal import Decimal
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Header, Depends, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.backends import default_backend
from jose import jwt, JWTError
import httpx

from config import settings
from models import *
from database import Database
from central_bank_client import CentralBankClient
from key_manager import key_manager

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

central_bank = None
scheduler = None
db = None
bank_prefix = "XXX"
bank_id = None


def verify_user(x_user_id: str = Header(None)) -> str:
    if not x_user_id:
        raise HTTPException(status_code=401, detail={"code": "UNAUTHORIZED", "message": "X-User-Id header required"})
    return x_user_id


async def heartbeat_task():
    global bank_id, central_bank
    if not bank_id or not central_bank:
        return
    try:
        await central_bank.send_heartbeat(bank_id)
        logger.info(f"Heartbeat OK: {bank_id}")
    except Exception as e:
        logger.error(f"Heartbeat failed: {e}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    global scheduler, db, bank_id, bank_prefix, central_bank
    
    import os
    logger.info(f"DEBUG ENV BANK_ID={os.environ.get('BANK_ID', 'NOT_SET')}")
    
    try:
        db = Database()
        db.init_db()
        scheduler = AsyncIOScheduler()
        
        private_key, public_key = key_manager.generate_ec_keys()
        central_bank = CentralBankClient(settings.CENTRAL_BANK_URL)
        
        bank_id = settings.BANK_ID or ""
        logger.info(f"DEBUG settings.BANK_ID={repr(settings.BANK_ID)}, bank_id={repr(bank_id)}")
        
        if bank_id:
            bank_prefix = bank_id[:3]
            try:
                await central_bank.send_heartbeat(bank_id)
                logger.info(f"Heartbeat OK: {bank_id}")
            except Exception as e:
                logger.warning(f"Heartbeat failed for {bank_id}, re-registering: {e}")
                try:
                    result = await central_bank.register_bank(settings.BANK_NAME, settings.BANK_ADDRESS, public_key)
                    bank_id = result.bankId
                    bank_prefix = bank_id[:3]
                    logger.info(f"Re-registered as: {bank_id}")
                except Exception as e2:
                    logger.error(f"Re-registration failed: {e2}")
                    bank_id = ""
                    bank_prefix = "XXX"
        else:
            logger.info("No BANK_ID, registering new bank...")
            try:
                result = await central_bank.register_bank(settings.BANK_NAME, settings.BANK_ADDRESS, public_key)
                bank_id = result.bankId
                bank_prefix = bank_id[:3]
                logger.info(f"Registered new bank: {bank_id}")
            except Exception as e:
                logger.error(f"Registration failed: {e}")
                bank_prefix = "XXX"
        
        scheduler.add_job(heartbeat_task, 'interval', minutes=settings.HEARTBEAT_INTERVAL_MINUTES, id='heartbeat')
        scheduler.start()
        logger.info(f"Server started, bank_id={bank_id}, prefix={bank_prefix}")
        
    except Exception as e:
        logger.error(f"LIFESPAN ERROR: {e}")
        bank_prefix = "XXX"
    
    yield
    
    scheduler.shutdown()
    if central_bank:
        await central_bank.close()
    db.close()


app = FastAPI(title="Branch Bank API", version="1.0.0", lifespan=lifespan, docs_url="/docs")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])


@app.get("/")
async def root():
    return RedirectResponse(url="/docs")


@app.get("/health")
async def health():
    return {"status": "ok", "timestamp": datetime.utcnow().isoformat(), "bankId": bank_id}


@app.get("/debug")
async def debug():
    return {
        "BANK_ID_env": str(settings.BANK_ID) if settings.BANK_ID else "NONE",
        "bank_id": str(bank_id) if bank_id else "NONE",
        "bank_prefix": str(bank_prefix),
        "bank_address": settings.BANK_ADDRESS
    }


# ==================== USERS ====================

@app.post("/api/v1/users", status_code=201)
async def register_user(req: UserRegistrationRequest):
    if req.email:
        existing = db.get_user_by_email(req.email)
        if existing:
            raise HTTPException(status_code=409, detail={"code": "DUPLICATE_USER", "message": "User exists"})
    return db.create_user(req.fullName, req.email)


@app.get("/api/v1/users/{user_id}")
async def get_user(user_id: str, x_user_id: str = Header(None)):
    verify_user(x_user_id)
    user = db.get_user(user_id)
    if not user:
        raise HTTPException(status_code=404, detail={"code": "USER_NOT_FOUND", "message": "Not found"})
    return user


# ==================== ACCOUNTS ====================

@app.post("/api/v1/users/{user_id}/accounts", status_code=201)
async def create_account(user_id: str, req: AccountCreationRequest, x_user_id: str = Header(None)):
    verify_user(x_user_id)
    user = db.get_user(user_id)
    if not user:
        raise HTTPException(status_code=404, detail={"code": "USER_NOT_FOUND", "message": "Not found"})
    return db.create_account(user_id, req.currency.upper(), bank_prefix)


@app.get("/api/v1/accounts/{account_number}")
async def lookup_account(account_number: str):
    account = db.get_account(account_number)
    if not account:
        raise HTTPException(status_code=404, detail={"code": "ACCOUNT_NOT_FOUND", "message": "Not found"})
    user = db.get_user(account["ownerId"])
    return {"accountNumber": account["accountNumber"], "ownerName": user["fullName"] if user else "?", "currency": account["currency"]}


@app.get("/api/v1/users/{user_id}/accounts")
async def list_accounts(user_id: str, x_user_id: str = Header(None)):
    verify_user(x_user_id)
    return db.get_user_accounts(user_id)


# ==================== TRANSFERS ====================

def sign_ec(payload: dict) -> str:
    private_key = serialization.load_pem_private_key(key_manager._get_private_key().encode(), password=None, backend=default_backend())
    return jwt.encode(payload, private_key, algorithm="ES256")


@app.post("/api/v1/transfers", status_code=201)
async def transfer(req: TransferRequest, x_user_id: str = Header(None)):
    verify_user(x_user_id)
    
    existing = db.get_transfer(req.transferId)
    if existing:
        raise HTTPException(status_code=409, detail={"code": "DUPLICATE_TRANSFER", "message": "Already exists"})
    
    source = db.get_account(req.sourceAccount)
    if not source:
        raise HTTPException(status_code=404, detail={"code": "ACCOUNT_NOT_FOUND", "message": "Source not found"})
    
    amount = Decimal(req.amount)
    if source["balance"] < amount:
        raise HTTPException(status_code=422, detail={"code": "INSUFFICIENT_FUNDS", "message": "No money"})
    
    dest_prefix = req.destinationAccount[:3]
    is_cross = dest_prefix != bank_prefix
    
    now = datetime.utcnow()
    result = {"transferId": req.transferId, "status": "completed", "sourceAccount": req.sourceAccount, 
              "destinationAccount": req.destinationAccount, "amount": req.amount, "timestamp": now.isoformat()}
    
    if is_cross:
        try:
            rates_resp = await central_bank.get_exchange_rates()
            rate = Decimal(rates_resp.rates.get(source["currency"], "1"))
            converted = str((amount / rate).quantize(Decimal("0.01")))
            result["convertedAmount"] = converted
            result["exchangeRate"] = str(rate)
            
            jwt_token = sign_ec({
                "transferId": req.transferId, "sourceAccount": req.sourceAccount,
                "destinationAccount": req.destinationAccount, "amount": converted,
                "sourceBankId": bank_id, "destinationBankId": dest_prefix,
                "timestamp": now.isoformat() + "Z", "nonce": str(uuid.uuid4())
            })
            
            dest_bank = await central_bank.get_bank(dest_prefix)
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.post(f"{dest_bank.address}/transfers/receive", json={"jwt": jwt_token})
                if resp.status_code != 200:
                    result["status"] = "pending"
        except Exception as e:
            logger.error(f"Cross-bank failed: {e}")
            result["status"] = "pending"
    else:
        dest = db.get_account(req.destinationAccount)
        if not dest:
            raise HTTPException(status_code=404, detail={"code": "ACCOUNT_NOT_FOUND", "message": "Dest not found"})
        db.update_balance(req.destinationAccount, dest["balance"] + amount)
    
    db.update_balance(req.sourceAccount, source["balance"] - amount)
    db.save_transfer(result)
    return result


@app.get("/api/v1/transfers/{transfer_id}")
async def get_transfer(transfer_id: str, x_user_id: str = Header(None)):
    verify_user(x_user_id)
    t = db.get_transfer(transfer_id)
    if not t:
        raise HTTPException(status_code=404, detail={"code": "NOT_FOUND", "message": "Not found"})
    return t


@app.get("/api/v1/transfers")
async def list_transfers(account_number: str = None, status: str = None, x_user_id: str = Header(None)):
    verify_user(x_user_id)
    return db.get_transfers(account_number, status)


# ==================== INTER-BANK RECEIVE ====================

@app.post("/api/v1/transfers/receive")
async def receive_transfer(req: InterBankTransferRequest):
    try:
        payload = jwt.decode(req.jwt, options={"verify_signature": False})
    except:
        raise HTTPException(status_code=401, detail={"code": "BAD_JWT", "message": "Invalid"})
    
    account = db.get_account(payload["destinationAccount"])
    if not account:
        raise HTTPException(status_code=404, detail={"code": "ACCOUNT_NOT_FOUND", "message": "Not found"})
    
    amount = Decimal(payload["amount"])
    db.update_balance(payload["destinationAccount"], account["balance"] + amount)
    
    return {"transferId": payload["transferId"], "status": "completed", 
            "destinationAccount": payload["destinationAccount"], "amount": payload["amount"],
            "timestamp": datetime.utcnow().isoformat()}


# ==================== CENTRAL BANK ====================

@app.get("/api/v1/central-bank/banks")
async def list_banks():
    if not central_bank:
        raise HTTPException(status_code=503, detail={"code": "UNAVAILABLE", "message": "No central bank"})
    return await central_bank.list_banks()


@app.get("/api/v1/central-bank/exchange-rates")
async def rates():
    if not central_bank:
        raise HTTPException(status_code=503, detail={"code": "UNAVAILABLE", "message": "No central bank"})
    return await central_bank.get_exchange_rates()


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
