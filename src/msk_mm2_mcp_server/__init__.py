"""
MSK MCP Server - A Model Context Protocol server for Amazon MSK
"""

__version__ = "0.1.0"
__author__ = "yhyyz"
__email__ = "m18311283082@gmail.com"

from .server import mcp

def main():
    """Main entry point for the MSK MCP Server"""
    mcp.run()

__all__ = ["mcp", "main"]