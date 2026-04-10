import os
from cryptography.hazmat.primitives.asymmetric import rsa, ec
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.backends import default_backend
from pathlib import Path


class KeyManager:
    def __init__(self, private_key_path: str = "keys/private_key.pem", 
                 public_key_path: str = "keys/public_key.pem"):
        self.private_key_path = private_key_path
        self.public_key_path = public_key_path
        self._ensure_keys_dir()
        
    def _ensure_keys_dir(self):
        Path(self.private_key_path).parent.mkdir(parents=True, exist_ok=True)
    
    def generate_ec_keys(self, force: bool = False) -> tuple[str, str]:
        ec_private_path = self.private_key_path.replace(".pem", "_ec.pem")
        ec_public_path = self.public_key_path.replace(".pem", "_ec.pem")
        
        if os.path.exists(ec_private_path) and not force:
            return self.load_ec_keys()
        
        private_key = ec.generate_private_key(ec.SECP256R1(), default_backend())
        
        private_pem = private_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption()
        )
        
        public_key = private_key.public_key()
        public_pem = public_key.public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo
        )
        
        with open(ec_private_path, "wb") as f:
            f.write(private_pem)
        
        with open(ec_public_path, "wb") as f:
            f.write(public_pem)
        
        return private_pem.decode(), public_pem.decode()
    
    def load_ec_keys(self) -> tuple[str, str]:
        ec_private_path = self.private_key_path.replace(".pem", "_ec.pem")
        ec_public_path = self.public_key_path.replace(".pem", "_ec.pem")
        
        with open(ec_private_path, "r") as f:
            private_pem = f.read()
        
        with open(ec_public_path, "r") as f:
            public_pem = f.read()
        
        return private_pem, public_pem
    
    def generate_rsa_keys(self, force: bool = False) -> tuple[str, str]:
        if os.path.exists(self.private_key_path) and not force:
            return self.load_keys()
        
        private_key = rsa.generate_private_key(
            public_exponent=65537,
            key_size=2048,
            backend=default_backend()
        )
        
        private_pem = private_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption()
        )
        
        public_key = private_key.public_key()
        public_pem = public_key.public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo
        )
        
        with open(self.private_key_path, "wb") as f:
            f.write(private_pem)
        
        with open(self.public_key_path, "wb") as f:
            f.write(public_pem)
        
        return private_pem.decode(), public_pem.decode()
    
    def load_keys(self) -> tuple[str, str]:
        with open(self.private_key_path, "r") as f:
            private_pem = f.read()
        
        with open(self.public_key_path, "r") as f:
            public_pem = f.read()
        
        return private_pem, public_pem
    
    def get_public_key_pem(self) -> str:
        try:
            return self.load_ec_keys()[1]
        except:
            _, public_pem = self.load_keys()
            return public_pem
    
    def _get_private_key(self) -> str:
        ec_private_path = self.private_key_path.replace(".pem", "_ec.pem")
        if os.path.exists(ec_private_path):
            with open(ec_private_path, "r") as f:
                return f.read()
        return self.load_keys()[0]


key_manager = KeyManager()
