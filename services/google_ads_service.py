import re
import logging
from dataclasses import dataclass
from datetime import date
from urllib.parse import urlparse

from agents.runtime_config import get_env_value
from db.storage import (
    create_negative_keyword_audit,
    delete_google_ads_connection,
    get_google_ads_connection,
    upsert_google_ads_connection,
)
from services.negative_keyword_service import SearchTermRow
from services.production_diagnostics import classify_env_value

GOOGLE_ADS_IMPORT_ERROR = None
GOOGLE_OAUTH_IMPORT_ERROR = None
CRYPTO_IMPORT_ERROR = None

try:
    from google.ads.googleads.client import GoogleAdsClient
    from google.ads.googleads.errors import GoogleAdsException
except ImportError as exc:
    GOOGLE_ADS_IMPORT_ERROR = exc
    GoogleAdsClient = None
    GoogleAdsException = Exception

try:
    from google_auth_oauthlib.flow import Flow
except ImportError as exc:
    GOOGLE_OAUTH_IMPORT_ERROR = exc
    Flow = None

try:
    from cryptography.fernet import Fernet, InvalidToken
except ImportError as exc:
    CRYPTO_IMPORT_ERROR = exc
    Fernet = None

    class InvalidToken(Exception):
        pass


SCOPES = [
    "https://www.googleapis.com/auth/adwords",
]
GAQL_VERSION = "v24"
CUSTOMER_ID_PATTERN = re.compile(r"^\d{10}$")
logger = logging.getLogger(__name__)


class GoogleAdsIntegrationError(Exception):
    def __init__(self, message: str, status_code: int = 400, error_code: str = "google_ads_error"):
        super().__init__(message)
        self.status_code = status_code
        self.error_code = error_code


@dataclass
class GoogleAdsConfig:
    developer_token: str
    client_id: str
    client_secret: str
    redirect_uri: str
    encryption_key: str


def get_google_ads_config() -> GoogleAdsConfig:
    return GoogleAdsConfig(
        developer_token=get_env_value("GOOGLE_ADS_DEVELOPER_TOKEN"),
        client_id=get_env_value("GOOGLE_ADS_CLIENT_ID"),
        client_secret=get_env_value("GOOGLE_ADS_CLIENT_SECRET"),
        redirect_uri=get_env_value("GOOGLE_ADS_REDIRECT_URI"),
        encryption_key=get_env_value("GOOGLE_ADS_TOKEN_ENCRYPTION_KEY"),
    )


def get_google_ads_status(user_id: int | None, owner_key: str) -> dict:
    config = get_google_ads_config()
    config_values = (
        ("GOOGLE_ADS_DEVELOPER_TOKEN", config.developer_token),
        ("GOOGLE_ADS_CLIENT_ID", config.client_id),
        ("GOOGLE_ADS_CLIENT_SECRET", config.client_secret),
        ("GOOGLE_ADS_REDIRECT_URI", config.redirect_uri),
        ("GOOGLE_ADS_TOKEN_ENCRYPTION_KEY", config.encryption_key),
    )
    missing = []
    malformed = []
    for name, value in config_values:
        status, _detail = classify_env_value(name, value)
        if status == "missing":
            missing.append(name)
        elif status == "malformed":
            malformed.append(name)
    connection = get_google_ads_connection(user_id, owner_key)
    return {
        "configured": not missing and not malformed and dependencies_available(),
        "connected": bool(connection),
        "missing_configuration": missing,
        "malformed_configuration": malformed,
        "dependency_ready": dependencies_available(),
        "redirect_uri": config.redirect_uri,
        "has_stored_token": bool(connection and connection.get("refresh_token_encrypted")),
        "reason": "Ready" if not missing and not malformed and dependencies_available() else build_status_reason(missing, malformed),
    }


