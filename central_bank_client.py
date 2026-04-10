import httpx
from typing import Optional
from datetime import datetime
import logging

from models import (
    BankRegistrationRequest, BankRegistrationResponse,
    BankDirectory, BankDetails, HeartbeatRequest, HeartbeatResponse,
    ExchangeRatesResponse
)

logger = logging.getLogger(__name__)


class CentralBankClient:
    def __init__(self, base_url: str):
        self.base_url = base_url.rstrip("/")
        self.client = httpx.AsyncClient(timeout=30.0)
        self._bank_id: Optional[str] = None
        self._expires_at: Optional[datetime] = None
    
    @property
    def bank_id(self) -> Optional[str]:
        return self._bank_id
    
    @bank_id.setter
    def bank_id(self, value: str):
        self._bank_id = value
    
    @property
    def expires_at(self) -> Optional[datetime]:
        return self._expires_at
    
    @expires_at.setter
    def expires_at(self, value: datetime):
        self._expires_at = value
    
    async def register_bank(self, name: str, address: str, public_key: str) -> BankRegistrationResponse:
        request = BankRegistrationRequest(
            name=name,
            address=address,
            publicKey=public_key
        )
        
        response = await self.client.post(
            f"{self.base_url}/banks",
            json=request.model_dump(mode="json")
        )
        
        if response.status_code == 201:
            data = response.json()
            result = BankRegistrationResponse(**data)
            self._bank_id = result.bankId
            self._expires_at = result.expiresAt
            logger.info(f"Bank registered successfully: {result.bankId}")
            return result
        elif response.status_code == 409:
            error = response.json()
            logger.warning(f"Bank already registered: {error}")
            raise ValueError(f"Bank already registered: {error.get('message')}")
        else:
            error = response.json()
            logger.error(f"Registration failed: {error}")
            raise Exception(f"Registration failed: {error.get('message')}")
    
    async def list_banks(self) -> BankDirectory:
        response = await self.client.get(f"{self.base_url}/banks")
        
        if response.status_code == 200:
            data = response.json()
            return BankDirectory(**data)
        else:
            error = response.json()
            raise Exception(f"Failed to list banks: {error.get('message')}")
    
    async def get_bank(self, bank_id: str) -> BankDetails:
        response = await self.client.get(f"{self.base_url}/banks/{bank_id}")
        
        if response.status_code == 200:
            data = response.json()
            return BankDetails(**data)
        elif response.status_code == 404:
            raise ValueError(f"Bank {bank_id} not found")
        else:
            error = response.json()
            raise Exception(f"Failed to get bank: {error.get('message')}")
    
    async def send_heartbeat(self, bank_id: str) -> HeartbeatResponse:
        request = HeartbeatRequest(timestamp=datetime.utcnow())
        
        response = await self.client.post(
            f"{self.base_url}/banks/{bank_id}/heartbeat",
            json=request.model_dump(mode="json")
        )
        
        if response.status_code == 200:
            data = response.json()
            result = HeartbeatResponse(**data)
            self._expires_at = result.expiresAt
            logger.debug(f"Heartbeat sent for {bank_id}, expires at {result.expiresAt}")
            return result
        elif response.status_code == 404:
            raise ValueError(f"Bank {bank_id} not found")
        elif response.status_code == 410:
            raise ValueError(f"Bank {bank_id} has been removed due to inactivity")
        else:
            error = response.json()
            raise Exception(f"Heartbeat failed: {error.get('message')}")
    
    async def get_exchange_rates(self) -> ExchangeRatesResponse:
        response = await self.client.get(f"{self.base_url}/exchange-rates")
        
        if response.status_code == 200:
            data = response.json()
            return ExchangeRatesResponse(**data)
        else:
            error = response.json()
            raise Exception(f"Failed to get exchange rates: {error.get('message')}")
    
    async def close(self):
        await self.client.aclose()
    
    async def health_check(self) -> bool:
        try:
            response = await self.client.get(f"{self.base_url}/banks")
            return response.status_code in [200, 404, 500, 503]
        except Exception as e:
            logger.error(f"Central bank health check failed: {e}")
            return False
