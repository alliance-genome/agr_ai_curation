#!/usr/bin/env python3
"""Test script to verify LangSmith integration."""

import asyncio
import httpx
from uuid import uuid4


async def test_langsmith_trace():
    """Test LangSmith tracing by making API calls."""

    base_url = "http://localhost:8000"

    # Test PDF document ID (you can replace with an actual ID)
    test_pdf_id = "f6f0a2a8-c74f-4f42-8bc0-3498ea68fe5f"

    async with httpx.AsyncClient() as client:
        try:
            print("🔍 Testing LangSmith Integration...")
            print(f"   Base URL: {base_url}")

            # 1. Create a RAG session
            print("\n1. Creating RAG session...")
            session_response = await client.post(
                f"{base_url}/api/rag/sessions",
                json={"pdf_id": test_pdf_id, "session_name": "LangSmith Test"},
            )

            if session_response.status_code == 200:
                session_data = session_response.json()
                session_id = session_data["session_id"]
                print(f"   ✅ Session created: {session_id}")
            else:
                print(f"   ❌ Failed to create session: {session_response.text}")
                # Try with a default PDF ID
                print("   Retrying with default test PDF...")
                session_response = await client.post(
                    f"{base_url}/api/rag/sessions",
                    json={"pdf_id": str(uuid4()), "session_name": "LangSmith Test"},
                )
                if session_response.status_code == 200:
                    session_data = session_response.json()
                    session_id = session_data["session_id"]
                    print(f"   ✅ Session created: {session_id}")
                else:
                    return

            # 2. Ask a question to generate a trace
            print("\n2. Asking a test question...")
            question = "What are the main findings in this document?"

            question_response = await client.post(
                f"{base_url}/api/rag/sessions/{session_id}/question",
                json={"question": question},
                headers={"Accept": "application/json"},
                timeout=30.0,
            )

            if question_response.status_code == 200:
                answer_data = question_response.json()
                print(f"   ✅ Question answered successfully")
                print(f"   Answer preview: {answer_data.get('answer', '')[:100]}...")
                print(
                    f"   Specialists invoked: {answer_data.get('specialists_invoked', [])}"
                )
            else:
                print(f"   ❌ Failed to get answer: {question_response.text}")

            print("\n" + "=" * 60)
            print("✨ LangSmith trace should now be visible at:")
            print("   https://smith.langchain.com/projects")
            print(f"   Project: ai-curation-dev")
            print("=" * 60)

        except Exception as e:
            print(f"❌ Error during test: {e}")
            print("\nMake sure the backend is running on port 8002")


if __name__ == "__main__":
    asyncio.run(test_langsmith_trace())
