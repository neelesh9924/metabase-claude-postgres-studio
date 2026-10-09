"""Query results as plain text, for the command line and for Claude."""


def format_result(result, show=20):
    columns = [c["name"] for c in result["columns"]]
    rows = [["" if v is None else str(v) for v in row] for row in result["rows"][:show]]
    widths = [min(40, max([len(c)] + [len(r[i]) for r in rows])) for i, c in enumerate(columns)]

    def line(cells):
        return "  ".join(c[:w].ljust(w) for c, w in zip(cells, widths)).rstrip()

    out = [line(columns), line(["-" * w for w in widths])]
    out += [line(row) for row in rows]
    count = result["row_count"]
    total = f"{count}{'+' if result['truncated'] else ''} {'row' if count == 1 else 'rows'}"
    shown = f" (showing {len(rows)})" if len(rows) < count else ""
    out += ["", f"{total}{shown} | {result['ms']} ms | plan cost {result['cost']:,.0f}"]
    hidden = [c["name"] for c in result["columns"] if c["pii"]]
    if hidden:
        out.append("Personal data hidden in: " + ", ".join(hidden) + ". Leave these columns out of cards.")
    return "\n".join(out)
