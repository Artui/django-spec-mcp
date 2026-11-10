from __future__ import annotations

NAMESPACE_DIGEST_CHARS: int = 12
"""Long enough to make a collision between two servers in one process implausible
while keeping a cache key or channel name readable. Not a security boundary: a
namespace is a partition, not a secret."""
