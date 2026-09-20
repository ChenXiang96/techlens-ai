# TechLens AI

TechLens AI is an evidence-grounded due-diligence agent for evaluating open-source technology adoption. T-001 provides only the typed, deterministic LangGraph skeleton. It performs no real research and produces no adoption recommendation.

## Requirements

- Python 3.11 or newer
- [uv](https://docs.astral.sh/uv/)

## Setup

```console
uv sync
```

## Run the deterministic workflow

Start a Python session in the project environment:

```console
uv run python
```

Then invoke the graph with a typed request:

```python
from techlens import (
    AssessmentRequest,
    TeamProfile,
    TechnologyCandidate,
    build_graph,
)

request = AssessmentRequest(
    assessment_id="assessment-001",
    candidates=[
        TechnologyCandidate(
            name="LangGraph",
            repository_url="https://github.com/langchain-ai/langgraph",
        )
    ],
    team_profile=TeamProfile(primary_languages=["Python"]),
    priorities=["Production readiness"],
)

result = build_graph().invoke({"request": request})
print(result["report"].model_dump())
```

The result is a deterministic stub. `current_round` remains `0`, and no web request, GitHub API request, model call, or paid service is used.

## Test

```console
uv run pytest
```

