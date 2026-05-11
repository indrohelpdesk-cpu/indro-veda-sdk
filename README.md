# 🛡️ Indro Vanguard SDK

[![PyPI version](https://badge.fury.io/py/indro-veda.svg)](https://badge.fury.io/py/indro-veda)
[![Python 3.8+](https://img.shields.io/badge/python-3.8+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

**The Official Enterprise Client for the Indro Vanguard Delivery Network.**

Indro-Veda is a military-grade, zero-trust data pipeline designed to securely stream and decrypt massive AI models and datasets on the fly. Built with hardcore cryptography and an asynchronous core, it allows developers to pull models without exposing API keys or passing unencrypted weights over the open internet.

## ✨ Enterprise Features

* **Zero-Trust Cryptography:** Utilizes local `X25519` keypair generation and ECDH key exchange to derive shared secrets. The server never transmits the decryption key over the network.
* **Military-Grade Encryption:** Streams are encrypted chunk-by-chunk using `AES-GCM` with dynamically rolling nonces.
* **Asynchronous Core:** Built on `aiohttp` to handle massive gigabyte streams concurrently without blocking your main thread.
* **Beautiful UX:** Integrated `tqdm` progress bars display real-time network speeds (MB/s) and ETAs directly in your terminal.
* **Bring Your Own Repo (BYOR):** Natively supports the Indro-Veda registry architecture, allowing developers to stream models hosted securely across the network.

---

## 📦 Installation

Install the SDK directly from the Python Package Index (PyPI):

```bash
pip install indro-veda
