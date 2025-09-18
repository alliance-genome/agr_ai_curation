#!/usr/bin/env python3
"""Test streaming chat functionality."""

import asyncio
import json
import httpx


async def test_streaming():
    # First, create a session
    async with httpx.AsyncClient() as client:
        # Use the first PDF for testing (assuming one exists)
        pdf_list = await client.get("http://localhost:8002/api/pdf")
        pdfs = pdf_list.json()
        if not pdfs:
            print("No PDFs uploaded. Please upload a PDF first.")
            return

        pdf_id = pdfs[0]["id"]
        print(f"Using PDF: {pdfs[0]['filename']} (ID: {pdf_id})")

        # Create a session
        session_resp = await client.post(
            "http://localhost:8002/api/rag/sessions", json={"pdf_id": pdf_id}
        )
        session = session_resp.json()
        session_id = session["session_id"]
        print(f"Created session: {session_id}")

        # Ask a question with streaming
        print("\nAsking question: 'What is the main topic of this document?'")
        print("Streaming response:")
        print("-" * 40)

        async with client.stream(
            "POST",
            f"http://localhost:8002/api/rag/sessions/{session_id}/question",
            json={"question": "What is the main topic of this document?"},
            headers={"Accept": "text/event-stream"},
        ) as response:
            async for line in response.aiter_lines():
                if line.startswith("data: "):
                    try:
                        data = json.loads(line[6:])
                        if data["type"] == "delta":
                            print(data.get("content", ""), end="", flush=True)
                        elif data["type"] == "final":
                            print("\n" + "-" * 40)
                            print(f"Final answer received")
                            print(f"Citations: {len(data.get('citations', []))}")
                            print(
                                f"Specialists invoked: {data.get('specialists_invoked', [])}"
                            )
                    except json.JSONDecodeError:
                        pass


if __name__ == "__main__":
    asyncio.run(test_streaming())
