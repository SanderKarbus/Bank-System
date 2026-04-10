# Pangasüsteem - Branch Bank Implementation

Distributed Banking System'i harupank, mis registreerub keskpangaga ja võimaldab ülekandeid.

## Kiire alustamine

### 1. Paigalda sõltuvused
```bash
pip install -r requirements.txt
```

### 2. Keskkonnaseaded
Kopeeri `.env.example` → `.env` ja muuda vajadusel:
```bash
cp .env.example .env
```

### 3. Käivita server
```bash
python main.py
```

## NB! Avalik URL

Keskpank kontrollib sinu panka enne registreerimist - ta kutsub välja `GET {BANK_ADDRESS}/health`.

**Kohustuslik:** Sul peab olema **avalikult kättesaadav URL**.

### Avaliku URL-i loomiseks

**Option 1: ngrok (kiire)**
```bash
ngrok http 8000
# Kopeeri forward URL ja pane see .env faili BANK_ADDRESS
```

**Option 2: Cloudflare Tunnel (tasuta, püsiv)**
```bash
cloudflared tunnel --url http://localhost:8000
```

## API Endpoint'id

### Keskpanga endpoint'id (vaata teised pangad)
- `GET /central-bank/banks` - Kõik registreeritud pangad
- `GET /central-bank/banks/{bankId}` - Konkreetse panga andmed
- `GET /central-bank/exchange-rates` - Valuutakursid

### Tervis
- `GET /health` - Panga tervise kontroll (kriitiline keskpangale!)

### Kasutajatoimingud
- `POST /api/v1/users` - Kasutaja registreerimine
- `POST /api/v1/users/{userId}/accounts` - Konto loomine
- `GET /api/v1/accounts/{accountNumber}` - Konto otsing

### Ülekanded
- `POST /api/v1/transfers` - Ülekanne
- `GET /api/v1/transfers/{transferId}` - Ülekande staatus
- `POST /api/v1/transfers/receive` - Cross-bank vastuvõtt (tulevikus)

## Heartbeat

Pank saadab automaatselt heartbeat'i keskpanka iga 25 minuti järel, et jääda aktiivseks (30 min timeout keskpangal).

## Struktuur

```
pangasüsteem/
├── main.py                  # FastAPI server
├── central_bank_client.py   # Keskpanga API klient
├── models.py                # Pydantic mudelid
├── config.py                # Seadistused
├── key_manager.py           # RSA võtmete haldus
├── requirements.txt
├── .env.example
└── keys/                    # Võtmed (genereeritakse automaatselt)
    ├── private_key.pem
    └── public_key.pem
```
