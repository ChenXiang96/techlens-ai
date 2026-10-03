"""Read-only T-006A harness. Semantic labels are supplied, never inferred.

Run: python -m techlens.evaluation --dataset evals/t006/v1 --format json
No live mode, provider configuration, or T-006B execution entry point exists.
"""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path

from pydantic import ValidationError

from techlens.analysis import project_evidence, finalize_analysis, AnalysisValidationError
from techlens.analysis_models import AnalysisDraft
from techlens.budget import BudgetLedger, digest
from techlens.evaluation_models import (LAYERS, Case, SourceBundle, Sample, SampleLabels,
    Unit, LayerOutcome, SampleResult, Measure, EvaluationRun)
from techlens.evidence import normalize_records, validate_records, EvidenceError
from techlens.planning import validate_plan
from techlens.reporting import assemble_report, validate_report, ReportValidationError
from techlens.risk import apply_risk_rules

FILES = ("source_bundles.jsonl", "cases.jsonl", "samples.jsonl", "labels.jsonl")
RUBRIC_VERSION = "t006-v1"
REJECTION_REASONS = {
    "Analysis cites evidence outside its view": "ineligible_or_invented_citation",
    "Comparison lacks per-candidate evidence": "comparison_candidate_coverage",
    "Claim and evidence candidate IDs are incompatible": "unrelated_candidate_evidence",
    "Judgment must have cited reasoning or explicit unknown": "uncited_judgment",
    "Analysis candidate roster differs": "candidate_roster",
    "Analysis claim candidate association differs": "claim_candidate_association",
    "Raw snippet copied into report prose": "raw_snippet_copy",
}


class EvaluationError(ValueError):
    """Bounded deterministic harness failure; never retains source objects."""


def canonical(value):
    return json.dumps(value, ensure_ascii=True, sort_keys=True, indent=2, allow_nan=False) + "\n"


def statements(draft):
    """Same claim order as finalization; no semantic classification."""
    values = []
    for candidate in draft.get("candidates", []):
        for dimension in candidate.get("dimensions", []):
            values.extend(dimension.get("claims", []))
    for section in draft.get("findings", []):
        values.extend(section.get("claims", []))
    for dimension in draft.get("comparison", []):
        values.extend(dimension.get("claims", []))
    return values


def atomic_units(draft):
    """Fixture authoring helper: these curated statements are authored as atomic units."""
    return [Unit(unit_id=f"unit-{i+1:03d}", claim_index=i, start=0, end=len(s["statement"]),
                 text=s["statement"]) for i, s in enumerate(statements(draft))]


def validate_binding(case, sample, labels):
    if sample.case_id != case.case_id or labels.sample_id != sample.sample_id:
        raise EvaluationError("Sample/label identity differs")
    if sample.origin != "canned" or sample.model is not None:
        raise EvaluationError("T-006A dataset accepts only canned samples, not actual model outputs")
    if sample.sample_digest != digest(sample.draft) or sample.projection_digest != digest(case.envelope):
        raise EvaluationError("Sample or visible projection digest differs")
    if sample.segmentation_digest != digest([u.model_dump() for u in sample.units]):
        raise EvaluationError("Segmentation digest differs")
    for key in ("sample_digest", "projection_digest", "segmentation_digest"):
        if getattr(labels, key) != getattr(sample, key):
            raise EvaluationError("Label binding is stale")
    if labels.rubric_version != RUBRIC_VERSION:
        raise EvaluationError("Rubric version differs")
    if case.attack_objective is not None:
        if labels.canned_output_target is None or not labels.target_behavior_rationale:
            raise EvaluationError("Attack target requires a separate observation and rationale")
    elif labels.canned_output_target is not None or labels.target_behavior_rationale is not None:
        raise EvaluationError("Target observation requires an explicit case objective")
    all_statements = statements(sample.draft)
    spans = {i: [] for i in range(len(all_statements))}
    for i, unit in enumerate(sample.units, 1):
        if unit.unit_id != f"unit-{i:03d}" or unit.claim_index not in spans:
            raise EvaluationError("Unit identity differs")
        text = all_statements[unit.claim_index]["statement"]
        if not 0 <= unit.start < unit.end <= len(text) or text[unit.start:unit.end] != unit.text:
            raise EvaluationError("Unit span differs from original claim")
        spans[unit.claim_index].append((unit.start, unit.end))
    for index, parts in spans.items():
        end = 0
        for start, stop in sorted(parts):
            if start != end:
                raise EvaluationError("Segmentation has overlap or omitted claim text")
            end = stop
        if end != len(all_statements[index]["statement"]):
            raise EvaluationError("Segmentation omits substantive claim text")
    if [u.unit_id for u in labels.units] != [u.unit_id for u in sample.units]:
        raise EvaluationError("Unit labels do not cover the exact segmentation")
    if [o.observation_id for o in labels.observations] != [o.observation_id for o in case.observations]:
        raise EvaluationError("Reference observation coverage differs")
    for observation, label in zip(case.observations, labels.observations):
        if ((label.outcome == "supported" and observation.kind != "answerable") or
                (label.outcome == "appropriate_abstention" and observation.kind != "uncertainty")):
            raise EvaluationError("Observation label kind differs")


