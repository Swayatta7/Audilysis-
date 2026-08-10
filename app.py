import json
import os
import socket
import time
import uuid
import threading
import webbrowser
from http import HTTPStatus
from pathlib import Path
from urllib.parse import urlparse
from datetime import datetime

from flask import Flask, request, jsonify, render_template, redirect, url_for, session, Response, has_request_context
from werkzeug.exceptions import HTTPException

# Imports from local packages
from db.storage import (
    init_db, create_run, insert_mention_result, insert_competitor_metrics,
    get_negative_keyword_report, upsert_run_provider_result,
    get_run, get_run_for_user, get_mention_results, get_competitor_metrics, get_trend_data,
    list_negative_keyword_audit,
)
from api.dataforseo import query_platform
from services.mailer import send_report_email
from services.pdf_generator import generate_pdf_report
from services.youtube_transcript_service import (
    YouTubeTranscriptError,
    build_download_from_url,
    fetch_transcript,
    get_languages,
    get_proxy_diagnostics,
    get_youtube_transcript_api_version,
    translate_existing_payload,
)
from services.audio_transcription import get_audio_transcription_diagnostics
from services.diarization import get_speaker_detection_diagnostics, log_speaker_detection_diagnostics
from services.google_ads_service import (
    GoogleAdsIntegrationError,
    apply_negative_keywords,
    build_google_ads_connect_url,
    disconnect_google_ads,
    fetch_google_ads_campaigns,
    fetch_google_ads_search_terms,
    get_google_ads_status,
    handle_google_ads_oauth_callback,
    list_google_ads_accounts,
    log_google_ads_redirect_uri,
)
from services.auth import (
    AuthError,
    authenticate_user,
    create_user_account,
    csrf_protect,
    current_user,
    get_csrf_token,
    is_api_request,
    login_required,
    login_user,
    logout_user,
)
from services.negative_keyword_service import (
    NegativeKeywordError,
    build_negative_keyword_csv,
    create_rule as create_negative_keyword_rule,
    delete_rule as delete_negative_keyword_rule,
    get_negative_keyword_workspace_state,
    reorder_rules as reorder_negative_keyword_rules,
    save_custom_instructions,
    update_rule as update_negative_keyword_rule,
)
from services.ownership import build_owner_context, extract_authenticated_user_id
from services.production_diagnostics import (
    DEV_FALLBACK_SECRET_KEY,
    apply_runtime_settings,
    is_debug_environment,
    log_startup_configuration_summary,
)
from services.report_health import (
    PLATFORM_ORDER,
    SUPPORTED_TARGET_COUNTRIES,
    evaluate_report_data_health,
    is_valid_platform_result,
)
from services.run_context import (
    build_heatmap_data,
    build_metric_record,
    build_visibility_summary_text,
    display_total_checks_value,
    load_run_analysis_context,
)
from services.tracker_interpretation import generate_tracker_interpretation
from services.tracker_providers import (
    combine_keywords,
    collect_tracker_provider_bundle,
    normalize_competitor_domains,
    normalize_domain,
)
from services.dataforseo_client import (
    build_dataforseo_provider_payload,
    build_skipped_dataforseo_payload,
    verify_dataforseo_credentials,
)
from agents.runtime_config import get_env_value
from agents.agent_manager import (
    CONTENT_GROUP,
    PPC_GROUP,
    SEO_GROUP,
    SOCIAL_GROUP,
    get_all_agents,
    get_agents_by_group,
    get_agent_metadata,
    run_agent,
)
from agents.negative_keyword import REPORT_DIR as NEGATIVE_KEYWORD_REPORT_DIR, is_safe_report_filename
from agents.negative_keyword import NegativeKeywordAgent

app = Flask(__name__)
configured_secret = get_env_value("FLASK_SECRET_KEY")
if configured_secret:
    app.secret_key = configured_secret
else:
    app.secret_key = DEV_FALLBACK_SECRET_KEY
    logging_message = "FLASK_SECRET_KEY is missing; using development fallback secret key."
    if is_debug_environment():
        app.logger.warning(logging_message)
    else:
        app.logger.error(logging_message)
apply_runtime_settings(app)
init_db()
log_startup_configuration_summary()
log_speaker_detection_diagnostics()
log_google_ads_redirect_uri("google_ads_startup")

# Thread-safe tracker cancellation map
cancelled_runs = set()

def extract_domain(url):
    """Utility to clean and parse domain name from a URL."""
    try:
        netloc = urlparse(url).netloc
        if netloc.startswith("www."):
            netloc = netloc[4:]
        return netloc.lower()
    except Exception:
        return ""

def generate_report_content(run_id):
    """
    Utility to fetch all dashboard statistics and build templates arguments.
    Reused by Dashboard view, Report export download, and SMTP email attachment.
    """
    auth_user = current_user() if has_request_context() else None
    user_id = int(auth_user["id"]) if auth_user else None
    run_context = load_run_analysis_context(run_id, user_id=user_id)
    if not run_context:
        return None

    run = run_context["run"]
    results = run_context["results"]
    metrics = run_context["metrics"]
    report_health = {
        **run_context["report_health"],
        "platform_summaries": run_context["platform_summaries"],
    }
    valid_results = run_context["valid_results"]
    total_checks = run_context["total_checks"]
    brand_mentions_metric = run_context["brand_mentions_metric"]
    share_of_voice_metric = run_context["share_of_voice_metric"]
    api_health_metric = run_context["api_health_metric"]

    keywords = sorted(list(set(r["keyword"] for r in results))) or run.get("keywords", [])
    dataforseo_status = run_context["provider_provenance"]["dataforseo"]["status"]
    heatmap_data = build_heatmap_data(results, keywords, dataforseo_status=dataforseo_status)

    platform_breakdown = {plat: 0 for plat in PLATFORM_ORDER}
    for r in valid_results:
        if r.get("mentioned") and r.get("platform") in platform_breakdown:
            platform_breakdown[r["platform"]] += 1

    competitor_domains = [m["domain"] for m in metrics if m["domain"].lower() != run["brand_domain"].lower()]
    trend_data = get_trend_data(run["brand_domain"], competitor_domains)
    domain_counts = {}
    for r in valid_results:
        for url in r.get("sources_cited", []):
            dom = extract_domain(url)
            if dom:
                domain_counts[dom] = domain_counts.get(dom, 0) + 1
    top_domains = [{"domain": dom, "count": count} for dom, count in sorted(domain_counts.items(), key=lambda x: x[1], reverse=True)]

    top_competitor_name = "None"
    top_competitor_mentions = None
    top_comp = run_context.get("top_competitor")
    if top_comp and top_comp.get("total_mentions", 0) > 0:
        top_competitor_name = top_comp["domain"]
        top_competitor_mentions = top_comp["total_mentions"]

    return {
        "run": run,
        "run_id": int(run["id"]),
        "results": results,
        "metrics": metrics,
        "keywords": keywords,
        "heatmap_data": heatmap_data,
        "platform_breakdown": platform_breakdown,
        "trend_data": trend_data,
        "top_domains": top_domains,
        "stat_total_checks": display_total_checks_value(total_checks, dataforseo_status=dataforseo_status),
        "stat_brand_mentions": brand_mentions_metric["value"],
        "stat_brand_sov": share_of_voice_metric["value"],
        "stat_api_health": api_health_metric["value"],
        "metric_provenance": {
            "brand_mentions": brand_mentions_metric,
            "share_of_voice": share_of_voice_metric,
            "api_health": api_health_metric,
            **run_context["provider_metrics"],
            "top_competitor_mentions": build_metric_record(
                top_competitor_mentions,
                source="database",
                run_id=int(run["id"]),
                reason="No competitor mentions were recorded for this run." if top_competitor_mentions is None else None,
                collected_at=run_context["collected_at"],
            ),
        },
        "report_health": report_health,
        "report_mode": run_context["report_mode"],
        "valid_results": valid_results,
        "keyword_groups": {
            "high_volume_keywords": run.get("high_volume_keywords", []),
            "brand_keywords": run.get("brand_keywords", []),
        },
        "openai_interpretation": run_context.get("openai_interpretation"),
        "top_competitor_name": top_competitor_name,
        "top_competitor_mentions": top_competitor_mentions,
        "visibility_summary_text": build_visibility_summary_text(
            run,
            report_mode=run_context["report_mode"],
            dataforseo_status=dataforseo_status,
        ),
        "source_provenance": {
            **run_context["provider_provenance"],
        },
    }


