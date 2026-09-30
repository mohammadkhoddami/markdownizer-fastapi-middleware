"""Deterministic join of the static Project IR and the live route table."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, replace
from typing import Any

from markdownizer.ir import ProjectIR
from markdownizer_fastapi.routes import RouteInfo


@dataclass
class EnrichedProject:
    """A Project IR together with the matched live route table."""

    ir: ProjectIR
    routes: list[RouteInfo]
    enriched_hash: str

    def to_dict(self) -> dict[str, Any]:
        data = self.ir.to_dict()
        data["routes"] = [route.to_dict() for route in self.routes]
        data["enriched_hash"] = self.enriched_hash
        return data


def match_routes(ir: ProjectIR, routes: list[RouteInfo]) -> list[RouteInfo]:
    """Match every route to its IR symbol by ``module`` + ``qualname``.

    Unmatched routes are preserved and annotated; nothing is guessed or
    dropped.
    """
    module_by_name = {module.name: module for module in ir.modules}
    symbols = {(symbol.file_path, symbol.qualified_name): symbol for symbol in ir.symbols}
    matched: list[RouteInfo] = []
    for route in routes:
        if not route.endpoint_module or not route.endpoint_qualname:
            matched.append(replace(route, matched=False, unmatched_reason="dynamic"))
            continue
        module = module_by_name.get(route.endpoint_module)
        if module is None:
            matched.append(replace(route, matched=False, unmatched_reason="not_in_ir"))
            continue
        symbol = symbols.get((module.path, route.endpoint_qualname))
        if symbol is None:
            matched.append(replace(route, matched=False, unmatched_reason="not_in_ir"))
            continue
        matched.append(replace(route, symbol_id=symbol.id, matched=True))
    return matched


def enriched_hash(ir: ProjectIR, routes: list[RouteInfo]) -> str:
    """Hash the static IR plus the canonical route table.

    With no routes the static hash is returned unchanged, so middleware-only
    deployments keep the exact upstream hash.
    """
    if not routes:
        return ir.hash
    payload = {"ir_hash": ir.hash, "routes": [route.to_dict() for route in routes]}
    material = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.blake2b(material.encode("utf-8")).hexdigest()