def disconnect_google_ads(user_id: int | None, owner_key: str) -> dict:
    if get_google_ads_connection(user_id, owner_key):
        create_negative_keyword_audit({
            "session_id": owner_key,
            "user_id": user_id,
            "owner_key": owner_key,
            "customer_id": "",
            "campaign_id": "",
            "campaign_name": "",
            "negative_keyword": "",
            "match_type": "",
            "action_status": "disconnected",
            "action_message": "Disconnected Google Ads authorization.",
            "recommendation_snapshot": None,
            "upstream_response": None,
        })
    delete_google_ads_connection(user_id, owner_key)
    return {"success": True, "connected": False}


def build_google_ads_connect_url(state: str) -> str:
    ensure_google_ads_dependencies()
    ensure_google_ads_configuration()
    log_google_ads_redirect_uri("google_ads_oauth_connect")
    flow = build_oauth_flow(state)
    auth_url, _ = flow.authorization_url(
        access_type="offline",
        include_granted_scopes="true",
        prompt="consent",
    )
    return auth_url


def handle_google_ads_oauth_callback(user_id: int | None, owner_key: str, state: str, authorization_response: str) -> dict:
    ensure_google_ads_dependencies()
    ensure_google_ads_configuration()
    flow = build_oauth_flow(state)
    flow.fetch_token(authorization_response=authorization_response)
    credentials = flow.credentials
    if not credentials.refresh_token:
        raise GoogleAdsIntegrationError(
            "Google did not return a refresh token. Reconnect with consent enabled.",
            status_code=400,
            error_code="google_ads_missing_refresh_token",
        )
    encrypted = encrypt_refresh_token(credentials.refresh_token)
    upsert_google_ads_connection(
        user_id=user_id,
        owner_key=owner_key,
        refresh_token_encrypted=encrypted,
        token_expiry=None,
        scopes=None,
        owner_type="user" if user_id is not None else "session",
    )
    accounts = list_google_ads_accounts(user_id, owner_key)
    return {
        "success": True,
        "connected": True,
        "account_count": len(accounts),
    }


def list_google_ads_accounts(user_id: int | None, owner_key: str) -> list[dict]:
    client = build_google_ads_client_for_session(user_id, owner_key)
    customer_service = client.get_service("CustomerService")
    response = customer_service.list_accessible_customers()
    accounts = []
    for resource_name in response.resource_names:
        customer_id = sanitize_customer_id(resource_name.split("/")[-1])
        try:
            accounts.append(fetch_account_summary(client, customer_id))
        except GoogleAdsIntegrationError:
            accounts.append({
                "customer_id": customer_id,
                "name": f"Customer {customer_id}",
                "currency_code": "",
                "time_zone": "",
                "manager": False,
            })
    return sorted(accounts, key=lambda item: (item["name"].lower(), item["customer_id"]))


def fetch_google_ads_campaigns(user_id: int | None, owner_key: str, customer_id: str, search: str = "") -> list[dict]:
    client = build_google_ads_client_for_session(user_id, owner_key)
    normalized_customer_id = assert_customer_access(user_id, owner_key, customer_id)
    search_clause = ""
    if search:
        safe_search = search.replace("'", "\\'")
        search_clause = f" AND campaign.name LIKE '%{safe_search}%'"
    query = f"""
        SELECT
            campaign.id,
            campaign.name,
            campaign.status,
            campaign.advertising_channel_type
        FROM campaign
        WHERE campaign.status != 'REMOVED'{search_clause}
        ORDER BY campaign.name
    """
    rows = run_search_stream(client, normalized_customer_id, query)
    return [{
        "campaign_id": str(row.campaign.id),
        "campaign_name": str(row.campaign.name),
        "campaign_status": enum_name(row.campaign.status),
        "campaign_type": enum_name(row.campaign.advertising_channel_type),
    } for row in rows]