def resolve_requested_run_id(*, allow_session_default: bool = True) -> int | None:
    explicit_run_id = request.args.get("run_id", type=int)
    if explicit_run_id is not None:
        return explicit_run_id
    if allow_session_default:
        session_run_id = session.get("last_run_id")
        try:
            return int(session_run_id) if session_run_id is not None else None
        except (TypeError, ValueError):
            return None
    return None


def get_current_user_id() -> int | None:
    user = current_user()
    if not user:
        return None
    return int(user["id"])


def build_dashboard_not_found_context(requested_run_id: int | None) -> dict:
    grouped_agents = get_all_agents()
    return {
        "run_not_found": True,
        "requested_run_id": requested_run_id,
        "run": {
            "id": requested_run_id,
            "brand_name": "Run not found",
            "brand_domain": "Data Unavailable",
            "run_date": "Data Unavailable",
            "country": "Data Unavailable",
            "language": "Data Unavailable",
        },
        "results": [],
        "metrics": [],
        "keywords": [],
        "heatmap_data": {},
        "platform_breakdown": {},
        "trend_data": [],
        "top_domains": [],
        "stat_total_checks": 0,
        "stat_brand_mentions": None,
        "stat_brand_sov": None,
        "stat_api_health": None,
        "metric_provenance": {
            "crawl_pages_crawled": {"value": None},
            "pagespeed_performance_score": {"value": None},
            "crux_lcp_ms": {"value": None},
        },
        "source_provenance": {
            "dataforseo": {
                "enabled": None,
                "status": "unavailable",
                "source": "dataforseo",
                "reason": "The requested run was not found.",
                "run_id": requested_run_id,
                "collected_at": None,
            },
            "database": {
                "enabled": True,
                "status": "unavailable",
                "source": "database",
                "reason": "The requested run was not found.",
                "run_id": requested_run_id,
                "collected_at": None,
            },
        },
        "report_health": {"platform_summaries": [], "report_mode": "technical_failure"},
        "report_mode": "technical_failure",
        "valid_results": [],
        "openai_interpretation": None,
        "top_competitor_name": "None",
        "top_competitor_mentions": None,
        "email_enabled": False,
        "agent_counts": {
            "SEO": len(grouped_agents.get(SEO_GROUP, [])),
            "PPC": len(grouped_agents.get(PPC_GROUP, [])),
            "Content": len(grouped_agents.get(CONTENT_GROUP, [])),
            "Social": len(grouped_agents.get(SOCIAL_GROUP, [])),
        },
    }

# ================= Routes =================

@app.route("/")
def index():
    """Default landing routing logic."""
    if session.get("last_run_id"):
        return redirect(url_for("dashboard"))
    return redirect(url_for("setup"))

@app.route("/setup")
@login_required
def setup():
    """Setup form screen."""
    return render_template("setup.html")


@app.route("/favicon.ico")
def favicon():
    """Prevent browser favicon requests from generating noisy 404 logs."""
    return Response(status=204)


def youtube_transcript_error_response(error):
    """Return user-safe JSON errors for the YouTube transcript feature."""
    return jsonify({
        "success": False,
        "status": "error",
        "error": getattr(error, "error_code", "transcript_error"),
        "message": str(error),
    }), getattr(error, "status_code", 400)


def google_ads_error_response(error):
    return jsonify({
        "success": False,
        "status": "error",
        "error": getattr(error, "error_code", "google_ads_error"),
        "message": str(error),
    }), getattr(error, "status_code", 400)


def negative_keyword_error_response(error):
    return jsonify({
        "success": False,
        "status": "error",
        "error": "negative_keyword_error",
        "message": str(error),
    }), getattr(error, "status_code", 400)


def json_error_response(message: str, status_code: int, error_code: str):
    return jsonify({
        "ok": False,
        "success": False,
        "error": message,
        "error_code": error_code,
        "message": message,
    }), status_code


def request_prefers_json_errors() -> bool:
    return is_api_request()


@app.errorhandler(404)
def handle_not_found(error):
    if request_prefers_json_errors():
        return json_error_response("Endpoint not found", 404, "not_found")
    return error


@app.errorhandler(405)
def handle_method_not_allowed(error):
    if request_prefers_json_errors():
        return json_error_response("Method not allowed", 405, "method_not_allowed")
    return error


@app.errorhandler(Exception)
def handle_unexpected_exception(error):
    if isinstance(error, HTTPException):
        if not request_prefers_json_errors():
            return error
        status_code = error.code or 500
        message = error.description or HTTPStatus(status_code).phrase
        return json_error_response(message, status_code, error.name.lower().replace(" ", "_"))
    if not request_prefers_json_errors():
        app.logger.exception("Unhandled page exception on %s", request.path)
        return "Internal Server Error", 500
    app.logger.exception("Unhandled API exception on %s", request.path)
    return json_error_response("Server error", 500, "server_error")


def get_browser_session_id():
    browser_session_id = session.get("browser_session_id")
    if not browser_session_id:
        browser_session_id = uuid.uuid4().hex
        session["browser_session_id"] = browser_session_id
    return browser_session_id


def allow_dev_session_ownership():
    return is_development_mode() and get_env_value("AUDILYSIS_ALLOW_DEV_SESSION_OWNERSHIP").lower() in {"1", "true", "yes", "on"}


def get_owner_context():
    auth_user_id = extract_authenticated_user_id(session)
    return build_owner_context(auth_user_id, get_browser_session_id(), allow_dev_session_ownership())


def is_development_mode():
    if app.debug or app.testing:
        return True
    return get_env_value("FLASK_DEBUG").lower() in {"1", "true", "yes", "on"}


def build_ownership_payload(owner):
    return {
        "user_id": owner.user_id,
        "owner_type": owner.owner_type,
        "secure_auth": owner.secure_auth,
        "development_mode": is_development_mode(),
        "show_session_warning": allow_dev_session_ownership() and not owner.secure_auth,
    }


@app.context_processor
def inject_template_auth_state():
    return {
        "current_user": current_user(),
        "csrf_token": get_csrf_token(),
    }


