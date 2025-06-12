from setuptools import setup, find_packages

# Read the contents of README file
with open("README.md", encoding="utf-8") as f:
    long_description = f.read()

setup(
    name="msk-mcp-server",
    version="0.1.4",
    author="yhyyz",
    author_email="m18311283082@gmail.com",
    description="A Model Context Protocol server for Amazon MSK",
    long_description=long_description,
    long_description_content_type="text/markdown",
    url="https://github.com/yhyyz/msk-mcp-server",
    packages=find_packages(where="src"),
    package_dir={"": "src"},
    classifiers=[
        "Development Status :: 3 - Alpha",
        "Intended Audience :: Developers",
        "License :: OSI Approved :: MIT License",
        "Programming Language :: Python :: 3.12",
    ],
    python_requires=">=3.8",
    install_requires=[
        "boto3>=1.38.31",
        "botocore>=1.38.31",
        "mcp>=1.9.3",
        "fastmcp>=2.7.0"
        # Add your dependencies here
    ],
    entry_points={
        "console_scripts": [
            "msk-mcp-server=src.main:main",
        ],
    },
)