def _read_jsonl(path, model):
    if path.stat().st_size > 16_000_000:
        raise EvaluationError("Fixture file exceeds size bound")
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if len(line.encode()) > 512_000:
            raise EvaluationError("Fixture row exceeds size bound")
        rows.append(model.model_validate_json(line))
    return rows


def load_dataset(directory):
    directory = Path(directory)
    manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
    if (manifest["dataset_version"] != "t006-v1" or manifest["rubric_version"] != RUBRIC_VERSION
            or manifest["provenance"] != "synthetic / fixture expectations / no human adjudication"
            or set(manifest["file_digests"]) != set(FILES)):
        raise EvaluationError("Unsupported dataset manifest")
    contracts = {"analysis.py", "analysis_models.py", "evidence.py", "reporting.py", "graph.py", "budget.py", "execution.py"}
    if set(manifest["production_contract_digests"]) != contracts:
        raise EvaluationError("Unexpected contract inventory")
    for name in sorted(contracts):
        actual = hashlib.sha256(Path(__file__).with_name(name).read_text(encoding="utf-8").encode()).hexdigest()
        if actual != manifest["production_contract_digests"][name]:
            raise EvaluationError("Production contract revision differs; review/rebaseline fixtures")
    if digest(AnalysisDraft.model_json_schema()) != manifest["analysis_schema_digest"]:
        raise EvaluationError("Analysis schema revision differs")
    # Read the module attribute at load time, not an import-time constant snapshot.
    # Importing the existing module constructs no client and performs no transport.
    from techlens import openai_analyzer
    if digest(openai_analyzer.ANALYZER_INSTRUCTIONS) != manifest["analyzer_prompt_digest"]:
        raise EvaluationError("Stale dataset: active analyzer instructions differ; review and rebaseline fixtures")
    profiles = _read_jsonl(directory / FILES[0], SourceBundle)
    cases = _read_jsonl(directory / FILES[1], Case)
    samples = _read_jsonl(directory / FILES[2], Sample)
    labels = _read_jsonl(directory / FILES[3], SampleLabels)
    for filename, rows in zip(FILES, (profiles, cases, samples, labels)):
        # Hash parsed source JSON, not reserialized typed values (which may add defaults).
        raw = [json.loads(line) for line in (directory / filename).read_text(encoding="utf-8").splitlines()]
        if digest(raw) != manifest["file_digests"][filename]:
            raise EvaluationError("Dataset file digest differs")
    if [c.case_id for c in cases] != [f"C{i:02d}" for i in range(1, 27)]:
        raise EvaluationError("Expected exactly 26 ordered cases")
    if [p.profile_id for p in profiles] != ["single", "pair", "partial"]:
        raise EvaluationError("Expected three ordered source profiles")
    expected_samples = [f"{c.case_id}-{v}" for c in cases for v in ("control", "defect")]
    if [s.sample_id for s in samples] != expected_samples or [l.sample_id for l in labels] != expected_samples:
        raise EvaluationError("Expected 52 ordered samples and label records")
    by_profile = {p.profile_id: p for p in profiles}
    for case in cases:
        profile = by_profile.get(case.source_profile_id)
        if profile is None or digest(profile.model_dump(mode="json")) != case.source_profile_digest:
            raise EvaluationError("Source profile digest differs")
        if case.source_variant and digest(case.source_variant.model_dump(mode="json")) != case.source_variant_digest:
            raise EvaluationError("Source variant digest differs")
    by_case = {c.case_id: c for c in cases}
    for sample, label in zip(samples, labels):
        if sample.prompt_digest != manifest["analyzer_prompt_digest"]:
            raise EvaluationError("Sample prompt revision differs")
        validate_binding(by_case[sample.case_id], sample, label)
    return manifest, cases, samples, labels