@app.route("/register", methods=["GET", "POST"])
@csrf_protect
def register():
    if current_user():
        return redirect(url_for("dashboard"))
    error = ""
    if request.method == "POST":
        email = request.form.get("email", "")
        password = request.form.get("password", "")
        confirm_password = request.form.get("confirm_password", "")
        if password != confirm_password:
            error = "Passwords do not match."
        else:
            try:
                user = create_user_account(email, password)
                login_user(user)
                next_url = request.args.get("next") or url_for("dashboard")
                return redirect(next_url)
            except AuthError as exc:
                error = str(exc)
    return render_template("register.html", error=error)


@app.route("/login", methods=["GET", "POST"])
@csrf_protect
def login():
    if current_user():
        return redirect(url_for("dashboard"))
    error = ""
    if request.method == "POST":
        try:
            user = authenticate_user(request.form.get("email", ""), request.form.get("password", ""))
            login_user(user)
            next_url = request.args.get("next") or url_for("dashboard")
            return redirect(next_url)
        except AuthError as exc:
            error = str(exc)
    return render_template("login.html", error=error)


@app.route("/logout", methods=["POST"])
@login_required
@csrf_protect
def logout():
    logout_user()
    return redirect(url_for("login"))

@app.route("/api/run", methods=["POST"])
@login_required
@csrf_protect
def api_run():
    """Handles configuration submission and saves settings inside Flask session."""
    data = request.json
    if not data:
        return jsonify({"status": "error", "message": "Missing payload."}), 400
        
    credentials = data.get("credentials")
    config = dict(data.get("config") or {})
    email_settings = data.get("email_settings")
    
    # Validations
    if not config or not config.get("brand_domain") or not config.get("brand_name"):
        return jsonify({"status": "error", "message": "Brand domain and brand name are required."}), 400
    high_volume_keywords = [item.strip() for item in (config.get("high_volume_keywords") or []) if str(item).strip()]
    brand_keywords = [item.strip() for item in (config.get("brand_keywords") or []) if str(item).strip()]
    fallback_keywords = [item.strip() for item in (config.get("keywords") or []) if str(item).strip()]
    combined_keywords = combine_keywords(
        high_volume_keywords or fallback_keywords,
        brand_keywords if high_volume_keywords or brand_keywords else [],
    )
    if not combined_keywords:
        return jsonify({"status": "error", "message": "At least one keyword is required."}), 400
    if len(combined_keywords) < 3:
        return jsonify({"status": "error", "message": "Provide at least 3 keywords for adequate report coverage."}), 400
    if config.get("country") not in SUPPORTED_TARGET_COUNTRIES:
        return jsonify({"status": "error", "message": "Select a supported target country."}), 400
    brand_domain = normalize_domain(config.get("brand_domain") or "")
    if not brand_domain:
        return jsonify({"status": "error", "message": "Enter a valid brand domain."}), 400
    normalized_competitors = normalize_competitor_domains(config.get("competitors") or [])
    normalized_competitors = [domain for domain in normalized_competitors if domain != brand_domain]
    config["brand_domain"] = brand_domain
    config["competitors"] = normalized_competitors
    config["high_volume_keywords"] = high_volume_keywords
    config["brand_keywords"] = brand_keywords
    config["keywords"] = combined_keywords
    use_dataforseo = bool(config.get("use_dataforseo", True))
    if use_dataforseo and (not credentials or not credentials.get("login") or not credentials.get("password")):
        return jsonify({"status": "error", "message": "DataForSEO credentials are required when DataForSEO is enabled."}), 400
    if not use_dataforseo:
        credentials = {"login": "", "password": ""}
    email_settings = email_settings or {}
    if email_settings.get("email_automatically"):
        required_email_fields = {
            "smtp_host": "SMTP host",
            "smtp_port": "SMTP port",
            "sender_email": "sender email",
            "sender_password": "sender app password",
            "recipient_emails": "recipient email(s)",
        }
        missing = [label for key, label in required_email_fields.items() if not str(email_settings.get(key) or "").strip()]
        if missing:
            return jsonify({"status": "error", "message": f"Automatic email requires: {', '.join(missing)}."}), 400
        
    # Store settings in flask session
    session["credentials"] = credentials
    session["tracker_config"] = config
    session["email_settings"] = email_settings
    
    # Assign unique session ID to manage thread cancel hooks
    session["session_run_id"] = str(uuid.uuid4())
    
    return jsonify({"status": "success"})

@app.route("/running")
@login_required
def running():
    """Progress screen rendering."""
    # Safety checks
    if "tracker_config" not in session:
        return redirect(url_for("setup"))
    return render_template("running.html")

@app.route("/api/cancel", methods=["POST"])
@login_required
@csrf_protect
def api_cancel():
    """Sets current session identifier as cancelled."""
    run_sid = session.get("session_run_id")
    if run_sid:
        cancelled_runs.add(run_sid)
    return jsonify({"status": "cancelled"})

