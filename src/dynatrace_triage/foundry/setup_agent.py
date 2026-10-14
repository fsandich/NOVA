"""Crea o actualiza el agente de Microsoft Foundry que usa la API de triage.

Uso:
    foundry-setup --dump-spec openapi.json                 # solo genera la especificación
    foundry-setup --connection-id <id> [--knowledge-dir knowledge] [--agent-id <id>]

La API se registra como herramienta OpenAPI autenticada con una conexión de Foundry
de tipo "custom keys" que guarda el header x-api-key (la clave no pasa por este script).
Requiere `pip install -e ".[foundry]"` y `az login` (DefaultAzureCredential).
"""

from __future__ import annotations

import argparse
import json
import os
from importlib import resources
from pathlib import Path
from typing import Any

TOOL_NAME = "dynatrace_triage"
TOOL_OPERATIONS = ("/v1/triage", "/v1/incidents")


def build_openapi_spec(public_url: str) -> dict[str, Any]:
    """Especificación OpenAPI con solo las operaciones que el agente puede usar."""
    from ..api.app import create_app
    from ..config import Settings

    spec = create_app(
        service_factory=lambda: None, settings=Settings(public_base_url=public_url)
    ).openapi()
    spec["paths"] = {p: v for p, v in spec["paths"].items() if p in TOOL_OPERATIONS}
    spec["servers"] = [{"url": public_url}]
    spec.setdefault("components", {})["securitySchemes"] = {
        "apiKeyHeader": {"type": "apiKey", "name": "x-api-key", "in": "header"}
    }
    spec["security"] = [{"apiKeyHeader": []}]
    # x-api-key ya va en securitySchemes: no se expone como parámetro al modelo.
    for path in spec["paths"].values():
        for op in path.values():
            op["parameters"] = [p for p in op.get("parameters", []) if p.get("name") != "x-api-key"]
            if not op["parameters"]:
                op.pop("parameters")
    return spec


def load_instructions() -> str:
    return resources.files(__package__).joinpath("instructions.md").read_text(encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--public-url", default=os.environ.get("PUBLIC_BASE_URL", ""))
    parser.add_argument(
        "--dump-spec", type=Path, help="escribe la especificación OpenAPI y termina"
    )
    parser.add_argument(
        "--connection-id", help="ID de la conexión custom keys de Foundry (x-api-key)"
    )
    parser.add_argument("--agent-id", help="actualiza este agente en vez de crear uno nuevo")
    parser.add_argument("--name", default="dynatrace-triage")
    parser.add_argument("--model", default=os.environ.get("MODEL_DEPLOYMENT_NAME", ""))
    parser.add_argument("--knowledge-dir", type=Path, help="archivos .md a subir como file search")
    args = parser.parse_args(argv)

    if not args.public_url:
        parser.error("falta --public-url o PUBLIC_BASE_URL")
    spec = build_openapi_spec(args.public_url)
    if args.dump_spec:
        args.dump_spec.write_text(json.dumps(spec, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"Especificación escrita en {args.dump_spec}")
        return 0
    if not args.connection_id or not args.model:
        parser.error("crear el agente requiere --connection-id y --model (o MODEL_DEPLOYMENT_NAME)")

    # Importación diferida: el resto del paquete no depende del SDK de Foundry.
    from azure.ai.agents import AgentsClient
    from azure.ai.agents.models import (
        FilePurpose,
        FileSearchTool,
        OpenApiConnectionAuthDetails,
        OpenApiConnectionSecurityScheme,
        OpenApiTool,
    )
    from azure.identity import DefaultAzureCredential

    client = AgentsClient(
        endpoint=os.environ["PROJECT_ENDPOINT"], credential=DefaultAzureCredential()
    )
    auth = OpenApiConnectionAuthDetails(
        security_scheme=OpenApiConnectionSecurityScheme(connection_id=args.connection_id)
    )
    openapi = OpenApiTool(
        name=TOOL_NAME,
        spec=spec,
        description="Triage de solo lectura de problems de Dynatrace y creación de incidentes en ServiceNow",
        auth=auth,
    )
    tools = list(openapi.definitions)
    tool_resources = None

    with client:
        if args.knowledge_dir:
            files = sorted(args.knowledge_dir.glob("*.md"))
            if not files:
                parser.error(f"no hay archivos .md en {args.knowledge_dir}")
            file_ids = [
                client.files.upload_and_poll(file_path=str(f), purpose=FilePurpose.AGENTS).id
                for f in files
            ]
            store = client.vector_stores.create_and_poll(
                file_ids=file_ids, name=f"{args.name}-conocimiento"
            )
            file_search = FileSearchTool(vector_store_ids=[store.id])
            tools += file_search.definitions
            tool_resources = file_search.resources
            print(f"Subidos {len(file_ids)} archivos de conocimiento (vector store {store.id})")

        kwargs = {
            "model": args.model,
            "name": args.name,
            "instructions": load_instructions(),
            "tools": tools,
            "tool_resources": tool_resources,
        }
        if args.agent_id:
            agent = client.update_agent(agent_id=args.agent_id, **kwargs)
            print(f"Agente actualizado: {agent.id}")
        else:
            agent = client.create_agent(**kwargs)
            print(f"Agente creado: {agent.id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