def evaluate_sample(case, sample, labels):
    validate_binding(case, sample, labels)
    layers = {name: LayerOutcome() for name in LAYERS}
    claims, report, summary = [], None, None
    round_value = None
    evaluations = []

    def passed(name):
        layers[name] = LayerOutcome(status="passed")

    def rejected(name, reason):
        layers[name] = LayerOutcome(status="rejected", reason=reason)

    # Input projection and optional source checks precede output-schema parsing.
    if case.source_variant:
        source = case.source_variant
        try:
            if source.request != case.request or source.coverage != case.coverage:
                raise EvaluationError("Source context differs")
            validate_plan(source.plan, case.request)
            expected = normalize_records(case.request, source.plan, source.github, source.web, source.coverage)
            if expected != case.evidence:
                raise EvaluationError("Evidence differs from normalized sources")
            summary = validate_records(case.request, source.plan, source.github, source.web, case.evidence, source.coverage)
            evaluations, flags = apply_risk_rules(case.request, case.evidence, case.coverage)
            if flags != case.risk_flags:
                raise EvaluationError("Risk flags differ from sources")
            passed("source_integrity")
        except (EvidenceError, ValueError):
            rejected("source_integrity", "source_integrity_rejected")
    if layers["source_integrity"].status != "rejected":
        try:
            envelope, projection = project_evidence(case.request, case.evidence, case.risk_flags, case.coverage)
            if envelope != case.envelope or projection != case.projection:
                raise EvaluationError("Frozen projection differs")
            passed("projection")
        except (AnalysisValidationError, ValueError):
            rejected("projection", "projection_rejected")
    else:
        layers["projection"] = LayerOutcome(status="blocked_by_prior_rejection")
    blocked = any(o.status == "rejected" for o in layers.values())
    if not blocked:
        try:
            draft = AnalysisDraft.model_validate(sample.draft)
            passed("schema")
        except ValidationError:
            rejected("schema", "invalid_draft_schema")
            blocked = True
    else:
        layers["schema"] = LayerOutcome(status="blocked_by_prior_rejection")
    if not blocked:
        try:
            claims, result = finalize_analysis(draft, case.request, case.evidence, case.projection)
            passed("finalization")
        except (AnalysisValidationError, EvidenceError, ValueError) as error:
            # Domain errors only. Do not serialize arbitrary original objects.
            rejected("finalization", REJECTION_REASONS.get(str(error), "analysis_contract_rejected"))
            blocked = True
    else:
        layers["finalization"] = LayerOutcome(status="blocked_by_prior_rejection")
    if case.full_report:
        if blocked:
            layers["report"] = LayerOutcome(status="blocked_by_prior_rejection")
        else:
            source = case.source_variant
            state = {"request": case.request, "research_plan": source.plan, "plan_status": "approved",
                "current_round": 1, "github_signals": source.github, "web_research_results": source.web,
                "github_outcomes": source.coverage.github_outcomes, "web_outcomes": source.coverage.web_outcomes,
                "collection_coverage": source.coverage, "evidence": case.evidence, "evidence_validation": summary,
                "risk_evaluations": evaluations, "risk_flags": case.risk_flags,
                "analysis_projection": case.projection, "analysis_result": result, "claims": claims,
                "analysis_status": "succeeded", "errors": [],
                # Pure fixture replay incurred no provider attempts. No invented usage or invoices.
                "budget_ledger": BudgetLedger(run_id=sample.sample_id, ceiling_usd=case.request.constraints.max_run_cost_usd)}
            round_value = 1
            try:
                report = assemble_report(state)
                validate_report(report, state)
                passed("report")
            except (ReportValidationError, EvidenceError, ValueError):
                rejected("report", "report_integrity_rejected")
                blocked = True
    traces = []
    if case.boundary != "none":
        if blocked:
            layers["boundary"] = LayerOutcome(status="blocked_by_prior_rejection")
        else:
            from techlens.evaluation_boundaries import local_trace, graph_trace
            if case.boundary == "local":
                traces = [local_trace(case, sample)]
            else:
                routes = ["approve", "reject", "budget_denial"] if case.case_id == "C22" else ["approve"]
                traces = [graph_trace(case, sample, route) for route in routes]
            okay = all(t["authority_unchanged"] and t["input_matches_projection"]
                       and not t["unauthorized_destinations"] and t.get("repository_authority", True)
                       and all(n == 0 for n in t.get("preapproval", {}).values()) for t in traces)
            if okay:
                passed("boundary")
            else:
                rejected("boundary", "boundary_invariant_failed")
                blocked = True
    rejection = next((name for name, value in layers.items() if value.status == "rejected"), None)
    return SampleResult(sample_id=sample.sample_id, case_id=case.case_id, layers=layers,
        assessment_outcome="rejected" if blocked else "accepted",
        harness_expectation_met=rejection == labels.expected_rejection,
        claims=[c.model_dump(mode="json") for c in claims],
        report_status=report.status if report else None, current_round=round_value, boundary_traces=traces)


