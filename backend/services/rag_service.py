import logging
from typing import Dict, List, Any
import requests
from backend.services.vector_service import VectorService
from backend.database import get_db_connection

logger = logging.getLogger(__name__)

# Ollama configuration
# OLLAMA_URL = "http://127.0.0.1:11434/api/generate"
# GENERATION_MODEL = "qwen2.5:3b"

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
        """Generate ALL questions for a section in a single batched LLM call."""
        import os, json, math, copy
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

        # Build pool configs (50% extra)
        pool_configs = []
        if generate_pool and questions_config:
            pool_count = math.ceil(len(questions_config) * 0.5)
            for i in range(pool_count):
                base = copy.deepcopy(questions_config[i % len(questions_config)])
                base["number"] = len(questions_config) + i + 1
                base["_is_pool"] = True
                pool_configs.append(base)

        all_configs = questions_config + pool_configs

        if not all_configs:
            return {"name": section_name, "instruction": section.get("instruction", ""), "questions": [], "pool": []}

        # Build a single prompt describing ALL questions to generate
        question_specs = []
        for idx, qc in enumerate(all_configs):
            bloom_level = qc.get("bloomLevel", "understand")
            total_marks = qc.get("totalMarks", 6)
            parts = qc.get("parts", [])
            module = qc.get("module", "")
            verbs = BLOOM_VERBS.get(bloom_level, ["Explain"])

            if parts:
                parts_desc = ", ".join([f"Part {p.get('label','?')} ({p.get('marks',0)} marks)" for p in parts])
                spec = f"Q{idx+1}: Bloom={bloom_level.upper()} | Marks={total_marks} | Parts: {parts_desc} | Use verb from: {verbs[:3]}"
            else:
                spec = f"Q{idx+1}: Bloom={bloom_level.upper()} | Marks={total_marks} | Single question | Use verb from: {verbs[:3]}"

            if module:
                spec += f" | Topic: {module}"
            question_specs.append(spec)

        specs_text = "\n".join(question_specs)
        context_excerpt = context[:3000]

        batch_prompt = f"""You are an expert exam question generator for {subject}.

Generate {len(all_configs)} examination questions based on the specifications below. Use the past paper context for style and topic guidance.

PAST PAPER CONTEXT:
{context_excerpt}

QUESTION SPECIFICATIONS:
{specs_text}

RULES:
- Output ONLY a valid JSON array. No explanation, no markdown, no preamble.
- Each element must have: "number" (int), "text" (string, the full question), "bloomLevel" (string), "totalMarks" (int), "parts" (array of objects with "label" and "text" if multi-part, else empty array)
- For multi-part questions, split the question text into parts in the "parts" array.
- Questions must be specific, exam-ready, and match the marks allocated.

OUTPUT FORMAT:
[
  {{"number": 1, "text": "Full question text here.", "bloomLevel": "remember", "totalMarks": 2, "parts": []}},
  {{"number": 2, "text": "...", "bloomLevel": "understand", "totalMarks": 6, "parts": [{{"label": "a", "text": "Part a text (3 marks)"}}, {{"label": "b", "text": "Part b text (3 marks)"}}]}}
]"""

        try:
            api_url = os.getenv("LLM_API_URL", "https://integrate.api.nvidia.com/v1/chat/completions")
            headers = {
                "Authorization": f"Bearer {os.getenv('NVIDIA_API_KEY', '')}",
                "Content-Type": "application/json"
            }
            payload = {
                "model": os.getenv("LLM_MODEL") or "qwen/qwen3-next-80b-a3b-thinking",
                "messages": [{"role": "user", "content": batch_prompt}],
                "temperature": 0.6,
                "top_p": 0.7,
                "max_tokens": 16384
            }

            response = requests.post(api_url, headers=headers, json=payload, timeout=300)

            if response.status_code != 200:
                logger.error(f"Batch LLM error: {response.status_code} - {response.text}")
                raise Exception("LLM API error")

            raw = response.json().get("choices", [{}])[0].get("message", {}).get("content", "")
            logger.info(f"Batch generation raw (first 200): {raw[:200]}")

            # Extract JSON array from thinking model output
            bracket_idx = raw.find('[')
            if bracket_idx >= 0:
                raw = raw[bracket_idx:]
            last_bracket = raw.rfind(']')
            if last_bracket != -1:
                raw = raw[:last_bracket + 1]

            batch_result = json.loads(raw)

            # Map results back to question / pool lists
            generated_questions = []
            pool_questions = []

            for item in batch_result:
                q_num = item.get("number", 0)
                is_pool = q_num > len(questions_config)

                # Find matching original config for validation info
                cfg_idx = (q_num - 1) if q_num >= 1 else 0
                if cfg_idx < len(all_configs):
                    orig_cfg = all_configs[cfg_idx]
                else:
                    orig_cfg = {}

                bloom_level = item.get("bloomLevel", orig_cfg.get("bloomLevel", "understand"))
                total_marks = item.get("totalMarks", orig_cfg.get("totalMarks", 6))
                text = item.get("text", "")
                parts_raw = item.get("parts", [])

                cleaned_text = self._clean_generated_question(text)
                validation = self._validate_question(cleaned_text, bloom_level, total_marks)

                q_obj = {
                    "number": q_num,
                    "bloomLevel": bloom_level,
                    "totalMarks": total_marks,
                    "parts": parts_raw if parts_raw else self._parse_question_parts(cleaned_text, orig_cfg.get("parts", [])),
                    "rawGeneration": cleaned_text,
                    "validation": validation
                }

                if is_pool:
                    pool_questions.append(q_obj)
                else:
                    generated_questions.append(q_obj)

            return {
                "name": section_name,
                "instruction": section.get("instruction", ""),
                "questions": generated_questions,
                "pool": pool_questions
            }

        except Exception as e:
            logger.error(f"Batch question generation failed: {e}. Falling back to per-question generation.")
            # Fallback: generate per question (original slow path)
            generated_questions = [self._generate_question(qc, context, subject, bloom_dist) for qc in questions_config]
            pool_questions = [self._generate_question(qc, context, subject, bloom_dist) for qc in pool_configs]
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
        module = q_config.get("module", None)
        
        # Build prompt
        prompt = self._build_question_prompt(
            subject, bloom_level, total_marks, parts, context, module
        )
        
        # Call LLM API
        try:
            import os
            # Separate context from prompt for better handling
            system_instruction = f"You are an expert exam question generator for {subject}. Generate a single examination question based on the user's requirements."
            
            chat_payload = {
                "model": os.getenv("LLM_MODEL") or "qwen/qwen3-next-80b-a3b-thinking",
                "messages": [
                    {"role": "user", "content": f"{system_instruction}\n\nTask: {prompt}"}
                ],
                "temperature": 0.6,
                "top_p": 0.7,
                "max_tokens": 4096
            }
            
            headers = {
                "Authorization": f"Bearer {os.getenv('NVIDIA_API_KEY', '')}",
                "Content-Type": "application/json"
            }
            
            api_url = os.getenv("LLM_API_URL", "https://integrate.api.nvidia.com/v1/chat/completions")
            
            response = requests.post(
                api_url,
                headers=headers,
                json=chat_payload,
                timeout=60
            )
            
            if response.status_code == 200:
                response_json = response.json()
                message = response_json.get("choices", [{}])[0].get("message", {})
                
                # Thinking models return reasoning separately in reasoning_content
                # The actual answer is in content
                generated_text = message.get("content", "")
                
                # Fallback to old structure if missing
                if not generated_text:
                    generated_text = response_json.get("message", {}).get("content", "") or response_json.get("response", "")
                
                # Strip thinking model reasoning that leaked into content
                # Pattern: model outputs "Okay, let's..." reasoning before the actual question
                generated_text = self._strip_thinking_prefix(generated_text)
                    
                logger.info(f"DEBUG: LLM Raw Response for Q{q_config.get('number')}: {generated_text[:100]}...")
                
                if not generated_text.strip():
                    logger.warning("Empty response received from LLM")
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
    
    def _strip_thinking_prefix(self, text: str) -> str:
        """Strip reasoning prefixes that thinking models output before the actual question."""
        text = text.strip()
        lines = text.split('\n')
        
        # If the first few lines contain thinking markers, find where the actual question starts
        thinking_markers = ["okay", "let's", "let me", "so,", "to generate", "first,"]
        
        start_idx = 0
        in_thinking_block = False
        
        for i, line in enumerate(lines):
            line_lower = line.strip().lower()
            if i < 3 and any(line_lower.startswith(marker) for marker in thinking_markers):
                in_thinking_block = True
                continue
                
            # If we're in a thinking block, wait until we see a line that looks like a question
            # or a blank line followed by a substantial line
            if in_thinking_block:
                if len(line.strip()) > 30 and line_lower[0].isalpha() and not any(line_lower.startswith(marker) for marker in thinking_markers):
                    start_idx = i
                    break
            else:
                break
                
        if start_idx > 0:
            return '\n'.join(lines[start_idx:]).strip()
        return text

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

    def _build_question_prompt(self, subject: str, bloom_level: str, marks: int, parts: List[Dict], context: str, module: str = None) -> str:
        """Build RAG prompt for question generation."""
        verb = BLOOM_VERBS.get(bloom_level, ["Explain"])[0]
        
        prompt = f"""You are an expert exam question generator for {subject}.

BLOOM'S TAXONOMY LEVEL: {bloom_level.upper()}
Required action verb: {verb}
"""

        if module:
            prompt += f"\nTARGET MODULE: {module}\nThis question MUST specifically cover topics from this module.\n"

        prompt += f"""
TASK:
Generate a NEW examination question for {subject} that tests the "{bloom_level}" cognitive level.

REQUIREMENTS:
1. Use the action verb "{verb}" or similar ({', '.join(BLOOM_VERBS[bloom_level][:3])})
2. Total marks: {marks}
3. Style matches the reference questions below, but DO NOT COPY THEM exactly.
4. Vary your question structure - don't use the same opening pattern every time
5. The question should be direct and professional"""

        # Add multi-part instructions if needed
        if len(parts) > 1:
            prompt += f"\n6. This is a MULTI-PART question with {len(parts)} parts. Format your response with clear part labels:\n"
            for part in parts:
                prompt += f"   ({part['label']}) [Your sub-question here] - {part['marks']} marks\n"
            prompt += "\nEnsure each part is clearly labeled and addresses a distinct aspect of the topic."
        
        prompt += """

GUIDELINES:
- Output ONLY the question text, no explanations or notes after it
- DO NOT output "Question:" or "Answer:" labels
- Avoid repetitive phrases like starting every question with "In the context of..."
- Keep the question focused and clear

REFERENCE CONTEXT (for style only, do not copy):
{context}

Generate the question now:""".format(context=context[:1500])
    
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
        import re
        
        # Try to detect part markers: (a), a), (i), i), 1., etc.
        # Common patterns: (a), (b), (c) OR a), b), c) OR (i), (ii) OR 1., 2., 3.
        patterns = [
            r'\([a-z]\)',  # (a), (b), (c)
            r'[a-z]\)',    # a), b), c)
            r'\([ivxlcdm]+\)',  # (i), (ii), (iii) - Roman numerals
            r'[ivxlcdm]+\)',    # i), ii), iii)
            r'\d+\.',      # 1., 2., 3.
        ]
        
        # Try each pattern
        for pattern in patterns:
            markers = re.finditer(pattern, generated_text, re.IGNORECASE)
            marker_positions = [(m.group(), m.start(), m.end()) for m in markers]
            
            # If we found markers equal to number of parts, use this pattern
            if len(marker_positions) >= len(parts_config):
                parsed_parts = []
                
                for i, part_config in enumerate(parts_config):
                    if i < len(marker_positions):
                        start_pos = marker_positions[i][2]  # End of marker
                        end_pos = marker_positions[i + 1][1] if i + 1 < len(marker_positions) else len(generated_text)
                        
                        part_text = generated_text[start_pos:end_pos].strip()
                        
                        parsed_parts.append({
                            "label": part_config["label"],
                            "marks": part_config["marks"],
                            "text": part_text
                        })
                
                return parsed_parts
        
        # Fallback: No clear markers found, split by paragraphs or sentences
        logger.warning("No clear part markers found, using fallback splitting")
        
        # Try splitting by double newlines (paragraphs)
        paragraphs = [p.strip() for p in generated_text.split('\n\n') if p.strip()]
        
        if len(paragraphs) >= len(parts_config):
            return [{
                "label": part_config["label"],
                "marks": part_config["marks"],
                "text": paragraphs[i] if i < len(paragraphs) else generated_text
            } for i, part_config in enumerate(parts_config)]
        
        # Ultimate fallback: Return full text for each part with note
        return [{
            "label": part_config["label"],
            "marks": part_config["marks"],
            "text": generated_text.strip()
        } for part_config in parts_config]
    
    
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