@app.route("/stream")
@login_required
def stream():
    """SSE streaming endpoint."""
    config = session.get("tracker_config")
    creds = session.get("credentials")
    email_cfg = session.get("email_settings")
    session_run_id = session.get("session_run_id")
    
    if not config or session_run_id is None:
        # Yield single error and exit
        def err_gen():
            yield f"data: {json.dumps({'status': 'error', 'error_message': 'Session expired or configuration missing.'})}\n\n"
        return Response(err_gen(), mimetype='text/event-stream')
        
    # Create the run in DB prior to streaming
    brand_domain = config.get("brand_domain")
    brand_name = config.get("brand_name")
    country = config.get("country", "India")
    language = config.get("language", "en")
    competitor_domains = config.get("competitors", [])
    keywords = config.get("keywords", [])
    high_volume_keywords = config.get("high_volume_keywords", [])
    brand_keywords = config.get("brand_keywords", [])
    use_dataforseo = bool(config.get("use_dataforseo", True))
    
    run_id = create_run(
        brand_domain,
        brand_name,
        country,
        language,
        competitor_domains,
        use_dataforseo=use_dataforseo,
        high_volume_keywords=high_volume_keywords,
        brand_keywords=brand_keywords,
        user_id=get_current_user_id(),
    )
    session["last_run_id"] = run_id
    
    # Outer generator execution scope passing isolated variables
    def generate(config, creds, email_cfg, run_id, session_run_id):
        provider_plan = [
            ("crawl", "Collecting crawl data..."),
            ("pagespeed", "Collecting PageSpeed data..."),
            ("crux", "Collecting Chrome UX Report data..."),
        ]
        platforms = ["google", "chat_gpt", "perplexity", "gemini", "claude"] if use_dataforseo else []
        verification_steps = 1
        total_steps = len(provider_plan) + verification_steps + (len(keywords) * len(platforms))
        current_step = 0
        
        # Clear cancellations list for this new thread execution if present
        if session_run_id in cancelled_runs:
            cancelled_runs.remove(session_run_id)
            
        website_url = f"https://{brand_domain}"
        provider_results = collect_tracker_provider_bundle(website_url)
        for index, provider_result in enumerate(provider_results, start=1):
            current_step += 1
            progress = (current_step / total_steps) * 100 if total_steps else 100
            provider = provider_result["provider"]
            upsert_run_provider_result(
                run_id,
                provider,
                provider_result["status"],
                payload=provider_result.get("payload"),
                reason=provider_result.get("reason"),
            )
            status_text = provider_result["status"].replace("_", " ")
            reason_text = provider_result.get("reason")
            message = f"[SOURCE] {provider.title()} → {status_text}"
            if reason_text:
                message += f" ({reason_text})"
            yield f"data: {json.dumps({'progress': progress, 'current_step': current_step, 'total_steps': total_steps, 'message': message, 'status': 'running'})}\n\n"

        current_step += 1
        progress = (current_step / total_steps) * 100 if total_steps else 100
        dataforseo_enabled_message = "[SOURCE] DataForSEO verification → "
        dataforseo_ready = use_dataforseo
        if not use_dataforseo:
            upsert_run_provider_result(
                run_id,
                "dataforseo",
                "skipped_by_user",
                payload=build_skipped_dataforseo_payload(run_id=run_id),
                reason="DataForSEO was intentionally disabled for this run.",
            )
            yield f"data: {json.dumps({'progress': progress, 'current_step': current_step, 'total_steps': total_steps, 'message': dataforseo_enabled_message + 'skipped by user', 'status': 'running'})}\n\n"
        else:
            verification_keyword = keywords[0] if keywords else brand_name
            verification = verify_dataforseo_credentials(
                creds,
                keyword=verification_keyword,
                country=country,
                language=language,
                run_id=run_id,
            )
            verified_status = verification["status"]
            verified_reason = verification["message"]
            upsert_run_provider_result(
                run_id,
                "dataforseo",
                verified_status,
                payload=verification.get("provider_payload"),
                reason=verified_reason,
            )
            if verification["connected"]:
                yield f"data: {json.dumps({'progress': progress, 'current_step': current_step, 'total_steps': total_steps, 'message': dataforseo_enabled_message + 'connected', 'status': 'running'})}\n\n"
            else:
                dataforseo_ready = False
                yield f"data: {json.dumps({'progress': progress, 'current_step': current_step, 'total_steps': total_steps, 'message': dataforseo_enabled_message + verified_status.replace('_', ' '), 'status': 'running'})}\n\n"

        if not use_dataforseo:
            progress = (current_step / total_steps) * 100 if total_steps else 100
            yield f"data: {json.dumps({'progress': progress, 'current_step': current_step, 'total_steps': total_steps, 'message': '[SYSTEM] DataForSEO was disabled for this run. Skipping AI visibility collection and continuing with other real providers.', 'status': 'running'})}\n\n"
        elif not dataforseo_ready:
            progress = (current_step / total_steps) * 100 if total_steps else 100
            yield f"data: {json.dumps({'progress': progress, 'current_step': current_step, 'total_steps': total_steps, 'message': '[SYSTEM] DataForSEO authentication failed or the provider was unavailable. Skipping DataForSEO-dependent analysis and continuing with other real providers.', 'status': 'running'})}\n\n"

        active_platforms = platforms if dataforseo_ready else []
        for keyword in keywords:
            for platform in active_platforms:
                # Polling Cancel Check
                if session_run_id in cancelled_runs:
                    yield f"data: {json.dumps({'progress': 100, 'current_step': current_step, 'total_steps': total_steps, 'message': '[SYSTEM] Tracker run aborted by user.', 'status': 'error', 'error_message': 'User cancelled run.'})}\n\n"
                    return
                    
                current_step += 1
                progress = (current_step / total_steps) * 100
                
                # Fetch platform name mapping
                platform_names = {
                    "google": "Google AI Mode",
                    "chat_gpt": "ChatGPT",
                    "perplexity": "Perplexity",
                    "gemini": "Gemini",
                    "claude": "Claude"
                }
                p_name = platform_names.get(platform, platform)
                log_message = f'[{current_step}/{total_steps}] Checking "{keyword}" on {p_name}...'
                yield f"data: {json.dumps({'progress': progress, 'current_step': current_step, 'total_steps': total_steps, 'message': log_message, 'status': 'running'})}\n\n"
                
                # Run the API query
                platform_result = query_platform(
                    platform, keyword, creds, brand_domain, brand_name, competitor_domains, country, language
                )
                response_text = platform_result.get("text", "")
                sources = platform_result.get("sources_cited", [])
                mentioned = None
                mention_position = None
                competitor_mentions = {}

                if platform_result.get("has_valid_data"):
                    mentioned = (brand_domain.lower() in response_text.lower()) or (brand_name.lower() in response_text.lower())
                    if mentioned:
                        lines = response_text.split('\n')
                        positions = [idx + 1 for idx, l in enumerate(lines) if brand_domain.lower() in l.lower() or brand_name.lower() in l.lower()]
                        if positions:
                            mention_position = int(round(sum(positions) / len(positions)))
                    for comp in competitor_domains:
                        competitor_mentions[comp] = (comp.lower() in response_text.lower())
                    status_lbl = "✓ Mentioned" if mentioned else "✗ Not Mentioned"
                    pos_lbl = f" (position {mention_position})" if (mentioned and mention_position) else ""
                    log_line = f'[{current_step}/{total_steps}] "{keyword}" → {p_name}... {status_lbl}{pos_lbl}'
                    app.logger.info(
                        "platform_response_validated platform=%s keyword=%s status=success valid=true",
                        platform,
                        keyword,
                    )
                else:
                    log_line = f'[{current_step}/{total_steps}] "{keyword}" → {p_name}... ❌ {platform_result.get("error_message")}'
                    app.logger.warning(
                        "platform_response_validated platform=%s keyword=%s status=%s valid=false",
                        platform,
                        keyword,
                        platform_result.get("response_status"),
                    )

                insert_mention_result(
                    run_id,
                    keyword,
                    platform,
                    mentioned,
                    mention_position,
                    sources,
                    competitor_mentions,
                    response_text,
                    response_status=platform_result.get("response_status"),
                    error_category=platform_result.get("error_category"),
                    error_message=platform_result.get("error_message"),
                    has_valid_data=platform_result.get("has_valid_data"),
                    retry_recommendation=platform_result.get("retry_recommendation"),
                )
                
                yield f"data: {json.dumps({'progress': progress, 'current_step': current_step, 'total_steps': total_steps, 'message': log_line, 'status': 'running'})}\n\n"
                
        # --- Run Complete Post-Processing ---
        # Calculate competitor SOV metrics locally
        results = get_mention_results(run_id)
        valid_results = [row for row in results if row.get("has_valid_data")]
        report_health = evaluate_report_data_health(results)
        app.logger.info(
            "report_mode_selected run_id=%s mode=%s success_rate=%s successful=%s failed=%s",
            run_id,
            report_health["report_mode"],
            report_health["success_rate"],
            report_health["successful_platforms"],
            report_health["failed_platforms"],
        )

        domains_to_track = [brand_domain.lower()] + [c.lower() for c in competitor_domains]
        domain_mentions = {d: 0 for d in domains_to_track}
        domain_positions = {d: [] for d in domains_to_track}
        valid_total_checks = len(valid_results)

        if use_dataforseo and dataforseo_ready:
            if valid_total_checks > 0:
                dataforseo_final_status = "success"
                dataforseo_final_reason = None
            else:
                dataforseo_final_status = "failed"
                dataforseo_final_reason = "DataForSEO credentials were verified, but no valid provider responses were collected for this run."
            upsert_run_provider_result(
                run_id,
                "dataforseo",
                dataforseo_final_status,
                payload=build_dataforseo_provider_payload(
                    enabled=True,
                    status=dataforseo_final_status,
                    authentication="verified",
                    endpoint="tracker_visibility",
                    run_id=run_id,
                ),
                reason=dataforseo_final_reason,
            )

        for res in valid_results:
            text = res.get("ai_response_text")
            if not text:
                continue
            if brand_domain.lower() in text.lower() or brand_name.lower() in text.lower():
                domain_mentions[brand_domain.lower()] += 1
                pos = res.get("mention_position")
                if pos is not None:
                    domain_positions[brand_domain.lower()].append(pos)
            for comp in competitor_domains:
                if comp.lower() in text.lower():
                    domain_mentions[comp.lower()] += 1
                    lines = text.split('\n')
                    comp_positions = [idx + 1 for idx, l in enumerate(lines) if comp.lower() in l.lower()]
                    if comp_positions:
                        avg_p = sum(comp_positions) / len(comp_positions)
                        domain_positions[comp.lower()].append(avg_p)
                        
        # Store competitor metrics
        if valid_total_checks > 0:
            for dom in domains_to_track:
                mentions_count = domain_mentions[dom]
                avg_pos = sum(domain_positions[dom]) / len(domain_positions[dom]) if domain_positions[dom] else None
                sov = round((mentions_count / valid_total_checks * 100), 1)
                insert_competitor_metrics(run_id, dom, mentions_count, avg_pos, sov)

        report_data = generate_report_content(run_id)
        interpretation = generate_tracker_interpretation(report_data) if report_data else {
            "provider": "openai",
            "status": "failed",
            "reason": "Report data could not be generated for interpretation.",
            "role": "interpretation_only",
            "payload": None,
        }
        upsert_run_provider_result(
            run_id,
            "openai",
            interpretation["status"],
            payload=interpretation.get("payload"),
            reason=interpretation.get("reason"),
        )

        # Handle automatic emailing if configured
        if email_cfg and email_cfg.get("email_automatically"):
            recipient_list = email_cfg.get('recipient_emails', '')
            yield f"data: {json.dumps({'progress': 100, 'current_step': total_steps, 'total_steps': total_steps, 'message': f'[SMTP] Preparing report email to {recipient_list}...', 'status': 'running'})}\n\n"
            
            # Fetch data & build report
            report_data = generate_report_content(run_id)
            if report_data:
                try:
                    # Generate dynamic PDF
                    report_pdf = generate_pdf_report(run_id)
                    if not report_pdf:
                        raise ValueError("PDF generation returned empty bytes.")
                    
                    # Create email subject and bodies
                    date_str = datetime.now().strftime("%Y-%m-%d")
                    subject = f"Audilysis 2.0 — {brand_name} AI Mention Tracking Report — {date_str}"
                    
                    body_html = f"""
                    <html>
                    <body style="font-family: sans-serif; color: #334155; line-height: 1.5; padding: 20px;">
                        <h2 style="color: #1e1a4f; margin-bottom: 4px;">Audilysis 2.0</h2>
                        <p style="font-size: 14px; color: #64748b; margin-top: 0;">AI Mention Tracking Summary</p>
                        <hr style="border: 0; border-top: 1px solid #e2e8f0; margin: 20px 0;">
                        <table style="width: 100%; max-width: 500px; border-collapse: collapse;">
                            <tr>
                                <td style="padding: 8px 0; font-weight: bold; color: #475569;">Brand Name:</td>
                                <td style="padding: 8px 0; text-align: right;">{brand_name} ({brand_domain})</td>
                            </tr>
                            <tr>
                                <td style="padding: 8px 0; font-weight: bold; color: #475569;">Mention Rate (SOV):</td>
                                <td style="padding: 8px 0; text-align: right; color: #10b981; font-weight: bold;">{"Data Unavailable" if report_data['stat_brand_sov'] is None else str(report_data['stat_brand_sov']) + "%"}</td>
                            </tr>
                            <tr>
                                <td style="padding: 8px 0; font-weight: bold; color: #475569;">Total Checks:</td>
                                <td style="padding: 8px 0; text-align: right;">{"Requires DataForSEO" if report_data['source_provenance']['dataforseo']['status'] == 'skipped_by_user' and report_data['stat_total_checks'] is None else ("Data Unavailable" if report_data['stat_total_checks'] is None else report_data['stat_total_checks'])}</td>
                            </tr>
                            <tr>
                                <td style="padding: 8px 0; font-weight: bold; color: #475569;">Top Competitor:</td>
                                <td style="padding: 8px 0; text-align: right;">{report_data['top_competitor_name']} ({"Data Unavailable" if report_data['top_competitor_mentions'] is None else str(report_data['top_competitor_mentions']) + " mentions"})</td>
                            </tr>
                        </table>
                        <p style="margin-top: 18px; font-size: 13px; color: #475569;">{report_data['visibility_summary_text']}</p>
                        <p style="margin-top: 24px; font-size: 14px;">The complete interactive PDF report is attached to this email. You can open and view it in any PDF reader.</p>
                    </body>
                    </html>
                    """
                    filename = f"Audilysis-2.0-AI-Mention-Report-{brand_domain}-{date_str}.pdf"
                    
                    # Send
                    to_str, mail_err = send_report_email(
                        email_cfg["smtp_host"],
                        email_cfg["smtp_port"],
                        email_cfg["sender_email"],
                        email_cfg["sender_password"],
                        email_cfg["recipient_emails"],
                        subject,
                        body_html,
                        report_pdf,
                        filename
                    )
                    
                    if mail_err:
                        yield f"data: {json.dumps({'progress': 100, 'current_step': total_steps, 'total_steps': total_steps, 'message': f'[SMTP] ❌ Auto-email failed: {mail_err}', 'status': 'running'})}\n\n"
                    else:
                        yield f"data: {json.dumps({'progress': 100, 'current_step': total_steps, 'total_steps': total_steps, 'message': f'[SMTP] ✓ Auto-email sent successfully to {to_str}', 'status': 'running'})}\n\n"
                except Exception as e:
                    yield f"data: {json.dumps({'progress': 100, 'current_step': total_steps, 'total_steps': total_steps, 'message': f'[SMTP] ❌ Auto-email render error: {str(e)}', 'status': 'running'})}\n\n"
                    
        # Redirect URL
        yield f"data: {json.dumps({'progress': 100, 'current_step': total_steps, 'total_steps': total_steps, 'message': '[SYSTEM] Redirecting to Dashboard...', 'status': 'completed', 'redirect_url': '/dashboard'})}\n\n"

    return Response(generate(config, creds, email_cfg, run_id, session_run_id), mimetype='text/event-stream')

