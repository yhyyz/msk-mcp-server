from msk_custom_domain.server import mcp_custom_domain
from msk_mm2_mcp_server.server import mcp_mm2
# from mcp_instance import mcp

__author__ = "yhyyz"
__email__ = "m18311283082@gmail.com"

from fastmcp import FastMCP

mcp = FastMCP("MSK MCP SERVER", 
             dependencies=["boto3", "botocore"])
mcp.mount("dr", mcp_custom_domain)
mcp.mount("mm2", mcp_mm2)

def main():
    """Main entry point for the MSK MCP Server"""
    mcp.run()

__all__ = ["mcp", "main"]