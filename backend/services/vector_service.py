import logging
import requests
from qdrant_client import QdrantClient
from qdrant_client.http import models
from backend.database import qdrant_client

logger = logging.getLogger(__name__)

# Constants
# OLLAMA_URL = "http://127.0.0.1:11434/api/embeddings"
# EMBEDDING_MODEL = "nomic-embed-text"
COLLECTION_NAME = "papers"
VECTOR_SIZE = 4096  # nv-embed-v1 dimension size


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
        except Exception as e:
            logger.error(f"Failed to check/create Qdrant collection: {e}")

    def get_embedding(self, text: str):
        """Generates embedding vector using API."""
        import os
        api_url = os.getenv("EMBEDDING_API_URL", "https://integrate.api.nvidia.com/v1/embeddings")
        headers = {
            "Authorization": f"Bearer {os.getenv('NVIDIA_EMBED_API_KEY', '')}",
            "Content-Type": "application/json"
        }
        try:
            response = requests.post(api_url, headers=headers, json={
                "model": os.getenv("EMBEDDING_MODEL", "nvidia/nv-embed-v1"),
                "input": text,
                "input_type": "passage"
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
        vector = self.get_embedding(query)
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
