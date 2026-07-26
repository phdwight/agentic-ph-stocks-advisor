"""
SQLAdmin panel for the PH Stocks Advisor database.

The schema is **reflected from the live database** rather than mirrored
by hand.  Hand-written models drifted every time a migration landed:
``holdings`` and ``portfolio_reports`` were never exposed at all,
``reports.score`` went missing the same day it was added (so the panel
showed the coarse binary verdict while the UI showed a five-band label
derived from that score), the disclaimer consent columns were invisible,
and at one point a model referenced a column that had never existed —
which made every user listing return 500.

Reflecting removes that whole class of bug: new tables and new columns
appear on their own, and nothing in this file has to be edited to keep
up.  Reflection happens once at startup, so a schema change is picked up
by restarting this container.

Security: protected by username/password authentication via SQLAdmin's
``AuthenticationBackend``.  Credentials are read from environment
variables ``ADMIN_USERNAME`` and ``ADMIN_PASSWORD``.  Views are
**read-only by default** — write access is granted per table below, and
deliberately withheld from identity fields (an editable ``oid`` or
``email`` would be an account-takeover primitive).
"""

from __future__ import annotations

import logging
import os
import secrets
import sys
import time
import types
from typing import Any

from sqladmin import Admin, ModelView
from sqladmin.authentication import AuthenticationBackend
from sqlalchemy import MetaData, String, create_engine
from sqlalchemy.ext.automap import automap_base
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.sessions import SessionMiddleware
from starlette.requests import Request
from starlette.responses import RedirectResponse
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)-7s] %(name)s :: %(message)s",
)
logger = logging.getLogger("admin")

# ---------------------------------------------------------------------------
# Database setup
# ---------------------------------------------------------------------------

DATABASE_URL = os.environ.get(
    "DATABASE_URL",
    "postgresql://ph_advisor:ph_advisor@db:5432/ph_advisor",
)

engine = create_engine(DATABASE_URL, pool_pre_ping=True)

# The application owns the schema; this panel only reads it.  On a
# brand-new database the tables briefly do not exist, and reflecting an
# empty schema would leave the panel permanently blank until somebody
# noticed and restarted it — so wait for the schema, then give up and let
# the container restart policy try again.
_REFLECT_ATTEMPTS = int(os.environ.get("ADMIN_REFLECT_ATTEMPTS", "30"))
_REFLECT_DELAY = float(os.environ.get("ADMIN_REFLECT_DELAY", "2"))


def _reflect_schema() -> MetaData:
    """Return metadata for the live schema, waiting for it to exist."""
    for attempt in range(1, _REFLECT_ATTEMPTS + 1):
        metadata = MetaData()
        try:
            metadata.reflect(bind=engine)
        except Exception as exc:
            logger.warning(
                "Database not reachable yet (attempt %d/%d): %s",
                attempt,
                _REFLECT_ATTEMPTS,
                exc,
            )
        else:
            if metadata.tables:
                return metadata
            logger.warning(
                "No tables yet (attempt %d/%d) — waiting for the application to create the schema.",
                attempt,
                _REFLECT_ATTEMPTS,
            )
        time.sleep(_REFLECT_DELAY)

    logger.error(
        "Schema still unavailable after %d attempts — exiting so the container restarts.",
        _REFLECT_ATTEMPTS,
    )
    sys.exit(1)


_metadata = _reflect_schema()

Base = automap_base(metadata=_metadata)


def _classname_for_table(_base: Any, tablename: str, _table: Any) -> str:
    """``webauthn_credentials`` -> ``WebauthnCredentials``."""
    return "".join(part.capitalize() for part in tablename.split("_")) or tablename


Base.prepare(classname_for_table=_classname_for_table)

_mapped = {cls.__table__.name: cls for cls in Base.classes}

# automap can only map a table that has a primary key.  Say so loudly
# rather than letting a table vanish from a panel whose whole purpose is
# showing everything that is in the database.
_unmapped = sorted(set(_metadata.tables) - set(_mapped))
if _unmapped:
    logger.warning(
        "Not shown — these tables have no primary key and cannot be mapped: %s",
        ", ".join(_unmapped),
    )

# ---------------------------------------------------------------------------
# Per-table configuration
# ---------------------------------------------------------------------------
#
# Anything not listed here still gets a view — read-only, with a title
# derived from the table name.  That is what makes a future table show up
# without a code change.

_READ_ONLY: dict[str, Any] = {
    "can_create": False,
    "can_edit": False,
    "can_delete": False,
}

_OVERRIDES: dict[str, dict[str, Any]] = {
    "reports": {
        "name": "Report",
        "name_plural": "Reports",
        "icon": "fa-solid fa-chart-line",
        # Reports are written by the analysis pipeline, never created by
        # hand, but correcting or removing one has to be possible.
        "can_edit": True,
        "can_delete": True,
    },
    "users": {
        "name": "User",
        "name_plural": "Users",
        "icon": "fa-solid fa-user-shield",
        "can_edit": True,
        # Deliberately narrow.  An editable oid or email is an account
        # takeover primitive, so only the privilege flag is writable.
        "form_columns": ["user_type"],
        "column_labels": {"user_type": "User Type (0=Normal, 1=Elevated)"},
    },
    "user_symbols": {
        "name": "User Symbol",
        "name_plural": "User Symbols",
        "icon": "fa-solid fa-users",
        "can_create": True,
        "can_delete": True,
    },
    "webauthn_credentials": {
        "name": "Passkey",
        "name_plural": "Passkeys",
        "icon": "fa-solid fa-key",
        # Editing a credential would break the WebAuthn ceremony — a
        # tampered sign_count defeats replay detection — but revoking a
        # lost or compromised passkey has to be possible.
        "can_delete": True,
    },
    "holdings": {
        "name": "Holding",
        "name_plural": "Holdings",
        "icon": "fa-solid fa-wallet",
    },
    "portfolio_reports": {
        "name": "Portfolio Report",
        "name_plural": "Portfolio Reports",
        "icon": "fa-solid fa-briefcase",
    },
}

