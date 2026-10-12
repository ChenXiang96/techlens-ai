"""Local MVP command boundary. Importing this module creates no clients."""
import argparse
from contextlib import ExitStack, redirect_stdout
from importlib.metadata import version
import io
import os
import platform
import sys

from techlens.application import (AssessmentSession, demo_dependencies, live_dependencies,
                                 private_execution, run_review)
from techlens.configuration import ApplicationError, read_configuration, live_configuration
from techlens.exporting import ExportDestination, export_bytes, save_request
from techlens.presentation import DEMO_NOTICE, PublicOutput, render_report, render_report_summary, safe_url
from techlens.request_builder import build_request, InputBoundaryError
from techlens.reporting import ReportValidationError
from techlens.evidence import EvidenceError
from techlens.analysis import AnalysisValidationError
from techlens.authorization import authorize_live_run, LiveAuthorizationError


class Parser(argparse.ArgumentParser):
    def error(self, message):
        # argparse's default embeds unknown arguments and their potentially secret values.
        raise ApplicationError("Invalid command arguments. Use techlens --help.")


def parser():
    root = Parser(prog="techlens", description="Local supervised technology assessment. Demo needs no credentials.")
    root.add_argument("--version", action="version", version="techlens " + version("techlens"))
    commands = root.add_subparsers(dest="command", required=True, parser_class=Parser)
    doctor = commands.add_parser("doctor", help="Local checks only; never verify keys or prices online")
    doctor.add_argument("--request")
    doctor.add_argument("--rate-card")
    request = commands.add_parser("request", help="Create a local request; never start assessment or grant live permission")
    request_commands = request.add_subparsers(dest="request_command", required=True, parser_class=Parser)
    create = request_commands.add_parser("create", help="Interactive English request builder; default budget USD 0")
    create.add_argument("--output", required=True, help="New JSON file in an existing parent directory")
    demo = commands.add_parser("demo", help="SYNTHETIC DEMO; no real research or model assessment")
    demo.add_argument("--scenario", choices=["single", "comparison", "partial"], default="single")
    demo.add_argument("--output-dir")
    demo.add_argument("--report-view", choices=["summary", "full"], default="summary")
    live = commands.add_parser("assess", help="Explicit LIVE mode; requires interactive human review and trusted prices")
    live.add_argument("--request", required=True)
    live.add_argument("--rate-card", required=True)
    live.add_argument("--output-dir")
    live.add_argument("--report-view", choices=["summary", "full"], default="summary")
    live.add_argument("--live", action="store_true", help="Request interactive authorization for this real run")
    return root


