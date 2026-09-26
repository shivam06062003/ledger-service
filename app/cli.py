"""Operator commands, run with `python -m app.cli <command>`.

create-api-key solves the bootstrap problem: issuing keys over the API needs
an admin key, so the very first one has to be created out of band.
"""

import argparse
import asyncio
import sys
from datetime import timedelta

from app.core.config import get_settings
from app.core.db import SessionLocal, engine
from app.schemas.api_key import ApiKeyCreate, Scope
from app.services import api_keys as api_key_service
from app.services import idempotency, reconciliation
from app.services import webhooks as webhook_service


async def _create_api_key(name: str, scopes: list[Scope]) -> None:
    async with SessionLocal() as session:
        created = await api_key_service.create_api_key(
            session, ApiKeyCreate(name=name, scopes=scopes)
        )
    # Only the key goes to stdout, so it can be captured: KEY=$(python -m app.cli ...)
    print(f"Created API key {created.id} with scopes {created.scopes}", file=sys.stderr)
    print(created.key)


async def _purge_idempotency_keys() -> None:
    retention = timedelta(hours=get_settings().idempotency_key_retention_hours)
    async with SessionLocal() as session:
        deleted = await idempotency.purge_expired(session, retention)
    print(f"Deleted {deleted} idempotency keys older than {retention}", file=sys.stderr)


async def _purge_webhook_history() -> None:
    retention = timedelta(days=get_settings().webhook_history_retention_days)
    async with SessionLocal() as session:
        deliveries, events = await webhook_service.purge_history(session, retention)
    print(
        f"Deleted {deliveries} deliveries and {events} events older than {retention}",
        file=sys.stderr,
    )


async def _reconcile() -> int:
    report = await reconciliation.reconcile()
    print(f"Ledger total:         {report.ledger_total} (must be 0)")
    print(f"Unbalanced transfers: {len(report.unbalanced_transfer_ids)}")
    print(f"Drifted accounts:     {len(report.drifted_account_ids)}")
    for account_id in report.drifted_account_ids:
        print(f"  drifted account {account_id}")
    for transfer_id in report.unbalanced_transfer_ids:
        print(f"  unbalanced transfer {transfer_id}")
    print("OK" if report.ok else "DISCREPANCIES FOUND")
    return 0 if report.ok else 1


async def _run(args: argparse.Namespace) -> int:
    try:
        if args.command == "create-api-key":
            await _create_api_key(args.name, [Scope(s) for s in args.scopes])
        elif args.command == "purge-idempotency-keys":
            await _purge_idempotency_keys()
        elif args.command == "purge-webhook-history":
            await _purge_webhook_history()
        elif args.command == "reconcile":
            return await _reconcile()
        return 0
    finally:
        await engine.dispose()


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="python -m app.cli")
    commands = parser.add_subparsers(dest="command", required=True)

    create = commands.add_parser("create-api-key", help="Issue a new API key")
    create.add_argument("--name", required=True)
    create.add_argument(
        "--scope",
        dest="scopes",
        action="append",
        required=True,
        choices=[scope.value for scope in Scope],
        help="Repeat for multiple scopes",
    )

    commands.add_parser("purge-idempotency-keys", help="Delete expired idempotency keys")
    commands.add_parser(
        "purge-webhook-history", help="Delete old succeeded deliveries and delivered events"
    )
    commands.add_parser(
        "reconcile", help="Verify ledger invariants (exit code 1 on any discrepancy)"
    )

    sys.exit(asyncio.run(_run(parser.parse_args(argv))))


if __name__ == "__main__":
    main()