@app.route("/agents")
@login_required
def agents_page():
    """SEO agent studio UI."""
    selected_agent = (request.args.get("agent") or "").strip()
    selected_run_id = request.args.get("run_id", type=int)
    selected_run = get_run_for_user(selected_run_id, get_current_user_id()) if selected_run_id else None
    if selected_agent == "negative_keyword":
        agent = get_agent_metadata("negative_keyword")
        if not agent:
            return redirect(url_for("agents_page"))
        return render_template(
            "agents.html",
            agents=[agent],
            standalone_agent=agent,
            selected_run=selected_run,
            selected_run_id=selected_run_id,
            run_not_found=bool(selected_run_id and not selected_run),
        )
    return render_template(
        "agents.html",
        agents=get_agents_by_group(SEO_GROUP),
        standalone_agent=None,
        selected_run=selected_run,
        selected_run_id=selected_run_id,
        run_not_found=bool(selected_run_id and not selected_run),
    )


@app.route("/content-agents")
@login_required
def content_agents_page():
    """Content marketing agent studio UI."""
    selected_run_id = request.args.get("run_id", type=int)
    selected_run = get_run_for_user(selected_run_id, get_current_user_id()) if selected_run_id else None
    return render_template(
        "content_agents.html",
        agents=get_agents_by_group(CONTENT_GROUP),
        selected_run=selected_run,
        selected_run_id=selected_run_id,
        run_not_found=bool(selected_run_id and not selected_run),
    )


