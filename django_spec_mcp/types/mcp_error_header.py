"""``MCP_ERROR_HEADER`` - response header naming the class of a transport-level rejection."""

from __future__ import annotations

MCP_ERROR_HEADER: str = "MCP-Error"
"""Response header naming the *class* of a transport-level rejection.

Ours, not the spec's, which fixes the status code and says nothing about
diagnosis: a ``404`` from an unknown session and a ``404`` from a load balancer
with no matching rule are otherwise indistinguishable, and the JSON-RPC body
that would tell them apart is lost to clients that log
``${status} ${statusText}`` over HTTP/2, which has no reason phrase.

It carries strictly less than the body it summarises, so it leaks nothing new:
the session slugs deliberately do not separate "unknown id" from "id owned by
another principal", preserving the no-ownership-oracle property. Set on failure
responses only.
"""
