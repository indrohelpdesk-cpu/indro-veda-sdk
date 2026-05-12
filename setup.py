import os
from setuptools import setup

here = os.path.abspath(os.path.dirname(__file__))
try:
    with open(os.path.join(here, 'README.md'), encoding='utf-8') as f:
        long_description = f.read()
except FileNotFoundError:
    long_description = "Python SDK for the Indro delivery network."

setup(
    name="indro",
    version="0.0.2",
    author="Abhinav Anand",
    description="Secure, asynchronous Python client for the Indro streaming network.",
    long_description=long_description,
    long_description_content_type="text/markdown",
    url="https://github.com/indrohelpdesk-cpu/indro-veda-sdk",
    packages=["indro"],
    install_requires=[
        "aiohttp>=3.8.5",
        "cryptography>=41.0.3",
        "tqdm>=4.65.0",
        "huggingface_hub>=0.19.0",
        "PyJWT>=2.8.0"
    ],
    classifiers=[
        "Development Status :: 5 - Production/Stable",
        "Intended Audience :: Developers",
        "Topic :: Software Development :: Libraries :: Python Modules",
        "Programming Language :: Python :: 3",
        "Programming Language :: Python :: 3.8",
        "Programming Language :: Python :: 3.9",
        "Programming Language :: Python :: 3.10",
        "Programming Language :: Python :: 3.11",
        "License :: OSI Approved :: MIT License",
        "Operating System :: OS Independent",
    ],
    python_requires=">=3.8",
)
