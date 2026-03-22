# HEDIS AI POC

This folder contains a self-contained proof of concept for exploring HEDIS measures, generating AI summaries, asking targeted questions, and converting uploaded PDF source material into the JSON dataset used by the app.

## Contents

- `interactive_ui.py`: main Streamlit app
- `loadJson.py`: loads the active HEDIS JSON dataset
- `genAIOverview.py`: generates executive overview text
- `genQuestions.py`: generates suggested questions
- `openai_helper.py`: loads `OPENAI_API_KEY` from local `.env`
- `hedis_measures.json`: active structured dataset used by the app
- `uploaded_hedis_source.pdf`: most recently uploaded PDF source, if present

## Requirements

- Python 3.10+
- An OpenAI API key

## Setup

1. Open a terminal in this folder:

```bash
cd 3_Hedis_AI
```

2. Create and activate a virtual environment:

```bash
python3 -m venv .venv
source .venv/bin/activate
```

3. Install dependencies:

```bash
pip install -r requirements.txt
```

4. Create a local `.env` file in this folder with your API key:

```env
OPENAI_API_KEY=your_key_here
```

## Run The App

Start the Streamlit UI with:

```bash
streamlit run interactive_ui.py
```

Then open the local URL shown in the terminal, typically `http://localhost:8501`.

## Hosting

You can host this app and keep your OpenAI key private by storing it as a platform secret instead of committing a `.env` file.

### Streamlit Community Cloud

1. Push this folder to a GitHub repository.
2. In Streamlit Community Cloud, create a new app from that repo.
3. Set the app entrypoint to:

```text
interactive_ui.py
```

4. In the app settings, add a secret:

```toml
OPENAI_API_KEY = "your_key_here"
```

5. Deploy the app and share the generated URL with your colleague.

### Other Hosting Options

You can also host it on Azure App Service, AWS, GCP, or an internal VM. In each case:

- install dependencies from `requirements.txt`
- store `OPENAI_API_KEY` as an environment variable or secret
- run:

```bash
streamlit run interactive_ui.py --server.port $PORT --server.address 0.0.0.0
```

### Security Notes

- Do not commit `.env` or real secrets to Git
- Do not place the OpenAI key in frontend code
- If the uploaded PDFs are sensitive, prefer private/internal hosting over a public link

## What To Validate

Ask your colleague to test these flows:

1. Open the app and browse the available HEDIS measures.
2. Generate the overview and confirm the response is coherent.
3. Ask a few measure-specific and dataset-level questions.
4. Upload a PDF and confirm it converts into a refreshed `hedis_measures.json`.
5. Confirm the selected filename, button layout, and upload warnings behave as expected.

## Notes

- This project is self-contained within `3_Hedis_AI` except for installed Python packages and access to the OpenAI API.
- Do not share your real `.env` file in source control or over email/chat.
- If upload conversion replaces the dataset, the active `hedis_measures.json` in this folder is updated.
# hedis-ai-poc
