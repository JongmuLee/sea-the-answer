"""Generate the CSV field dictionary; database DDL lives only in root migrations/."""
import argparse
import json
from pathlib import Path
from .prepare_maritime_data import FIELDS


def generate(out):
    out.mkdir(parents=True, exist_ok=True)
    payload = {"contract": "prepared CSV fields; ship_id is app-owned metadata", "fields": FIELDS}
    (out / "dictionary.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=Path(__file__).resolve().parents[1] / "output/maritime-data-contract")
    args = parser.parse_args()
    generate(args.out)
