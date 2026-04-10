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
from auth import create_user_token, verify_token, invalidate_token

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

central_bank = None
scheduler = None
db = None
bank_prefix = "XXX"
bank_id = None

_bank_cache = {"data": None, "last_synced": None}


def verify_user(authorization: str = Header(None)) -> dict:
    if not authorization:
        raise HTTPException(status_code=401, detail={"code": "UNAUTHORIZED", "message": "Bearer token required"}, headers={"WWW-Authenticate": "Bearer"})
    
    if authorization.startswith("Bearer "):
        token = authorization[7:]
    else:
        token = authorization
    
    payload = verify_token(token)
    if not payload:
        raise HTTPException(status_code=401, detail={"code": "UNAUTHORIZED", "message": "Invalid or expired token"}, headers={"WWW-Authenticate": "Bearer"})
    
    return payload


async def get_cached_banks():
    global _bank_cache, central_bank
    
    now = datetime.utcnow()
    cache_age = (now - _bank_cache["last_synced"]).total_seconds() if _bank_cache["last_synced"] else float('inf')
    
    if _bank_cache["data"] is None or cache_age > 300:
        try:
            directory = await central_bank.list_banks()
            _bank_cache["data"] = directory.banks
            _bank_cache["last_synced"] = now
            logger.info(f"Bank cache refreshed: {len(directory.banks)} banks")
        except Exception as e:
            logger.warning(f"Central bank unavailable, using cache: {e}")
            if _bank_cache["data"] is None:
                raise HTTPException(status_code=503, detail={"code": "CENTRAL_BANK_UNAVAILABLE", "message": "Central bank is temporarily unavailable. Using cached directory data for routing."})
    
    return _bank_cache["data"]


async def heartbeat_task():
    global bank_id, central_bank
    if not bank_id or not central_bank:
        return
    try:
        await central_bank.send_heartbeat(bank_id)
        logger.info(f"Heartbeat OK: {bank_id}")
    except Exception as e:
        logger.error(f"Heartbeat failed: {e}")


async def process_pending_transfers():
    global db, bank_prefix, central_bank
    
    if not db:
        return
    
    pending = db.get_pending_transfers()
    
    for transfer in pending:
        if transfer["status"] != "pending":
            continue
        
        pending_since = datetime.fromisoformat(transfer.get("pendingSince", transfer.get("timestamp")))
        elapsed = datetime.utcnow() - pending_since
        
        if elapsed.total_seconds() > 14400:
            db.update_transfer_status(transfer["transferId"], "failed_timeout", 
                                     "Transfer timed out after 4 hours. Funds refunded to source account.")
            db.update_balance(transfer["sourceAccount"], 
                            Decimal(db.get_account(transfer["sourceAccount"])["balance"]) + 
                            Decimal(transfer["amount"]))
            logger.info(f"Transfer {transfer['transferId']} timed out, refunded")
            continue
        
        retry_count = transfer.get("retryCount", 0)
        delays = [60, 120, 240, 480, 960, 1920, 3600]
        delay = delays[min(retry_count, len(delays) - 1)]
        
        last_retry = transfer.get("lastRetryAt")
        if last_retry:
            last_retry_time = datetime.fromisoformat(last_retry)
            if (datetime.utcnow() - last_retry_time).total_seconds() < delay:
                continue
        
        try:
            dest_bank_id = transfer["destinationAccount"][:3]
            dest_bank = await central_bank.get_bank(dest_bank_id)
            
            jwt_token = sign_ec({
                "transferId": transfer["transferId"],
                "sourceAccount": transfer["sourceAccount"],
                "destinationAccount": transfer["destinationAccount"],
                "amount": transfer.get("convertedAmount", transfer["amount"]),
                "sourceBankId": bank_id,
                "destinationBankId": dest_bank_id,
                "timestamp": datetime.utcnow().isoformat() + "Z",
                "nonce": str(uuid.uuid4())
            })
            
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.post(f"{dest_bank.address}/transfers/receive", json={"jwt": jwt_token})
                
                if resp.status_code == 200:
                    db.update_transfer_status(transfer["transferId"], "completed")
                    db.update_transfer_retry(transfer["transferId"], retry_count + 1)
                    logger.info(f"Pending transfer {transfer['transferId']} completed on retry")
                else:
                    db.update_transfer_retry(transfer["transferId"], retry_count + 1)
                    logger.warning(f"Retry {retry_count + 1} failed for {transfer['transferId']}")
                    
        except Exception as e:
            logger.error(f"Retry failed for {transfer['transferId']}: {e}")
            db.update_transfer_retry(transfer["transferId"], retry_count + 1)


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
        
        raw_bank_id = settings.BANK_ID or ""
        bank_id = "" if raw_bank_id.upper() in ("", "NONE", "NULL", "UNSET") else raw_bank_id
        if not bank_id or len(bank_id) < 3:
            bank_id = "MIN001"
            logger.warning("Using hardcoded bank_id MIN001 from central bank registration")
        logger.info(f"DEBUG settings.BANK_ID={repr(settings.BANK_ID)}, bank_id={repr(bank_id)}")
        
        if bank_id and len(bank_id) >= 3:
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
        scheduler.add_job(process_pending_transfers, 'interval', minutes=1, id='pending_transfers')
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


