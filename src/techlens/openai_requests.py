"""Shared Responses payload and sanitized usage-preserving outcomes."""
import json
import openai
from pydantic import ValidationError
from techlens.budget import AttemptOutcome, Usage


def structured_request(model, instructions, data, schema_type):
    return {"model": model, "instructions": instructions,
            "input": [{"role": "user", "content": json.dumps(data, ensure_ascii=True)}],
            "text": {"format": {"type": "json_schema", "name": schema_type.__name__,
                                "strict": True, "schema": schema_type.model_json_schema()}},
            "truncation": "disabled"}


def call_openai(client, shared, *, schema_type=None, max_output_tokens=None):
    category, data, usage, retry_after = None, None, Usage(), None
    try:
        if schema_type is None:
            response = client.responses.input_tokens.count(**shared)
            if type(response.input_tokens) is not int or response.input_tokens < 0:
                category = "malformed"
            else:
                data = {"input_tokens": response.input_tokens}
        else:
            response = client.responses.create(**shared, max_output_tokens=max_output_tokens, store=False)
            if response.usage is not None:
                usage = Usage(input_tokens=response.usage.input_tokens, output_tokens=response.usage.output_tokens)
            refused = any(getattr(content, "type", None) == "refusal"
                          for item in response.output for content in getattr(item, "content", []))
            if refused:
                category = "refusal"
            elif response.status != "completed":
                category = "incomplete"
            elif not response.output_text:
                category = "missing_output"
            else:
                data = schema_type.model_validate_json(response.output_text).model_dump(mode="json")
    except openai.APITimeoutError:
        category = "timeout"
    except openai.APIConnectionError:
        category = "network"
    except openai.APIStatusError as error:
        code, status = getattr(error, "code", None), error.status_code
        category = ("authentication" if status in {401, 403} else "quota" if code == "insufficient_quota"
                    else "rate_limit" if status == 429 and code == "rate_limit_exceeded"
                    else "quota_or_rate_unknown" if status == 429
                    else "server" if status >= 500 else "invalid_request")
        header = error.response.headers.get("retry-after", "")
        if header.isdigit():
            retry_after = min(float(header), 5.0)
    except (openai.APIError, ValidationError, ValueError, TypeError, AttributeError):
        category = "malformed"
    return AttemptOutcome(status="failed" if category else "succeeded", category=category,
                          data=data if category is None else None, usage=usage, retry_after=retry_after)
