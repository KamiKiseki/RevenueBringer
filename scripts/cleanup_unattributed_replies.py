"""
Remove MessageEvent rows with status=replied that are not tied to a prior outbound email send.

These usually come from POST /webhooks/reply tests or mis-configured automations without a real cold email in our DB.

Usage:
  python scripts/cleanup_unattributed_replies.py          # dry-run: print counts only
  python scripts/cleanup_unattributed_replies.py --execute
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

_root = Path(__file__).resolve().parents[1]
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

from models import MessageEvent, get_session, init_db  # noqa: E402
from tracking import orphan_replied_message_event_clause  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--execute", action="store_true", help="Delete orphan rows (default is dry-run)")
    args = parser.parse_args()

    init_db()
    with get_session() as session:
        clause = orphan_replied_message_event_clause()
        n = session.query(MessageEvent).filter(clause).count()
        print(f"orphan_replied_events={n}")
        if args.execute and n:
            deleted = session.query(MessageEvent).filter(clause).delete(synchronize_session=False)
            session.commit()
            print(f"deleted_rows={deleted}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
