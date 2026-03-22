from loadJson import load_measures
from openai_helper import get_client


def generate_overview(measures, sample_size=10):
    if not measures:
        raise ValueError("No HEDIS measures were provided.")

    sample = measures[:sample_size]
    total_measures = len(measures)

    prompt = f"""
You are analyzing HEDIS measures for a healthcare quality toolkit.

Here is a sample of the dataset:
{sample}

The full dataset contains {total_measures} measures.

Generate a concise overview with:
1. A high-level summary
2. Key categories or themes in the measures
3. The total number of measures
4. Two or three notable insights

Keep the response short, clear, and business-friendly.
"""

    client = get_client()
    response = client.chat.completions.create(
        model="gpt-5-mini",
        messages=[{"role": "user", "content": prompt}],
    )
    return response.choices[0].message.content


def main():
    measures = load_measures()
    overview = generate_overview(measures)
    print(overview)


if __name__ == "__main__":
    main()
