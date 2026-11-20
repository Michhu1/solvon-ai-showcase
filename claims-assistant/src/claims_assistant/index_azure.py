"""`python -m claims_assistant.index_azure` - create the Azure AI Search index and upload the policy clauses."""

from __future__ import annotations

from .config import Settings
from .documents import load_chunks
from .retrieval import AzureSearchRetriever


def main() -> None:
    settings = Settings.from_env()
    if not settings.azure_search_endpoint:
        raise SystemExit("Set AZURE_SEARCH_ENDPOINT (and AZURE_SEARCH_API_KEY or sign in with `az login`).")
    chunks = load_chunks()
    AzureSearchRetriever(
        settings.azure_search_endpoint,
        settings.azure_search_index,
        settings.azure_search_api_key,
        settings.azure_search_semantic_config,
    ).upload(chunks)
    print(f"Uploaded {len(chunks)} clauses to index '{settings.azure_search_index}'.")


if __name__ == "__main__":
    main()
