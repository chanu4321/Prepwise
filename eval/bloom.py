"""Bloom's-level rubric shared by the LLM judge and the manual (human) check, and helpers to read the
generated questions out of a step-4 run. Both raters see the same rubric and are blind to the target level."""
import json
from pathlib import Path

LEVELS = ["remember", "understand", "apply", "analyze", "evaluate", "create"]

# Revised taxonomy (Anderson & Krathwohl, 2001)
RUBRIC = """Bloom's taxonomy levels (revised, Anderson & Krathwohl 2001):
- remember: retrieve facts or definitions from memory (define, list, state, name, recall).
- understand: construct meaning; explain, describe, summarise, classify, give examples, compare in simple terms.
- apply: carry out or use a procedure, method or concept in a given situation; solve, compute, implement, use.
- analyze: break material into parts and work out how the parts relate; differentiate, examine, investigate causes.
- evaluate: make and defend a judgement against criteria; justify, critique, assess, argue for or against.
- create: put elements together into a new, coherent whole; design, plan, formulate, construct something original.

Rules: rate what the question actually requires a student to do, not just the verb it opens with. For a question
with several parts, give the highest level that any part genuinely requires."""

JUDGE_PROMPT = """BLOOM_JUDGE
You are an experienced university examiner. Classify the cognitive level of the exam question below.

{rubric}

Subject: {subject}
Question:
\"\"\"
{question}
\"\"\"

Reply with ONLY a JSON object: {{"level": "<one of remember|understand|apply|analyze|evaluate|create>", "confidence": "<high|medium|low>", "reason": "<one sentence>"}}"""


def question_text(question: dict, path: str) -> str | None:
    """The text a student would read. None for a question the app flagged as failed."""
    if question.get("error"):
        return None
    text = (question.get("rawGeneration") or "").strip()
    if path == "batch":
        # The batch path can put the sub-questions only in `parts`
        for part in question.get("parts") or []:
            part_text = (part.get("text") or "").strip()
            if part_text and part_text not in text:
                text += f"\n({part.get('label', '')}) {part_text}"
    return text or None


def requested_level(section_config: dict, number) -> str | None:
    """The Bloom level the request asked for, by question number, as the app numbers them: 1..m are the
    section's questions, m+1.. are pool copies of questions[(i) % m]. (The batch path stores the level the LLM
    reports back, so the target must come from the request, not the response.)"""
    configs = section_config["questions"]
    m = len(configs)
    if not isinstance(number, int) or number < 1 or not m:
        return None
    return configs[number - 1]["bloomLevel"] if number <= m else configs[(number - m - 1) % m]["bloomLevel"]


def iter_questions(run_dir: Path, configs: dict):
    """Yields one row per question returned in a step-4 run (main and pool), failed ones included."""
    for result_file in sorted((run_dir / "results").glob("*.json")):
        result = json.loads(result_file.read_text(encoding="utf-8"))
        section_configs = configs[result["paper"]]["sections"]
        for s_index, section in enumerate(result.get("sections") or []):
            letter = chr(ord("A") + s_index)
            for kind in ("questions", "pool"):
                for index, question in enumerate(section.get(kind) or [], start=1):
                    tag = "q" if kind == "questions" else "pool"
                    yield {
                        "qid": f"{result['paper']}-{result['path']}-{letter}-{tag}{index}",
                        "paper": result["paper"], "path": result["path"], "subject": result["subject"],
                        "is_pool": kind == "pool", "number": question.get("number"),
                        "target": requested_level(section_configs[s_index], question.get("number")),
                        "reported_level": (question.get("bloomLevel") or "").lower(),
                        "marks": question.get("totalMarks"), "failed": bool(question.get("error")),
                        "validation": question.get("validation"),
                        "text": question_text(question, result["path"]),
                    }
