import os
import sys
import time
import uuid
import ctypes
import hashlib
import hmac
import platform
import asyncio
import aiohttp
from pathlib import Path
from typing import Tuple, List, Dict

from cryptography.hazmat.primitives.asymmetric import x25519
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

try:
    from tqdm.asyncio import tqdm
except ImportError:
    print("FATAL: Please install tqdm -> `pip install tqdm`")
    sys.exit(1)

try:
    from huggingface_hub import HfApi
    HF_HUB_AVAILABLE = True
except ImportError:
    HF_HUB_AVAILABLE = False


# ==========================================
# 1. HARDWARE ATTESTATION
# ==========================================
class VanguardAttestation:
    @staticmethod
    def get_device_fingerprint() -> str:
        system_info = f"{platform.node()}_{platform.machine()}_{platform.system()}_{platform.processor()}"
        return hashlib.sha256(system_info.encode('utf-8')).hexdigest()

    @staticmethod
    def get_binary_proof() -> str:
        try:
            with open(os.path.abspath(__file__), "rb") as f:
                return hashlib.sha512(f.read()).hexdigest()
        except Exception:
            return hashlib.sha512(b"vanguard_secure_sdk_v16").hexdigest()

# ==========================================
# 2. CRYPTOGRAPHIC KERNEL
# ==========================================
class VanguardCryptoKernel:
    def __init__(self):
        self.client_priv = x25519.X25519PrivateKey.generate()
        self.master_key: bytes = b""
        self.CHUNKS_PER_KEY_GROUP = 256
        self.CHUNK_SIZE = 1024 * 1024 * 2 # 2MB

    def get_public_hex(self) -> str:
        return self.client_priv.public_key().public_bytes(
            serialization.Encoding.Raw, serialization.PublicFormat.Raw
        ).hex()

    def derive_master_secret(self, server_pub_hex: str):
        server_pub_bytes = bytes.fromhex(server_pub_hex)
        server_pub_key = x25519.X25519PublicKey.from_public_bytes(server_pub_bytes)
        shared_secret = self.client_priv.exchange(server_pub_key)
        
        self.master_key = HKDF(algorithm=hashes.SHA256(), length=32, salt=None, info=b'indro-stream').derive(shared_secret)
        self._shred_memory(shared_secret)

    def derive_rotating_subkey(self, chunk_index: int) -> AESGCM:
        chunk_group = chunk_index // self.CHUNKS_PER_KEY_GROUP
        subkey = HKDF(algorithm=hashes.SHA256(), length=32, salt=None, info=f"vanguard-subkey-{chunk_group}".encode()).derive(self.master_key)
        return AESGCM(subkey)

    def verify_chunk_signature(self, chunk_index: int, session_id: str, provided_sig: bytes):
        expected_sig = hmac.new(self.master_key, f"{session_id}:{chunk_index}".encode(), hashlib.sha256).digest()[:16]
        if not hmac.compare_digest(provided_sig, expected_sig):
            raise RuntimeError(f"CRITICAL: Chunk {chunk_index} signature verification failed! Network MitM attack detected.")

    def shred_master_key(self):
        self._shred_memory(self.master_key)
        self.master_key = b""

    @staticmethod
    def _shred_memory(data: bytes):
        try:
            buffer = (ctypes.c_char * len(data)).from_buffer_copy(data)
            ctypes.memset(ctypes.addressof(buffer), 0, len(data))
        except Exception:
            pass