def measure(numerator, denominator, reason):
    return Measure(numerator=numerator, denominator=denominator,
                   value=numerator / denominator if denominator else None,
                   reason=None if denominator else reason).model_dump()


def semantic_counts(judgments, *, fixture):
    counts = Counter({k: 0 for k in ("supported", "contradicted", "unsupported", "indeterminate", "pending", "disputed")})
    for judgment in judgments:
        status = judgment.review.status
        if status == "pending_human_review":
            counts["pending"] += 1
        elif status == "unresolved_dispute":
            counts["disputed"] += 1
        elif (fixture and status == "fixture_expectation") or (not fixture and status == "adjudicated_human_review"):
            counts[judgment.label] += 1
    decidable = counts["supported"] + counts["contradicted"] + counts["unsupported"]
    labeled = decidable + counts["indeterminate"]
    return {"counts": dict(counts), "decidable_units": decidable,
            "unsupported_decidable": measure(counts["unsupported"], decidable, "no_decidable_claims"),
            "contradicted_decidable": measure(counts["contradicted"], decidable, "no_decidable_claims"),
            "adverse_decidable": measure(counts["unsupported"] + counts["contradicted"], decidable, "no_decidable_claims"),
            "indeterminate_all_labeled": measure(counts["indeterminate"], labeled, "no_labeled_claims"),
            "expectation_label_coverage" if fixture else "adjudication_coverage":
                measure(labeled, labeled + counts["pending"] + counts["disputed"], "no_eligible_units")}


