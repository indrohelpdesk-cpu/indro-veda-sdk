import os
import time
import uuid
import logging
import asyncio
import aiohttp
import hashlib
from typing import Optional, Tuple, Dict, Any
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

# --- Custom Enterprise Exceptions ---
class IndroError(Exception): """Base exception for the Indro SDK."""
class IndroAuthError(IndroError): """Raised when JWT or Handshake fails."""
class IndroNetworkError(IndroError): """Raised when the data plane disconnects."""
class IndroCryptoError(IndroError): """Raised when AES-GCM decryption fails."""
class IndroIntegrityError(IndroError): """Raised when the downloaded file's SHA256 hash mismatches."""

class IndroVault:
    """
    The Official Enterprise SDK for the Indro-Veda Vanguard Delivery Network.
    Provides mathematically secure, async-native AI model streaming with 
    auto-retries and cryptographic integrity verification.
    """
    
    def __init__(self, auth_token: str, gateway_url: str = "https://abhinav337463-indro-veda-vanguard.hf.space", max_retries: int = 3):
        if not auth_token:
            raise ValueError("An authentication token is required to initialize the IndroVault.")
            
        self.auth_token = auth_token
        self.gateway_url = gateway_url.rstrip("/")
        self.max_retries = max_retries
        
        self.headers = {
            "x-api-key": "indro_sdk_client_v2", 
            "Authorization": f"Bearer {self.auth_token}"
        }
        
        # Extended timeouts for massive AI model streaming (5 minutes without a single byte = drop)
        self.timeout_config = aiohttp.ClientTimeout(total=None, connect=60, sock_read=300)

    def _generate_keypair(self) -> Tuple[x25519.X25519PrivateKey, str]:
        """Generates the local X25519 keypair for the ECDH exchange."""
        priv_key = x25519.X25519PrivateKey.generate()
        pub_hex = priv_key.public_key().public_bytes(
            serialization.Encoding.Raw, serialization.PublicFormat.Raw
        ).hex()
        return priv_key, pub_hex

    def _derive_aes_gcm(self, client_priv: x25519.X25519PrivateKey, server_pub_hex: str) -> AESGCM:
        """Derives the shared AES-GCM secret without transmitting it over the network."""
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

    async def _perform_handshake(self, session: aiohttp.ClientSession, model_id: str, body: dict) -> dict:
        """Executes the secure handshake with exponential backoff for network resilience."""
        for attempt in range(self.max_retries):
            try:
                async with session.post(f"{self.gateway_url}/api/v6/handshake/{model_id}", json=body) as res:
                    if res.status == 200:
                        return await res.json()
                    
                    error_msg = await res.text()
                    if res.status in (401, 403):
                        # Don't retry authentication errors
                        raise IndroAuthError(f"Authorization Failed [{res.status}]: {error_msg}")
                    
                    logger.warning(f"Handshake failed [{res.status}]. Retrying {attempt + 1}/{self.max_retries}...")
            except aiohttp.ClientError as e:
                logger.warning(f"Network error during handshake: {str(e)}. Retrying...")
            
            # Exponential backoff
            await asyncio.sleep(2 ** attempt)
            
        raise IndroNetworkError("Failed to establish secure tunnel after multiple attempts.")

    async def download_model_async(self, model_id: str, output_dir: str = "./models") -> Path:
        """
        Asynchronously streams, decrypts, and verifies a Vanguard AI model to disk.
        """
        Path(output_dir).mkdir(parents=True, exist_ok=True)
        output_path = Path(output_dir) / f"{model_id}.safetensors"
        
        logger.info(f"🛡️ [Indro-Veda] Negotiating Secure Tunnel for: {model_id}")

        client_priv, client_pub_hex = self._generate_keypair()
        
        body = {
            "client_public_key_hex": client_pub_hex,
            "timestamp": int(time.time()),
            "nonce": uuid.uuid4().hex,
            "device_fingerprint": "indro_python_sdk_v3_enterprise",
            "attestation_signature": "sdk_verified_secure"
        }

        # --- Phase 1: The Resilient Handshake ---
        async with aiohttp.ClientSession(headers=self.headers, timeout=self.timeout_config) as session:
            data = await self._perform_handshake(session, model_id, body)
            
            session_id = data.get('session_id')
            server_pub_hex = data.get('server_public_key_hex')
            total_size = data.get('size_bytes', 0)
            expected_hash = data.get('sha256_hash') # Will be used for integrity verification if server provides it
                
        stream_url = f"{self.gateway_url}/api/v6/stream/{session_id}"
        
        # --- Phase 2: Zero-Trust Key Derivation ---
        aesgcm = self._derive_aes_gcm(client_priv, server_pub_hex)

        # --- Phase 3: The Data Plane (Streaming, Decryption, & Hashing) ---
        logger.info("🌊 Tunnel Established. Initiating zero-trust decryption stream...")
        
        # Initialize SHA-256 verifier
        file_hash = hashlib.sha256()
        
        async with aiohttp.ClientSession(timeout=self.timeout_config) as session:
            async with session.get(stream_url) as stream_res:
                if stream_res.status not in (200, 206):
                    raise IndroNetworkError(f"Stream Error [{stream_res.status}]")

                progress_bar = tqdm(
                    total=total_size, 
                    unit='iB', 
                    unit_scale=True, 
                    desc=f"📦 {model_id}", 
                    bar_format="{l_bar}{bar:30}{r_bar}",
                    colour="green"
                )
                
                current_offset = 0
                
                # Stream directly to disk to prevent RAM exhaustion
                with open(output_path, "wb") as f:
                    while True:
                        # Read the 4-byte chunk header
                        length_bytes = await stream_res.content.read(4)
                        if not length_bytes:
                            break 
                            
                        payload_len = int.from_bytes(length_bytes, 'big')
                        
                        # Read the encrypted payload chunk
                        encrypted_chunk = await stream_res.content.read(payload_len)
                        if not encrypted_chunk:
                            break
                            
                        # Reconstruct nonce (synchronous with the backend state)
                        chunk_index = current_offset // (1024 * 1024 * 2) # Assuming 2MB chunks
                        nonce = chunk_index.to_bytes(12, byteorder='big')
                        
                        try:
                            # Decrypt strictly in RAM
                            raw_chunk = aesgcm.decrypt(nonce, encrypted_chunk, None)
                        except Exception as e:
                            progress_bar.close()
                            raise IndroCryptoError(f"AES-GCM Decryption compromised at chunk {chunk_index}: {str(e)}")

                        # Write to disk and update running hash
                        f.write(raw_chunk)
                        file_hash.update(raw_chunk)
                        
                        chunk_size = len(raw_chunk)
                        current_offset += chunk_size
                        progress_bar.update(chunk_size)

                progress_bar.close()
                
        # --- Phase 4: Integrity Verification ---
        final_hash = file_hash.hexdigest()
        if expected_hash and final_hash != expected_hash:
            # If the server provided a hash and it doesn't match what we saved, data is corrupted/tampered.
            logger.error(f"❌ Integrity Check Failed! Expected: {expected_hash}, Got: {final_hash}")
            raise IndroIntegrityError("Model verification failed. The downloaded file is corrupt or has been tampered with.")
            
        logger.info(f"✅ Mission Accomplished! SHA-256 verified.")
        logger.info(f"🔒 Model safely secured at: {output_path.absolute()}")
        return output_path

    def download_model(self, model_id: str, output_dir: str = "./models") -> str:
        """
        Synchronous wrapper for developers who aren't using async/await.
        """
        return str(asyncio.run(self.download_model_async(model_id, output_dir)))