# Sidebar order.  Tables that are not listed sort to the end, so a table
# added in future lands somewhere it will be noticed.
_ORDER = [
    "reports",
    "users",
    "user_symbols",
    "webauthn_credentials",
    "holdings",
    "portfolio_reports",
]

# Report sections run to thousands of characters and would make the list
# view unusable, so values are truncated there.  The detail view (the eye
# icon) still shows everything in full.
_TRUNCATE_AT = int(os.environ.get("ADMIN_LIST_TRUNCATE", "60"))


def _truncating_formatter(column_name: str):
    def _format(obj: Any, _attr: Any) -> str:
        value = getattr(obj, column_name, None)
        text = "" if value is None else str(value)
        return text if len(text) <= _TRUNCATE_AT else text[:_TRUNCATE_AT] + "…"

    return _format


def _build_view(table_name: str, model: Any) -> type[ModelView]:
    """Generate a ModelView exposing every column of one table."""
    table = model.__table__
    columns = [c.name for c in table.columns]
    primary_keys = {c.name for c in table.primary_key.columns}

    cfg = dict(_READ_ONLY)
    cfg.update(_OVERRIDES.get(table_name, {}))

    title = table_name.replace("_", " ").title()
    form_columns = cfg.pop("form_columns", None)

    attrs: dict[str, Any] = {
        "name": cfg.pop("name", title),
        "name_plural": cfg.pop("name_plural", title),
        "icon": cfg.pop("icon", "fa-solid fa-table"),
        # "__all__" is the point of the exercise: whatever the table has
        # today, including columns added after this file was last edited.
        "column_list": "__all__",
        "column_details_list": "__all__",
        "column_sortable_list": columns,
        # Searching an unbounded TEXT column means an ILIKE across every
        # report body, so restrict search to bounded VARCHARs.
        "column_searchable_list": [c.name for c in table.columns if isinstance(c.type, String) and c.type.length],
        "column_formatters": {getattr(model, name): _truncating_formatter(name) for name in columns},
        "can_export": True,
        "can_view_details": True,
        "page_size": 25,
    }

    if "created_at" in columns:
        attrs["column_default_sort"] = ("created_at", True)  # newest first

    if cfg.get("can_edit"):
        # Never offer the primary key or the creation timestamp for
        # editing — changing either rewrites identity or history.
        attrs["form_columns"] = form_columns or [c for c in columns if c not in primary_keys and c != "created_at"]

    attrs.update(cfg)

    return types.new_class(
        f"{model.__name__}Admin",
        (ModelView,),
        {"model": model},
        lambda ns: ns.update(attrs),
    )


# ---------------------------------------------------------------------------
# Authentication backend for SQLAdmin
# ---------------------------------------------------------------------------

_ADMIN_USERNAME = os.environ.get("ADMIN_USERNAME", "admin")
_ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "")


class AdminAuth(AuthenticationBackend):
    """Simple username/password gate for the admin panel.

    When ``ADMIN_PASSWORD`` is not set the panel is **locked** — no
    login is possible.  This prevents accidental exposure of an
    unauthenticated admin interface in production (CWE-306).
    """

    async def login(self, request: Request) -> bool:
        form = await request.form()
        username = form.get("username", "")
        password = form.get("password", "")

        if not _ADMIN_PASSWORD:
            # No password configured — deny all logins.
            return False

        if secrets.compare_digest(str(username), _ADMIN_USERNAME) and secrets.compare_digest(
            str(password), _ADMIN_PASSWORD
        ):
            request.session.update({"authenticated": True})
            return True
        return False

    async def logout(self, request: Request) -> bool:
        request.session.clear()
        return True

    async def authenticate(self, request: Request) -> RedirectResponse | bool:
        if not request.session.get("authenticated"):
            return RedirectResponse(request.url_for("admin:login"), status_code=302)
        return True


# ---------------------------------------------------------------------------
# Starlette app + SQLAdmin wiring
# ---------------------------------------------------------------------------

secret_key = os.environ.get("ADMIN_SECRET_KEY", "sqladmin-dev-secret-change-me")

# Restrict trusted proxy hosts to Docker-internal networks by default.
_trusted_hosts = os.environ.get("ADMIN_TRUSTED_HOSTS", "127.0.0.1,::1")
trusted_hosts_list: list[str] | str = [h.strip() for h in _trusted_hosts.split(",") if h.strip()]
if "*" in trusted_hosts_list:
    trusted_hosts_list = "*"

app = Starlette(
    middleware=[
        Middleware(ProxyHeadersMiddleware, trusted_hosts=trusted_hosts_list),
        Middleware(SessionMiddleware, secret_key=secret_key),
    ],
)

authentication_backend = AdminAuth(secret_key=secret_key)

admin = Admin(
    app,
    engine,
    title="PH Stocks Advisor — Admin",
    authentication_backend=authentication_backend,
)


def _sort_key(table_name: str) -> tuple[int, str]:
    return (
        _ORDER.index(table_name) if table_name in _ORDER else len(_ORDER),
        table_name,
    )


for _table_name in sorted(_mapped, key=_sort_key):
    admin.add_view(_build_view(_table_name, _mapped[_table_name]))
    logger.info(
        "Mapped %s (%d columns)",
        _table_name,
        len(_mapped[_table_name].__table__.columns),
    )

logger.info("Admin panel ready — %d tables reflected from the database.", len(_mapped))
