from setuptools import setup, find_packages

# Read the contents of README file
with open("README.md", encoding="utf-8") as f:
    long_description = f.read()

setup(
    name="msk-mcp-server",
    version="0.1.1",
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
        # Add your dependencies here
    ],
    entry_points={
        "console_scripts": [
            "msk-mcp-server=msk_mm2_mcp_server:main",
        ],
    },
)
