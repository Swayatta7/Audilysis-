import secrets
from functools import wraps
from urllib.parse import urlencode

from flask import jsonify, redirect, request, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash

from db.storage import create_user, get_user_by_email, get_user_by_id


AUTH_SESSION_KEY = "auth_user_id"
CSRF_SESSION_KEY = "csrf_token"


class AuthError(Exception):
    pass


def normalize_email(email: str) -> str:
    return str(email or "").strip().lower()


def create_user_account(email: str, password: str):
    normalized_email = normalize_email(email)
    if not normalized_email or "@" not in normalized_email:
        raise AuthError("Enter a valid email address.")
    if len(password or "") < 8:
        raise AuthError("Password must be at least 8 characters long.")
    if get_user_by_email(normalized_email):
        raise AuthError("An account with this email already exists.")
    password_hash = generate_password_hash(password)
    user_id = create_user(normalized_email, password_hash)
    return get_user_by_id(user_id)


def authenticate_user(email: str, password: str):
    user = get_user_by_email(normalize_email(email))
    if not user or not check_password_hash(user["password_hash"], password or ""):
        raise AuthError("Invalid email or password.")
    return user


def login_user(user: dict):
    session[AUTH_SESSION_KEY] = int(user["id"])
    rotate_csrf_token()


def logout_user():
    session.pop(AUTH_SESSION_KEY, None)
    session.pop(CSRF_SESSION_KEY, None)


def current_user():
    user_id = session.get(AUTH_SESSION_KEY)
    if not user_id:
        return None
    return get_user_by_id(int(user_id))


def is_authenticated() -> bool:
    return current_user() is not None


def rotate_csrf_token() -> str:
    token = secrets.token_urlsafe(32)
    session[CSRF_SESSION_KEY] = token
    return token


def get_csrf_token() -> str:
    token = session.get(CSRF_SESSION_KEY)
    if not token:
        token = rotate_csrf_token()
    return token


def validate_csrf():
    expected = session.get(CSRF_SESSION_KEY, "")
    provided = (
        request.headers.get("X-CSRF-Token", "")
        or request.form.get("csrf_token", "")
        or ((request.get_json(silent=True) or {}).get("csrf_token", ""))
    )
    if not expected or not provided or not secrets.compare_digest(expected, provided):
        raise AuthError("CSRF validation failed.")


def is_api_request() -> bool:
    return (
        request.path.startswith("/api/")
        or request.path in {"/run-agent", "/stream"}
        or request.is_json
        or request.headers.get("X-Requested-With") == "XMLHttpRequest"
        or "application/json" in (request.headers.get("Accept", "") or "").lower()
    )


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if is_authenticated():
            return view(*args, **kwargs)
        if is_api_request():
            return jsonify({
                "ok": False,
                "success": False,
                "error": "Authentication required",
                "error_code": "authentication_required",
                "message": "Authentication required",
            }), 401
        query = urlencode({"next": request.full_path if request.query_string else request.path})
        return redirect(f"{url_for('login')}?{query}")
    return wrapped


def csrf_protect(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if request.method in {"GET", "HEAD", "OPTIONS"}:
            return view(*args, **kwargs)
        try:
            validate_csrf()
        except AuthError as error:
            if is_api_request():
                return jsonify({
                    "ok": False,
                    "success": False,
                    "error": str(error),
                    "error_code": "csrf_failed",
                    "message": str(error),
                }), 403
            return str(error), 400
        return view(*args, **kwargs)
    return wrapped