def fetch_google_ads_search_terms(
    user_id: int | None,
    owner_key: str,
    customer_id: str,
    campaign_ids: list[str],
    start_date: str,
    end_date: str,
) -> tuple[list[SearchTermRow], dict]:
    client = build_google_ads_client_for_session(user_id, owner_key)
    normalized_customer_id = assert_customer_access(user_id, owner_key, customer_id)
    normalized_campaign_ids = [sanitize_customer_id(campaign_id) for campaign_id in campaign_ids]
    if not normalized_campaign_ids:
        raise GoogleAdsIntegrationError("Select at least one campaign.", 400, "google_ads_missing_campaigns")
    validate_date_range(start_date, end_date)
    query = f"""
        SELECT
            search_term_view.search_term,
            campaign.id,
            campaign.name,
            campaign.status,
            campaign.advertising_channel_type,
            ad_group.name,
            metrics.clicks,
            metrics.impressions,
            metrics.cost_micros,
            metrics.conversions,
            metrics.ctr
        FROM search_term_view
        WHERE campaign.id IN ({",".join(normalized_campaign_ids)})
          AND segments.date BETWEEN '{start_date}' AND '{end_date}'
        ORDER BY metrics.cost_micros DESC
    """
    rows = run_search_stream(client, normalized_customer_id, query)
    normalized_rows = []
    for index, row in enumerate(rows, start=1):
        search_term = str(row.search_term_view.search_term or "").strip()
        if not search_term:
            continue
        normalized_rows.append(SearchTermRow(
            search_term=search_term,
            campaign=str(row.campaign.name or "Unassigned Campaign"),
            ad_group=str(getattr(row.ad_group, "name", "") or ""),
            clicks=int(row.metrics.clicks or 0),
            impressions=int(row.metrics.impressions or 0),
            cost=round((int(row.metrics.cost_micros or 0) / 1_000_000), 2),
            conversions=float(row.metrics.conversions or 0),
            ctr=float(row.metrics.ctr or 0),
            source_row=index,
            raw={
                "campaign_id": str(row.campaign.id),
                "campaign_status": enum_name(row.campaign.status),
                "campaign_type": enum_name(row.campaign.advertising_channel_type),
                "date_start": start_date,
                "date_end": end_date,
            },
        ))
    metadata = {
        "customer_id": normalized_customer_id,
        "campaign_ids": normalized_campaign_ids,
        "date_start": start_date,
        "date_end": end_date,
        "parsed_rows": len(normalized_rows),
        "source_rows": len(normalized_rows),
        "file_type": "google_ads_api",
        "filename": "live_google_ads",
    }
    return normalized_rows, metadata


