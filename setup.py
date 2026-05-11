import os
from setuptools import setup

# Safely read README.md for PyPI's homepage description
try:
    with open("README.md", "r", encoding="utf-8") as fh:
        long_description = fh.read()
except FileNotFoundError:
    long_description = "The Official Enterprise SDK for the Indro-Veda Vanguard Delivery Network."

setup(
    name="indro-veda",
    version="16.0.0",
    author="Abhinav Anand",
    author_email="indrohelpdesk@gmail.com", 
    description="Zero-Trust, Hyperscale AI Model Delivery Network SDK",
    long_description=long_description,
    long_description_content_type="text/markdown",
    url="https://github.com/indrohelpdesk-cpu/indro-veda-sdk",
    
    # HARDCODED for absolute reliability. Matches the folder name exactly.
    packages=["indro-veda"], 
    
    # Exact dependencies for the Titan SDK
    install_requires=[
        "aiohttp>=3.8.5",
        "cryptography>=41.0.3",
        "tqdm>=4.65.0",
        "huggingface_hub>=0.19.0",
    ],
    
    # Enterprise Metadata Classifiers
    classifiers=[
        "Development Status :: 5 - Production/Stable",
        "Intended Audience :: Developers",
        "Intended Audience :: Science/Research",
        "Topic :: Scientific/Engineering :: Artificial Intelligence",
        "Topic :: Security :: Cryptography",
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
