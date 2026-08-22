#!/bin/sh
# Stream only Empire Today claim mail for a long-running agent.
# Requires: mailkit service start
set -e
exec mailkit events stream \
  --account "${MAILKIT_ACCOUNT:-work}" \
  --mailbox INBOX \
  --subject "Empire Today" \
  --format ndjson