def apply_negative_keywords(
    user_id: int | None,
    owner_key: str,
    customer_id: str,
    selected_recommendations: list[dict],
    confirmed: bool,
) -> dict:
    if not confirmed:
        raise GoogleAdsIntegrationError(
            "Confirm before applying negative keywords to Google Ads.",
            400,
            "google_ads_apply_confirmation_required",
        )
    normalized_customer_id = assert_customer_access(user_id, owner_key, customer_id)
    client = build_google_ads_client_for_session(user_id, owner_key)
    if not selected_recommendations:
        raise GoogleAdsIntegrationError(
            "Select at least one NEGATIVE recommendation to apply.",
            400,
            "google_ads_apply_missing_selection",
        )

    service = client.get_service("CampaignCriterionService")
    campaign_service = client.get_service("CampaignService")
    applied = []
    failed = []

    for recommendation in selected_recommendations:
        if normalize_recommendation_classification(recommendation.get("classification")) != "NEGATIVE":
            continue
        campaign_id = sanitize_customer_id(recommendation.get("campaign_id", ""))
        negative_keyword = clean_keyword_value(recommendation.get("negative_keyword"))
        match_type = normalize_match_type(recommendation.get("match_type"))
        if not negative_keyword or not match_type:
            raise GoogleAdsIntegrationError(
                "Each selected recommendation must include campaign_id, negative_keyword, and match_type.",
                400,
                "google_ads_apply_invalid_selection",
            )
        operation = client.get_type("CampaignCriterionOperation")
        criterion = operation.create
        criterion.campaign = campaign_service.campaign_path(normalized_customer_id, campaign_id)
        criterion.negative = True
        criterion.keyword.text = negative_keyword
        criterion.keyword.match_type = getattr(client.enums.KeywordMatchTypeEnum, match_type)

        audit_record = {
            "session_id": owner_key,
            "user_id": user_id,
            "owner_key": owner_key,
            "customer_id": normalized_customer_id,
            "campaign_id": campaign_id,
            "campaign_name": recommendation.get("campaign"),
            "negative_keyword": negative_keyword,
            "match_type": match_type,
            "recommendation_snapshot": recommendation,
        }

        try:
            response = service.mutate_campaign_criteria(
                customer_id=normalized_customer_id,
                operations=[operation],
            )
            result_names = [getattr(item, "resource_name", "") for item in getattr(response, "results", [])]
            audit_id = create_negative_keyword_audit(
                audit_record | {
                    "action_status": "applied",
                    "action_message": "Applied to Google Ads.",
                    "upstream_response": {"resource_names": result_names},
                }
            )
            applied.append({
                "audit_id": audit_id,
                "campaign_id": campaign_id,
                "negative_keyword": negative_keyword,
                "match_type": match_type,
                "resource_names": result_names,
            })
        except Exception as exc:
            translated = translate_google_ads_exception(exc)
            audit_id = create_negative_keyword_audit(
                audit_record | {
                    "action_status": "failed",
                    "action_message": str(translated),
                    "upstream_response": {"error_code": translated.error_code},
                }
            )
            failed.append({
                "audit_id": audit_id,
                "campaign_id": campaign_id,
                "negative_keyword": negative_keyword,
                "match_type": match_type,
                "error": str(translated),
                "error_code": translated.error_code,
            })

    if not applied and not failed:
        raise GoogleAdsIntegrationError(
            "Only NEGATIVE recommendations can be applied to Google Ads.",
            400,
            "google_ads_apply_invalid_selection",
        )

    return {
        "success": True,
        "applied_count": len(applied),
        "failed_count": len(failed),
        "applied": applied,
        "failed": failed,
    }


def dependencies_available() -> bool:
    return not any([GOOGLE_ADS_IMPORT_ERROR, GOOGLE_OAUTH_IMPORT_ERROR, CRYPTO_IMPORT_ERROR])


def ensure_google_ads_dependencies():
    if GOOGLE_ADS_IMPORT_ERROR:
        raise GoogleAdsIntegrationError(
            "Google Ads dependencies are not installed. Install project requirements first.",
            status_code=500,
            error_code="google_ads_dependency_missing",
        )
    if GOOGLE_OAUTH_IMPORT_ERROR:
        raise GoogleAdsIntegrationError(
            "Google OAuth dependencies are not installed. Install project requirements first.",
            status_code=500,
            error_code="google_oauth_dependency_missing",
        )
    if CRYPTO_IMPORT_ERROR:
        raise GoogleAdsIntegrationError(
            "Encryption dependencies are not installed. Install project requirements first.",
            status_code=500,
            error_code="google_ads_crypto_missing",
        )


def ensure_google_ads_configuration():
    status = get_google_ads_status(None, "configuration-check")
    if status["missing_configuration"] or status.get("malformed_configuration"):
        missing = ", ".join(status["missing_configuration"])
        malformed = ", ".join(status.get("malformed_configuration") or [])
        detail_parts = []
        if missing:
            detail_parts.append(f"Missing: {missing}")
        if malformed:
            detail_parts.append(f"Malformed: {malformed}")
        raise GoogleAdsIntegrationError(
            f"Google Ads is not configured. {'; '.join(detail_parts)}.",
            status_code=500,
            error_code="google_ads_config_missing",
        )


