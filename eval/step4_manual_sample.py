"""Step 4d: draw the 30-question sample for the manual Bloom's-level check.

Seeded random sample of successfully generated questions from the stream path (the one the web UI uses).
The CSV shows only the subject and question text, so the rater is blind to the requested level and the judge.
The sample-to-question key is kept in the run directory."""
import argparse
import csv
import json
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from eval.bloom import RUBRIC, iter_questions  # noqa: E402
from eval.common import DATA, write_json  # noqa: E402

SEED = 20261107
SAMPLE_SIZE = 30


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-dir", required=True)
    args = parser.parse_args()
    run_dir = Path(args.run_dir)

    out = DATA / "bloom_manual_sample.csv"
    if out.exists():
        raise SystemExit(f"{out} already exists; refusing to redraw the sample")
    configs = json.loads((DATA / "generation_configs.json").read_text(encoding="utf-8"))
    eligible = [row for row in iter_questions(run_dir, configs) if row["path"] == "stream" and row["text"]]
    sample = random.Random(SEED).sample(eligible, min(SAMPLE_SIZE, len(eligible)))

    with out.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["sample_id", "subject", "question", "human_level", "notes"])
        for index, row in enumerate(sample, start=1):
            writer.writerow([f"s{index:02d}", row["subject"], row["text"], "", ""])
    (DATA / "bloom_rubric.txt").write_text(RUBRIC + "\n", encoding="utf-8")
    write_json(run_dir / "manual_sample_key.json",
               {"seed": SEED, "eligible": len(eligible),
                "key": {f"s{i:02d}": row["qid"] for i, row in enumerate(sample, start=1)}})
    print(f"wrote {len(sample)} of {len(eligible)} eligible questions to {out}")


if __name__ == "__main__":
    main()