@app.route("/social-agents")
@login_required
def social_agents_page():
    """Social media agent studio UI."""
    selected_run_id = request.args.get("run_id", type=int)
    selected_run = get_run_for_user(selected_run_id, get_current_user_id()) if selected_run_id else None
    return render_template(
        "social_agents.html",
        agents=get_agents_by_group(SOCIAL_GROUP),
        selected_run=selected_run,
        selected_run_id=selected_run_id,
        run_not_found=bool(selected_run_id and not selected_run),
    )


@app.route("/youtube-multilingual-transcripter")
@login_required
def youtube_multilingual_transcripter_page():
    """YouTube multilingual transcript tool UI."""
    return render_template("youtube_multilingual_transcripter.html")


@app.route("/api/youtube-transcript/health")
@login_required
def youtube_transcript_health():
    """Health endpoint for the YouTube transcript feature."""
    return jsonify({
        "success": True,
        "status": "ok",
        "service": "youtube_transcript",
        "youtube_transcript_api_version": get_youtube_transcript_api_version(),
        "translation_provider": "google_cloud_translation",
        "speaker_detection": get_speaker_detection_diagnostics(),
        "audio_transcription": get_audio_transcription_diagnostics(),
        "youtube_proxy": get_proxy_diagnostics(),
    })


@app.route("/api/youtube-transcript/languages")
@login_required
def youtube_transcript_languages():
    """Return backend-controlled supported translation languages."""
    return jsonify({
        "success": True,
        "languages": get_languages(),
    })


@app.route("/api/youtube-transcript/generate", methods=["POST"])
@login_required
@csrf_protect
def youtube_transcript_generate():
    """Fetch a real YouTube transcript and optionally translate it."""
    payload = request.get_json(silent=True) or {}
    try:
        result = fetch_transcript(
            payload.get("url", ""),
            payload.get("target_language", "original"),
            enable_speaker_detection=bool(payload.get("enable_speaker_detection")),
        )
    except YouTubeTranscriptError as error:
        return youtube_transcript_error_response(error)
    return jsonify({
        "success": True,
        "transcript": result,
        "transcript_status": result.get("transcript_status", {"status": "Completed"}),
        "speaker_detection": result.get("speaker_detection", {"status": "Not Run"}),
    })


@app.route("/api/youtube-transcript/translate", methods=["POST"])
@login_required
@csrf_protect
def youtube_transcript_translate():
    """Translate existing normalized transcript segments with Google Translation."""
    payload = request.get_json(silent=True) or {}
    try:
        result = translate_existing_payload(payload)
    except YouTubeTranscriptError as error:
        return youtube_transcript_error_response(error)
    return jsonify({"success": True, "translation": result})


@app.route("/api/youtube-transcript/download/<path:video_id>")
@login_required
def youtube_transcript_download(video_id):
    """Download a validated transcript as TXT, SRT, JSON, or VTT."""
    target_language = request.args.get("lang", "original")
    fmt = request.args.get("format", "txt")
    try:
        content, mimetype, filename = build_download_from_url(video_id, target_language, fmt)
    except YouTubeTranscriptError as error:
        return youtube_transcript_error_response(error)
    return Response(
        content,
        mimetype=mimetype,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'}
    )


@app.route("/integrations/google-ads/connect", methods=["GET"])
@login_required
def google_ads_connect():
    state = uuid.uuid4().hex
    session["google_ads_oauth_state"] = state
    try:
        auth_url = build_google_ads_connect_url(state)
    except GoogleAdsIntegrationError as error:
        return google_ads_error_response(error)
    return redirect(auth_url)


@app.route("/integrations/google-ads/callback")
@login_required
def google_ads_callback():
    expected_state = session.get("google_ads_oauth_state")
    callback_state = request.args.get("state", "")
    if not expected_state or callback_state != expected_state:
        return google_ads_error_response(GoogleAdsIntegrationError(
            "Google Ads OAuth state validation failed.",
            status_code=400,
            error_code="google_ads_invalid_state",
        ))
    try:
        owner = get_owner_context()
        handle_google_ads_oauth_callback(owner.user_id, owner.owner_key, expected_state, request.url)
    except GoogleAdsIntegrationError as error:
        return google_ads_error_response(error)
    finally:
        session.pop("google_ads_oauth_state", None)
    return redirect(url_for("agents_page", agent="negative_keyword", google_ads="connected"))


@app.route("/api/negative-keywords/google-ads/status")
@login_required
def google_ads_status():
    owner = get_owner_context()
    return jsonify({
        "success": True,
        "ownership": build_ownership_payload(owner),
        "google_ads": get_google_ads_status(owner.user_id, owner.owner_key),
    })


@app.route("/api/negative-keywords/google-ads/disconnect", methods=["POST"])
@login_required
@csrf_protect
def google_ads_disconnect():
    owner = get_owner_context()
    return jsonify(disconnect_google_ads(owner.user_id, owner.owner_key))


@app.route("/api/negative-keywords/google-ads/accounts")
@login_required
def google_ads_accounts():
    try:
        owner = get_owner_context()
        accounts = list_google_ads_accounts(owner.user_id, owner.owner_key)
    except GoogleAdsIntegrationError as error:
        return google_ads_error_response(error)
    return jsonify({"success": True, "accounts": accounts})


@app.route("/api/negative-keywords/google-ads/audit")
@login_required
def google_ads_audit():
    owner = get_owner_context()
    return jsonify({"success": True, "audit": list_negative_keyword_audit(owner.user_id, owner.owner_key)})


@app.route("/api/negative-keywords/google-ads/campaigns", methods=["POST"])
@login_required
@csrf_protect
def google_ads_campaigns():
    payload = request.get_json(silent=True) or {}
    try:
        owner = get_owner_context()
        campaigns = fetch_google_ads_campaigns(
            owner.user_id,
            owner.owner_key,
            payload.get("customer_id", ""),
            payload.get("search", ""),
        )
    except GoogleAdsIntegrationError as error:
        return google_ads_error_response(error)
    return jsonify({"success": True, "campaigns": campaigns})


@app.route("/api/negative-keywords/analyse", methods=["POST"])
@login_required
@csrf_protect
def negative_keywords_analyse():
    payload = request.get_json(silent=True) or {}
    owner = get_owner_context()
    try:
        rows, source_metadata = fetch_google_ads_search_terms(
            owner.user_id,
            owner.owner_key,
            payload.get("customer_id", ""),
            payload.get("campaign_ids") or [],
            payload.get("start_date", ""),
            payload.get("end_date", ""),
        )
        agent = NegativeKeywordAgent()
        result = agent.build_response_from_rows(
            rows,
            payload | {"_owner_key": owner.owner_key, "_user_id": owner.user_id},
            source_metadata=source_metadata,
            data_source_code="google_ads_api",
            data_sources=[
                {
                    "name": "Google Ads API",
                    "status": "Connected",
                    "detail": (
                        f"{source_metadata['parsed_rows']} search terms from customer "
                        f"{source_metadata['customer_id']} between {source_metadata['date_start']} and {source_metadata['date_end']}"
                    ),
                }
            ],
            api_used=["Google Ads API", "Audilysis negative keyword rules"],
        )
    except GoogleAdsIntegrationError as error:
        return google_ads_error_response(error)
    return jsonify(result)


