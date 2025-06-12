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
        spec = importlib.util.find_spec("msk_mm2_mcp_server")
        self.assertIsNotNone(spec, "Package msk_mm2_mcp_server should be importable")
    
    def test_version(self):
        """Test that the package has a version."""
        # Only import if the package is available
        if importlib.util.find_spec("main"):
            import main
            self.assertTrue(hasattr(main, "__version__"))
            self.assertIsInstance(main.__version__, str)
            self.assertTrue(len(main.__version__) > 0)


class TestMCPServer(unittest.TestCase):
    """Test MCP server basic functionality."""
    
    def test_mcp_object_exists(self):
        """Test that the MCP object exists."""
        # Only import if the package is available
        if importlib.util.find_spec("msk_mm2_mcp_server"):
            from msk_mm2_mcp_server.server import mcp_mm2
            self.assertIsNotNone(mcp_mm2, "MCP object should exist")


if __name__ == "__main__":
    unittest.main()
