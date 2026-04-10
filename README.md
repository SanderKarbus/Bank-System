# 🏦 Minu Pank - Branch Bank API

Distributed Banking System branch bank that communicates with the central bank.

## Live API

- **URL:** https://bank-system-production-2902.up.railway.app
- **Swagger UI:** https://bank-system-production-2902.up.railway.app/docs
- **Health:** https://bank-system-production-2902.up.railway.app/health

## Technologies

- **Python 3.11** + **FastAPI**
- **SQLite** database with proper transactions
- **JWT (HS256)** user authentication
- **JWT (ES256)** cross-bank authentication
- **Railway** hosting
- **APScheduler** for background tasks

## Microservices Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                        Branch Bank API                          │
├─────────────────────────────────────────────────────────────────┤
│  User Service     │ Account Service  │ Transfer Service         │
│  - Registration  │ - Creation       │ - Same-bank transfers    │
│  - Token auth    │ - Lookup         │ - Cross-bank transfers   │
│                  │                  │ - Pending/retry logic    │
├─────────────────────────────────────────────────────────────────┤
│                      Central Bank Client                        │
│  - Bank registration │ Heartbeat │ Bank directory (cached)    │
│  - Exchange rates    │ Bank lookup                            │
├─────────────────────────────────────────────────────────────────┤
│                      Database Layer                             │
│  SQLite with transactions for data integrity                    │
└─────────────────────────────────────────────────────────────────┘
```

### Service Components

| Service | Responsibility |
|---------|---------------|
| `main.py` | FastAPI app, routing, business logic |
| `database.py` | SQLite operations, data persistence |
| `central_bank_client.py` | Central Bank API integration |
| `auth.py` | JWT token generation and verification |
| `key_manager.py` | EC key pair for cross-bank JWT signing |

## Database Schema

```sql
-- Users table
CREATE TABLE users (
    id TEXT PRIMARY KEY,
    fullName TEXT NOT NULL,
    email TEXT UNIQUE,
    createdAt TEXT NOT NULL
);

-- Accounts table
CREATE TABLE accounts (
    accountNumber TEXT PRIMARY KEY,
    ownerId TEXT NOT NULL,
    currency TEXT NOT NULL,
    balance REAL DEFAULT 0.0,
    createdAt TEXT NOT NULL,
    FOREIGN KEY (ownerId) REFERENCES users(id)
);

-- Transfers table
CREATE TABLE transfers (
    transferId TEXT PRIMARY KEY,
    status TEXT NOT NULL,           -- completed, pending, failed, failed_timeout
    sourceAccount TEXT NOT NULL,
    destinationAccount TEXT NOT NULL,
    amount TEXT NOT NULL,
    convertedAmount TEXT,            -- for cross-currency
    exchangeRate TEXT,               -- 6 decimal places
    rateCapturedAt TEXT,
    timestamp TEXT NOT NULL,
    pendingSince TEXT,               -- when transfer became pending
    nextRetryAt TEXT,               -- scheduled retry time
    retryCount INTEGER DEFAULT 0,
    lastRetryAt TEXT,
    errorMessage TEXT
);

-- Indexes for performance
CREATE INDEX idx_accounts_owner ON accounts(ownerId);
CREATE INDEX idx_transfers_source ON transfers(sourceAccount);
CREATE INDEX idx_transfers_dest ON transfers(destinationAccount);
```

## API Endpoints

### Authentication

| Method | Endpoint | Auth | Description |
|--------|----------|------|-------------|
| POST | `/api/v1/users` | None | Register user (returns token) |
| POST | `/auth/token` | None | Login with email |

### Users

| Method | Endpoint | Auth | Description |
|--------|----------|------|-------------|
| GET | `/api/v1/users/{userId}` | Bearer | Get user info |

### Accounts

| Method | Endpoint | Auth | Description |
|--------|----------|------|-------------|
| POST | `/api/v1/users/{userId}/accounts` | Bearer | Create account |
| GET | `/api/v1/accounts/{accountNumber}` | None | Lookup account |
| GET | `/api/v1/users/{userId}/accounts` | Bearer | List user accounts |

### Transfers

| Method | Endpoint | Auth | Description |
|--------|----------|------|-------------|
| POST | `/api/v1/transfers` | Bearer | Initiate transfer |
| GET | `/api/v1/transfers/{transferId}` | Bearer | Get transfer status |
| POST | `/api/v1/transfers/receive` | None | Receive cross-bank transfer |

### Central Bank

| Method | Endpoint | Auth | Description |
|--------|----------|------|-------------|
| GET | `/api/v1/central-bank/banks` | None | List all banks (cached) |
| GET | `/api/v1/central-bank/exchange-rates` | None | Get exchange rates |

### Health

| Method | Endpoint | Auth | Description |
|--------|----------|------|-------------|
| GET | `/health` | None | Health check |
| GET | `/debug` | None | Debug info |

## Authentication

### Bearer Token Flow

1. **Register User** - `POST /api/v1/users`
   - Returns user data + access token
   - Token expires in 24 hours

2. **Use Token** - Add to all protected requests:
   ```
   Authorization: Bearer <accessToken>
   ```

### Cross-Bank JWT (ES256)

Cross-bank transfers use JWT signed with bank's EC private key:
- Algorithm: ES256
- Contains: transferId, sourceAccount, destinationAccount, amount, bank IDs, timestamp, nonce

## Transfer Processing

### Same-Bank Transfers
1. Validate source account has sufficient funds
2. Deduct from source
3. Add to destination
4. Return completed status

### Cross-Bank Transfers
1. Validate source account
2. Get exchange rate from central bank
3. Convert amount
4. Sign JWT with bank's private key
5. Send to destination bank
6. If destination unavailable → status = "pending"

### Pending Transfer Retry
Exponential backoff: 1m → 2m → 4m → 8m → 16m → 32m → 1h

### Timeout Handling
- Pending transfers expire after 4 hours
- Status changes to "failed_timeout"
- Funds automatically refunded to source account

## Installation

```bash
# Clone repo
git clone https://github.com/SanderKarbus/Bank-System.git
cd Bank-System

