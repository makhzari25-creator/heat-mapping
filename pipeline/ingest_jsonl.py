"""
ingest_jsonl.py — Ingests events from a JSONL file into the API in batches of 500.

Usage:
    python pipeline/ingest_jsonl.py events_output.jsonl
    python pipeline/ingest_jsonl.py events_output.jsonl --api http://localhost:8000
"""
import argparse
import json
import sys
try:
    import urllib.request
    import urllib.error
except ImportError:
    pass

BATCH_SIZE = 500


def ingest_file(jsonl_path: str, api_url: str):
    events = []
    with open(jsonl_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    events.append(json.loads(line))
                except json.JSONDecodeError as e:
                    print(f"[WARN] Skipping malformed line: {e}")

    print(f"[INFO] Loaded {len(events)} events from {jsonl_path}")

    total_accepted = 0
    total_rejected = 0
    total_duplicate = 0
    batch_num = 0

    for i in range(0, len(events), BATCH_SIZE):
        batch = events[i:i+BATCH_SIZE]
        batch_num += 1
        payload = json.dumps({"events": batch}).encode("utf-8")

        req = urllib.request.Request(
            f"{api_url}/events/ingest",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST"
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                result = json.loads(resp.read())
                total_accepted += result.get("accepted", 0)
                total_rejected += result.get("rejected", 0)
                total_duplicate += result.get("duplicate", 0)
                print(f"[INFO] Batch {batch_num}: accepted={result.get('accepted')} "
                      f"rejected={result.get('rejected')} duplicate={result.get('duplicate')}")
        except urllib.error.URLError as e:
            print(f"[ERROR] Batch {batch_num} failed: {e}")
            sys.exit(1)

    print(f"\n[SUMMARY] Ingestion complete")
    print(f"  Total accepted:   {total_accepted}")
    print(f"  Total rejected:   {total_rejected}")
    print(f"  Total duplicate:  {total_duplicate}")
    print(f"  Total batches:    {batch_num}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Ingest JSONL events into Store Intelligence API")
    parser.add_argument("jsonl_file", help="Path to events JSONL file")
    parser.add_argument("--api", default="http://localhost:8000",
                        help="API base URL (default: http://localhost:8000)")
    args = parser.parse_args()
    ingest_file(args.jsonl_file, args.api)