app = FastAPI(
    title="Branch Bank API",
    version="1.0.0",
    lifespan=lifespan,
    docs_url="/docs",
    openapi_url="/openapi.json"
)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])

app.security = HTTPBearer(auto_error=False)


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
        "bank_address": settings.BANK_ADDRESS,
        "bank_cache_age": (datetime.utcnow() - _bank_cache["last_synced"]).total_seconds() if _bank_cache["last_synced"] else None
    }


@app.post("/auth/token", response_model=dict)
async def login_for_token(req: UserRegistrationRequest):
    user = db.get_user_by_email(req.email)
    if not user:
        raise HTTPException(status_code=401, detail={"code": "INVALID_CREDENTIALS", "message": "Invalid email"})
    
    token_data = create_user_token(user["id"], user["fullName"])
    return token_data


@app.post("/api/v1/users", status_code=201, response_model=UserRegistrationResponse)
async def register_user(req: UserRegistrationRequest):
    if req.email:
        existing = db.get_user_by_email(req.email)
        if existing:
            raise HTTPException(status_code=409, detail={"code": "DUPLICATE_USER", "message": "User exists"})
    
    user = db.create_user(req.fullName, req.email)
    
    token_data = create_user_token(user["userId"], user["fullName"])
    user["token"] = token_data
    
    return user


@app.get("/api/v1/users/{user_id}")
async def get_user(user_id: str, auth: dict = Depends(verify_user)):
    if auth["user_id"] != user_id:
        raise HTTPException(status_code=403, detail={"code": "FORBIDDEN", "message": "Cannot access other user's data"})
    
    user = db.get_user(user_id)
    if not user:
        raise HTTPException(status_code=404, detail={"code": "USER_NOT_FOUND", "message": "Not found"})
    return user


@app.post("/api/v1/users/{user_id}/accounts", status_code=201)
async def create_account(user_id: str, req: AccountCreationRequest, auth: dict = Depends(verify_user)):
    if auth["user_id"] != user_id:
        raise HTTPException(status_code=403, detail={"code": "FORBIDDEN", "message": "Cannot create account for other user"})
    
    user = db.get_user(user_id)
    if not user:
        raise HTTPException(status_code=404, detail={"code": "USER_NOT_FOUND", "message": "Not found"})
    
    account = db.create_account(user_id, req.currency.upper(), bank_prefix)
    return account


@app.get("/api/v1/accounts/{account_number}")
async def lookup_account(account_number: str):
    account = db.get_account(account_number.upper())
    if not account:
        raise HTTPException(status_code=404, detail={"code": "ACCOUNT_NOT_FOUND", "message": f"Account with number '{account_number}' not found"})
    user = db.get_user(account["ownerId"])
    return {"accountNumber": account["accountNumber"], "ownerName": user["fullName"] if user else "?", "currency": account["currency"]}


@app.get("/api/v1/users/{user_id}/accounts")
async def list_accounts(user_id: str, auth: dict = Depends(verify_user)):
    if auth["user_id"] != user_id:
        raise HTTPException(status_code=403, detail={"code": "FORBIDDEN", "message": "Cannot access other user's accounts"})
    return db.get_user_accounts(user_id)


def sign_ec(payload: dict) -> str:
    private_key = serialization.load_pem_private_key(key_manager._get_private_key().encode(), password=None, backend=default_backend())
    return jwt.encode(payload, private_key, algorithm="ES256")


