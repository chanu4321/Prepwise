import logging
from typing import Dict, List, Any
import requests
from backend.services.vector_service import VectorService
from backend.database import get_db_connection

logger = logging.getLogger(__name__)

# Ollama configuration
OLLAMA_URL = "http://127.0.0.1:11434/api/generate"
GENERATION_MODEL = "qwen2.5:3b"

# Bloom's Taxonomy action verbs
BLOOM_VERBS = {
    "remember": ["Define", "List", "State", "Identify", "Name", "Recall"],
    "understand": ["Explain", "Describe", "Summarize", "Interpret", "Classify", "Compare"],
    "apply": ["Demonstrate", "Apply", "Solve", "Use", "Implement", "Execute"],
    "analyze": ["Analyze", "Differentiate", "Examine", "Compare and contrast", "Investigate"],
    "evaluate": ["Evaluate", "Critique", "Justify", "Assess", "Argue", "Defend"],
    "create": ["Design", "Formulate", "Develop", "Construct", "Plan", "Compose"]
}

class RAGService:
    def __init__(self):
        self.vector_service = VectorService()
    
    def generate_mock_paper(self, config: Dict[str, Any]) -> Dict[str, Any]:
        """
        Generate a mock examination paper using RAG.
        
        Args:
            config: Paper configuration including subject, sections, Bloom settings
        
        Returns:
            Generated paper with questions
        """
        subject = config.get("subject", "")
        sections = config.get("sections", [])
        
        # 1. Retrieve relevant past papers
        similar_papers = self._retrieve_similar_papers(subject, limit=3)
        
        if not similar_papers:
            logger.warning(f"No papers found for subject: {subject}")
            return {"error": "No similar papers found in database"}
        
        # 2. Extract context from retrieved papers
        context = self._extract_paper_context(similar_papers)
        
        # Debugging
        print(f"DEBUG: Received {len(sections)} sections in request")
        
        # 3. Generate questions for each section
        generated_sections = []
        for i, section in enumerate(sections):
            print(f"DEBUG: Processing section {i+1}: {section.get('name')}")
            section_result = self._generate_section(section, context, subject)
            generated_sections.append(section_result)
        
        return {
            "subject": subject,
            "sections": generated_sections,
            "sourcePapers": [p["filename"] for p in similar_papers],
            "totalSections": len(generated_sections)
        }
    
    def _retrieve_similar_papers(self, subject: str, limit: int = 3) -> List[Dict]:
        """Retrieve similar papers using vector search."""
        try:
            # Semantic search for subject
            results = self.vector_service.search_similar(subject, limit, with_payload=True)
            
            logger.info(f"Vector search returned {len(results)} results")
            
            if not results:
                logger.warning("No vector search results found")
                return []
            
            papers = []
            for point in results:
                payload = point.payload or {}
                papers.append({
                    "id": point.id,
                    "filename": payload.get("filename", "Unknown"),
                    "subjectCode": payload.get("subject_code"),
                    "subjectName": payload.get("subject_name"),
                    "ocrText": payload.get("full_text", "")
                })
            
            return papers
            
        except Exception as e:
            logger.error(f"Error retrieving papers: {e}", exc_info=True)
            return []
    
    def _extract_paper_context(self, papers: List[Dict]) -> str:
        """Extract question examples from retrieved papers."""
        context_parts = []
        
        for paper in papers:
            text = paper.get("ocrText", "")
            if not text:
                logger.warning(f"No OCR text found for paper {paper.get('filename')}")
                continue
            
            # Use first 2000 characters for context (reduced to prevent overwhelming small models)
            excerpt = text[:2000]
            context_parts.append(f"=== {paper['filename']} ===\n{excerpt}\n")
        
        if not context_parts:
            logger.warning("No context extracted from papers")
            return "No past paper context available."
        
        return "\n".join(context_parts)
    
    def _generate_section(self, section: Dict, context: str, subject: str) -> Dict:
        """Generate questions for a single section."""
        section_name = section.get("name", "Section")
        questions_config = section.get("questions", [])
        bloom_mode = section.get("bloomMode", "simple")
        generate_pool = section.get("generate_pool", False)
        
        # Calculate Bloom distribution
        if bloom_mode == "simple":
            difficulty = section.get("difficulty", "medium")
            bloom_dist = self._get_fuzzy_bloom_distribution(difficulty)
        else:
            bloom_dist = section.get("bloomDistribution", {})
        
        generated_questions = []
        pool_questions = []
        
        # 1. Generate the core requested questions
        for q_config in questions_config:
            question = self._generate_question(q_config, context, subject, bloom_dist)
            generated_questions.append(question)
            
        # 2. Generate extra pool questions if requested
        if generate_pool and questions_config:
            import math
            import copy
            
            # Calculate 50% more questions (at least 1 if any exist)
            pool_count = math.ceil(len(questions_config) * 0.5)
            
            # For pool questions, we cycle through the configs or pick random ones
            # For MVP: simple cycling
            for i in range(pool_count):
                config_idx = i % len(questions_config)
                base_config = copy.deepcopy(questions_config[config_idx])
                
                # Update number for pool questions (temporary)
                base_config["number"] = len(generated_questions) + len(pool_questions) + 1
                
                pool_q = self._generate_question(base_config, context, subject, bloom_dist)
                pool_questions.append(pool_q)
        
        return {
            "name": section_name,
            "instruction": section.get("instruction", ""),
            "questions": generated_questions,
            "pool": pool_questions
        }
    
    def _get_fuzzy_bloom_distribution(self, difficulty: str) -> Dict[str, int]:
        """Get Bloom distribution based on difficulty level."""
        distributions = {
            "easy": {
                "remember": 40, "understand": 30, "apply": 20,
                "analyze": 10, "evaluate": 0, "create": 0
            },
            "medium": {
                "remember": 20, "understand": 25, "apply": 30,
                "analyze": 15, "evaluate": 10, "create": 0
            },
            "hard": {
                "remember": 10, "understand": 15, "apply": 25,
                "analyze": 25, "evaluate": 15, "create": 10
            }
        }
        return distributions.get(difficulty, distributions["medium"])
    
    def _generate_question(self, q_config: Dict, context: str, subject: str, bloom_dist: Dict) -> Dict:
        """Generate a single question using Ollama."""
        bloom_level = q_config.get("bloomLevel", "understand")
        total_marks = q_config.get("totalMarks", 6)
        parts = q_config.get("parts", [])
        
        # Build prompt
        prompt = self._build_question_prompt(
            subject, bloom_level, total_marks, parts, context
        )
        
        # Call Ollama Chat API
        try:
            # Separate context from prompt for better handling
            system_instruction = f"You are an expert exam question generator for {subject}. Generate a single examination question based on the user's requirements."
            
            chat_payload = {
                "model": GENERATION_MODEL,
                "messages": [
                    {"role": "system", "content": system_instruction},
                    {"role": "user", "content": prompt}
                ],
                "stream": False,
                "options": {"temperature": 0.7}
            }
            
            response = requests.post(
                "http://127.0.0.1:11434/api/chat",
                json=chat_payload,
                timeout=45  # Increased timeout slightly
            )
            
            if response.status_code == 200:
                response_json = response.json()
                # Handle chat response structure
                generated_text = response_json.get("message", {}).get("content", "")
                
                # Fallback to old structure if message/content missing (unlikely)
                if not generated_text:
                     generated_text = response_json.get("response", "")
                     
                logger.info(f"DEBUG: Ollama Raw Response for Q{q_config.get('number')}: {generated_text[:100]}...")
                
                if not generated_text.strip():
                    logger.warning("Empty response received from Ollama")
                    return self._fallback_question(q_config)

                # Post-processing to clean up the question
                cleaned_text = self._clean_generated_question(generated_text)
                
                # Validate the question
                validation = self._validate_question(cleaned_text, bloom_level, total_marks)
                
                if validation["severity"] == "error":
                    logger.error(f"Q{q_config.get('number')} validation failed: {', '.join(validation['issues'])}")
                    # Still return the question but flag it
                elif validation["issues"]:
                    logger.warning(f"Q{q_config.get('number')} validation warnings: {', '.join(validation['issues'])}")

                return {
                    "number": q_config.get("number", 1),
                    "bloomLevel": bloom_level,
                    "totalMarks": total_marks,
                    "parts": self._parse_question_parts(cleaned_text, parts),
                    "rawGeneration": cleaned_text,
                    "validation": validation  # Include validation results
                }
            else:
                logger.error(f"Ollama error: {response.status_code} - {response.text}")
                return self._fallback_question(q_config)
                
        except Exception as e:
            logger.error(f"Question generation failed: {e}")
            return self._fallback_question(q_config)
    
    def _clean_generated_question(self, text: str) -> str:
        """Clean up repetitive prefixes and unwanted commentary."""
        # 1. Remove common repetitive prefixes
        prefixes_to_remove = [
            "In the context of software project management,",
            "In the context of the given text,",
            "Based on the text provided,",
            "Question:",
            "Answer:"
        ]
        
        for prefix in prefixes_to_remove:
            if text.lower().startswith(prefix.lower()):
                text = text[len(prefix):].strip()
                # Remove leading comma if present after prefix removal
                if text.startswith(","):
                    text = text[1:].strip()
        
        # 2. Capitalize first letter if needed
        if text:
            text = text[0].upper() + text[1:]
            
        # 3. Remove trailing commentary
        # Split by common separators that models use for explanation
        separators = ["\n\n---", "\n---", "Rationale:", "Explanation:", "Note:", "This question tests"]
        for sep in separators:
            if sep in text:
                text = text.split(sep)[0].strip()
                
        return text

    def _validate_question(self, question_text: str, bloom_level: str, marks: int) -> Dict[str, Any]:
        """
        Validate a generated question for quality and alignment.
        
        Returns:
            Dict with 'valid' (bool), 'issues' (list), and 'severity' (str)
        """
        issues = []
        severity = "none"  # none, warning, error
        
        # 1. Check minimum length
        if len(question_text.strip()) < 20:
            issues.append("Question too short (less than 20 characters)")
            severity = "error"
        
        # 2. Check for appropriate Bloom verb
        expected_verbs = BLOOM_VERBS.get(bloom_level, [])
        has_verb = any(verb.lower() in question_text.lower() for verb in expected_verbs)
        
        if not has_verb:
            issues.append(f"Question doesn't use expected {bloom_level} verbs: {', '.join(expected_verbs[:3])}")
            severity = "warning" if severity != "error" else "error"
        
        # 3. Check for placeholder text
        placeholders = ["[insert", "[add", "[fill", "[example]", "xxx", "..."]
        if any(placeholder in question_text.lower() for placeholder in placeholders):
            issues.append("Question contains placeholder text")
            severity = "error"
        
        # 4. Check for question mark (should end with ?, :, or . for imperative questions)
        valid_endings = ("?", ":", ".")
        if not question_text.strip().endswith(valid_endings):
            issues.append("Question doesn't end with proper punctuation (?, :, or .)")
            severity = "warning" if severity != "error" else "error"
        
        # 5. Check length matches complexity (higher marks should have longer questions)
        expected_min_length = 30 + (marks * 5)  # Base 30 + 5 chars per mark
        if len(question_text) < expected_min_length and marks > 3:
            issues.append(f"Question may be too brief for {marks} marks")
            severity = "warning" if severity != "error" else "error"
        
        # 6. Check for repetitive context (reduced strictness - it's OK occasionally)
        repetitive_patterns = ["in the context of", "based on the text", "according to"]
        if any(pattern in question_text.lower() for pattern in repetitive_patterns):
            issues.append("Question uses contextual phrase - ensure variety across questions")
            severity = "warning" if severity != "error" else severity  # Warning only, not error
        
        return {
            "valid": len(issues) == 0 or severity == "warning",
            "issues": issues,
            "severity": severity
        }

    def _build_question_prompt(self, subject: str, bloom_level: str, marks: int, parts: List[Dict], context: str) -> str:
        """Build RAG prompt for question generation."""
        verb = BLOOM_VERBS.get(bloom_level, ["Explain"])[0]
        
        prompt = f"""You are an expert exam question generator for {subject}.

BLOOM'S TAXONOMY LEVEL: {bloom_level.upper()}
Required action verb: {verb}

TASK:
Generate a NEW examination question for {subject} that tests the "{bloom_level}" cognitive level.

REQUIREMENTS:
1. Use the action verb "{verb}" or similar ({', '.join(BLOOM_VERBS[bloom_level][:3])})
2. Total marks: {marks}
3. Style matches the reference questions below, but DO NOT COPY THEM exactly.
4. Vary your question structure - don't use the same opening pattern every time
5. The question should be direct and professional

GUIDELINES:
- Output ONLY the question text, no explanations or notes after it
- DO NOT output "Question:" or "Answer:" labels
- Avoid repetitive phrases like starting every question with "In the context of..."
- Keep the question focused and clear

REFERENCE CONTEXT (for style only, do not copy):
{context[:1500]}

Generate the question now:"""
    
        return prompt
    def _parse_question_parts(self, generated_text: str, parts_config: List[Dict]) -> List[Dict]:
        """Parse generated text into question parts."""
        if len(parts_config) == 1 and not parts_config[0].get("label"):
            # Single part question
            return [{
                "label": "",
                "marks": parts_config[0]["marks"],
                "text": generated_text.strip()
            }]
        
        # Multi-part question parsing
        parsed_parts = []
        for part_config in parts_config:
            label = part_config["label"]
            # Simple heuristic: look for (a), (b), etc.
            # For MVP, just split evenly or return full text
            parsed_parts.append({
                "label": label,
                "marks": part_config["marks"],
                "text": f"[Part {label}] {generated_text[:200]}..."  # Simplified for MVP
            })
        
        return parsed_parts
    
    def _fallback_question(self, q_config: Dict) -> Dict:
        """Return a fallback question if generation fails."""
        return {
            "number": q_config.get("number", 1),
            "bloomLevel": q_config.get("bloomLevel", "understand"),
            "totalMarks": q_config.get("totalMarks", 6),
            "parts": [{
                "label": "",
                "marks": q_config.get("totalMarks", 6),
                "text": "[Question generation failed - please regenerate]"
            }],
            "error": True
        }
