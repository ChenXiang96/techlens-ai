"""Bounded local request prompts; no session, authorization or provider work."""
from decimal import Decimal, InvalidOperation
import re
from uuid import uuid4

from techlens.configuration import ApplicationError, parse_model
from techlens.models import AssessmentRequest, TechnologyCandidate, TeamProfile
from techlens.presentation import render_request_summary, safe_url


class InputBoundaryError(ApplicationError):
    """Fatal bounded-input failure; never recover by reading a possible suffix."""

    def __init__(self):
        super().__init__("Input line exceeds the local input bound; operation cancelled.")


def build_request(read, write, public):
    """Return a validated request after exact SAVE, or None after CANCEL."""
    write("LOCAL REQUEST BUILDER — no assessment or provider calls\n"
          "Skip optional context with Enter. Lists accept one item per line; Enter ends a list.\n")

    def read_value(prompt):
        raw = read(prompt)
        if len(raw) > 4096:
            raise InputBoundaryError()
        public.check(raw)
        return raw

    def ask(prompt, parse, guidance, *, limit=1024):
        while True:
            raw = read_value(prompt)
            invalid = len(raw) > limit
            result = None
            if not invalid:
                try:
                    result = parse(raw.strip())
                except (ValueError, TypeError, InvalidOperation, ApplicationError):
                    invalid = True
            if not invalid:
                return result
            write(guidance + "\n")

    def text(value):
        if not value:
            raise ValueError()
        return value

    def integer(value, default=None, maximum=None):
        if not value:
            return default
        if not re.fullmatch(r"[0-9]{1,9}", value):
            raise ValueError()
        result = int(value)
        if result < 1 or (maximum is not None and result > maximum):
            raise ValueError()
        return result

    def items(label):
        result = []
        while True:
            value = ask(f"{label} item {len(result) + 1} [Enter to finish]: ", lambda v: v,
                        f"{label} items must contain at most 256 characters.", limit=256)
            if not value:
                return result
            if len(result) == 20:
                write(f"{label} accepts at most 20 items. Press Enter to finish.\n")
                continue
            result.append(value)

    assessment_id = ask("Assessment ID [Enter to generate]: ",
                        lambda v: v or f"assessment-{uuid4().hex}",
                        "Assessment ID must contain at most 128 characters.", limit=128)
    count = ask("Number of candidates [1–3; default 1]: ", lambda v: integer(v, 1, 3),
                "Enter a candidate count from 1 to 3.", limit=9)
    candidates = []
    for index in range(1, count + 1):
        name = ask(f"Candidate {index} name: ", text,
                   "Candidate name is required and must contain at most 256 characters.", limit=256)

        def repository(value):
            candidate = parse_model({"name": name, "repository_url": value}, TechnologyCandidate)
            safe_url(str(candidate.repository_url))
            return candidate

        candidate = ask(f"Candidate {index} GitHub repository URL: ", repository,
                        "Enter an HTTPS GitHub repository-root URL without credentials or extra path segments.",
                        limit=2048)

        def homepage(value):
            if not value:
                return None
            safe_url(value)
            return parse_model({**candidate.model_dump(), "homepage": value}, TechnologyCandidate).homepage

        home = ask(f"Candidate {index} homepage [Enter to skip]: ", homepage,
                   "Enter a credential-free HTTP/HTTPS homepage, or press Enter to skip.", limit=2048)
        candidates.append(candidate.model_copy(update={"homepage": home}))

    team = {"team_size": ask("Team size [Enter if unknown]: ", integer,
                             "Enter a positive whole number, or press Enter to skip.", limit=9),
            "primary_languages": items("Primary languages"), "backend_stack": items("Backend stack")}
    for field, label in (("ai_experience", "AI experience"),
                         ("deployment_environment", "Deployment environment"),
                         ("production_criticality", "Production criticality"),
                         ("framework_churn_tolerance", "Framework churn tolerance")):
        team[field] = ask(f"{label} [Enter if unknown]: ", lambda v: v or None,
                          f"{label} must contain at most 1024 characters, or be left blank.")
    priorities = items("Priorities")
    searches = ask("Maximum web-search attempts [1–8; default 2]: ", lambda v: integer(v, 2, 8),
                   "Enter a search-attempt limit from 1 to 8.", limit=9)
    rounds = ask("Declared maximum research rounds [1–2; default 1]: ", lambda v: integer(v, 1, 2),
                "Enter a declared round limit of 1 or 2.", limit=9)
    write("Current execution performs at most one research round, even with a higher declared limit.\n"
          "Guided budget defaults to USD 0; the existing JSON model default remains USD 0.15.\n"
          "Zero dollars blocks paid work. A positive setting is not permission to spend.\n")

    def money(value):
        value = value or "0"
        if not re.fullmatch(r"[0-9]+(?:\.[0-9]+)?", value):
            raise ValueError()
        result = Decimal(value)
        if not result.is_finite() or result < 0:
            raise ValueError()
        return result

    budget = ask("Run budget in USD [default 0; setting a budget does not authorize spending]: ", money,
                 "Enter a finite, non-negative USD decimal without currency symbols or exponents.", limit=32)
    request = parse_model({"assessment_id": assessment_id,
        "candidates": [c.model_dump() for c in candidates],
        "team_profile": parse_model(team, TeamProfile).model_dump(), "priorities": priorities,
        "constraints": {"max_candidates": count, "max_web_searches": searches,
                        "max_research_rounds": rounds, "max_run_cost_usd": budget}}, AssessmentRequest)
    write(render_request_summary(request, public))
    while True:
        answer = read_value("Type SAVE to create the request, or CANCEL to discard: ")
        if answer == "SAVE":
            return request
        if answer == "CANCEL":
            return None
        write("Enter exactly SAVE or CANCEL. No file has been published.\n")
