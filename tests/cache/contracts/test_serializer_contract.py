"""
Serializer architecture contracts.
"""

from __future__ import annotations

from nova.cache.backends.serializers import PickleSerializer


class TestSerializerContract:
    def create_serializer(self) -> PickleSerializer:
        return PickleSerializer()

    def test_roundtrip_dict(self) -> None:
        serializer = self.create_serializer()

        payload = {"a": 1, "b": [1, 2, 3]}

        raw = serializer.dumps(payload)
        restored = serializer.loads(raw)

        assert restored == payload

    def test_roundtrip_list(self) -> None:
        serializer = self.create_serializer()

        payload = [1, "two", 3.0]

        raw = serializer.dumps(payload)
        restored = serializer.loads(raw)

        assert restored == payload

    def test_roundtrip_nested(self) -> None:
        serializer = self.create_serializer()

        payload = {
            "user": {
                "id": 1,
                "tags": ["admin", "staff"],
            }
        }

        raw = serializer.dumps(payload)
        restored = serializer.loads(raw)

        assert restored == payload

    def test_corrupted_payload_returns_none(self) -> None:
        serializer = self.create_serializer()

        assert serializer.loads(b"corrupted-payload") is None

    def test_none_payload_roundtrip(self) -> None:
        serializer = self.create_serializer()

        raw = serializer.dumps(None)
        restored = serializer.loads(raw)

        assert restored is None