def redact_google_ads_redirect_uri() -> dict:
    redirect_uri = get_google_ads_config().redirect_uri.strip()
    if not redirect_uri:
        return {"configured": False, "scheme": "", "host": "", "path": ""}
    parsed = urlparse(redirect_uri)
    return {
        "configured": True,
        "scheme": parsed.scheme,
        "host": parsed.netloc,
        "path": parsed.path or "/",
    }


def log_google_ads_redirect_uri(event: str = "google_ads_redirect_uri") -> None:
    details = redact_google_ads_redirect_uri()
    logger.info(
        "%s configured=%s scheme=%s host=%s path=%s",
        event,
        details["configured"],
        details["scheme"] or "-",
        details["host"] or "-",
        details["path"] or "-",
    )


def build_oauth_flow(state: str):
    config = get_google_ads_config()
    client_config = {
        "web": {
            "client_id": config.client_id,
            "client_secret": config.client_secret,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": [config.redirect_uri],
        }
    }
    flow = Flow.from_client_config(client_config, scopes=SCOPES, state=state)
    flow.redirect_uri = config.redirect_uri
    return flow


def build_google_ads_client_for_session(user_id: int | None, owner_key: str):
    ensure_google_ads_dependencies()
    ensure_google_ads_configuration()
    connection = get_google_ads_connection(user_id, owner_key)
    if not connection:
        raise GoogleAdsIntegrationError(
            "Google Ads is not connected for this account.",
            status_code=400,
            error_code="google_ads_not_connected",
        )
    config = get_google_ads_config()
    client_config = {
        "developer_token": config.developer_token,
        "client_id": config.client_id,
        "client_secret": config.client_secret,
        "refresh_token": decrypt_refresh_token(connection["refresh_token_encrypted"]),
        "use_proto_plus": True,
    }
    try:
        return GoogleAdsClient.load_from_dict(client_config, version=GAQL_VERSION)
    except Exception as exc:
        raise translate_google_ads_exception(exc)


def assert_customer_access(user_id: int | None, owner_key: str, customer_id: str) -> str:
    normalized_customer_id = sanitize_customer_id(customer_id)
    accessible = {account["customer_id"] for account in list_google_ads_accounts(user_id, owner_key)}
    if normalized_customer_id not in accessible:
        raise GoogleAdsIntegrationError(
            "The selected Google Ads account is not accessible for this user.",
            status_code=403,
            error_code="google_ads_forbidden_customer",
        )
    return normalized_customer_id


def fetch_account_summary(client, customer_id: str) -> dict:
    query = """
        SELECT
            customer.id,
            customer.descriptive_name,
            customer.currency_code,
            customer.time_zone,
            customer.manager
        FROM customer
        LIMIT 1
    """
    rows = run_search_stream(client, customer_id, query)
    if not rows:
        return {
            "customer_id": customer_id,
            "name": f"Customer {customer_id}",
            "currency_code": "",
            "time_zone": "",
            "manager": False,
        }
    row = rows[0]
    return {
        "customer_id": str(row.customer.id),
        "name": str(row.customer.descriptive_name or f"Customer {customer_id}"),
        "currency_code": str(row.customer.currency_code or ""),
        "time_zone": str(row.customer.time_zone or ""),
        "manager": bool(row.customer.manager),
    }


def run_search_stream(client, customer_id: str, query: str) -> list:
    try:
        service = client.get_service("GoogleAdsService")
        stream = service.search_stream(customer_id=customer_id, query=" ".join(query.split()))
        rows = []
        for batch in stream:
            rows.extend(batch.results)
        return rows
    except Exception as exc:
        raise translate_google_ads_exception(exc)


def encrypt_refresh_token(refresh_token: str) -> str:
    cipher = build_token_cipher()
    return cipher.encrypt(refresh_token.encode("utf-8")).decode("utf-8")


