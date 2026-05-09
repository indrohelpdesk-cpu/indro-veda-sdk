from setuptools import setup, find_packages

setup(
    name="indro-veda",
    version="1.0.0",
    author="Indro Studio",
    description="The official SDK for the Indro-Veda Vanguard Delivery Network.",
    packages=find_packages(),
    install_requires=[
        "requests",
        "cryptography",
        "pyjwt"
    ],
    classifiers=[
        "Programming Language :: Python :: 3",
        "License :: OSI Approved :: MIT License",
        "Operating System :: OS Independent",
    ],
    python_requires='>=3.8',
)
