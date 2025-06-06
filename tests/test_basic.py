"""
Basic tests for the MSK MCP Server package.
"""

import unittest
import importlib.util
import sys


class TestPackageImport(unittest.TestCase):
    """Test basic package import functionality."""

    def test_package_import(self):
        """Test that the package can be imported."""
        # Check if the package is installed or available in path
        spec = importlib.util.find_spec("mcp_server_msk_mm2")
        self.assertIsNotNone(spec, "Package mcp_server_msk_mm2 should be importable")
    
    def test_version(self):
        """Test that the package has a version."""
        # Only import if the package is available
        if importlib.util.find_spec("mcp_server_msk_mm2"):
            import mcp_server_msk_mm2
            self.assertTrue(hasattr(mcp_server_msk_mm2, "__version__"))
            self.assertIsInstance(mcp_server_msk_mm2.__version__, str)
            self.assertTrue(len(mcp_server_msk_mm2.__version__) > 0)


class TestMCPServer(unittest.TestCase):
    """Test MCP server basic functionality."""
    
    def test_mcp_object_exists(self):
        """Test that the MCP object exists."""
        # Only import if the package is available
        if importlib.util.find_spec("mcp_server_msk_mm2"):
            from mcp_server_msk_mm2 import mcp
            self.assertIsNotNone(mcp, "MCP object should exist")


if __name__ == "__main__":
    unittest.main()
