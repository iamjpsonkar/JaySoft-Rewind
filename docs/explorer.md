# Explore a captured request

[Documentation home](index.md)

Turn a snapshot into a readable, standalone HTML report:

```bash
rewind explore .rewind/snapshots/<snapshot-id>.rewind.json --output report.html
```

You can also pass a snapshot ID and its store:

```bash
rewind explore <snapshot-id> --store .rewind/snapshots --output report.html
```

Open `report.html` in your browser. It works offline: styles and controls are
embedded, and it uses no CDN, remote fonts, analytics or external assets. You do
not need the application source or an application factory to explore an artifact.

## What the report shows

- **Overview:** capture completeness, exclusion reasons, application identity,
  Python and dependency versions, producer version, and capture policy.
- **Request / input:** decoded inbound ASGI/WSGI request data or callable arguments.
- **Dependency calls:** recorded order, operation and dependency names, arguments,
  returned values or exception arguments, and available observation durations.
- **Final outcome:** the recorded handler result or exception type and arguments.
- **Optional diagnostics:** recorded function/span events, offsets, elapsed times
  and the count of dropped diagnostic events, when available.

Expand individual sections, expand all, or filter dependency calls and diagnostics
by text. Search covers only rendered previews, not omitted data. Browser JavaScript
powers these local controls; the content and native collapsible sections remain
readable when JavaScript is disabled.

Incomplete recordings can still be explored. Their reasons explain why strict
replay may be ineligible; exploration does not turn an incomplete capture into a
complete one. Diagnostics show only captured events. The report does not invent
source execution, unrecorded local variables, a full stack trace, or task scheduling.

## Privacy and display limits

The report contains captured data and may contain sensitive values. Keep it private
like the source snapshot. The command writes an owner-only file (`0600`) atomically
and refuses to replace any existing file or symlink. Choose an existing destination
directory with suitable access controls; its permissions are not changed. Use a new
filename when generating another report.

Snapshots are validated before rendering, without importing application code.
Payloads are escaped as text, including HTML-looking strings and `</script>`.
The embedded content-security policy blocks external resources. Payload URLs are
shown as text rather than clickable links.

The HTML report is capped at 2 MiB. Byte values show at most their first 256 bytes,
strings show at most 512 characters, collections show at most 40 entries, nesting
previews stop after eight levels, and each value preview has a 12,000-character
budget. Omitted values and detail blocks are labeled. These are display limits;
the source snapshot is unchanged. HTTP adapter base64 body fields are shown as
readable bytes, while other data retains its decoded typed representation.

## Replay after exploring

Use the report to identify the request and the dependency observation associated
with the failure. Replay still requires an explicitly selected local application
factory:

```bash
rewind replay .rewind/snapshots/<snapshot-id>.rewind.json \
  --app your_package.replay:replay_target
```

See [the CLI guide](cli.md) for replay outcomes and
[the server guide](server-guide.md) for capturing and replaying request handlers.
