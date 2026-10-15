"""CLI for human approval gate sign-off."""

import argparse
import sys

from audit.events import record_approval


def main() -> None:
    """CLI entry point to record human approval or rejection."""
    parser = argparse.ArgumentParser(
        description="Record human sign-off for an irreversible BidBox action.",
    )
    parser.add_argument("run_id", help="Investigation run ID to authorize")
    parser.add_argument(
        "--approver",
        required=True,
        help="Full name of the human approver (mandatory)",
    )
    parser.add_argument(
        "--role",
        default="Procurement Review Officer",
        help="Official role or title of the approver",
    )
    parser.add_argument(
        "--decision",
        choices=["approve", "reject"],
        default="approve",
        help="Decision (approve or reject)",
    )
    parser.add_argument(
        "--action",
        default="create_investigation_file",
        help="The action being authorized",
    )
    parser.add_argument(
        "--reason",
        default="Authorized by human reviewer",
        help="Explanation or notes for the decision",
    )

    args = parser.parse_args()

    try:
        res = record_approval(
            run_id=args.run_id,
            action=args.action,
            approver_name=args.approver,
            approver_role=args.role,
            decision=args.decision,
            reason=args.reason,
        )
        print(
            f"Gate recorded: {args.decision.upper()} for run '{args.run_id}' "
            f"by {args.approver} ({args.role}). Seq: {res.get('seq')}"
        )
    except Exception as exc:
        print(f"Approval gate failed: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
