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

# Public search and RAG only see live papers. Points written before statuses existed have no
# `status` and count as live until `admin_cli.py dedupe --apply` tags them.
LIVE_ONLY = models.Filter(must_not=[models.FieldCondition(key="status", match=models.MatchAny(any=["review", "rejected"]))])


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
        """Embeds and stores a live paper. (Removed once uploads go through the background worker.)"""
        vector = self.get_embedding(text)
        if not vector:
            return False
        return self.upsert_paper_vector(paper_id, vector, text, metadata, "live")

    def upsert_paper_vector(self, paper_id: int, vector: list, text: str, metadata: dict, status: str) -> bool:
        """Stores a precomputed vector with the paper's metadata, full text and moderation status."""
        try:
            payload = metadata.copy()
            payload["full_text"] = text
            payload["status"] = status
            self.client.upsert(
                collection_name=COLLECTION_NAME,
                points=[models.PointStruct(id=paper_id, vector=vector, payload=payload)]
            )
            return True
        except Exception as e:
            logger.error(f"Qdrant Upsert Error: {e}")
            return False

    def search_similar(self, query: str, limit: int = 5, with_payload: bool = True):
        """Searches live papers by vector similarity."""
        vector = self.get_embedding(query, input_type="query")
        if not vector:
            return []

        try:
            results = self.client.query_points(
                collection_name=COLLECTION_NAME,
                query=vector,
                limit=limit,
                with_payload=with_payload,
                query_filter=LIVE_ONLY,
            )
            return results.points
        except Exception as e:
            logger.error(f"Qdrant Search Error: {e}")
            return []

    def nearest_papers(self, vector: list, limit: int, exclude_id: int):
        """The closest stored papers of any status (only live, review and rejected papers have vectors).
        Raises when Qdrant fails, so the upload worker retries instead of skipping the duplicate check."""
        results = self.client.query_points(
            collection_name=COLLECTION_NAME,
            query=vector,
            limit=limit,
            with_payload=True,
            query_filter=models.Filter(must_not=[models.HasIdCondition(has_id=[exclude_id])]),
        )
        return results.points

    def set_paper_payload(self, paper_id: int, payload: dict) -> bool:
        """Updates some payload keys (e.g. status, details) without re-embedding."""
        try:
            self.client.set_payload(collection_name=COLLECTION_NAME, payload=payload, points=[paper_id])
            return True
        except Exception:
            logger.exception("Qdrant payload update failed for paper %s", paper_id)
            return False

    def delete_paper(self, paper_id: int) -> bool:
        try:
            self.client.delete(collection_name=COLLECTION_NAME,
                               points_selector=models.PointIdsList(points=[paper_id]))
            return True
        except Exception:
            logger.exception("Qdrant delete failed for paper %s", paper_id)
            return False

    def get_paper_texts(self, ids) -> dict[int, str]:
        """Stored full text per paper id; papers without a vector are missing from the result."""
        ids = list(ids)
        if not ids:
            return {}
        try:
            points = self.client.retrieve(collection_name=COLLECTION_NAME, ids=ids, with_payload=True)
        except Exception:
            logger.exception("Qdrant retrieve failed for papers %s", ids)
            return {}
        return {point.id: (point.payload or {}).get("full_text", "") for point in points}
