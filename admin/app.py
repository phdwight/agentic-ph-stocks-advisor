"""
Lightweight SQLAdmin panel for the PH Stocks Advisor database.

This is a standalone Starlette + SQLAdmin application that connects to
the same PostgreSQL database used by the main application, providing
a web-based admin interface for browsing and managing reports and
user-symbol associations.
"""

from __future__ import annotations

import os

from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.sessions import SessionMiddleware
from sqlalchemy import (
    Column,
    DateTime,
    Integer,
    String,
    Text,
    create_engine,
)
from sqlalchemy.orm import DeclarativeBase
from sqladmin import Admin, ModelView


# ---------------------------------------------------------------------------
# Database setup
# ---------------------------------------------------------------------------

DATABASE_URL = os.environ.get(
    "DATABASE_URL",
    "postgresql://ph_advisor:ph_advisor@db:5432/ph_advisor",
)

engine = create_engine(DATABASE_URL, pool_pre_ping=True)


class Base(DeclarativeBase):
    pass


# ---------------------------------------------------------------------------
# Models (mirror the tables created by repository_postgres.py)
# ---------------------------------------------------------------------------


class Report(Base):
    __tablename__ = "reports"

    id = Column(Integer, primary_key=True)
    symbol = Column(String(20), nullable=False)
    verdict = Column(String(20), nullable=False)
    summary = Column(Text, nullable=False)
    price_section = Column(Text, nullable=False, server_default="")
    dividend_section = Column(Text, nullable=False, server_default="")
    movement_section = Column(Text, nullable=False, server_default="")
    valuation_section = Column(Text, nullable=False, server_default="")
    controversy_section = Column(Text, nullable=False, server_default="")
    created_at = Column(DateTime(timezone=True), nullable=False)


class UserSymbol(Base):
    __tablename__ = "user_symbols"

    user_id = Column(String(320), primary_key=True)
    symbol = Column(String(20), primary_key=True)
    created_at = Column(DateTime(timezone=True), nullable=False)


# ---------------------------------------------------------------------------
# Admin views
# ---------------------------------------------------------------------------


class ReportAdmin(ModelView, model=Report):
    name = "Report"
    name_plural = "Reports"
    icon = "fa-solid fa-chart-line"

    column_list = [
        Report.id,
        Report.symbol,
        Report.verdict,
        Report.created_at,
    ]
    column_searchable_list = [Report.symbol, Report.verdict]
    column_sortable_list = [
        Report.id,
        Report.symbol,
        Report.verdict,
        Report.created_at,
    ]
    column_default_sort = ("created_at", True)  # newest first

    form_columns = [
        "symbol",
        "verdict",
        "summary",
        "price_section",
        "dividend_section",
        "movement_section",
        "valuation_section",
        "controversy_section",
    ]

    can_create = False  # reports are created by the analysis pipeline
    can_export = True
    page_size = 25


class UserSymbolAdmin(ModelView, model=UserSymbol):
    name = "User Symbol"
    name_plural = "User Symbols"
    icon = "fa-solid fa-users"

    column_list = [
        UserSymbol.user_id,
        UserSymbol.symbol,
        UserSymbol.created_at,
    ]
    column_searchable_list = [UserSymbol.user_id, UserSymbol.symbol]
    column_sortable_list = [
        UserSymbol.user_id,
        UserSymbol.symbol,
        UserSymbol.created_at,
    ]
    column_default_sort = ("created_at", True)

    can_create = True
    can_export = True
    page_size = 25


# ---------------------------------------------------------------------------
# Starlette app + SQLAdmin wiring
# ---------------------------------------------------------------------------

secret_key = os.environ.get(
    "ADMIN_SECRET_KEY", "sqladmin-dev-secret-change-me"
)

app = Starlette(
    middleware=[
        Middleware(SessionMiddleware, secret_key=secret_key),
    ],
)

admin = Admin(
    app,
    engine,
    title="PH Stocks Advisor — Admin",
)
admin.add_view(ReportAdmin)
admin.add_view(UserSymbolAdmin)