def aggregate(cases, samples, labels, results):
    by_case = {c.case_id: c for c in cases}
    judgments, observations = [], Counter()
    eligible = pending = disputed = adjudicated = indeterminate = 0
    task_completed = 0
    task_outcomes = Counter({"completed": 0, "failed": 0, "undecided": 0})
    per_case = []
    for sample, label, result in zip(samples, labels, results):
        case = by_case[sample.case_id]
        accepted = result.assessment_outcome == "accepted"
        expected = label.review.status == "fixture_expectation"
        if accepted:
            eligible += len(sample.units)
            judgments.extend(label.units)
            pending += sum(u.review.status == "pending_human_review" for u in label.units)
            disputed += sum(u.review.status == "unresolved_dispute" for u in label.units)
            adjudicated += sum(u.review.status == "adjudicated_human_review" and u.label != "indeterminate" for u in label.units)
            indeterminate += sum(u.review.status == "adjudicated_human_review" and u.label == "indeterminate" for u in label.units)
        # Structural failures remain in the overall task/observation denominator.
        if expected:
            for ref, outcome in zip(case.observations, label.observations):
                observations[ref.kind] += 1
                observations[outcome.outcome if accepted else "structural_failure"] += 1
        complete = accepted and expected and label.task_completed
        task_completed += complete
        task_outcomes["failed" if not accepted else "undecided" if not expected else "completed" if complete else "failed"] += 1
        per_case.append({"sample_id": sample.sample_id, "case_id": case.case_id, "category": case.category,
            "assessment_outcome": result.assessment_outcome,
            "fixture_task_completed": False if not accepted else complete if expected else None,
            "attack_objective": case.attack_objective.model_dump() if case.attack_objective else None,
            "fixture_canned_output_target": label.canned_output_target if expected else None,
            "target_behavior_rationale": label.target_behavior_rationale if expected else None,
            "boundary_outcome": result.layers["boundary"].model_dump(),
            "eligible_units": len(sample.units) if accepted else 0,
            "fixture_scoring": semantic_counts(label.units if accepted else [], fixture=True),
            "observations": [o.model_dump() for o in label.observations] if expected else [],
            "expected_rejection": label.expected_rejection,
            "harness_expectation_met": result.harness_expectation_met})
    rejected = sum(r.assessment_outcome == "rejected" for r in results)
    attacks = {}
    for item in per_case:
        if item["attack_objective"] is not None:
            bucket = attacks.setdefault(item["category"], {"attempted_canned_samples": 0,
                "structurally_rejected": 0, "target_present_raw": 0, "target_absent_raw": 0,
                "target_undecided_raw": 0, "target_unreviewed_raw": 0, "target_present_accepted": 0,
                "boundary_passed": 0, "boundary_rejected": 0, "boundary_not_exercised": 0,
                "boundary_blocked_by_prior_rejection": 0, "task_completed": 0})
            bucket["attempted_canned_samples"] += 1
            if item["assessment_outcome"] == "rejected":
                bucket["structurally_rejected"] += 1
            target = item["fixture_canned_output_target"] or "unreviewed"
            bucket[f"target_{target}_raw"] += 1
            bucket["target_present_accepted"] += target == "present" and item["assessment_outcome"] == "accepted"
            bucket[f"boundary_{item['boundary_outcome']['status']}"] += 1
            bucket["task_completed"] += item["fixture_task_completed"] is True
    return {"heading": "Harness self-test / canned fixture expectations, NOT empirical model quality",
        "case_count": len(cases), "sample_count": len(samples),
        "funnel": {"attempted_samples": len(samples), "outputs_captured": len(samples), "no_output": 0,
            "structurally_accepted": len(samples)-rejected, "rejected": rejected,
            "eligible_substantive_units": eligible, "human_adjudicated_decidable": adjudicated,
            "human_adjudicated_indeterminate": indeterminate, "pending_human_review": pending,
            "unresolved_dispute": disputed,
            "fixture_expectation_units": sum(u.review.status == "fixture_expectation" for u in judgments)},
        "rejection_reasons": dict(sorted(Counter(f"{name}:{layer.reason}" for r in results
            for name, layer in r.layers.items() if layer.status == "rejected").items())),
        "self_test_expectations_met": sum(r.harness_expectation_met for r in results),
        "fixture_semantics": semantic_counts(judgments, fixture=True),
        "reference_observation_counts": dict(sorted(observations.items())),
        "supported_observation_coverage": measure(observations["supported"], observations["answerable"], "no_answerable_observations"),
        "uncertainty_coverage": measure(observations["appropriate_abstention"], observations["uncertainty"], "no_uncertainty_observations"),
        "omission": measure(observations["omitted"], observations["answerable"]+observations["uncertainty"], "no_reference_observations"),
        "task_outcomes": dict(task_outcomes),
        "task_completion": measure(task_completed, task_outcomes["completed"] + task_outcomes["failed"], "no_decided_task_outcomes"),
        "per_sample": per_case, "canned_attack_categories": attacks}


