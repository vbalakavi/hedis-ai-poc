from loadJson import load_measures
from openai_helper import get_client


def generate_questions(measures, sample_size=10):
    if not measures:
        raise ValueError("No HEDIS measures were provided.")

    sample = measures[:sample_size]
    total_measures = len(measures)

    prompt = f"""
You are analyzing HEDIS measures for a healthcare quality toolkit.

Here is a sample of the dataset:
{sample}

The full dataset contains {total_measures} measures.

Generate 8 concise example questions that a user might ask about this HEDIS dataset.
The questions should cover:
- measure definitions
- eligibility or denominator logic
- exclusions
- coding details
- comparison across measures
- operational or reporting use cases

Return only the questions as a numbered list.
"""

    client = get_client()
    response = client.chat.completions.create(
        model="gpt-5-mini",
        messages=[{"role": "user", "content": prompt}],
    )
    return response.choices[0].message.content


def main():
    measures = load_measures()
    questions = generate_questions(measures)
    print(questions)


if __name__ == "__main__":
    main()
