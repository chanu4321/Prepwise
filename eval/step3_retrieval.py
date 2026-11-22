"""Step 3: retrieval quality of VectorService.search_similar over the rebuilt eval_papers index.

Input: eval/data/queries.csv, labelled by hand (columns: query_id, query, relevant_paper_ids, notes;
relevant ids are separated by ';' and refer to the `id` column of eval/data/corpus_index.csv; 'none' marks a query
no paper is relevant to, which is reported but cannot enter recall or MRR).
Metrics: recall@k = |relevant in top k| / |relevant|, averaged over queries; MRR = mean of 1 / rank of the first
relevant paper (0 if none in the top 10). Also reports the expected score of a random ranking for context."""
import csv
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from eval.common import DATA, eval_vector_service, install_call_logger, new_run_dir, now_iso, write_json  # noqa: E402

KS = (5, 10)


def load_queries(path: Path):
    with path.open(encoding="utf-8-sig", newline="") as handle:
        rows = [r for r in csv.DictReader(handle) if r["query"].strip()]
    queries, no_relevant = [], []
    for r in rows:
        label = r["relevant_paper_ids"].strip()
        if label.lower() == "none":  # a syllabus topic no paper covers: kept on record, excluded from recall/MRR
            no_relevant.append(r["query_id"])
            continue
        relevant = {int(x) for x in label.replace(",", ";").split(";") if x.strip()}
        if not relevant:
            raise SystemExit(f"{r['query_id']}: relevant_paper_ids is blank (write ids, or 'none')")
        queries.append({"id": r["query_id"], "query": r["query"].strip(), "relevant": relevant})
    return queries, no_relevant


def main():
    queries, no_relevant = load_queries(DATA / "queries.csv")
    run_dir = new_run_dir("step3_retrieval")
    set_context = install_call_logger(run_dir, "step3_retrieval")
    vectors = eval_vector_service()
    corpus_size = vectors.client.get_collection("eval_papers").points_count

    per_query = []
    for q in queries:
        set_context(query_id=q["id"])
        ranked = [point.id for point in vectors.search_similar(q["query"], max(KS))]
        reciprocal = next((1 / (rank + 1) for rank, pid in enumerate(ranked) if pid in q["relevant"]), 0.0)
        row = {"query_id": q["id"], "query": q["query"], "relevant": sorted(q["relevant"]), "ranked_top10": ranked,
               "reciprocal_rank": round(reciprocal, 4)}
        for k in KS:
            row[f"recall@{k}"] = round(len(q["relevant"] & set(ranked[:k])) / len(q["relevant"]), 4)
        per_query.append(row)

    n = len(per_query)
    summary = {"measured_at": now_iso(), "n_queries": n, "corpus_size_papers": corpus_size,
               "queries_with_no_relevant_paper_excluded": no_relevant,
               "mean_relevant_per_query": round(statistics.mean(len(q["relevant"]) for q in queries), 2),
               "queries_with_empty_results": sum(1 for r in per_query if not r["ranked_top10"]),
               "MRR": round(statistics.mean(r["reciprocal_rank"] for r in per_query), 4)}
    for k in KS:
        summary[f"recall@{k}"] = round(statistics.mean(r[f"recall@{k}"] for r in per_query), 4)
        # Expected recall@k if papers were ranked at random: k / corpus size (capped at 1)
        summary[f"random_baseline_recall@{k}"] = round(min(1.0, k / corpus_size), 4)
    write_json(run_dir / "retrieval_results.json", {"summary": summary, "per_query": per_query})
    for key, value in summary.items():
        print(f"{key}: {value}")
    print("run dir:", run_dir)


if __name__ == "__main__":
    main()