def main(argv=None, *, stdin=None, stdout=None, environ=None, factory=live_dependencies):
    stdin, stdout = stdin or sys.stdin, stdout or sys.stdout
    env = dict(os.environ if environ is None else environ)
    public = PublicOutput(env)
    session = None
    authorization = None
    phase = "initial"

    def write(text):
        public.check(text)
        stdout.write(text)
        stdout.flush()

    def read(prompt):
        write(prompt)
        line = stdin.readline(4099)
        if not line:
            raise EOFError()
        # At most 4096 data characters plus one LF or CRLF terminator.
        # Check raw length first; repeated CR is data, not arbitrary padding.
        terminator = 2 if line.endswith("\r\n") else 1 if line.endswith("\n") else 0
        if len(line) > 4096 + terminator:
            raise InputBoundaryError()
        value = line[:-terminator] if terminator else line
        public.check(value)
        return value

    def diagnostic(text):
        # Even fixed diagnostics pass the same configured-secret boundary.
        try:
            write(text + "\n")
        except ApplicationError:
            pass  # Exit code still communicates failure; never leak to explain a leak.

    try:
        # Catch known secrets even in argv before argparse help/error/version output.
        public.check(list(sys.argv[1:] if argv is None else argv))
        help_output = io.StringIO()
        help_code = None
        with redirect_stdout(help_output):
            try:
                args = parser().parse_args(argv)
            except SystemExit as exit_result:
                help_code = exit_result.code
        if help_code is not None:
            write(help_output.getvalue())
            return help_code
        with private_execution(), ExitStack() as stack:
            if args.command == "request":
                phase = "request"
                if not (stdin.isatty() and stdout.isatty()):
                    raise ApplicationError("Request creation requires an interactive terminal. No file was published.")
                public.check(args.output)
                request = build_request(read, write, public)
                if request is None:
                    write("Request creation cancelled. No assessment was started.\n")
                    return 130
                save_request(args.output, request, public)
                write("Request saved. No assessment was started and no live permission was granted.\n")
                return 0
            if args.command == "doctor":
                write(f"Python {platform.python_version()}; techlens {version('techlens')}\n")
                for name in ("OPENAI_API_KEY", "TAVILY_API_KEY", "GITHUB_TOKEN"):
                    write(f"{name}: {'present' if env.get(name, '').strip() else 'missing'}\n")
                if bool(args.request) != bool(args.rate_card):
                    raise ApplicationError("Supply both request and rate-card files for configuration checks.")
                if args.request:
                    request, rates = read_configuration(args.request, args.rate_card)
                    public.check(request.model_dump(mode="json"))
                    public.check(rates.model_dump(mode="json"))
                    live_configuration(request, rates, env)
                    write("Local configuration checks passed. Caller must establish price authority.\n")
                else:
                    write("Demo available. Live request/pricing not checked; missing credentials block live use.\n")
                write("No network used. Keys, provider acceptance and billing are NOT VERIFIED.\n")
                return 0
            if args.command == "demo":
                from techlens.demo import demo_request
                request, mode = demo_request(args.scenario), "demo"
                dependencies = demo_dependencies(args.scenario)
                write(DEMO_NOTICE + "\nCosts/usage are fictional.\n")
            else:
                mode = "live"
                if not args.live or not (stdin.isatty() and stdout.isatty()):
                    raise LiveAuthorizationError("Live authorization was not granted. No provider work started.")
                request, rates = read_configuration(args.request, args.rate_card)
                public.check(request.model_dump(mode="json"))
                public.check(rates.model_dump(mode="json"))
                model = live_configuration(request, rates, env,
                    interactive=stdin.isatty() and stdout.isatty())
                for candidate in request.candidates:
                    safe_url(str(candidate.repository_url))
                    if candidate.homepage is not None:
                        safe_url(str(candidate.homepage))
            destination = ExportDestination(args.output_dir) if args.output_dir else None
            if mode == "live":
                authorization = authorize_live_run(request, rates, env, read=read, write=write,
                    public=public, interactive=stdin.isatty() and stdout.isatty())
                stack.callback(authorization.close)
                dependencies = factory(stack, rates, model, env, authorization=authorization)
            session = AssessmentSession(request, dependencies, authorization=authorization)
            state = run_review(session, read, write, public, mode=mode)
            scenario = args.scenario if mode == "demo" else None
            renderer = render_report if args.report_view == "full" else render_report_summary
            rendered = renderer(state, public, mode=mode, scenario=scenario)
            phase = "terminal"
            write(rendered)
            if destination:
                destination.publish(export_bytes(state, public, mode=mode, scenario=scenario), public)
                write("Export complete: report.json, report.md, manifest.json.\n")
            status_code = {"completed": 0, "rejected": 3, "partial": 4, "failed": 5}[state["report"].status]
            if args.report_view == "summary" and stdin.isatty() and stdout.isatty():
                phase = "viewer"
                while True:
                    try:
                        action = read("Report [full / done; Enter=done]: ").strip().casefold()
                    except EOFError:
                        return status_code
                    if action in {"", "done"}:
                        return status_code
                    if action == "full":
                        write(render_report(state, public, mode=mode, scenario=scenario))
                    else:
                        write("Enter full or done. This viewer performs no assessment work.\n")
            return status_code
    except (EOFError, KeyboardInterrupt):
        if phase == "viewer":
            message = "Report viewing cancelled. The terminal assessment result remains available; no assessment was restarted."
        elif phase == "terminal":
            message = "Assessment output interrupted. A validated terminal result exists; export completion is not assumed. No assessment was restarted."
        elif phase == "request":
            message = "Request creation cancelled. No assessment was started."
        else:
            with private_execution():
                message = session.interruption_summary() if session else "Cancelled before assessment initialization."
        # Summaries contain only application text and numeric accounting.
        diagnostic(message)
        return 130
    except LiveAuthorizationError as error:
        diagnostic(str(error))
        return 7
    except InputBoundaryError as error:
        diagnostic(str(error))
        if phase in {"terminal", "viewer"}:
            diagnostic("The terminal assessment result remains available; no assessment was restarted.")
        return 2
    except ApplicationError as error:
        # Fixed diagnostic messages do not contain original input or paths.
        diagnostic(str(error))
        return error.code
    except (ReportValidationError, EvidenceError, AnalysisValidationError):
        diagnostic("Assessment integrity validation failed; no report exported.")
        return 5
    except Exception:
        diagnostic("Application failed; no automatic restart. Accounting may be incomplete.")
        return 6


if __name__ == "__main__":
    raise SystemExit(main())