@app.route("/api/negative-keywords/google-ads/apply", methods=["POST"])
@login_required
@csrf_protect
def negative_keywords_apply():
    payload = request.get_json(silent=True) or {}
    try:
        owner = get_owner_context()
        result = apply_negative_keywords(
            owner.user_id,
            owner.owner_key,
            payload.get("customer_id", ""),
            payload.get("recommendations") or [],
            bool(payload.get("confirm")),
        )
    except GoogleAdsIntegrationError as error:
        return google_ads_error_response(error)
    return jsonify(result)


@app.route("/api/negative-keywords/rules", methods=["GET", "POST"])
@login_required
@csrf_protect
def negative_keyword_rules():
    owner = get_owner_context()
    try:
        if request.method == "GET":
            return jsonify({"success": True, "ownership": build_ownership_payload(owner), **get_negative_keyword_workspace_state(owner.owner_key, owner.user_id)})
        payload = request.get_json(silent=True) or {}
        return jsonify({"success": True, **create_negative_keyword_rule(owner.owner_key, owner.user_id, payload)})
    except NegativeKeywordError as error:
        return negative_keyword_error_response(error)


@app.route("/api/negative-keywords/rules/<int:rule_id>", methods=["PUT", "DELETE"])
@login_required
@csrf_protect
def negative_keyword_rule_detail(rule_id):
    owner = get_owner_context()
    try:
        if request.method == "DELETE":
            return jsonify({"success": True, **delete_negative_keyword_rule(owner.owner_key, owner.user_id, rule_id)})
        payload = request.get_json(silent=True) or {}
        return jsonify({"success": True, **update_negative_keyword_rule(owner.owner_key, owner.user_id, rule_id, payload)})
    except NegativeKeywordError as error:
        return negative_keyword_error_response(error)


@app.route("/api/negative-keywords/rules/reorder", methods=["POST"])
@login_required
@csrf_protect
def negative_keyword_rule_reorder():
    payload = request.get_json(silent=True) or {}
    try:
        owner = get_owner_context()
        return jsonify({"success": True, **reorder_negative_keyword_rules(owner.owner_key, owner.user_id, payload.get("rule_ids") or [])})
    except NegativeKeywordError as error:
        return negative_keyword_error_response(error)


@app.route("/api/negative-keywords/instructions", methods=["GET", "POST"])
@login_required
@csrf_protect
def negative_keyword_instructions():
    owner = get_owner_context()
    if request.method == "GET":
        return jsonify({"success": True, "ownership": build_ownership_payload(owner), **get_negative_keyword_workspace_state(owner.owner_key, owner.user_id)})
    payload = request.get_json(silent=True) or {}
    return jsonify({"success": True, **save_custom_instructions(owner.owner_key, owner.user_id, payload.get("custom_instructions", ""))})


@app.route("/api/negative-keywords/export/csv", methods=["POST"])
@login_required
@csrf_protect
def negative_keyword_export_csv():
    payload = request.get_json(silent=True) or {}
    rows = payload.get("rows") or []
    csv_content = build_negative_keyword_csv(rows)
    return Response(
        csv_content,
        mimetype="text/csv",
        headers={"Content-Disposition": 'attachment; filename="negative-keywords.csv"'},
    )


@app.route("/seo-reports")
@login_required
def seo_reports_page():
    """SEO reports page showing reporting agents."""
    run_id = resolve_requested_run_id()
    run_context = load_run_analysis_context(run_id, user_id=get_current_user_id()) if run_id else None
    return render_template(
        "seo_reports.html",
        run_context=run_context,
        selected_run_id=run_id,
        run_not_found=bool(run_id and not run_context),
    )


@app.route("/seo-strategy")
@login_required
def seo_strategy_page():
    """SEO strategy page showing the strategy agent."""
    run_id = resolve_requested_run_id()
    run_context = load_run_analysis_context(run_id, user_id=get_current_user_id()) if run_id else None
    return render_template(
        "seo_strategy.html",
        run_context=run_context,
        selected_run_id=run_id,
        run_not_found=bool(run_id and not run_context),
    )


@app.route("/run-agent", methods=["POST"])
@login_required
@csrf_protect
def run_agent_route():
    """Execute a selected SEO, Content, or Social agent and return structured JSON."""
    if request.mimetype == "multipart/form-data":
        payload = request.form.to_dict(flat=True)
        payload["_files"] = {key: request.files[key] for key in request.files}
        for key, file_storage in payload["_files"].items():
            payload[key] = file_storage
    else:
        payload = request.get_json(silent=True) or {}
    owner = get_owner_context()
    payload["_owner_key"] = owner.owner_key
    payload["_user_id"] = owner.user_id
    payload_run_id = payload.get("run_id")
    if payload_run_id not in (None, "", []):
        run_context = load_run_analysis_context(payload_run_id, user_id=get_current_user_id())
        if not run_context:
            return json_error_response("Run not found", 404, "run_not_found")
        run = run_context["run"]
        try:
            run_competitors = json.loads(run["competitors"]) if run.get("competitors") else []
        except (TypeError, ValueError):
            run_competitors = []
        payload["run_id"] = run_context["run_id"]
        payload["_tracker_run_context"] = run_context
        payload.setdefault("website_url", f"https://{run['brand_domain']}")
        payload.setdefault("brand_name", run["brand_name"])
        payload.setdefault("country", run["country"])
        payload.setdefault("language", run["language"])
        payload.setdefault("competitors", run_competitors)
    agent_id = (payload.get("agent") or payload.get("agent_id") or "").strip()
    if not agent_id:
        return jsonify({"success": False, "message": "Agent is required.", "agent": None, "summary": "", "recommendations": [], "data": {}}), 400

    try:
        result = run_agent(agent_id, payload)
    except Exception:
        app.logger.exception("run_agent_route_failed agent=%s", agent_id)
        return json_error_response("Server error", 500, "server_error")
    if result.get("status") == "error" and not result.get("success"):
        return jsonify(result), 400
    return jsonify(result)


