"""Local application validation; no provider construction or price discovery."""
import json
from pathlib import Path
from typing import get_args

from pydantic import BaseModel

from techlens.budget import RateCard
from techlens.models import AssessmentRequest
from techlens.openai_planner import DEFAULT_MODEL


class ApplicationError(Exception):
    """Only application-owned, non-sensitive messages belong here."""

    def __init__(self, message, code=2):
        super().__init__(message)
        self.code = code


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON key")
        result[key] = value
    return result


def load_json(path):
    failed = False
    try:
        with Path(path).open("rb") as handle:
            raw = handle.read(1_048_577)
        if len(raw) > 1_048_576:
            raise ValueError("Input too large")
        value = json.loads(raw, object_pairs_hook=_pairs,
                           parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
    except (OSError, ValueError, UnicodeError, RecursionError):
        failed = True
    if failed:
        raise ApplicationError("Cannot read valid bounded JSON input.")
    return value


def _check_fields(value, annotation):
    """Recurse through declared model fields, containers and optional types."""
    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        if isinstance(value, dict):
            if set(value) - set(annotation.model_fields):
                raise ValueError("Unknown field")
            for name, item in value.items():
                _check_fields(item, annotation.model_fields[name].annotation)
    else:
        args = get_args(annotation)
        if isinstance(value, list) and args:
            for item in value:
                _check_fields(item, args[0])
        else:
            for arg in args:
                _check_fields(value, arg)


def parse_model(value, model):
    failed = False
    try:
        _check_fields(value, model)
        result = model.model_validate(value)
    except (ValueError, TypeError, RecursionError):
        failed = True
    if failed:
        raise ApplicationError("Input contains invalid or unknown fields.")
    return result


def live_configuration(request, rates, env, *, interactive=True):
    """Presence/applicability only: the caller remains responsible for price authority."""
    if not interactive:
        raise ApplicationError("Live assessment requires an interactive review terminal.")
    if any(not env.get(k, "").strip() for k in ("OPENAI_API_KEY", "TAVILY_API_KEY")):
        raise ApplicationError("Required live credentials are missing.")
    if env.get("OPENAI_BASE_URL"):
        raise ApplicationError("Custom provider endpoints are not supported.")
    model = env.get("OPENAI_MODEL", DEFAULT_MODEL).strip()
    if not model or rates.model != model:
        raise ApplicationError("Model and rate-card applicability differ.")
    if not rates.source_reference.strip() or rates.max_output_tokens < 8192:
        raise ApplicationError("Generation price provenance or output bound is incomplete.")
    for kind in ("count", "search"):
        upper = getattr(rates, f"{kind}_upper_fee")
        reference = getattr(rates, f"{kind}_source_reference")
        effective = getattr(rates, f"{kind}_effective_date")
        if upper is None or not reference or not reference.strip() or effective is None:
            raise ApplicationError("Independent count/search price basis is required; live dispatch blocked.")
    if rates.count_upper_fee == 0 and not rates.count_authoritative_zero:
        raise ApplicationError("A zero count fee requires an authoritative independent basis.")
    ceiling = request.constraints.max_run_cost_usd
    if ceiling <= 0 or rates.count_upper_fee > ceiling:
        raise ApplicationError("Initial planning admission exceeds the run ceiling.")
    return model


def read_configuration(request_path, rates_path):
    return (parse_model(load_json(request_path), AssessmentRequest),
            parse_model(load_json(rates_path), RateCard))