# Install dependencies
pip install -r requirements.txt

# Run
python main.py
```

## Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| BANK_NAME | Bank name | "My Branch Bank" |
| BANK_ADDRESS | Bank URL (https:// automatically added) | "http://localhost:8000" |
| BANK_ID | Central bank assigned ID | Auto-register |
| CENTRAL_BANK_URL | Central bank API URL | "https://test.diarainfra.com/central-bank/api/v1" |
| HEARTBEAT_INTERVAL_MINUTES | Heartbeat interval | 25 |

## Examples

### 1. Register User
```bash
curl -X POST "https://bank-system-production-2902.up.railway.app/api/v1/users" \
  -H "Content-Type: application/json" \
  -d '{"fullName": "John Doe", "email": "john@example.com"}'
```

Response:
```json
{
  "userId": "user-550e8400-e29b-41d4-a716-446655440000",
  "fullName": "John Doe",
  "email": "john@example.com",
  "createdAt": "2026-04-10T12:00:00Z"
}
```

### 2. Create Account
```bash
curl -X POST "https://bank-system-production-2902.up.railway.app/api/v1/users/user-550e8400-e29b-41d4-a716-446655440000/accounts" \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{"currency": "EUR"}'
```

Response:
```json
{
  "accountNumber": "MIN12345",
  "ownerId": "user-550e8400-e29b-41d4-a716-446655440000",
  "currency": "EUR",
  "balance": "0.00",
  "createdAt": "2026-04-10T12:00:05Z"
}
```

### 3. Make Transfer
```bash
curl -X POST "https://bank-system-production-2902.up.railway.app/api/v1/transfers" \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{
    "transferId": "550e8400-e29b-41d4-a716-446655440000",
    "sourceAccount": "MIN12345",
    "destinationAccount": "MIN54321",
    "amount": "100.00"
  }'
```

### 4. Lookup Account
```bash
curl "https://bank-system-production-2902.up.railway.app/api/v1/accounts/MIN12345"
```

### 5. Get Exchange Rates
```bash
curl "https://bank-system-production-2902.up.railway.app/api/v1/central-bank/exchange-rates"
```

## Test Results

### ✅ Unit Tests
- User registration and authentication
- Account creation with unique numbers
- Same-bank transfers with balance updates
- Cross-bank transfer JWT signing
- Idempotency (duplicate transferId rejection)

### ✅ Integration Tests
- Central bank registration
- Heartbeat (30-min interval)
- Bank directory caching
- Exchange rate fetching

### ✅ Edge Cases
- Duplicate user registration → 409
- Invalid token → 401
- Insufficient funds → 422
- Account not found → 404
- Duplicate transferId → 409
- Transfer timeout → 423

## Error Codes

| Code | HTTP | Description |
|------|------|-------------|
| UNAUTHORIZED | 401 | Missing or invalid token |
| FORBIDDEN | 403 | Cannot access other user's data |
| USER_NOT_FOUND | 404 | User does not exist |
| ACCOUNT_NOT_FOUND | 404 | Account does not exist |
| TRANSFER_NOT_FOUND | 404 | Transfer does not exist |
| DUPLICATE_USER | 409 | Email already registered |
| DUPLICATE_TRANSFER | 409 | Transfer ID already used |
| TRANSFER_ALREADY_PENDING | 409 | Cannot submit duplicate pending transfer |
| INSUFFICIENT_FUNDS | 422 | Not enough money |
| TRANSFER_TIMEOUT | 423 | Transfer timed out, cannot modify |
| CENTRAL_BANK_UNAVAILABLE | 503 | Central bank unreachable (using cache) |

## GitHub

https://github.com/SanderKarbus/Bank-System
