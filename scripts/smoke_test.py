from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

import httpx


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://localhost:8080")
    parser.add_argument("--api-key", required=True)
    parser.add_argument(
        "--payload",
        default="examples/generate_plan_stage1_dispatch.json",
    )
    args = parser.parse_args()

    body = json.loads(Path(args.payload).read_text())
    async with httpx.AsyncClient(timeout=300) as client:
        response = await client.post(
            f"{args.base_url.rstrip('/')}/orchestrator/dispatch",
            headers={"X-A2A-API-Key": args.api_key},
            json=body,
        )
        print("HTTP", response.status_code)
        print(json.dumps(response.json(), indent=2, ensure_ascii=False))
        response.raise_for_status()


if __name__ == "__main__":
    asyncio.run(main())
