import logging
import os
import requests
from qdrant_client import QdrantClient
from qdrant_client.http import models
from database import qdrant_client

logger = logging.getLogger(__name__)

# Constants
# OLLAMA_URL = "http://127.0.0.1:11434/api/embeddings"
# EMBEDDING_MODEL = "nomic-embed-text"
COLLECTION_NAME = os.getenv("QDRANT_COLLECTION") or "papers"
VECTOR_SIZE = int(os.getenv("EMBEDDING_DIM") or 2048)  # nemotron-3-embed-1b output size


def embed_text(text: str, input_type: str = "passage"):
    """Generates an embedding vector using the API. Use input_type="query" for search queries."""
    api_url = os.getenv("EMBEDDING_API_URL") or "https://integrate.api.nvidia.com/v1/embeddings"
    headers = {
        "Authorization": f"Bearer {os.getenv('NVIDIA_EMBED_API_KEY', '')}",
        "Content-Type": "application/json"
    }
    try:
        response = requests.post(api_url, headers=headers, json={
            "model": os.getenv("EMBEDDING_MODEL") or "nvidia/nemotron-3-embed-1b",
            "input": text,
            "input_type": input_type
        }, timeout=30)
        if response.status_code == 200:
            data = response.json()
            return data.get("data", [{}])[0].get("embedding", [])
        else:
            logger.error(f"Embedding API Error: {response.text}")
            return None
    except Exception as e:
        logger.error(f"Embedding generation failed: {e}")
        return None


class VectorService:
    def __init__(self):
        self.client = qdrant_client
        self._ensure_collection()

    def _ensure_collection(self):
        """Creates the Qdrant collection if it doesn't exist."""
        try:
            collections = self.client.get_collections().collections
            exists = any(c.name == COLLECTION_NAME for c in collections)
            
            if not exists:
                self.client.create_collection(
                    collection_name=COLLECTION_NAME,
                    vectors_config=models.VectorParams(
                        size=VECTOR_SIZE,
                        distance=models.Distance.COSINE
                    )
                )
                logger.info(f"Created Qdrant collection: {COLLECTION_NAME}")
            else:
                vectors = self.client.get_collection(COLLECTION_NAME).config.params.vectors
                size = getattr(vectors, "size", None)
                if size != VECTOR_SIZE:
                    logger.error(
                        f"Qdrant collection '{COLLECTION_NAME}' holds {size}-dim vectors but the embedding "
                        f"model produces {VECTOR_SIZE}. Migrate with: python backend/dev-scripts/manage_db.py reembed"
                    )
        except Exception as e:
            logger.error(f"Failed to check/create Qdrant collection: {e}")

    def get_embedding(self, text: str, input_type: str = "passage"):
        """Generates embedding vector using API."""
        return embed_text(text, input_type)

    def upsert_paper(self, paper_id: int, text: str, metadata: dict):
        """Uploads paper vector and metadata to Qdrant."""
        vector = self.get_embedding(text)
        if not vector:
            return False

        try:
            # Add full text to metadata for retrieval
            payload = metadata.copy()
            payload["full_text"] = text
            
            self.client.upsert(
                collection_name=COLLECTION_NAME,
                points=[
                    models.PointStruct(
                        id=paper_id,
                        vector=vector,
                        payload=payload
                    )
                ]
            )
            return True
        except Exception as e:
            logger.error(f"Qdrant Upsert Error: {e}")
            return False

    def search_similar(self, query: str, limit: int = 5, with_payload: bool = True):
        """Searches for similar papers using vector similarity."""
        vector = self.get_embedding(query, input_type="query")
        if not vector:
            return []

        try:
            results = self.client.query_points(
                collection_name=COLLECTION_NAME,
                query=vector,
                limit=limit,
                with_payload=with_payload
            )
            return results.points
        except Exception as e:
            logger.error(f"Qdrant Search Error: {e}")
            return []
