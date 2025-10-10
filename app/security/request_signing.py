"""
Request Signing and Verification
Advanced: Prevents replay attacks and ensures request integrity

Uses HMAC-SHA256 for signing requests with timestamps
"""
import hmac
import hashlib
import json
import time
from typing import Any, Dict


class RequestSigner:
    """
    Sign and verify API requests to prevent tampering and replay attacks
    """

    def __init__(self, secret_key: str, max_age_seconds: int = 300):
        """
        Initialize request signer

        Args:
            secret_key: Secret key for HMAC signing
            max_age_seconds: Maximum age of valid signatures (default 5 minutes)
        """
        self.secret_key = secret_key.encode()
        self.max_age_seconds = max_age_seconds

    def sign(self, payload: Dict[str, Any], timestamp: str = None) -> str:
        """
        Sign a request payload

        Args:
            payload: Request data to sign
            timestamp: Optional timestamp (uses current time if not provided)

        Returns:
            HMAC signature (hex string)
        """
        if timestamp is None:
            timestamp = str(int(time.time()))

        # Create canonical string: timestamp + JSON payload
        canonical = f"{timestamp}:{json.dumps(payload, sort_keys=True)}"

        # Generate HMAC-SHA256 signature
        signature = hmac.new(
            self.secret_key,
            canonical.encode(),
            hashlib.sha256
        ).hexdigest()

        return signature

    def verify(
        self,
        payload: Dict[str, Any],
        signature: str,
        timestamp: str
    ) -> bool:
        """
        Verify request signature

        Args:
            payload: Request data
            signature: Signature to verify
            timestamp: Request timestamp

        Returns:
            True if signature is valid and not expired
        """
        # Check timestamp freshness (replay attack prevention)
        try:
            request_time = int(timestamp)
            current_time = int(time.time())

            age = current_time - request_time

            if age < 0 or age > self.max_age_seconds:
                # Timestamp is in the future or too old
                return False

        except (ValueError, TypeError):
            return False

        # Verify signature
        expected_signature = self.sign(payload, timestamp)

        # Use constant-time comparison to prevent timing attacks
        return hmac.compare_digest(signature, expected_signature)

    def create_signed_request(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """
        Create a signed request with timestamp and signature

        Args:
            payload: Request data

        Returns:
            Signed request with timestamp and signature
        """
        timestamp = str(int(time.time()))
        signature = self.sign(payload, timestamp)

        return {
            "payload": payload,
            "timestamp": timestamp,
            "signature": signature
        }
