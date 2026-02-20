"""
Integration tests for FastAPI endpoints
Tests the actual API endpoints with HTTP requests
"""

import pytest
import requests
import time


# Base URL for the API (assumes API is running)
API_BASE_URL = "http://localhost:8081"


def is_api_running():
    """Check if API is accessible"""
    try:
        response = requests.get(f"{API_BASE_URL}/healthz", timeout=2)
        return response.status_code == 200
    except:
        return False


@pytest.mark.skipif(not is_api_running(), reason="API not running at localhost:8081")
class TestAPIEndpoints:
    """Integration tests for API endpoints"""

    def test_root_endpoint(self):
        """Test the root endpoint returns welcome message"""
        response = requests.get(f"{API_BASE_URL}/")
        assert response.status_code == 200
        data = response.json()
        assert "message" in data
        assert "Consult" in data["message"]

    def test_health_check(self):
        """Test health check endpoint"""
        response = requests.get(f"{API_BASE_URL}/healthz")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ok"


# Standalone tests that don't require running API
class TestAPIWithoutServer:
    """Tests that can run without a live server"""

    def test_api_structure(self):
        """Test that we can import the API module"""
        import sys
        from pathlib import Path

        #sys.path.insert(0, str(Path(__file__).parent.parent / "src" / "llm-api"))
        sys.path.insert(0, str(Path(__file__).parents[2] / "src" / "llm-api"))

        from api.server import app

        assert app.title == "The Consult · Gemini Proxy"
        # No version made yet: assert app.version == "v1"