# ==========================================
# 3. VANGUARD ENTERPRISE SDK
# ==========================================
class IndroVault:
    def __init__(self, api_key: str = None, gateway_url: str = "https://abhinav337463-indro-veda-vanguard.hf.space"):
        # The SDK automatically looks for the key in the user's computer!
        self.api_key = api_key or os.getenv("VANGUARD_API_KEY")
        
        if not self.api_key: 
            raise ValueError("Vanguard API Key missing. Set the 'VANGUARD_API_KEY' environment variable.")
            
        self.gateway_url = gateway_url.rstrip("/")
        self.fingerprint = VanguardAttestation.get_device_fingerprint()
        
        self.headers = {
            "Authorization": f"Bearer {self.api_key}",
            "X-Device-Fingerprint": self.fingerprint,
            "X-Attestation-Proof": VanguardAttestation.get_binary_proof(),
            "Content-Type": "application/json"
        }

    # --- FEATURE 1: SECURE DOWNLOAD ---
    async def download_model_async(self, model_id: str, output_dir: str = "./models"):
        Path(output_dir).mkdir(parents=True, exist_ok=True)
        output_path = Path(output_dir) / f"{model_id}.safetensors"
        crypto = VanguardCryptoKernel()
        
        print(f"\n[Vanguard] 🛡️ Initiating Zero-Trust Handshake for '{model_id}'...")
        
        timestamp = int(time.time())
        nonce = uuid.uuid4().hex
        pub_hex = crypto.get_public_hex()
        
        req_sig_payload = f"{timestamp}|{nonce}|{pub_hex}"
        request_signature = hmac.new(self.api_key.encode(), req_sig_payload.encode(), hashlib.sha256).hexdigest()

        payload = {"timestamp": timestamp, "nonce": nonce, "client_public_key_hex": pub_hex, "request_signature": request_signature}

        async with aiohttp.ClientSession(headers=self.headers) as session:
            async with session.post(f"{self.gateway_url}/api/v6/handshake/{model_id}", json=payload) as res:
                if res.status != 200:
                    print(f"[Vanguard] ❌ Handshake Rejected: {await res.text()}")
                    sys.exit(1)
                
                meta = await res.json()
                session_token = meta['session_token']
                crypto.derive_master_secret(meta['server_public_key_hex'])
                nonce_prefix = bytes.fromhex(meta['nonce_prefix_hex'])
                expected_sha256 = meta['sha256_hash']
                session_id = session_token.split("|")[0]

        print(f"[Vanguard] 🔐 Tunnel Secured. Pumping Decrypted Stream...")
        stream_url = f"{self.gateway_url}/api/v6/stream/{session_token}"
        integrity_hash = hashlib.sha256()
        current_offset = 0

        async with aiohttp.ClientSession(headers=self.headers) as session:
            async with session.get(stream_url) as stream_res:
                if stream_res.status not in (200, 206):
                    print(f"[Vanguard] ❌ Data Plane Failed: {stream_res.status}")
                    sys.exit(1)

                total_size = int(stream_res.headers.get("Content-Length", 0))
                progress = tqdm(total=total_size, unit='iB', unit_scale=True, bar_format="{l_bar}{bar:40}{r_bar}")

                with open(output_path, "wb") as f:
                    while True:
                        length_bytes = await stream_res.content.read(4)
                        if not length_bytes: break 
                        payload_len = int.from_bytes(length_bytes, 'big')
                        
                        full_payload = await stream_res.content.read(payload_len)
                        if not full_payload: break

                        chunk_sig = full_payload[:16]
                        encrypted_chunk = full_payload[16:]
                        chunk_index = current_offset // crypto.CHUNK_SIZE
                        
                        crypto.verify_chunk_signature(chunk_index, session_id, chunk_sig)
                        aesgcm = crypto.derive_rotating_subkey(chunk_index)
                        nonce = nonce_prefix + chunk_index.to_bytes(8, byteorder='big')
                        aad = f"{session_id}:{chunk_index}".encode('utf-8')
                        
                        raw_chunk = aesgcm.decrypt(nonce, encrypted_chunk, aad)

                        if len(raw_chunk) == crypto.CHUNK_SIZE + 16:
                            raw_chunk = raw_chunk[:-16]

                        f.write(raw_chunk)
                        integrity_hash.update(raw_chunk)
                        
                        chunk_size = len(raw_chunk)
                        current_offset += chunk_size
                        progress.update(len(full_payload) + 4)

                progress.close()
                
        final_hash = integrity_hash.hexdigest()
        crypto.shred_master_key()

        if expected_sha256 and final_hash != expected_sha256:
            output_path.unlink()
            print("\n[Vanguard] ❌ CRITICAL: Integrity Check Failed! File purged.")
            sys.exit(1)

        print(f"\n[Vanguard] ✅ Stream Complete! Secured at: {output_path.absolute()}")
        return str(output_path.absolute())

    def download_model(self, model_id: str, output_dir: str = "./models") -> str:
        return str(asyncio.run(self.download_model_async(model_id, output_dir)))

    # --- FEATURE 2: DIRECT-TO-CLOUD UPLOAD ---
    async def upload_model_async(self, file_path: str, model_id: str, repo_id: str):
        if not HF_HUB_AVAILABLE: raise RuntimeError("huggingface_hub is required for uploads. Run `pip install huggingface_hub`")
        
        print(f"\n[Vanguard] 🚀 Requesting Upload Ticket for '{model_id}'...")
        file_hash = hashlib.sha256()
        with open(file_path, "rb") as f:
            for byte_block in iter(lambda: f.read(4096),b""): file_hash.update(byte_block)
        
        payload = {"model_id": model_id, "repo_id": repo_id, "target_file": Path(file_path).name}

        async with aiohttp.ClientSession(headers=self.headers) as session:
            async with session.post(f"{self.gateway_url}/api/v6/models/upload/ticket", json=payload) as res:
                if res.status != 200:
                    print(f"[Vanguard] ❌ Upload Ticket Denied: {await res.text()}")
                    sys.exit(1)
                ticket_data = await res.json()

        print(f"[Vanguard] ☁️ Ticket Acquired. Streaming directly to Cloud Storage...")
        api = HfApi(token=ticket_data["temp_token"])
        api.upload_file(path_or_fileobj=file_path, path_in_repo=Path(file_path).name, repo_id=repo_id, repo_type="model")

        confirm_payload = {"ticket_id": ticket_data["ticket_id"], "file_hash": file_hash.hexdigest()}
        async with aiohttp.ClientSession(headers=self.headers) as session:
            async with session.post(f"{self.gateway_url}/api/v6/models/upload/confirm", json=confirm_payload) as res:
                if res.status != 200:
                    print(f"[Vanguard] ❌ Backend Registration Failed: {await res.text()}")
                    sys.exit(1)
        print(f"[Vanguard] ✅ Upload Complete & Registered inside Vanguard Data Plane!")

    def upload_model(self, file_path: str, model_id: str, repo_id: str):
        asyncio.run(self.upload_model_async(file_path, model_id, repo_id))

    # --- FEATURE 3: LIST MODELS ---
    def list_models(self):
        """Fetches and prints all models registered to the user's workspace."""
        async def fetch():
            async with aiohttp.ClientSession(headers=self.headers) as session:
                # We hit a mock endpoint here. You will need to add a simple GET /api/v6/models endpoint to your main.py!
                async with session.get(f"{self.gateway_url}/api/v6/models") as res:
                    if res.status == 200:
                        models = await res.json()
                        print("\n[Vanguard] 📦 Registered Workspace Models:")
                        print("-" * 50)
                        for m in models:
                            size_gb = m.get('size_bytes', 0) / (1024**3)
                            print(f" ID: {m.get('model_id'):<20} | Repo: {m.get('repo_id'):<20} | Size: {size_gb:.2f} GB")
                        print("-" * 50)
                    else:
                        print(f"[Vanguard] ❌ Failed to fetch models: {await res.text()}")
        asyncio.run(fetch())

    # --- FEATURE 4: DELETE MODEL ---
    def delete_model(self, model_id: str):
        """Terminates a model deployment."""
        async def fetch():
            async with aiohttp.ClientSession(headers=self.headers) as session:
                async with session.delete(f"{self.gateway_url}/api/v6/models/{model_id}") as res:
                    if res.status in (200, 204):
                        print(f"\n[Vanguard] 🗑️ Model '{model_id}' successfully terminated from network.")
                    else:
                        print(f"\n[Vanguard] ❌ Failed to delete model: {await res.text()}")
        asyncio.run(fetch())
