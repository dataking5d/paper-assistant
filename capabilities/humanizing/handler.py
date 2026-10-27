"""Legacy Router entry point.

The default workflow is host-agent driven: run `python main.py humanize-scan`
to get the read-only report, then generate an edits.json and use the existing
validate-only / attempt flow. This handler intentionally does not rewrite.
"""


def handle(context):
    return {
        "capability": "humanizing",
        "status": "manual",
        "note": "Run `python main.py humanize-scan <docx> --json`; rewrite only through edits.json.",
    }
