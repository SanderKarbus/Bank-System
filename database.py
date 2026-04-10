import sqlite3
import uuid
from datetime import datetime
from decimal import Decimal
from typing import Optional, Dict, List
from contextlib import contextmanager


class Database:
    def __init__(self, db_path: str = "bank.db"):
        self.db_path = db_path
        self._connection = None
    
    def _get_connection(self) -> sqlite3.Connection:
        if self._connection is None:
            self._connection = sqlite3.connect(self.db_path, check_same_thread=False)
            self._connection.row_factory = sqlite3.Row
        return self._connection
    
    @contextmanager
    def _cursor(self):
        conn = self._get_connection()
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
        if self._connection:
            self._connection.close()
            self._connection = None
    
    def init_db(self):
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
                    createdAt TEXT NOT NULL,
                    FOREIGN KEY (ownerId) REFERENCES users(id)
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
                    errorMessage TEXT
                )
            """)
            
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS bank_info (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    bankId TEXT,
                    bankPrefix TEXT,
                    bankAddress TEXT
                )
            """)
            
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_accounts_owner ON accounts(ownerId)
            """)
            
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_transfers_source ON transfers(sourceAccount)
            """)
            
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_transfers_dest ON transfers(destinationAccount)
            """)
    
    def create_user(self, fullName: str, email: Optional[str] = None) -> dict:
        user_id = f"user-{uuid.uuid4()}"
        now = datetime.utcnow().isoformat()
        
        with self._cursor() as cursor:
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
            cursor.execute("SELECT * FROM users WHERE id = ?", (user_id,))
            row = cursor.fetchone()
        
        if row:
            return dict(row)
        return None
    
    def get_all_user_ids(self) -> list:
        with self._cursor() as cursor:
            cursor.execute("SELECT id FROM users")
            rows = cursor.fetchall()
        return [r["id"] for r in rows]
    
    def get_user_by_email(self, email: str) -> Optional[dict]:
        with self._cursor() as cursor:
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
    
    def update_account_balance(self, account_number: str, new_balance: Decimal):
        with self._cursor() as cursor:
            cursor.execute(
                "UPDATE accounts SET balance = ? WHERE accountNumber = ?",
                (float(new_balance), account_number.upper())
            )
    
    def create_transfer(self, transferId: str, status: str, sourceAccount: str,
                       destinationAccount: str, amount: str, timestamp: datetime,
                       convertedAmount: Optional[str] = None,
                       exchangeRate: Optional[str] = None,
                       rateCapturedAt: Optional[datetime] = None,
                       pendingSince: Optional[datetime] = None,
                       nextRetryAt: Optional[datetime] = None,
                       retryCount: int = 0,
                       errorMessage: Optional[str] = None) -> dict:
        
        with self._cursor() as cursor:
            cursor.execute("""
                INSERT OR REPLACE INTO transfers 
                (transferId, status, sourceAccount, destinationAccount, amount, timestamp,
                 convertedAmount, exchangeRate, rateCapturedAt, pendingSince, nextRetryAt, retryCount, errorMessage)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                transferId, status, sourceAccount.upper(), destinationAccount.upper(),
                amount, timestamp.isoformat() if isinstance(timestamp, datetime) else timestamp,
                convertedAmount, exchangeRate,
                rateCapturedAt.isoformat() if rateCapturedAt and isinstance(rateCapturedAt, datetime) else rateCapturedAt,
                pendingSince.isoformat() if pendingSince and isinstance(pendingSince, datetime) else pendingSince,
                nextRetryAt.isoformat() if nextRetryAt and isinstance(nextRetryAt, datetime) else nextRetryAt,
                retryCount, errorMessage
            ))
        
        return self.get_transfer(transferId)
    
    def get_transfer(self, transfer_id: str) -> Optional[dict]:
        with self._cursor() as cursor:
            cursor.execute("SELECT * FROM transfers WHERE transferId = ?", (transfer_id,))
            row = cursor.fetchone()
        
        if row:
            return dict(row)
        return None
    
    def get_transfers(self, account_number: Optional[str] = None,
                      status: Optional[str] = None,
                      owner_id: Optional[str] = None) -> list:
        query = "SELECT DISTINCT t.* FROM transfers t"
        params = []
        conditions = []
        
        if owner_id:
            query += " JOIN accounts a ON t.sourceAccount = a.accountNumber"
            conditions.append("a.ownerId = ?")
            params.append(owner_id)
        
        if account_number:
            conditions.append("(t.sourceAccount = ? OR t.destinationAccount = ?)")
            params.extend([account_number.upper(), account_number.upper()])
        
        if status:
            conditions.append("t.status = ?")
            params.append(status)
        
        if conditions:
            query += " WHERE " + " AND ".join(conditions)
        
        query += " ORDER BY t.timestamp DESC"
        
        with self._cursor() as cursor:
            cursor.execute(query, params)
            rows = cursor.fetchall()
        
        return [dict(r) for r in rows]
    
    def update_bank_info(self, bank_id: str, bank_prefix: str, bank_address: str):
        with self._cursor() as cursor:
            cursor.execute("""
                INSERT OR REPLACE INTO bank_info (id, bankId, bankPrefix, bankAddress)
                VALUES (1, ?, ?, ?)
            """, (bank_id, bank_prefix, bank_address))
    
    def get_bank_info(self) -> Optional[dict]:
        with self._cursor() as cursor:
            cursor.execute("SELECT * FROM bank_info WHERE id = 1")
            row = cursor.fetchone()
        
        if row:
            return dict(row)
        return None