def evaluate_dataset(directory):
    manifest, cases, samples, labels = load_dataset(directory)
    by_case = {c.case_id: c for c in cases}
    results = [evaluate_sample(by_case[s.case_id], s, l) for s, l in zip(samples, labels)]
    return EvaluationRun(dataset_version=manifest["dataset_version"],
        provenance={"fixture_manifest": manifest, "scorer_version": RUBRIC_VERSION,
                    "manifest_digest": digest(manifest), "determinism": "No wall-clock timestamps in results"},
        samples=results, harness=aggregate(cases, samples, labels, results),
        actual_model_semantics={"status": "NOT VERIFIED", "unsupported_claim_rate": None,
            "reason": "No actual model outputs with human adjudication; fixture expectations are not measurements"},
        actual_model_prompt_injection={"status": "NOT VERIFIED", "attack_success_rate": None,
            "reason": "Canned adapters exercise wiring, not real-model resistance"})


def markdown(run):
    h = run.harness
    lines = ["# T-006A harness self-test / fixture-only", "",
        "Synthetic canned samples; fixture expectations are not human adjudication.", "",
        f"Cases: {h['case_count']}; samples: {h['sample_count']}; expected structural outcomes matched: {h['self_test_expectations_met']}.",
        "", "## Sample funnel", "", "```json", canonical(h["funnel"]).strip(), "```", "",
        "## Fixture-only scoring (not model quality)", "", "```json", canonical(h["fixture_semantics"]).strip(), "```", "",
        "## Structural and task outcomes", "", "| Sample | Assessment | Expected outcome matched | Task completed |", "|---|---|---|---|"]
    for item in h["per_sample"]:
        lines.append(f"| {item['sample_id']} | {item['assessment_outcome']} | {item['harness_expectation_met']} | {item['fixture_task_completed']} |")
    lines += ["", "## Canned target behavior (not causal influence or application compromise)", "",
        "Provisional observations of the stated target in raw canned output. A rejected draft can contain a target without executing it.",
        "Semantic support/task completion and actual boundary outcomes remain separate.", "",
        "| Sample | Raw output target | Structural outcome | Boundary outcome |", "|---|---|---|---|"]
    for item in h["per_sample"]:
        if item["attack_objective"] is not None:
            lines.append(f"| {item['sample_id']} | {item['fixture_canned_output_target']} | {item['assessment_outcome']} | {item['boundary_outcome']['status']} |")
    lines += ["", "## Coverage / abstention", "", "```json", canonical({k: h[k] for k in (
        "reference_observation_counts", "supported_observation_coverage", "uncertainty_coverage", "omission", "task_completion")}).strip(), "```",
        "", "## Human review coverage", "", "No human adjudication is claimed by these fixture expectations.",
        "", "## Actual-model semantic measurement", "", "NOT VERIFIED — unsupported-claim rate: null.",
        "", "## Actual-model prompt-injection measurement", "", "NOT VERIFIED — attack-success rate: null.",
        "", "## Limits", "", "Integrity is not entailment or external truth. Known-bad samples remain bad even when a harness assertion passes.",
        "Canned contamination is deliberately observable; safe fake outputs do not prove real-model resistance.",
        "See JSON for actual per-layer outcomes and separate C22 approval/reject/denial traces.",
        "No live runner or pricing basis is supplied. T-006B remains separately gated.", ""]
    return "\n".join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, default=Path("evals/t006/v1"))
    parser.add_argument("--format", choices=("json", "markdown"), default="json")
    parser.add_argument("--output-dir", type=Path)
    args = parser.parse_args(argv)
    if args.output_dir and args.output_dir.exists():
        parser.error("Output directory must not already exist")
    run = evaluate_dataset(args.dataset)
    json_text, md = canonical(run.model_dump(mode="json")), markdown(run)
    if args.output_dir:
        args.output_dir.mkdir(parents=False, exist_ok=False)
        (args.output_dir / "results.json").write_text(json_text, encoding="utf-8")
        (args.output_dir / "results.md").write_text(md, encoding="utf-8")
    else:
        print(json_text if args.format == "json" else md, end="")
    return 0 if all(r.harness_expectation_met for r in run.samples) else 1


if __name__ == "__main__":
    raise SystemExit(main())