def decrypt_refresh_token(value: str) -> str:
    cipher = build_token_cipher()
    try:
        return cipher.decrypt(value.encode("utf-8")).decode("utf-8")
    except InvalidToken as exc:
        raise GoogleAdsIntegrationError(
            "Stored Google Ads credentials could not be decrypted with the configured encryption key.",
            status_code=500,
            error_code="google_ads_decrypt_failed",
        ) from exc


def build_token_cipher():
    key = get_google_ads_config().encryption_key
    try:
        return Fernet(key.encode("utf-8"))
    except Exception as exc:
        raise GoogleAdsIntegrationError(
            "GOOGLE_ADS_TOKEN_ENCRYPTION_KEY is invalid. Use a valid Fernet key.",
            status_code=500,
            error_code="google_ads_encryption_key_invalid",
        ) from exc


def sanitize_customer_id(value: str, allow_blank: bool = False) -> str:
    clean = re.sub(r"\D", "", str(value or ""))
    if allow_blank and not clean:
        return ""
    if not CUSTOMER_ID_PATTERN.fullmatch(clean):
        raise GoogleAdsIntegrationError(
            "Google Ads customer IDs must contain 10 digits.",
            status_code=400,
            error_code="google_ads_invalid_customer_id",
        )
    return clean


def validate_date_range(start_date: str, end_date: str):
    try:
        start = date.fromisoformat(start_date)
        end = date.fromisoformat(end_date)
    except ValueError as exc:
        raise GoogleAdsIntegrationError(
            "Choose a valid start and end date in YYYY-MM-DD format.",
            status_code=400,
            error_code="google_ads_invalid_date_range",
        ) from exc
    if start > end:
        raise GoogleAdsIntegrationError(
            "Start date must be earlier than or equal to end date.",
            status_code=400,
            error_code="google_ads_invalid_date_range",
        )
    yesterday = date.today().fromordinal(date.today().toordinal() - 1)
    if start > yesterday or end > yesterday:
        raise GoogleAdsIntegrationError(
            "Google Ads date ranges cannot include today or future dates.",
            status_code=400,
            error_code="google_ads_invalid_date_range",
        )


def enum_name(value) -> str:
    if value is None:
        return ""
    if hasattr(value, "name"):
        return str(value.name)
    raw = str(value)
    return raw.split(".")[-1]


def translate_google_ads_exception(exc: Exception) -> GoogleAdsIntegrationError:
    if isinstance(exc, GoogleAdsIntegrationError):
        return exc
    if GoogleAdsException is not Exception and isinstance(exc, GoogleAdsException):
        message = exc.error.message if getattr(exc, "error", None) else str(exc)
        status_code = 502
        error_code = "google_ads_upstream_error"
        if "authentication" in message.lower() or "oauth" in message.lower():
            status_code = 401
            error_code = "google_ads_authentication_failed"
        return GoogleAdsIntegrationError(message, status_code, error_code)
    message = str(exc) or exc.__class__.__name__
    return GoogleAdsIntegrationError(message, 502, "google_ads_request_failed")


def build_status_reason(missing: list[str], malformed: list[str] | None = None) -> str:
    dependency_messages = []
    if GOOGLE_ADS_IMPORT_ERROR:
        dependency_messages.append("Missing google-ads")
    if GOOGLE_OAUTH_IMPORT_ERROR:
        dependency_messages.append("Missing google-auth-oauthlib")
    if CRYPTO_IMPORT_ERROR:
        dependency_messages.append("Missing cryptography")
    if missing:
        dependency_messages.append("Missing configuration: " + ", ".join(missing))
    if malformed:
        dependency_messages.append("Malformed configuration: " + ", ".join(malformed))
    return "; ".join(dependency_messages) if dependency_messages else "Ready"


def normalize_match_type(value: str) -> str:
    cleaned = str(value or "").upper().strip()
    return cleaned if cleaned in {"EXACT", "PHRASE", "BROAD"} else ""


def clean_keyword_value(value: str) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip())


def normalize_recommendation_classification(value: str) -> str:
    return str(value or "").upper().strip()
