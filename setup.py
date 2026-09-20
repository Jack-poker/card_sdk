from setuptools import setup, find_packages

import os

_ROOT = os.path.dirname(os.path.abspath(__file__))


def read_requirements():
    with open(os.path.join(_ROOT, "requirements.txt")) as f:
        return [line.strip() for line in f if line.strip() and not line.startswith("#")]


def read_readme():
    with open(os.path.join(_ROOT, "README.md"), encoding="utf-8") as f:
        return f.read()


setup(
    name="card-agent-sdk",
    version="0.1.0",
    description=(
        "Kaascan card SDK — generate student ID cards (SINGLE / MULTIPLE) with "
        "SVG templates, QR-code API-user auth, and a FastAPI web layer."
    ),
    long_description=read_readme(),
    long_description_content_type="text/markdown",
    author="Kaascan",
    license="MIT",
    project_urls={
        "Homepage": "https://kaascan.com",
        "Documentation": "https://docs.kaascan.com",
    },
    keywords=[
        "card",
        "student-card",
        "id-card",
        "qrcode",
        "pdf",
        "card-sdk",
        "kaascan",
    ],
    classifiers=[
        "Development Status :: 4 - Beta",
        "Intended Audience :: Developers",
        "License :: OSI Approved :: MIT License",
        "Operating System :: OS Independent",
        "Programming Language :: Python :: 3",
        "Programming Language :: Python :: 3.10",
        "Programming Language :: Python :: 3.11",
        "Programming Language :: Python :: 3.12",
        "Topic :: Multimedia :: Graphics",
        "Topic :: Software Development :: Libraries :: Python Modules",
    ],
    python_requires=">=3.10",
    packages=find_packages(exclude=["output", "docs", "build", "dist", "draf"]),
    include_package_data=True,
    package_data={
        "card_sdk": [
            "fonts/*.otf",
            "fonts/*.ttf",
            "templates/info.json",
            "templates/templates_base/templates.json",
            "templates/templates_base/*/front.*",
            "templates/templates_base/*/back.*",
        ],
    },
    install_requires=read_requirements(),
)