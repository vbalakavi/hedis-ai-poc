from genAIOverview import generate_overview
from genQuestions import generate_questions
from loadJson import load_measures


def main():
    measures = load_measures()
    overview = generate_overview(measures)
    questions = generate_questions(measures)

    print("=== LOADED MEASURES ===")
    print(f"Total measures: {len(measures)}")

    print("\n=== AI OVERVIEW ===")
    print(overview)

    print("\n=== SUGGESTED QUESTIONS ===")
    print(questions)


if __name__ == "__main__":
    main()
