import os
import hmac
import base64
import json
import ctypes
import hashlib
import asyncio
import aiohttp
import logging
from pathlib import Path
from typing import Optional, Dict, Tuple

# Enterprise Cryptography
from cryptography.hazmat.primitives.asymmetric import x25519, ec
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

try:
    from tqdm.asyncio import tqdm
except ImportError:
    tqdm = None

try:
    from huggingface_hub import HfApi
    HF_HUB_AVAILABLE = True
except ImportError:
    HF_HUB_AVAILABLE = False

logger = logging.getLogger("indro")

# ==========================================
# CUSTOM EXCEPTIONS
# ==========================================
class IndroSDKError(Exception): pass
class IndroAuthError(IndroSDKError): pass
class IndroNetworkError(IndroSDKError): pass
class IndroIntegrityError(IndroSDKError): pass

# ==========================================
# ASYNC EVENT LOOP HELPER (Audit Fix 2)
# ==========================================
def _run_sync(coro):
    """
    Strictly forces sync execution. If inside an existing async loop, 
    it throws an error instead of silently returning an unresolved Task.
    """
    try:
        asyncio.get_running_loop()
        raise RuntimeError(
            "CRITICAL: You are running inside an async environment (like Jupyter, FastAPI, or Discord.py). "
            "You MUST use the async versions of the SDK methods (e.g., await client.download_model_async(...))."
        )
    except RuntimeError as e:
        if "CRITICAL" in str(e): raise
        return asyncio.run(coro)

async def read_exact(reader: aiohttp.StreamReader, size: int) -> bytes:
    data = bytearray()
    while len(data) < size:
        chunk = await reader.read(size - len(data))
        if not chunk: raise ConnectionError("Unexpected EOF from Edge Node.")
        data.extend(chunk)
    return bytes(data)

# ==========================================
# 1. HARDWARE ATTESTATION ENGINE
# ==========================================
class DeviceAuthenticator:
    def __init__(self):
        self._private_key = ec.generate_private_key(ec.SECP256R1())
        
    def get_public_pem(self) -> str:
        return self._private_key.public_key().public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo
        ).decode('utf-8')

    def sign_challenge(self, challenge_nonce: str) -> str:
        return self._private_key.sign(challenge_nonce.encode('utf-8'), ec.ECDSA(hashes.SHA256())).hex()

# ==========================================
# 2. CRYPTOGRAPHIC KERNEL
# ==========================================
class StreamDecryptor:
    def __init__(self):
        self.client_priv = x25519.X25519PrivateKey.generate()
        self.master_key: bytes = b""
        self.CHUNKS_PER_KEY_GROUP = 256
        self.CHUNK_SIZE = 1024 * 1024 * 2 # 2MB
        self.MAX_FRAME_SIZE = 3 * 1024 * 1024 # 3MB absolute limit per cryptographic frame (Audit Fix 4)

    def get_public_hex(self) -> str:
        return self.client_priv.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw).hex()

    def derive_master_secret(self, server_pub_hex: str):
        server_pub_bytes = bytes.fromhex(server_pub_hex)
        server_pub_key = x25519.X25519PublicKey.from_public_bytes(server_pub_bytes)
        shared_secret = self.client_priv.exchange(server_pub_key)
        self.master_key = HKDF(algorithm=hashes.SHA256(), length=32, salt=None, info=b'indro-stream').derive(shared_secret)
        self._best_effort_shred(shared_secret)

    def derive_rotating_subkey(self, chunk_index: int) -> AESGCM:
        chunk_group = chunk_index // self.CHUNKS_PER_KEY_GROUP
        subkey = HKDF(algorithm=hashes.SHA256(), length=32, salt=None, info=f"vanguard-subkey-{chunk_group}".encode()).derive(self.master_key)
        return AESGCM(subkey)

    def verify_chunk_signature(self, chunk_index: int, session_id: str, provided_sig: bytes):
        expected_sig = hmac.new(self.master_key, f"{session_id}:{chunk_index}".encode(), hashlib.sha256).digest()[:16]
        if not hmac.compare_digest(provided_sig, expected_sig):
            raise IndroIntegrityError(f"Chunk {chunk_index} signature verification failed! Possible MitM attack.")

    def cleanup(self):
        self._best_effort_shred(self.master_key)
        self.master_key = b""

    @staticmethod
    def _best_effort_shred(data: bytes):
        """Python memory limits true secure shredding. This is a best-effort overwrite."""
        try:
            buffer = (ctypes.c_char * len(data)).from_buffer(data)
            ctypes.memset(ctypes.addressof(buffer), 0, len(data))
        except Exception:
            pass