@app.route("/download-negative-keyword-report/<path:filename>")
@login_required
def download_negative_keyword_report(filename):
    """Download a previously generated negative keyword Excel report."""
    if not is_safe_report_filename(filename):
        return "Invalid report filename.", 400
    owner = get_owner_context()
    report_record = get_negative_keyword_report(owner.user_id, owner.owner_key, filename)
    if not report_record:
        return "Report not found.", 404
    report_path = (NEGATIVE_KEYWORD_REPORT_DIR / filename).resolve()
    if NEGATIVE_KEYWORD_REPORT_DIR.resolve() not in report_path.parents:
        return "Invalid report path.", 400
    if not report_path.exists():
        return "Report not found.", 404
    return Response(
        report_path.read_bytes(),
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.route("/dashboard")
@login_required
def dashboard():
    """Analytics dashboard main interface view."""
    run_id = resolve_requested_run_id()
    if not run_id:
        return redirect(url_for("setup"))

    report_data = generate_report_content(run_id)
    if not report_data:
        return render_template("dashboard.html", **build_dashboard_not_found_context(run_id))
        
    # Check if SMTP email credentials exist in current session
    email_enabled = False
    email_cfg = session.get("email_settings")
    if email_cfg and email_cfg.get("smtp_host") and email_cfg.get("sender_email") and email_cfg.get("sender_password"):
        email_enabled = True
        
    report_data["email_enabled"] = email_enabled
    grouped_agents = get_all_agents()
    report_data["agent_counts"] = {
        "SEO": len(grouped_agents.get(SEO_GROUP, [])),
        "PPC": len(grouped_agents.get(PPC_GROUP, [])),
        "Content": len(grouped_agents.get(CONTENT_GROUP, [])),
        "Social": len(grouped_agents.get(SOCIAL_GROUP, [])),
    }
    return render_template("dashboard.html", **report_data)

@app.route("/download-report")
@login_required
def download_report():
    """Generates a downloadable offline PDF file."""
    run_id = resolve_requested_run_id()
    if not run_id:
        return "Run not found.", 404

    user_id = get_current_user_id()
    run_data = get_run_for_user(run_id, user_id) if user_id is not None else None
    if not run_data:
        return "Run not found.", 404
        
    # Generate dynamic PDF report bytes
    report_pdf = generate_pdf_report(run_id)
    if not report_pdf:
        return "Failed to generate PDF report.", 500
        
    # Filename
    brand_domain = run_data["brand_domain"]
    date_str = datetime.now().strftime("%Y-%m-%d")
    filename = f"Audilysis-2.0-AI-Mention-Report-{brand_domain}-{date_str}.pdf"
    
    return Response(
        report_pdf,
        mimetype="application/pdf",
        headers={"Content-Disposition": f"attachment;filename={filename}"}
    )

@app.route("/api/email-report", methods=["POST"])
@login_required
@csrf_protect
def api_email_report():
    """AJAX endpoint to email latest report on demand as PDF."""
    data = request.json or {}
    run_id = data.get("run_id") or session.get("last_run_id")
    if not run_id:
        return jsonify({"status": "error", "message": "No run data available to email."}), 404
            
    # Read SMTP configurations from session
    email_cfg = session.get("email_settings")
    if (
        not email_cfg
        or not email_cfg.get("smtp_host")
        or not email_cfg.get("smtp_port")
        or not email_cfg.get("sender_email")
        or not email_cfg.get("sender_password")
        or not email_cfg.get("recipient_emails")
    ):
        return jsonify({"status": "error", "message": "Email settings are missing in current session. Configure SMTP settings in Setup page first."}), 400
        
    report_data = generate_report_content(run_id)
    if not report_data:
        return jsonify({"status": "error", "message": "Run report content could not be found."}), 404
        
    brand_name = report_data["run"]["brand_name"]
    brand_domain = report_data["run"]["brand_domain"]
    
    try:
        # Generate dynamic PDF report
        report_pdf = generate_pdf_report(run_id)
        if not report_pdf:
            return jsonify({"status": "error", "message": "Failed to compile PDF report."}), 500
            
        # Build Subject and Body HTML
        date_str = datetime.now().strftime("%Y-%m-%d")
        subject = f"Audilysis 2.0 — {brand_name} AI Mention Tracking Report — {date_str}"
        
        body_html = f"""
        <html>
        <body style="font-family: sans-serif; color: #334155; line-height: 1.5; padding: 20px;">
            <h2 style="color: #1e1a4f; margin-bottom: 4px;">Audilysis 2.0</h2>
            <p style="font-size: 14px; color: #64748b; margin-top: 0;">AI Mention Tracking Summary</p>
            <hr style="border: 0; border-top: 1px solid #e2e8f0; margin: 20px 0;">
            <table style="width: 100%; max-width: 500px; border-collapse: collapse;">
                <tr>
                    <td style="padding: 8px 0; font-weight: bold; color: #475569;">Brand Name:</td>
                    <td style="padding: 8px 0; text-align: right;">{brand_name} ({brand_domain})</td>
                </tr>
                <tr>
                    <td style="padding: 8px 0; font-weight: bold; color: #475569;">Mention Rate (SOV):</td>
                    <td style="padding: 8px 0; text-align: right; color: #10b981; font-weight: bold;">{"Data Unavailable" if report_data['stat_brand_sov'] is None else str(report_data['stat_brand_sov']) + "%"}</td>
                </tr>
                <tr>
                    <td style="padding: 8px 0; font-weight: bold; color: #475569;">Total Checks:</td>
                    <td style="padding: 8px 0; text-align: right;">{"Requires DataForSEO" if report_data['source_provenance']['dataforseo']['status'] == 'skipped_by_user' and report_data['stat_total_checks'] is None else ("Data Unavailable" if report_data['stat_total_checks'] is None else report_data['stat_total_checks'])}</td>
                </tr>
                <tr>
                    <td style="padding: 8px 0; font-weight: bold; color: #475569;">Top Competitor:</td>
                    <td style="padding: 8px 0; text-align: right;">{report_data['top_competitor_name']} ({"Data Unavailable" if report_data['top_competitor_mentions'] is None else str(report_data['top_competitor_mentions']) + " mentions"})</td>
                </tr>
            </table>
            <p style="margin-top: 18px; font-size: 13px; color: #475569;">{report_data['visibility_summary_text']}</p>
            <p style="margin-top: 24px; font-size: 14px;">Your complete Audilysis 2.0 report is attached as a PDF file. You can view it in any PDF reader.</p>
        </body>
        </html>
        """
        filename = f"Audilysis-2.0-AI-Mention-Report-{brand_domain}-{date_str}.pdf"
        
        # Send mail
        to_str, mail_err = send_report_email(
            email_cfg["smtp_host"],
            email_cfg["smtp_port"],
            email_cfg["sender_email"],
            email_cfg["sender_password"],
            email_cfg["recipient_emails"],
            subject,
            body_html,
            report_pdf,
            filename
        )
        
        if mail_err:
            return jsonify({"status": "error", "message": mail_err}), 500
            
        return jsonify({"status": "sent", "to": to_str})
        
    except Exception:
        app.logger.exception("email_report_route_failed run_id=%s", run_id)
        return jsonify({"status": "error", "message": "Email delivery failed."}), 500

# ================= Startup =================

def find_available_port(start_port=5000, host="127.0.0.1"):
    """Returns the first available local TCP port at or above start_port."""
    port = start_port
    while port < start_port + 100:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                sock.bind((host, port))
                return port
            except OSError:
                port += 1
    raise RuntimeError("Unable to find an open local port for the Flask app.")


def open_browser(port):
    """Opens local Flask app in default browser after server initializes."""
    time.sleep(1.0)
    webbrowser.open(f"http://127.0.0.1:{port}")

if __name__ == "__main__":
    # Initialize SQLite schema
    init_db()
    host = "127.0.0.1"
    port = find_available_port(5000, host)
    
    # Print clean terminal startup banner
    print("\n" + "="*50)
    print(" ✦ Audilysis 2.0 is running!")
    print(f" ✦ Open in your browser: http://{host}:{port}")
    print(" ✦ Press Ctrl+C to stop.")
    print("="*50 + "\n")
    
    # Launch browser window asynchronously
    threading.Thread(target=open_browser, args=(port,), daemon=True).start()
    
    # Start Flask (Local-only binding)
    debug_enabled = get_env_value("FLASK_DEBUG").lower() in {"1", "true", "yes", "on"}
    app.run(host=host, port=port, debug=debug_enabled, use_reloader=False)
