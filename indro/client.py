import os
import time
import uuid
import logging
import asyncio
import aiohttp
from typing import Optional, Tuple
from pathlib import Path
from tqdm.asyncio import tqdm

# Cryptography
from cryptography.hazmat.primitives.asymmetric import x25519
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

# Configure internal SDK logging
logging.basicConfig(level=logging.INFO, format='%(message)s')
logger = logging.getLogger("IndroVault")

# --- Custom Exceptions ---
class IndroError(Exception): """Base exception for the Indro SDK."""
class IndroAuthError(IndroError): """Raised when JWT or Handshake fails."""
class IndroNetworkError(IndroError): """Raised when the data plane disconnects."""

class IndroVault:
    """
    The Official Enterprise SDK for the Indro-Veda Vanguard Delivery Network.
    Provides mathematically secure, async-native AI model streaming.
    """
    
    def __init__(self, auth_token: str, gateway_url: str = "https://abhinav337463-indro-veda-vanguard.hf.space"):
        if not auth_token:
            raise ValueError("An authentication token is required to initialize the IndroVault.")
            
        self.auth_token = auth_token
        self.gateway_url = gateway_url.rstrip("/")
        self.headers = {
            "x-api-key": "indro_sdk_client_v2", 
            "Authorization": f"Bearer {self.auth_token}"
        }

    def _generate_keypair(self) -> Tuple[x25519.X25519PrivateKey, str]:
        """Generates the local X25519 keypair for the ECDH exchange."""
        priv_key = x25519.X25519PrivateKey.generate()
        pub_hex = priv_key.public_key().public_bytes(
            serialization.Encoding.Raw, serialization.PublicFormat.Raw
        ).hex()
        return priv_key, pub_hex

    def _derive_aes_gcm(self, client_priv: x25519.X25519PrivateKey, server_pub_hex: str) -> AESGCM:
        """Derives the shared AES-GCM secret without transmitting it."""
        server_pub_bytes = bytes.fromhex(server_pub_hex)
        server_pub_key = x25519.X25519PublicKey.from_public_bytes(server_pub_bytes)
        
        shared_secret = client_priv.exchange(server_pub_key)
        aes_key = HKDF(
            algorithm=hashes.SHA256(), 
            length=32, 
            salt=None, 
            info=b'indro-stream'
        ).derive(shared_secret)
        
        return AESGCM(aes_key)

    async def download_model_async(self, model_id: str, output_dir: str = "./models") -> Path:
        """
        Asynchronously streams and decrypts a Vanguard AI model to disk.
        """
        Path(output_dir).mkdir(parents=True, exist_ok=True)
        output_path = Path(output_dir) / f"{model_id}.safetensors"
        
        logger.info(f"🛡️ [Indro-Veda] Negotiating Secure Tunnel for: {model_id}")

        client_priv, client_pub_hex = self._generate_keypair()
        
        body = {
            "client_public_key_hex": client_pub_hex,
            "timestamp": int(time.time()),
            "nonce": uuid.uuid4().hex,
            "device_fingerprint": "indro_python_sdk_v2",
            "attestation_signature": "sdk_verified_secure"
        }

        # 1. The Handshake (Using aiohttp for async speed)
        async with aiohttp.ClientSession(headers=self.headers) as session:
            async with session.post(f"{self.gateway_url}/api/v6/handshake/{model_id}", json=body) as res:
                if res.status != 200:
                    error_msg = await res.text()
                    raise IndroAuthError(f"Handshake Rejected [{res.status}]: {error_msg}")
                
                data = await res.json()
                session_id = data.get('session_id')
                server_pub_hex = data.get('server_public_key_hex')
                total_size = data.get('size_bytes', 0) # Fallback to 0 if not provided
                
        stream_url = f"{self.gateway_url}/api/v6/stream/{session_id}"
        
        # 2. Key Derivation
        aesgcm = self._derive_aes_gcm(client_priv, server_pub_hex)

        # 3. The Data Plane (Streaming & Decryption)
        logger.info("🌊 Tunnel Established. Initiating zero-trust decryption stream...")
        
        async with aiohttp.ClientSession() as session:
            async with session.get(stream_url) as stream_res:
                if stream_res.status not in (200, 206):
                    raise IndroNetworkError(f"Stream Error [{stream_res.status}]")

                # Setup the beautiful progress bar
                progress_bar = tqdm(
                    total=total_size, 
                    unit='iB', 
                    unit_scale=True, 
                    desc=f"📦 {model_id}", 
                    bar_format="{l_bar}{bar:30}{r_bar}"
                )
                
                current_offset = 0
                
                with open(output_path, "wb") as f:
                    while True:
                        # Read 4-byte header
                        length_bytes = await stream_res.content.read(4)
                        if not length_bytes:
                            break 
                            
                        payload_len = int.from_bytes(length_bytes, 'big')
                        
                        # Read encrypted payload
                        encrypted_chunk = await stream_res.content.read(payload_len)
                        if not encrypted_chunk:
                            break
                            
                        # Reconstruct nonce & decrypt
                        chunk_index = current_offset // (1024 * 1024 * 2)
                        nonce = chunk_index.to_bytes(12, byteorder='big')
                        
                        try:
                            raw_chunk = aesgcm.decrypt(nonce, encrypted_chunk, None)
                        except Exception as e:
                            progress_bar.close()
                            raise IndroCryptoError(f"AES-GCM Decryption failed at chunk {chunk_index}: {str(e)}")

                        f.write(raw_chunk)
                        
                        chunk_size = len(raw_chunk)
                        current_offset += chunk_size
                        progress_bar.update(chunk_size)

                progress_bar.close()
                
        logger.info(f"✅ Mission Accomplished! Model secured at: {output_path.absolute()}")
        return output_path

    def download_model(self, model_id: str, output_dir: str = "./models") -> str:
        """
        Synchronous wrapper for developers who aren't using async/await.
        """
        return str(asyncio.run(self.download_model_async(model_id, output_dir)))
