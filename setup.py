import os
from setuptools import setup, find_packages

# Read the README.md for the long description on PyPI
here = os.path.abspath(os.path.dirname(__file__))
try:
    with open(os.path.join(here, 'README.md'), encoding='utf-8') as f:
        long_description = f.read()
except FileNotFoundError:
    long_description = "The Official Enterprise SDK for the Indro-Veda Vanguard Delivery Network."

setup(
    name="indro-vanguard",
    version="1.0.3",
    author="Abhinav Anand",
    author_email="indrohelpdesk@gmail.com"
    description="Zero-Trust, Hyperscale AI Model Delivery Network SDK",
    long_description=long_description,
    long_description_content_type="text/markdown",
    url="coming soon", # main website or GitHub repo
    
    # Automatically finds the 'indro_vanguard' folder we created
    packages=find_packages(),
    
    # These are the exact dependencies the SDK needs to run
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
        "License :: OSI Approved :: MIT License", # Assuming MIT, change if proprietary
        "Operating System :: OS Independent",
        "Environment :: Console",
    ],
    
    # Requires modern Python
    python_requires=">=3.8",
    
    # Include any non-Python files if needed later
    include_package_data=True,
)
