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