# ==========================================
# 3. INDRO ENTERPRISE SDK CLIENT
# ==========================================
class IndroClient:
    def __init__(self, api_key: str = None, gateway_url: str = "https://abhinav337463-indro-veda-vanguard.hf.space", max_retries: int = 5):
        self.api_key = api_key or os.getenv("INDRO_API_KEY")
        if not self.api_key: raise ValueError("API Key missing. Pass it or set 'INDRO_API_KEY' environment variable.")
            
        self.gateway_url = gateway_url.rstrip("/")
        self.max_retries = max_retries
        self.auth_headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
        self._session = None 

    async def __aenter__(self):
        await self._get_session()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.close()

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            timeout_obj = aiohttp.ClientTimeout(total=None, connect=10, sock_read=60)
            self._session = aiohttp.ClientSession(headers=self.auth_headers, timeout=timeout_obj)
        return self._session

    async def close(self):
        if self._session and not self._session.closed:
            await self._session.close()

    async def _request_with_retry(self, method: str, endpoint: str, **kwargs) -> dict:
        session = await self._get_session()
        url = f"{self.gateway_url}{endpoint}"
        for attempt in range(self.max_retries):
            try:
                async with session.request(method, url, **kwargs) as res:
                    if res.status >= 400:
                        error_text = await res.text()
                        if res.status in (401, 403): raise IndroAuthError(f"Permission Error: {error_text}")
                        if res.status in (429, 500, 502, 503, 504): raise aiohttp.ClientResponseError(res.request_info, res.history, status=res.status, message=error_text)
                        raise IndroSDKError(f"API Error {res.status}: {error_text}")
                    text = await res.text()
                    return json.loads(text) if text else {}
            except (aiohttp.ClientError, asyncio.TimeoutError) as e:
                if attempt == self.max_retries - 1: raise IndroNetworkError(f"Network request failed after {self.max_retries} attempts: {str(e)}")
                await asyncio.sleep(2 ** attempt)

    async def _solve_attestation_challenge(self) -> dict:
        auth = DeviceAuthenticator()
        session = await self._get_session()
        async with session.post(f"{self.gateway_url}/api/v7/auth/challenge", json={"client_public_key_pem": auth.get_public_pem()}) as res:
            if res.status != 200: raise IndroAuthError(f"Attestation Challenge Failed: {await res.text()}")
            challenge_nonce = (await res.json())["challenge"]

        headers = self.auth_headers.copy()
        headers["X-Attestation-Challenge"] = challenge_nonce
        headers["X-Attestation-Signature"] = auth.sign_challenge(challenge_nonce)
        return headers

    def _extract_meta(self, meta: dict, token: str) -> Tuple[str, str]:
        if "session_id" in meta and "org_id" in meta:
            return meta["session_id"], meta["org_id"]
        try:
            payload_b64 = token.split('.')[1]
            payload_b64 += '=' * (-len(payload_b64) % 4)
            claims = json.loads(base64.b64decode(payload_b64).decode('utf-8'))
            return claims["sess"], claims["org"]
        except Exception:
            raise IndroSDKError("Invalid Handshake Meta.")

    # --- FEATURE 1: SECURE DOWNLOAD ---
    async def download_model_async(self, model_id: str, output_dir: str = "./models", show_progress: bool = True) -> Path:
        final_path = Path(output_dir) / f"{model_id}.safetensors"
        part_path = final_path.with_suffix(".safetensors.part") 
        lock_path = final_path.with_suffix(".lock") # Audit Fix 6: Concurrency Locks
        
        part_path.parent.mkdir(parents=True, exist_ok=True)
        
        try:
            lock_fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.close(lock_fd)
        except FileExistsError:
            raise IndroSDKError(f"Another process is currently downloading {model_id}.")

        crypto = StreamDecryptor()
        
        try:
            secure_headers = await self._solve_attestation_challenge()
            session = await self._get_session()
            
            payload = {"nonce": os.urandom(16).hex(), "client_public_key_hex": crypto.get_public_hex()}
            async with session.post(f"{self.gateway_url}/api/v7/handshake/{model_id}", json=payload, headers=secure_headers) as res:
                if res.status != 200: raise IndroAuthError(f"Handshake Rejected: {await res.text()}")
                meta = await res.json()

            session_token = meta['session_token']
            crypto.derive_master_secret(meta['server_public_key_hex'])
            nonce_prefix = bytes.fromhex(meta['nonce_prefix_hex'])
            expected_hash = meta.get("sha256_hash")
            
            session_id, org_id = self._extract_meta(meta, session_token)
            stream_url = f"{self.gateway_url}/api/v7/stream/{session_token}"
            
            current_offset = 0
            integrity_hash = hashlib.sha256()

            # Audit Fix 5: Hash Resume Logic
            if part_path.exists():
                file_size = part_path.stat().st_size
                current_offset = (file_size // crypto.CHUNK_SIZE) * crypto.CHUNK_SIZE
                with open(part_path, "ab") as f: f.truncate(current_offset)
                
                if current_offset > 0:
                    logger.info(f"Resuming download. Verifying integrity of existing {current_offset} bytes...")
                    with open(part_path, "rb") as existing:
                        while chunk := existing.read(1024 * 1024 * 8):
                            integrity_hash.update(chunk)

            for attempt in range(self.max_retries):
                try:
                    headers = secure_headers.copy()
                    headers["Range"] = f"bytes={current_offset}-"
                    
                    async with session.get(stream_url, headers=headers) as stream_res:
                        if stream_res.status not in (200, 206): raise IndroNetworkError(f"Data Plane Failed: {stream_res.status}")

                        pbar = None
                        if show_progress and tqdm:
                            total_expected = int(meta.get("size_bytes", stream_res.headers.get("Content-Length", 0)))
                            pbar = tqdm(total=total_expected, initial=current_offset, unit='iB', unit_scale=True, desc=f"Downloading {model_id}")

                        mode = "ab" if current_offset > 0 else "wb"
                        with open(part_path, mode) as f:
                            while True:
                                length_bytes = await stream_res.content.read(4)
                                if not length_bytes: break 
                                payload_len = int.from_bytes(length_bytes, 'big')
                                
                                # Audit Fix 4: Size Validation
                                if payload_len > crypto.MAX_FRAME_SIZE:
                                    raise IndroIntegrityError("CRITICAL: Received malformed TCP frame size. Terminating.")

                                full_payload = await read_exact(stream_res.content, payload_len)

                                chunk_sig = full_payload[:16]
                                encrypted_chunk = full_payload[16:]
                                chunk_index = current_offset // crypto.CHUNK_SIZE
                                
                                crypto.verify_chunk_signature(chunk_index, session_id, chunk_sig)
                                
                                aesgcm = crypto.derive_rotating_subkey(chunk_index)
                                nonce = nonce_prefix + chunk_index.to_bytes(8, byteorder='big')
                                aad = f"{session_id}:{chunk_index}:WM_ORG_{org_id}".encode('utf-8')
                                
                                try:
                                    raw_chunk = aesgcm.decrypt(nonce, encrypted_chunk, aad)
                                except Exception:
                                    raise IndroIntegrityError(f"Decryption failed at chunk {chunk_index}.")

                                if len(raw_chunk) == crypto.CHUNK_SIZE + 16: raw_chunk = raw_chunk[:-16]

                                f.write(raw_chunk)
                                integrity_hash.update(raw_chunk)
                                
                                chunk_size = len(raw_chunk)
                                current_offset += chunk_size
                                
                                if pbar: pbar.update(chunk_size)

                        if pbar: pbar.close()
                        break 
                        
                except (aiohttp.ClientError, asyncio.TimeoutError, ConnectionError) as e:
                    if attempt == self.max_retries - 1: raise IndroNetworkError(f"Stream dropped. Max retries exceeded.")
                    logger.warning(f"Network interruption. Resuming securely from boundary {current_offset}...")
                    await asyncio.sleep(2 ** attempt)
                    
            crypto.cleanup()
            
            if expected_hash and integrity_hash.hexdigest() != expected_hash:
                raise IndroIntegrityError("CRITICAL: Final file hash does not match server. Download corrupted.")
                
            part_path.rename(final_path)
            return final_path
            
        finally:
            if lock_path.exists():
                lock_path.unlink(missing_ok=True)

    def download_model(self, model_id: str, output_dir: str = "./models", show_progress: bool = True) -> Path:
        return _run_sync(self.download_model_async(model_id, output_dir, show_progress))

    # --- FEATURE 2: DIRECT-TO-CLOUD UPLOAD ---
    async def upload_model_async(self, file_path: str, model_id: str, repo_id: str):
        if not HF_HUB_AVAILABLE: raise IndroSDKError("huggingface_hub is required for uploads.")
        
        secure_headers = await self._solve_attestation_challenge()
        file_hash = hashlib.sha256()
        with open(file_path, "rb") as f:
            for byte_block in iter(lambda: f.read(4096), b""): file_hash.update(byte_block)
        
        session = await self._get_session()
        payload = {"model_id": model_id, "repo_id": repo_id, "target_file": Path(file_path).name}
        async with session.post(f"{self.gateway_url}/api/v7/models/upload/ticket", json=payload, headers=secure_headers) as res:
            if res.status != 200: raise IndroSDKError(f"Upload Ticket Denied: {await res.text()}")
            ticket_data = await res.json()

        api = HfApi(token=ticket_data["temp_token"])
        await asyncio.to_thread(api.upload_file, path_or_fileobj=file_path, path_in_repo=Path(file_path).name, repo_id=repo_id, repo_type="model")

        confirm = {"ticket_id": ticket_data["ticket_id"], "file_hash": file_hash.hexdigest()}
        async with session.post(f"{self.gateway_url}/api/v7/models/upload/confirm", json=confirm, headers=secure_headers) as res:
            if res.status != 200: raise IndroSDKError("Backend Registration Failed.")

    def upload_model(self, file_path: str, model_id: str, repo_id: str):
        _run_sync(self.upload_model_async(file_path, model_id, repo_id))

    # --- FEATURE 3 & 4: LIST & DELETE ---
    async def list_models_async(self) -> list:
        return await self._request_with_retry("GET", "/api/v7/models")

    def list_models(self) -> list:
        return _run_sync(self.list_models_async())

    async def delete_model_async(self, model_id: str) -> dict:
        return await self._request_with_retry("DELETE", f"/api/v7/models/{model_id}")

    def delete_model(self, model_id: str) -> dict:
        return _run_sync(self.delete_model_async(model_id))