@app.post("/api/v1/transfers", status_code=201)
async def transfer(req: TransferRequest, auth: dict = Depends(verify_user)):
    existing = db.get_transfer(req.transferId)
    if existing:
        if existing["status"] == "pending":
            raise HTTPException(status_code=409, detail={"code": "TRANSFER_ALREADY_PENDING", "message": f"Transfer with ID '{req.transferId}' is already pending. Cannot submit duplicate transfer."})
        raise HTTPException(status_code=409, detail={"code": "DUPLICATE_TRANSFER", "message": "A transfer with this ID already exists"})
    
    source = db.get_account(req.sourceAccount.upper())
    if not source:
        raise HTTPException(status_code=404, detail={"code": "ACCOUNT_NOT_FOUND", "message": "Source account not found"})
    
    amount = Decimal(req.amount)
    if source["balance"] < amount:
        raise HTTPException(status_code=422, detail={"code": "INSUFFICIENT_FUNDS", "message": "Insufficient funds in source account"})
    
    dest_prefix = req.destinationAccount[:3].upper()
    is_cross = dest_prefix != bank_prefix
    
    now = datetime.utcnow()
    result = {
        "transferId": req.transferId,
        "status": "completed",
        "sourceAccount": req.sourceAccount.upper(),
        "destinationAccount": req.destinationAccount.upper(),
        "amount": req.amount,
        "timestamp": now.isoformat()
    }
    
    if is_cross:
        try:
            rates_resp = await central_bank.get_exchange_rates()
            rate = Decimal(rates_resp.rates.get(source["currency"], "1"))
            converted = str((amount / rate).quantize(Decimal("0.01")))
            result["convertedAmount"] = converted
            result["exchangeRate"] = str(rate.quantize(Decimal("0.000001")))
            result["rateCapturedAt"] = rates_resp.timestamp.isoformat() if hasattr(rates_resp.timestamp, 'isoformat') else rates_resp.timestamp
            
            jwt_token = sign_ec({
                "transferId": req.transferId,
                "sourceAccount": req.sourceAccount.upper(),
                "destinationAccount": req.destinationAccount.upper(),
                "amount": converted,
                "sourceBankId": bank_id,
                "destinationBankId": dest_prefix,
                "timestamp": now.isoformat() + "Z",
                "nonce": str(uuid.uuid4())
            })
            
            try:
                banks = await get_cached_banks()
                dest_bank_entry = next((b for b in banks if b.bankId == dest_prefix), None)
                
                if dest_bank_entry:
                    async with httpx.AsyncClient(timeout=10) as client:
                        resp = await client.post(f"{dest_bank_entry.address}/transfers/receive", json={"jwt": jwt_token})
                        if resp.status_code != 200:
                            result["status"] = "pending"
                            result["pendingSince"] = now.isoformat()
                            result["nextRetryAt"] = (now + timedelta(minutes=1)).isoformat()
                            result["retryCount"] = 0
                else:
                    result["status"] = "pending"
                    result["pendingSince"] = now.isoformat()
                    result["nextRetryAt"] = (now + timedelta(minutes=1)).isoformat()
                    result["retryCount"] = 0
            except httpx.TimeoutException:
                result["status"] = "pending"
                result["pendingSince"] = now.isoformat()
                result["nextRetryAt"] = (now + timedelta(minutes=1)).isoformat()
                result["retryCount"] = 0
                
        except Exception as e:
            logger.error(f"Cross-bank failed: {e}")
            result["status"] = "pending"
            result["pendingSince"] = now.isoformat()
            result["nextRetryAt"] = (now + timedelta(minutes=1)).isoformat()
            result["retryCount"] = 0
    else:
        dest = db.get_account(req.destinationAccount.upper())
        if not dest:
            raise HTTPException(status_code=404, detail={"code": "ACCOUNT_NOT_FOUND", "message": "Destination account not found"})
        db.update_balance(req.destinationAccount.upper(), dest["balance"] + amount)
    
    db.update_balance(req.sourceAccount.upper(), source["balance"] - amount)
    db.save_transfer(result)
    return result


@app.get("/api/v1/transfers/{transfer_id}")
async def get_transfer(transfer_id: str, auth: dict = Depends(verify_user)):
    t = db.get_transfer(transfer_id)
    if not t:
        raise HTTPException(status_code=404, detail={"code": "TRANSFER_NOT_FOUND", "message": f"Transfer with ID '{transfer_id}' not found"})
    
    if t.get("status") == "failed_timeout":
        raise HTTPException(status_code=423, detail={"code": "TRANSFER_TIMEOUT", "message": "Transfer has timed out and cannot be modified or retried. Status is failed_timeout with refund processed."})
    
    return t


@app.get("/api/v1/transfers")
async def list_transfers(account_number: str = None, status: str = None, auth: dict = Depends(verify_user)):
    return db.get_transfers(account_number, status)


@app.post("/api/v1/transfers/receive")
async def receive_transfer(req: InterBankTransferRequest):
    try:
        payload = jwt.decode(req.jwt, options={"verify_signature": False})
    except:
        raise HTTPException(status_code=401, detail={"code": "BAD_JWT", "message": "Invalid JWT"})
    
    account = db.get_account(payload["destinationAccount"])
    if not account:
        raise HTTPException(status_code=404, detail={"code": "ACCOUNT_NOT_FOUND", "message": "Destination account not found"})
    
    amount = Decimal(payload["amount"])
    db.update_balance(payload["destinationAccount"], account["balance"] + amount)
    
    return {
        "transferId": payload["transferId"],
        "status": "completed",
        "destinationAccount": payload["destinationAccount"],
        "amount": payload["amount"],
        "timestamp": datetime.utcnow().isoformat()
    }


@app.get("/api/v1/central-bank/banks")
async def list_banks():
    if not central_bank:
        raise HTTPException(status_code=503, detail={"code": "UNAVAILABLE", "message": "No central bank"})
    
    try:
        banks = await get_cached_banks()
        return {"banks": [b.model_dump() if hasattr(b, 'model_dump') else b for b in banks], "lastSyncedAt": _bank_cache["last_synced"].isoformat() if _bank_cache["last_synced"] else datetime.utcnow().isoformat()}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to list banks: {e}")
        raise HTTPException(status_code=503, detail={"code": "UNAVAILABLE", "message": str(e)})


@app.get("/api/v1/central-bank/exchange-rates")
async def rates():
    if not central_bank:
        raise HTTPException(status_code=503, detail={"code": "UNAVAILABLE", "message": "No central bank"})
    return await central_bank.get_exchange_rates()


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
