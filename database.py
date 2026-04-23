import sqlite3
import uuid
import os
from datetime import datetime
from decimal import Decimal
from typing import Optional, Dict, List
from contextlib import contextmanager

USE_POSTGRES = os.getenv("DATABASE_URL", "").startswith("postgresql")

if USE_POSTGRES:
    import psycopg2


class Database:
    def __init__(self, db_path: str = None):
        if db_path:
            self.db_path = db_path
        elif os.getenv("RAILWAY_VOLUME_MOUNT_PATH"):
            self.db_path = os.path.join(os.getenv("RAILWAY_VOLUME_MOUNT_PATH"), "bank.db")
        else:
            self.db_path = os.getenv("DATABASE_PATH", "bank.db")
        self._connection = None
        
        if USE_POSTGRES:
            import psycopg2
            self._pg_conn = None
            self._db_url = os.getenv("DATABASE_URL")
    
    def _get_connection(self):
        if USE_POSTGRES:
            if self._pg_conn is None or self._pg_conn.closed:
                import psycopg2
                self._pg_conn = psycopg2.connect(self._db_url)
                self._pg_conn.autocommit = False
            return self._pg_conn
        else:
            if self._connection is None:
                self._connection = sqlite3.connect(self.db_path, check_same_thread=False)
                self._connection.row_factory = sqlite3.Row
            return self._connection
    
    @contextmanager
    def _cursor(self):
        conn = self._get_connection()
        if USE_POSTGRES:
            cursor = conn.cursor()
            try:
                yield cursor
                conn.commit()
            except Exception:
                conn.rollback()
                raise
            finally:
                cursor.close()
        else:
            cursor = conn.cursor()
            try:
                yield cursor
                conn.commit()
            except Exception:
                conn.rollback()
                raise
            finally:
                cursor.close()
    
    def close(self):
        if USE_POSTGRES:
            if self._pg_conn:
                self._pg_conn.close()
        else:
            if self._connection:
                self._connection.close()
                self._connection = None
    
    def init_db(self):
        if USE_POSTGRES:
            with self._cursor() as cursor:
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS users (
                        id TEXT PRIMARY KEY,
                        "fullName" TEXT NOT NULL,
                        email TEXT UNIQUE,
                        "createdAt" TEXT NOT NULL
                    )
                """)
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS accounts (
                        "accountNumber" TEXT PRIMARY KEY,
                        "ownerId" TEXT NOT NULL,
                        currency TEXT NOT NULL,
                        balance REAL DEFAULT 0.0,
                        "createdAt" TEXT NOT NULL
                    )
                """)
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS transfers (
                        "transferId" TEXT PRIMARY KEY,
                        status TEXT NOT NULL,
                        "sourceAccount" TEXT NOT NULL,
                        "destinationAccount" TEXT NOT NULL,
                        amount TEXT NOT NULL,
                        "convertedAmount" TEXT,
                        "exchangeRate" TEXT,
                        "rateCapturedAt" TEXT,
                        timestamp TEXT NOT NULL,
                        "pendingSince" TEXT,
                        "nextRetryAt" TEXT,
                        "retryCount" INTEGER DEFAULT 0,
                        "lastRetryAt" TEXT,
                        "errorMessage" TEXT
                    )
                """)
        else:
            with self._cursor() as cursor:
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS users (
                        id TEXT PRIMARY KEY,
                        fullName TEXT NOT NULL,
                        email TEXT UNIQUE,
                        createdAt TEXT NOT NULL
                    )
                """)
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS accounts (
                        accountNumber TEXT PRIMARY KEY,
                        ownerId TEXT NOT NULL,
                        currency TEXT NOT NULL,
                        balance REAL DEFAULT 0.0,
                        createdAt TEXT NOT NULL
                    )
                """)
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS transfers (
                        transferId TEXT PRIMARY KEY,
                        status TEXT NOT NULL,
                        sourceAccount TEXT NOT NULL,
                        destinationAccount TEXT NOT NULL,
                        amount TEXT NOT NULL,
                        convertedAmount TEXT,
                        exchangeRate TEXT,
                        rateCapturedAt TEXT,
                        timestamp TEXT NOT NULL,
                        pendingSince TEXT,
                        nextRetryAt TEXT,
                        retryCount INTEGER DEFAULT 0,
                        lastRetryAt TEXT,
                        errorMessage TEXT
                    )
                """)
                cursor.execute("CREATE INDEX IF NOT EXISTS idx_accounts_owner ON accounts(ownerId)")
                cursor.execute("CREATE INDEX IF NOT EXISTS idx_transfers_source ON transfers(sourceAccount)")
                cursor.execute("CREATE INDEX IF NOT EXISTS idx_transfers_dest ON transfers(destinationAccount)")
    
    def create_user(self, fullName: str, email: Optional[str] = None) -> dict:
        user_id = f"user-{uuid.uuid4()}"
        now = datetime.utcnow().isoformat()
        
        with self._cursor() as cursor:
            if USE_POSTGRES:
                cursor.execute(
                    "INSERT INTO users (id, \"fullName\", email, \"createdAt\") VALUES (%s, %s, %s, %s)",
                    (user_id, fullName, email, now)
                )
            else:
                cursor.execute(
                    "INSERT INTO users (id, fullName, email, createdAt) VALUES (?, ?, ?, ?)",
                    (user_id, fullName, email, now)
                )
        
        return {
            "userId": user_id,
            "fullName": fullName,
            "email": email,
            "createdAt": now
        }
    
    def get_user(self, user_id: str) -> Optional[dict]:
        if not user_id.startswith("user-"):
            user_id = f"user-{user_id}"
        
        with self._cursor() as cursor:
            if USE_POSTGRES:
                cursor.execute('SELECT * FROM users WHERE id = %s', (user_id,))
                row = cursor.fetchone()
                if row:
                    return {"id": row[0], "fullName": row[1], "email": row[2], "createdAt": row[3]}
            else:
                cursor.execute("SELECT * FROM users WHERE id = ?", (user_id,))
                row = cursor.fetchone()
                if row:
                    return dict(row)
        return None
    
    def get_user_by_email(self, email: str) -> Optional[dict]:
        with self._cursor() as cursor:
            if USE_POSTGRES:
                cursor.execute("SELECT * FROM users WHERE email = %s", (email,))
                row = cursor.fetchone()
                if row:
                    return {"id": row[0], "fullName": row[1], "email": row[2], "createdAt": row[3]}
            else:
                cursor.execute("SELECT * FROM users WHERE email = ?", (email,))
                row = cursor.fetchone()
                if row:
                    return dict(row)
        return None
    
    def create_account(self, user_id: str, currency: str, bank_prefix: str) -> dict:
        if not user_id.startswith("user-"):
            user_id = f"user-{user_id}"
        
        suffix = uuid.uuid4().hex[:5].upper()
        account_number = f"{bank_prefix}{suffix}"
        
        while self.get_account(account_number):
            suffix = uuid.uuid4().hex[:5].upper()
            account_number = f"{bank_prefix}{suffix}"
        
        now = datetime.utcnow().isoformat()
        
        with self._cursor() as cursor:
            if USE_POSTGRES:
                cursor.execute(
                    'INSERT INTO accounts ("accountNumber", "ownerId", currency, balance, "createdAt") VALUES (%s, %s, %s, %s, %s)',
                    (account_number, user_id, currency, 0.0, now)
                )
            else:
                cursor.execute(
                    "INSERT INTO accounts (accountNumber, ownerId, currency, balance, createdAt) VALUES (?, ?, ?, ?, ?)",
                    (account_number, user_id, currency, 0.0, now)
                )
        
        return {
            "accountNumber": account_number,
            "ownerId": user_id,
            "currency": currency,
            "balance": "0.00",
            "createdAt": now
        }
    
    def get_account(self, account_number: str) -> Optional[dict]:
        with self._cursor() as cursor:
            if USE_POSTGRES:
                cursor.execute('SELECT * FROM accounts WHERE "accountNumber" = %s', (account_number.upper(),))
                row = cursor.fetchone()
                if row:
                    return {
                        "accountNumber": row[0],
                        "ownerId": row[1],
                        "currency": row[2],
                        "balance": f"{row[3]:.2f}",
                        "createdAt": row[4]
                    }
            else:
                cursor.execute("SELECT * FROM accounts WHERE accountNumber = ?", (account_number.upper(),))
                row = cursor.fetchone()
                if row:
                    result = dict(row)
                    result["balance"] = f"{result['balance']:.2f}"
                    return result
        return None
    
    def get_user_accounts(self, user_id: str) -> list:
        if not user_id.startswith("user-"):
            user_id = f"user-{user_id}"
        
        with self._cursor() as cursor:
            if USE_POSTGRES:
                cursor.execute('SELECT * FROM accounts WHERE "ownerId" = %s', (user_id,))
                rows = cursor.fetchall()
                return [
                    {
                        "accountNumber": r[0],
                        "ownerId": r[1],
                        "currency": r[2],
                        "balance": f"{r[3]:.2f}",
                        "createdAt": r[4]
                    }
                    for r in rows
                ]
            else:
                cursor.execute("SELECT * FROM accounts WHERE ownerId = ?", (user_id,))
                rows = cursor.fetchall()
                return [
                    {
                        "accountNumber": r["accountNumber"],
                        "ownerId": r["ownerId"],
                        "currency": r["currency"],
                        "balance": f"{r['balance']:.2f}",
                        "createdAt": r["createdAt"]
                    }
                    for r in rows
                ]
    
    def update_balance(self, account_number: str, new_balance: Decimal):
        with self._cursor() as cursor:
            if USE_POSTGRES:
                cursor.execute(
                    'UPDATE accounts SET balance = %s WHERE "accountNumber" = %s',
                    (float(new_balance), account_number.upper())
                )
            else:
                cursor.execute(
                    "UPDATE accounts SET balance = ? WHERE accountNumber = ?",
                    (float(new_balance), account_number.upper())
                )
    
    def get_transfer(self, transfer_id: str) -> Optional[dict]:
        with self._cursor() as cursor:
            if USE_POSTGRES:
                cursor.execute('SELECT * FROM transfers WHERE "transferId" = %s', (transfer_id,))
                row = cursor.fetchone()
                if row:
                    return {
                        "transferId": row[0], "status": row[1], "sourceAccount": row[2],
                        "destinationAccount": row[3], "amount": row[4], "convertedAmount": row[5],
                        "exchangeRate": row[6], "rateCapturedAt": row[7], "timestamp": row[8],
                        "pendingSince": row[9], "nextRetryAt": row[10], "retryCount": row[11],
                        "lastRetryAt": row[12], "errorMessage": row[13]
                    }
            else:
                cursor.execute("SELECT * FROM transfers WHERE transferId = ?", (transfer_id,))
                row = cursor.fetchone()
                if row:
                    return dict(row)
        return None
    
    def get_pending_transfers(self) -> list:
        with self._cursor() as cursor:
            if USE_POSTGRES:
                cursor.execute("SELECT * FROM transfers WHERE status = 'pending' ORDER BY \"pendingSince\" ASC")
                rows = cursor.fetchall()
                return [
                    {"transferId": r[0], "status": r[1], "sourceAccount": r[2], "destinationAccount": r[3],
                     "amount": r[4], "convertedAmount": r[5], "pendingSince": r[9], "retryCount": r[11]}
                    for r in rows
                ]
            else:
                cursor.execute("SELECT * FROM transfers WHERE status = 'pending' ORDER BY pendingSince ASC")
                rows = cursor.fetchall()
                return [dict(r) for r in rows]
    
    def update_transfer_status(self, transfer_id: str, status: str, error_message: Optional[str] = None):
        with self._cursor() as cursor:
            if error_message:
                if USE_POSTGRES:
                    cursor.execute(
                        'UPDATE transfers SET status = %s, "errorMessage" = %s WHERE "transferId" = %s',
                        (status, error_message, transfer_id)
                    )
                else:
                    cursor.execute(
                        "UPDATE transfers SET status = ?, errorMessage = ? WHERE transferId = ?",
                        (status, error_message, transfer_id)
                    )
            else:
                if USE_POSTGRES:
                    cursor.execute(
                        'UPDATE transfers SET status = %s WHERE "transferId" = %s',
                        (status, transfer_id)
                    )
                else:
                    cursor.execute(
                        "UPDATE transfers SET status = ? WHERE transferId = ?",
                        (status, transfer_id)
                    )
    
    def update_transfer_retry(self, transfer_id: str, retry_count: int):
        from datetime import timedelta
        delays = [60, 120, 240, 480, 960, 1920, 3600]
        delay = delays[min(retry_count, len(delays) - 1)]
        next_retry = datetime.utcnow() + timedelta(seconds=delay)
        
        with self._cursor() as cursor:
            if USE_POSTGRES:
                cursor.execute(
                    'UPDATE transfers SET "retryCount" = %s, "nextRetryAt" = %s, "lastRetryAt" = %s WHERE "transferId" = %s',
                    (retry_count, next_retry.isoformat(), datetime.utcnow().isoformat(), transfer_id)
                )
            else:
                cursor.execute(
                    "UPDATE transfers SET retryCount = ?, nextRetryAt = ?, lastRetryAt = ? WHERE transferId = ?",
                    (retry_count, next_retry.isoformat(), datetime.utcnow().isoformat(), transfer_id)
                )
    
    def save_transfer(self, transfer_data: dict):
        if USE_POSTGRES:
            if self._pg_conn is None:
                self._pg_conn = psycopg2.connect(self._db_url)
                self._pg_conn.autocommit = True
            cursor = self._pg_conn.cursor()
            try:
                cursor.execute("""
                    INSERT INTO transfers 
                    ("transferId", status, "sourceAccount", "destinationAccount", amount, timestamp,
                     "convertedAmount", "exchangeRate", "rateCapturedAt", "pendingSince", "nextRetryAt", "retryCount", "lastRetryAt", "errorMessage")
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT ("transferId") DO UPDATE SET status = EXCLUDED.status
                """, (
                    transfer_data.get("transferId"),
                    transfer_data.get("status"),
                    transfer_data.get("sourceAccount", "").upper(),
                    transfer_data.get("destinationAccount", "").upper(),
                    transfer_data.get("amount"),
                    transfer_data.get("timestamp"),
                    transfer_data.get("convertedAmount"),
                    transfer_data.get("exchangeRate"),
                    transfer_data.get("rateCapturedAt"),
                    transfer_data.get("pendingSince"),
                    transfer_data.get("nextRetryAt"),
                    transfer_data.get("retryCount", 0),
                    transfer_data.get("lastRetryAt"),
                    transfer_data.get("errorMessage")
                ))
            except Exception as e:
                print(f"Transfer save error: {e}")
                try:
                    cursor.execute("""
                        UPDATE transfers SET status = %s WHERE "transferId" = %s
                    """, (transfer_data.get("status"), transfer_data.get("transferId")))
                except:
                    pass
            finally:
                cursor.close()
        else:
            with self._cursor() as cursor:
                cursor.execute("""
                    INSERT OR REPLACE INTO transfers 
                    (transferId, status, sourceAccount, destinationAccount, amount, timestamp,
                     convertedAmount, exchangeRate, rateCapturedAt, pendingSince, nextRetryAt, retryCount, errorMessage)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    transfer_data.get("transferId"),
                    transfer_data.get("status"),
                    transfer_data.get("sourceAccount", "").upper(),
                    transfer_data.get("destinationAccount", "").upper(),
                    transfer_data.get("amount"),
                    transfer_data.get("timestamp"),
                    transfer_data.get("convertedAmount"),
                    transfer_data.get("exchangeRate"),
                    transfer_data.get("rateCapturedAt"),
                    transfer_data.get("pendingSince"),
                    transfer_data.get("nextRetryAt"),
                    transfer_data.get("retryCount", 0),
                    transfer_data.get("errorMessage")
                ))
    
    def get_transfers(self, account_number: Optional[str] = None, status: Optional[str] = None) -> list:
        query = "SELECT * FROM transfers WHERE 1=1"
        params = []
        
        if account_number:
            query += " AND (sourceAccount = %s OR destinationAccount = %s)" if USE_POSTGRES else " AND (sourceAccount = ? OR destinationAccount = ?)"
            params.extend([account_number.upper(), account_number.upper()])
        
        if status:
            query += " AND status = %s" if USE_POSTGRES else " AND status = ?"
            params.append(status)
        
        query += " ORDER BY timestamp DESC"
        
        with self._cursor() as cursor:
            if USE_POSTGRES:
                cursor.execute(query, params)
            else:
                cursor.execute(query, params)
            rows = cursor.fetchall()
            
            if USE_POSTGRES:
                return [
                    {"transferId": r[0], "status": r[1], "sourceAccount": r[2], "destinationAccount": r[3],
                     "amount": r[4], "convertedAmount": r[5], "exchangeRate": r[6], "rateCapturedAt": r[7],
                     "timestamp": r[8], "pendingSince": r[9], "nextRetryAt": r[10], "retryCount": r[11],
                     "lastRetryAt": r[12], "errorMessage": r[13]}
                    for r in rows
                ]
            return [dict(r) for r in rows]
    
    def execute_atomic_transfer(self, source_acc: str, dest_acc: str, amount: Decimal, transfer_id: str) -> bool:
        now = datetime.utcnow().isoformat()
        amount_float = float(amount)
        
        if USE_POSTGRES:
            conn = self._pg_conn
            if conn is None or conn.closed:
                conn = psycopg2.connect(self._db_url)
                self._pg_conn = conn
            
            cursor = conn.cursor()
            try:
                cursor.execute("BEGIN")
                
                cursor.execute(
                    'UPDATE accounts SET balance = balance - %s WHERE "accountNumber" = %s AND balance >= %s',
                    (amount_float, source_acc.upper(), amount_float)
                )
                
                if cursor.rowcount == 0:
                    cursor.execute("ROLLBACK")
                    raise ValueError("INSUFFICIENT_FUNDS")
                
                cursor.execute(
                    'UPDATE accounts SET balance = balance + %s WHERE "accountNumber" = %s',
                    (amount_float, dest_acc.upper())
                )
                
                cursor.execute(
                    'INSERT INTO transfers ("transferId", status, "sourceAccount", "destinationAccount", amount, timestamp) VALUES (%s, %s, %s, %s, %s, %s)',
                    (transfer_id, 'completed', source_acc.upper(), dest_acc.upper(), str(amount), now)
                )
                
                conn.commit()
                return True
                
            except Exception as e:
                conn.rollback()
                raise e
            finally:
                cursor.close()
        else:
            conn = self._connection
            cursor = conn.cursor()
            try:
                cursor.execute(
                    "UPDATE accounts SET balance = balance - ? WHERE accountNumber = ? AND balance >= ?",
                    (amount_float, source_acc.upper(), amount_float)
                )
                
                if cursor.rowcount == 0:
                    raise ValueError("INSUFFICIENT_FUNDS")
                
                cursor.execute(
                    "UPDATE accounts SET balance = balance + ? WHERE accountNumber = ?",
                    (amount_float, dest_acc.upper())
                )
                
                cursor.execute(
                    "INSERT INTO transfers (transferId, status, sourceAccount, destinationAccount, amount, timestamp) VALUES (?, ?, ?, ?, ?, ?)",
                    (transfer_id, 'completed', source_acc.upper(), dest_acc.upper(), str(amount), now)
                )
                
                conn.commit()
                return True
                
            except Exception as e:
                conn.rollback()
                raise e
            finally:
                cursor.close()
