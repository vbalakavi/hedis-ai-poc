import json
from pathlib import Path


DATA_FILE = Path(__file__).with_name("hedis_measures.json")


def load_measures():
    with DATA_FILE.open(encoding="utf-8") as file:
        return json.load(file)


def main():
    measures = load_measures()
    print(f"Loaded {len(measures)} records from {DATA_FILE.name}")


if __name__ == "__main__":
    main()
