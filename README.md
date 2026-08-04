# Audilysis 2.0

Audilysis is a Flask application with AI mention tracking agents and a YouTube multilingual transcript tool.

## Run Locally

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
python app.py
```

Gunicorn entry point:

```bash
gunicorn --bind 127.0.0.1:8000 wsgi:app
```

## Environment Variables

Copy `.env.example` to `.env` and configure the keys you need.

Required for secure Flask sessions in production:

```env
FLASK_SECRET_KEY=
```

Required for YouTube transcript translation:

```env
GOOGLE_TRANSLATE_API_KEY=
```

Optional YouTube proxy variables:

```env
WEBSHARE_PROXY=
WEBSHARE_PROXY_USERNAME=
WEBSHARE_PROXY_PASSWORD=
WEBSHARE_PROXY_HOST=
WEBSHARE_PROXY_PORT=
YOUTUBE_PROXY_HTTP_URL=
YOUTUBE_PROXY_HTTPS_URL=
```

Optional real speaker diarization:

```env
HUGGINGFACE_TOKEN=
```

Required for live Google Ads integration in the Negative Keyword Agent:

```env
GOOGLE_ADS_DEVELOPER_TOKEN=
GOOGLE_ADS_CLIENT_ID=
GOOGLE_ADS_CLIENT_SECRET=
GOOGLE_ADS_REDIRECT_URI=
GOOGLE_ADS_TOKEN_ENCRYPTION_KEY=
```

Generate a valid Fernet encryption key for secure refresh-token storage:

```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

Google Ads OAuth callback for local development should point to:

```env
GOOGLE_ADS_REDIRECT_URI=http://127.0.0.1:5000/integrations/google-ads/callback
```

## Speaker Detection

Speaker detection is disabled by default. The YouTube Transcripter UI includes an `Enable Speaker Detection` checkbox. When disabled, transcript generation uses YouTube captions exactly as before.

When enabled, Audilysis attempts real audio diarization using `pyannote.audio`. It never guesses speakers from caption text and never invents speaker names. If diarization dependencies, audio download, model access, or confidence matching fail, Audilysis returns the normal transcript without speaker labels.

Optional install:

```bash
pip install -r requirements-diarization.txt
```

System requirement:

```bash
sudo apt install ffmpeg -y
```

- `ffmpeg` must be installed for audio extraction.
- CPU works but can be slow for long videos.
- GPU is recommended for long podcasts or frequent diarization.

Add the Hugging Face token to `.env`:

```env
HUGGINGFACE_TOKEN=hf_xxxxxxxxx
```

The token must have access to the pyannote speaker diarization model.

Temporary audio files are written to the system temp directory and deleted after processing.

## Tests

```bash
python -m unittest discover -s tests
```

## Local Google Ads Test Flow

1. Create `.env` from `.env.example` and add the Google Ads variables above.
2. Start the Flask app:

```bash
python app.py
```

3. Log in to Audilysis.
4. Open the Negative Keyword Agent in the agent studio.
5. In the `Google Ads` tab, confirm the connection panel no longer shows `Missing configuration`.
6. Click `Connect Google Ads`, complete OAuth, and return to Audilysis.
7. Verify:
   - `Connected` status appears
   - accessible customer accounts load
   - campaigns load for the selected account
   - date presets populate the date range
   - selected campaign count updates
   - `Analyze Selected Campaigns` returns real search-term analysis
8. Export CSV and Excel from the result panel.
9. Test `Apply selected negatives` only with a non-production account first.
