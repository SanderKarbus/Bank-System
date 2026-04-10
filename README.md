# 🏦 Branch Bank API

Distributed Banking System branch bank that communicates with the central bank.

## Technologies

- **Python 3.11** + **FastAPI**
- **SQLite** database
- **JWT (ES256)** authentication
- **Railway** hosting

## Live API

- **URL:** https://bank-system-production-2902.up.railway.app
- **Swagger UI:** https://bank-system-production-2902.up.railway.app/docs
- **Health:** https://bank-system-production-2902.up.railway.app/health

## API Endpoints

### Users
| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/v1/users` | Register user |
| GET | `/api/v1/users/{id}` | Get user info |

### Accounts
| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/v1/users/{id}/accounts` | Create account |
| GET | `/api/v1/accounts/{number}` | Lookup account |
| GET | `/api/v1/users/{id}/accounts` | List user accounts |

### Transfers
| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/v1/transfers` | Initiate transfer |
| GET | `/api/v1/transfers/{id}` | Get transfer status |
| POST | `/api/v1/transfers/receive` | Receive cross-bank transfer |

### Central Bank
| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/v1/central-bank/banks` | List all banks |
| GET | `/api/v1/central-bank/exchange-rates` | Get exchange rates |

## Authentication

Protected endpoints require `X-User-Id` header with the user's ID.

## Installation

```bash
# Clone repo
git clone https://github.com/SanderKarbus/Bank-System.git
cd Bank-System

# Install dependencies
pip install -r requirements.txt

# Configure environment
cp .env.example .env
# Edit .env as needed

# Run
python main.py
```

## Environment Variables

| Variable | Description | Example |
|----------|-------------|---------|
| BANK_NAME | Bank name | "My Branch Bank" |
| BANK_ADDRESS | Bank URL | "https://mybank.railway.app" |
| BANK_ID | Central bank ID | "MYB001" |
| CENTRAL_BANK_URL | Central bank URL | "https://test.diarainfra.com/central-bank/api/v1" |

## Database Schema

```
users:
  - id (TEXT PRIMARY KEY)
  - fullName (TEXT)
  - email (TEXT UNIQUE)
  - createdAt (TEXT)

accounts:
  - accountNumber (TEXT PRIMARY KEY)
  - ownerId (TEXT)
  - currency (TEXT)
  - balance (REAL)
  - createdAt (TEXT)

transfers:
  - transferId (TEXT PRIMARY KEY)
  - status (TEXT)
  - sourceAccount (TEXT)
  - destinationAccount (TEXT)
  - amount (TEXT)
  - timestamp (TEXT)
```

## Examples

### Register User
```bash
curl -X POST "https://bank-system-production-2902.up.railway.app/api/v1/users" \
  -H "Content-Type: application/json" \
  -d '{"fullName": "John Doe", "email": "john@test.com"}'
```

### Create Account
```bash
curl -X POST "https://bank-system-production-2902.up.railway.app/api/v1/users/{userId}/accounts" \
  -H "X-User-Id: {userId}" \
  -H "Content-Type: application/json" \
  -d '{"currency": "EUR"}'
```

### Make Transfer
```bash
curl -X POST "https://bank-system-production-2902.up.railway.app/api/v1/transfers" \
  -H "X-User-Id: {userId}" \
  -H "Content-Type: application/json" \
  -d '{
    "transferId": "uuid-here",
    "sourceAccount": "MYB12345",
    "destinationAccount": "MYB54321",
    "amount": "100.00"
  }'
```

## GitHub

https://github.com/SanderKarbus/Bank-System
