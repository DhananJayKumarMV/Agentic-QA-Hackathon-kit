"""Deterministic oracles: one pure function per OracleType.

Each oracle reads only the Executor's probe output (`run.observations["probes"][i]`),
the before/after snapshots and `run.network` (see CONVENTIONS.md section 4) and
returns an OracleResult whose `expected` / `observed` strings are written so they can
be pasted into a bug report verbatim.

Bias: when a fact cannot be established (probe missing, probe error, unresolved
parameter, foreign activity muddying a snapshot), the result is `error`, never `fail`.
A `fail` is only returned when the observed facts contradict the expectation.
"""
from __future__ import annotations

import fnmatch
import re
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Callable, Iterable
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from ..contracts import Evidence, Oracle, OracleResult, Scenario, ScenarioRun, SiteMap

# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------


class OracleError(Exception):
    """Raised inside an oracle when its facts cannot be established (-> status error)."""


_PLACEHOLDER = re.compile(r"\{\{\s*([A-Za-z0-9_.\-]+)\s*\}\}")
_MINUS_CHARS = "−﹣－‒–—"
_NUM = re.compile(r"(?<![\d.])(\d{1,3}(?:,\d{3})+|\d+)(?:\.(\d+))?|(?<![\d])\.(\d+)")
_ALL_VALUES = {"", "all", "any", "*", "none", "null"}


def parse_money(value: Any) -> Decimal | None:
    """Parse a displayed or API money value into a Decimal.

    Handles "$1,234.56", "-$5.00", "$-5.00", "−5.00", "(5.00)", "1234.56 USD",
    "USD 1,234.56", numbers and Decimals. Returns None when no amount is present.
    """
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, Decimal):
        return value
    if isinstance(value, (int, float)):
        return Decimal(str(value))
    s = str(value).replace(" ", " ").replace(" ", " ").strip()
    for ch in _MINUS_CHARS:
        s = s.replace(ch, "-")
    m = _NUM.search(s)
    if not m:
        return None
    if m.group(3) is not None:
        num = "0." + m.group(3)
    else:
        num = m.group(1).replace(",", "") + ("." + m.group(2) if m.group(2) else "")
    try:
        amount = Decimal(num)
    except InvalidOperation:
        return None
    prefix = s[: m.start()]
    suffix = s[m.end():]
    negative = bool(re.search(r"-\s*[^\d\s\-]{0,4}\s*$", prefix))
    if not negative and re.search(r"\(\s*[^\d\s()]{0,4}\s*$", prefix) and re.match(r"\s*[A-Za-z]{0,3}\s*\)", suffix):
        negative = True  # accounting style (5.00)
    return -amount if negative else amount


def _q(d: Decimal) -> Decimal:
    return d.quantize(Decimal("0.01")) if d.as_tuple().exponent >= -2 else d


def fmt_money(d: Decimal | None) -> str:
    return "n/a" if d is None else f"{_q(d):,}"


def fmt_delta(d: Decimal | None) -> str:
    if d is None:
        return "n/a"
    q = _q(d)
    return f"+{q:,}" if q > 0 else f"{q:,}"


def get_tz(sitemap: SiteMap | None) -> ZoneInfo | timezone:
    name = (sitemap.entities.get("timezone") if sitemap else None) or None
    if not name:
        return timezone.utc
    try:
        return ZoneInfo(str(name))
    except (ZoneInfoNotFoundError, ValueError):
        return timezone.utc


def substitute(value: Any, params: dict[str, str]) -> Any:
    """Replace {{name}} placeholders recursively; raise OracleError if any is unresolved."""
    if isinstance(value, str):
        def repl(m: re.Match[str]) -> str:
            name = m.group(1)
            if name not in params:
                raise OracleError(f"unresolved parameter {{{{{name}}}}}")
            return str(params[name])
        return _PLACEHOLDER.sub(repl, value)
    if isinstance(value, dict):
        return {k: substitute(v, params) for k, v in value.items()}
    if isinstance(value, list):
        return [substitute(v, params) for v in value]
    return value


def substitute_regex(pattern: str, params: dict[str, str]) -> str:
    """Like substitute() but escapes parameter values so they match literally."""
    def repl(m: re.Match[str]) -> str:
        name = m.group(1)
        if name not in params:
            raise OracleError(f"unresolved parameter {{{{{name}}}}}")
        return re.escape(str(params[name]))
    return _PLACEHOLDER.sub(repl, pattern)


def _first(d: dict[str, Any], *keys: str) -> Any:
    for k in keys:
        if k in d and d[k] is not None:
            return d[k]
    return None


def _norm_label(v: Any) -> str:
    return re.sub(r"[\s_\-]+", " ", str(v or "")).strip().lower()


# ---------------------------------------------------------------------------
# Transaction normalisation (tolerant to field-name variants)
# ---------------------------------------------------------------------------


def tx_id(tx: dict[str, Any]) -> str | None:
    v = _first(tx, "id", "tx_id", "transaction_id", "uuid")
    return None if v is None else str(v)


def tx_type(tx: dict[str, Any]) -> str | None:
    v = _first(tx, "type", "kind", "transaction_type", "tx_type", "category")
    return None if v is None else _norm_label(v)


def tx_status(tx: dict[str, Any]) -> str | None:
    v = _first(tx, "status", "state")
    return None if v is None else _norm_label(v)


def parse_datetime(v: Any) -> datetime | date | None:
    if v is None:
        return None
    if isinstance(v, datetime):
        return v
    if isinstance(v, date):
        return v
    s = str(v).strip()
    if not s:
        return None
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", s):
        return date.fromisoformat(s)
    m = re.fullmatch(r"(\d{1,2})/(\d{1,2})/(\d{4})", s)
    if m:
        return date(int(m.group(3)), int(m.group(1)), int(m.group(2)))
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00").replace(" ", "T", 1))
    except ValueError:
        return None


def local_date(v: Any, tz: Any) -> date | None:
    d = parse_datetime(v)
    if d is None:
        return None
    if isinstance(d, datetime):
        if d.tzinfo is None:
            d = d.replace(tzinfo=timezone.utc)
        return d.astimezone(tz).date()
    return d


def tx_datetime_raw(tx: dict[str, Any]) -> Any:
    return _first(tx, "occurred_at", "date", "created_at", "timestamp", "posted_at", "datetime",
                  "time", "transaction_date", "booked_at")


def _account_refs(v: Any) -> tuple[set[str], set[str]]:
    """Return (ids, aliases) for an account reference (object, id string, or list)."""
    ids: set[str] = set()
    aliases: set[str] = set()
    if v is None:
        return ids, aliases
    if isinstance(v, list):
        for item in v:
            i, a = _account_refs(item)
            ids |= i
            aliases |= a
        return ids, aliases
    if isinstance(v, dict):
        for k in ("id", "account_id"):
            if v.get(k) is not None:
                ids.add(str(v[k]))
        if isinstance(v.get("account"), (dict, str, int)):
            i, a = _account_refs(v["account"])
            ids |= i
            aliases |= a
        for k in ("name", "masked_number", "number_masked", "display_name"):
            if v.get(k):
                aliases.add(_norm_label(v[k]))
        return ids, aliases
    ids.add(str(v))
    return ids, aliases


def tx_accounts(tx: dict[str, Any]) -> tuple[set[str], set[str]]:
    ids: set[str] = set()
    aliases: set[str] = set()
    keys = (
        "source_account", "destination_account", "source_account_id", "destination_account_id",
        "from_account", "to_account", "from_account_id", "to_account_id", "source", "destination",
        "account_id", "account", "accounts", "account_ids", "counterparty_account_id",
    )
    for k in keys:
        if k in tx:
            i, a = _account_refs(tx[k])
            ids |= i
            aliases |= a
    for k in ("account_deltas", "entries", "deltas", "legs", "postings"):
        v = tx.get(k)
        if isinstance(v, list):
            for e in v:
                if isinstance(e, dict):
                    i, a = _account_refs({kk: e[kk] for kk in ("account_id", "account") if kk in e})
                    ids |= i
                    aliases |= a
        elif isinstance(v, dict):  # {account_id: delta}
            ids |= {str(x) for x in v}
    return ids, aliases


def _parse_filter_date(v: Any) -> date | None:
    d = parse_datetime(v)
    if isinstance(d, datetime):
        return d.date()
    return d


def tx_matches_filters(tx: dict[str, Any], filters: dict[str, Any], tz: Any) -> tuple[bool, list[str]]:
    """Return (matches, list of violated filter descriptions). Raises OracleError if undecidable."""
    violations: list[str] = []
    for key, raw in filters.items():
        if raw is None or (isinstance(raw, str) and raw.strip().lower() in _ALL_VALUES):
            continue
        k = key.lower()
        if k in ("account", "account_id"):
            ids, aliases = tx_accounts(tx)
            if str(raw) not in ids and _norm_label(raw) not in aliases:
                violations.append(f"account={raw} (tx accounts: {sorted(ids) or 'none'})")
        elif k in ("type", "kind"):
            t = tx_type(tx)
            if t is None:
                raise OracleError(f"transaction {tx_id(tx)} has no type field")
            if t != _norm_label(raw):
                violations.append(f"type={raw} (tx type: {t})")
        elif k in ("status", "state"):
            s = tx_status(tx)
            if s is None:
                raise OracleError(f"transaction {tx_id(tx)} has no status field")
            if s != _norm_label(raw):
                violations.append(f"status={raw} (tx status: {s})")
        elif k in ("start_date", "end_date", "from", "to", "date_from", "date_to", "start", "end"):
            bound = _parse_filter_date(raw)
            if bound is None:
                raise OracleError(f"cannot parse date filter {key}={raw!r}")
            d = local_date(tx_datetime_raw(tx), tz)
            if d is None:
                raise OracleError(f"transaction {tx_id(tx)} has no parseable date")
            is_start = k in ("start_date", "from", "date_from", "start")
            if is_start and d < bound:
                violations.append(f"{key}={bound.isoformat()} (tx local date {d.isoformat()})")
            if not is_start and d > bound:
                violations.append(f"{key}={bound.isoformat()} (tx local date {d.isoformat()})")
        else:
            raise OracleError(f"unsupported filter {key!r}")
    return (not violations), violations


def _tx_list(v: Any) -> list[dict[str, Any]] | None:
    if isinstance(v, dict):
        for k in ("transactions", "items", "data", "results"):
            if isinstance(v.get(k), list):
                return v[k]
        return None
    if isinstance(v, list):
        return [x for x in v if isinstance(x, dict)]
    return None


def _describe_filters(filters: dict[str, Any]) -> str:
    active = {k: v for k, v in filters.items()
              if not (v is None or (isinstance(v, str) and v.strip().lower() in _ALL_VALUES))}
    return ", ".join(f"{k}={v}" for k, v in active.items()) or "no filters"


# ---------------------------------------------------------------------------
# Context passed to each oracle
# ---------------------------------------------------------------------------


class Ctx:
    def __init__(self, oracle: Oracle, run: ScenarioRun, scenario: Scenario, sitemap: SiteMap | None,
                 index: int | None):
        self.oracle = oracle
        self.run = run
        self.scenario = scenario
        self.sitemap = sitemap
        self.index = index
        self.params = dict(run.resolved_params)
        self.tz = get_tz(sitemap)

    def args(self) -> dict[str, Any]:
        return substitute(self.oracle.args, self.params)

    def probe(self) -> dict[str, Any]:
        probes = self.run.observations.get("probes")
        p: Any = None
        if self.index is None:
            raise OracleError("oracle index unknown; cannot locate probe")
        if isinstance(probes, dict):
            p = probes.get(self.index, probes.get(str(self.index)))
        elif isinstance(probes, list) and self.index < len(probes):
            p = probes[self.index]
        if p is None:
            raise OracleError("no probe output recorded for this oracle")
        if not isinstance(p, dict):
            raise OracleError(f"malformed probe output: {str(p)[:200]}")
        if p.get("error"):
            raise OracleError(f"probe failed: {p['error']}")
        return p

    def account_label(self, acc_id: str) -> str:
        for a in (self.sitemap.entities.get("accounts") or []) if self.sitemap else []:
            if isinstance(a, dict) and str(a.get("id")) == str(acc_id) and a.get("name"):
                return f"{a['name']}"
        return str(acc_id)

    def resolve_account(self, ref: str) -> str:
        ref = str(ref)
        m = _PLACEHOLDER.fullmatch(ref.strip())
        if m:
            ref = m.group(1)
        return str(self.params.get(ref, ref))


def _probe_evidence(p: dict[str, Any] | None) -> list[Evidence]:
    out: list[Evidence] = []
    for e in (p or {}).get("evidence") or []:
        try:
            out.append(e if isinstance(e, Evidence) else Evidence.model_validate(e))
        except Exception:  # noqa: BLE001 - evidence is best effort
            continue
    return out


Result = tuple[str, str, str]  # status, expected, observed


# ---------------------------------------------------------------------------
# UI oracles
# ---------------------------------------------------------------------------


def _url_matches(c: Ctx) -> Result:
    a = c.args()
    pattern = a.get("pattern")
    if not pattern:
        raise OracleError("url_matches requires 'pattern'")
    url = c.probe().get("url")
    if url is None:
        raise OracleError("probe has no url")
    parts = urlsplit(str(url))
    path_query = parts.path + (f"?{parts.query}" if parts.query else "")
    candidates = [path_query]
    if parts.fragment:
        candidates.append(path_query + "#" + parts.fragment)
        candidates.append(parts.fragment)
    ok = any(re.search(pattern, cand) for cand in candidates)
    return ("pass" if ok else "fail", f"URL path matches /{pattern}/", f"URL was {path_query or url}")


def _element_visible(c: Ctx) -> Result:
    p = c.probe()
    key = (c.oracle.args.get("target") or {}).get("key", "element")
    vis = bool(p.get("visible"))
    return ("pass" if vis else "fail", f"'{key}' is visible",
            f"'{key}' {'is visible' if vis else 'is not visible'} (matches: {p.get('count', 'n/a')})")


def _element_absent(c: Ctx) -> Result:
    p = c.probe()
    key = (c.oracle.args.get("target") or {}).get("key", "element")
    vis = bool(p.get("visible"))
    return ("fail" if vis else "pass", f"'{key}' is not shown",
            f"'{key}' {'is visible' if vis else 'is not visible'} (matches: {p.get('count', 'n/a')})")


def _text_common(c: Ctx) -> tuple[bool, str, str]:
    raw = c.oracle.args.get("pattern")
    if not raw:
        raise OracleError("text oracle requires 'pattern'")
    pattern = substitute_regex(str(raw), c.params)
    p = c.probe()
    text = p.get("text")
    if text is None:
        raise OracleError("probe has no text")
    # Page text spans many lines: "." must cross line breaks (e.g. /A.*B/ over two table rows).
    flags = re.DOTALL | (re.IGNORECASE if c.oracle.args.get("ignore_case") else 0)
    try:
        m = re.search(pattern, str(text), flags)
    except re.error as e:
        raise OracleError(f"invalid regex {pattern!r}: {e}")
    where = (c.oracle.args.get("target") or {}).get("key") or "main content"
    return bool(m), pattern, f"{where} text: {_clip(str(text))!r}" + (f" (matched {m.group(0)!r})" if m else "")


def _text_matches(c: Ctx) -> Result:
    ok, pattern, obs = _text_common(c)
    return ("pass" if ok else "fail", f"text matches /{pattern}/", obs)


def _text_not_matches(c: Ctx) -> Result:
    ok, pattern, obs = _text_common(c)
    return ("fail" if ok else "pass", f"text does not match /{pattern}/", obs)


# "(?!\d)" rather than "\b": a currency code may be glued to the number in innerText
# ("$4,369.75USD" when the code sits in a visually separate element).
DEFAULT_MONEY_PATTERN = r"\$?-?[\d,]+\.\d{2}(?!\d)"


def money_pattern(pattern: str | None) -> str:
    """Planner patterns often end the cents with ``\b``; treat that as "no third decimal" instead."""
    if not pattern:
        return DEFAULT_MONEY_PATTERN
    return re.sub(r"(\\d\{2\}|\\d\\d|\[0-9\]\{2\})\\b", r"\1(?!\\d)", str(pattern))


def _money_format(c: Ctx) -> Result:
    pattern = money_pattern(c.oracle.args.get("pattern"))
    texts = c.probe().get("texts")
    if not isinstance(texts, list) or not texts:
        raise OracleError("probe returned no texts to check")
    norm = [str(t).replace("−", "-") for t in texts]
    bad = [t for t in norm if not re.search(pattern, t)]
    exp = f"every displayed amount has 2-decimal currency format (/{pattern}/)"
    if bad:
        return "fail", exp, f"{len(bad)} of {len(texts)} values not formatted: " + ", ".join(repr(_clip(b, 40)) for b in bad[:8])
    return "pass", exp, f"all {len(texts)} values formatted correctly"


def _masked_identifier(c: Ctx) -> Result:
    p = c.probe()
    text = p.get("visible_text")
    if text is None:
        raise OracleError("probe has no visible_text")
    ids = [str(i) for i in (p.get("full_ids") or []) if i is not None and len(str(i).strip()) >= 6]
    if not ids:
        raise OracleError("probe has no full identifiers to check against")
    text = str(text)
    collapsed = re.sub(r"(?<=\d)[\s\-](?=\d)", "", text)
    leaked = [i for i in ids if i in text or (i.isdigit() and i in collapsed)]
    exp = "account identifiers are masked; no full account id/number is visible"
    if leaked:
        return "fail", exp, "full identifier(s) visible on page: " + ", ".join(leaked[:5])
    return "pass", exp, f"none of {len(ids)} full identifiers visible"


def _row_count_equals(c: Ctx) -> Result:
    a = c.args()
    try:
        n = int(str(a.get("n")).strip())
    except (TypeError, ValueError):
        raise OracleError(f"invalid n={a.get('n')!r}")
    count = c.probe().get("count")
    if count is None:
        raise OracleError("probe has no count")
    return ("pass" if int(count) == n else "fail", f"{n} row(s) displayed", f"{count} row(s) displayed")


def _empty_state_shown(c: Ctx) -> Result:
    p = c.probe()
    rows = p.get("row_count")
    if rows is None:
        raise OracleError("probe has no row_count")
    status = [s for s in (p.get("status_texts") or []) if str(s).strip()]
    alerts = [s for s in (p.get("alert_texts") or []) if str(s).strip()]
    exp = "no rows, an empty-state message, and no error alert"
    problems = []
    if int(rows) != 0:
        problems.append(f"{rows} row(s) displayed")
    if not status:
        problems.append("no empty-state/status message shown")
    if alerts:
        problems.append("error alert shown: " + "; ".join(_clip(str(x), 80) for x in alerts[:3]))
    if problems:
        return "fail", exp, "; ".join(problems)
    return "pass", exp, f"0 rows; status message {_clip(str(status[0]), 80)!r}"


_SUCCESS_WORDS = re.compile(r"\b(success|successful|successfully|succeeded|completed?|complete)\b", re.I)
_NEGATION = re.compile(r"\b(not|unable|fail\w*|error|invalid|cannot|could ?n.?t|did ?n.?t|unsuccessful|declined|rejected)\b", re.I)


def _no_success_ui(c: Ctx) -> Result:
    p = c.probe()
    alerts = p.get("alerts")
    if alerts is None:
        raise OracleError("probe has no alerts list")
    hits = []
    for al in alerts:
        if not isinstance(al, dict):
            al = {"text": str(al)}
        classes = al.get("classes") or ""
        classes = " ".join(classes) if isinstance(classes, list) else str(classes)
        words = f"{al.get('title') or ''} {al.get('text') or ''}"
        if "success" in classes.lower() or (_SUCCESS_WORDS.search(words) and not _NEGATION.search(words)):
            hits.append(_clip(words.strip() or classes, 100))
    exp = "no success confirmation is shown"
    if hits:
        return "fail", exp, "success message shown: " + "; ".join(repr(h) for h in hits[:3])
    return "pass", exp, f"{len(alerts)} alert(s), none indicating success"


def _button_disabled_during(c: Ctx) -> Result:
    p = c.probe()
    reqs = p.get("matching_requests")
    if reqs is None:
        raise OracleError("probe has no matching_requests")
    reqs = int(reqs)
    if reqs == 0:
        raise OracleError("no matching request was sent; cannot judge pending-state behaviour")
    guarded = bool(p.get("disabled_while_pending")) or bool(str(p.get("busy_text") or "").strip())
    exp = "control is disabled/busy while the request is pending and only one request is sent"
    obs = (f"disabled_while_pending={bool(p.get('disabled_while_pending'))}, busy_text={p.get('busy_text')!r}, "
           f"{reqs} matching request(s) sent")
    return ("pass" if guarded and reqs <= 1 else "fail", exp, obs)


def _no_horizontal_overflow(c: Ctx) -> Result:
    a = c.args()
    widths = a.get("widths") or []
    by = c.probe().get("by_width")
    if not isinstance(by, dict):
        raise OracleError("probe has no by_width")
    bad, missing = [], []
    for w in widths:
        m = by.get(w, by.get(str(w)))
        if not isinstance(m, dict) or m.get("scroll_width") is None or m.get("client_width") is None:
            missing.append(str(w))
            continue
        if float(m["scroll_width"]) > float(m["client_width"]) + 1:
            bad.append(f"{w}px: scrollWidth {m['scroll_width']} > clientWidth {m['client_width']}")
    exp = "no horizontal overflow at widths " + ", ".join(f"{w}px" for w in widths)
    if bad:
        return "fail", exp, "; ".join(bad)
    if missing:
        raise OracleError("no measurement for width(s) " + ", ".join(missing))
    return "pass", exp, "content fits at every width"


# ---------------------------------------------------------------------------
# Snapshot oracles
# ---------------------------------------------------------------------------


def _snapshots(c: Ctx):
    b, a = c.run.state_before, c.run.state_after
    if b is None or a is None:
        raise OracleError("before/after state snapshot missing")
    return b, a


_FOREIGN = "foreign activity during the run (transactions not created by this scenario); cannot attribute the change"


def _balance_delta(c: Ctx) -> Result:
    b, a = _snapshots(c)
    raw = c.oracle.args.get("deltas") or c.scenario.expected_money_delta
    if not raw:
        raise OracleError("balance_delta has no deltas")
    exp_parts, obs_parts, bad = [], [], False
    for acc_ref, val in raw.items():
        acc = c.resolve_account(acc_ref)
        expected = parse_money(substitute(str(val), c.params))
        if expected is None:
            raise OracleError(f"cannot parse expected delta {val!r}")
        if acc not in b.balances or acc not in a.balances:
            raise OracleError(f"account {acc_ref} ({acc}) missing from snapshot")
        before, after = parse_money(b.balances[acc]), parse_money(a.balances[acc])
        if before is None or after is None:
            raise OracleError(f"unparseable balance for account {acc}")
        actual = after - before
        label = c.account_label(acc)
        exp_parts.append(f"{label} balance changes by {fmt_delta(expected)}")
        obs_parts.append(f"{label} changed by {fmt_delta(actual)} ({fmt_money(before)} -> {fmt_money(after)})")
        if actual != expected:
            bad = True
    if bad and c.run.foreign_activity:
        raise OracleError(_FOREIGN)
    return ("fail" if bad else "pass", "; ".join(exp_parts), "; ".join(obs_parts))


def _no_state_change(c: Ctx) -> Result:
    b, a = _snapshots(c)
    changes = []
    for acc in sorted(set(b.balances) | set(a.balances)):
        before, after = parse_money(b.balances.get(acc)), parse_money(a.balances.get(acc))
        if before != after:
            changes.append(f"{c.account_label(acc)} balance {fmt_money(before)} -> {fmt_money(after)}")
    new = [t for t in a.tx_ids if t not in set(b.tx_ids)]
    if new:
        changes.append(f"{len(new)} new transaction(s): {', '.join(new[:5])}")
    exp = "no balance changes and no new transactions"
    if changes and c.run.foreign_activity:
        raise OracleError(_FOREIGN)
    return ("fail" if changes else "pass", exp, "; ".join(changes) or "balances and transactions unchanged")


def _new_tx_count(c: Ctx) -> Result:
    b, a = _snapshots(c)
    try:
        n = int(str(c.args().get("n")).strip())
    except (TypeError, ValueError):
        raise OracleError(f"invalid n={c.oracle.args.get('n')!r}")
    new = [t for t in a.tx_ids if t not in set(b.tx_ids)]
    if len(new) > n and c.run.foreign_activity:
        raise OracleError(_FOREIGN)
    return ("pass" if len(new) == n else "fail", f"exactly {n} new transaction(s)",
            f"{len(new)} new transaction(s)" + (f": {', '.join(new[:5])}" if new else ""))


def _tx_present_in_histories(c: Ctx) -> Result:
    p = c.probe()
    txid = p.get("tx_id")
    if not txid:
        raise OracleError("no transaction id identified for this scenario")
    present = p.get("present")
    if not isinstance(present, dict):
        raise OracleError("probe has no presence map")
    accounts = [c.resolve_account(x) for x in (c.oracle.args.get("accounts") or [])] or list(present)
    missing_probe = [acc for acc in accounts if acc not in present]
    absent = [acc for acc in accounts if acc in present and not present[acc]]
    exp = f"transaction {txid} appears in the history of " + ", ".join(c.account_label(x) for x in accounts)
    if absent:
        return "fail", exp, "missing from history of " + ", ".join(c.account_label(x) for x in absent)
    if missing_probe:
        raise OracleError("presence not checked for " + ", ".join(missing_probe))
    return "pass", exp, "present in every listed history"


# ---------------------------------------------------------------------------
# Data-integrity oracles (UI vs API)
# ---------------------------------------------------------------------------


def _filters(c: Ctx) -> dict[str, Any]:
    f = c.args().get("filters") or {}
    if not isinstance(f, dict):
        raise OracleError("filters must be an object")
    return f


def _rows_satisfy_filters(c: Ctx) -> Result:
    filters = _filters(c)
    p = c.probe()
    rows = p.get("rows")
    if not isinstance(rows, list):
        raise OracleError("probe has no rows")
    all_tx = _tx_list(p.get("all_tx"))
    by_id = {tx_id(t): t for t in (all_tx or []) if tx_id(t)}
    exp = f"every displayed row satisfies {_describe_filters(filters)} (dates inclusive, {c.tz})"
    violations, undecided = [], []
    for r in rows:
        if not isinstance(r, dict):
            undecided.append(str(r)[:40])
            continue
        rid = None if r.get("id") is None else str(r["id"])
        tx = by_id.get(rid) if rid else None
        if tx is None:
            # Not in the API list: fall back to what the row itself shows; never fail on guesses.
            tx = {"id": rid, "datetime": r.get("datetime")}
            if r.get("account_ids"):
                tx["account_ids"] = r["account_ids"]
            elif any(k in filters and filters[k] not in (None, "") for k in ("account", "account_id")):
                undecided.append(f"{rid or '?'} (no account info)")
                continue
            for label, text in (r.get("cells") or {}).items():
                lk = _norm_label(label)
                if lk in ("type", "kind"):
                    tx.setdefault("type", text)
                if lk in ("status", "state"):
                    tx.setdefault("status", text)
        try:
            ok, why = tx_matches_filters(tx, filters, c.tz)
        except OracleError as e:
            undecided.append(f"{rid or '?'} ({e})")
            continue
        if not ok:
            violations.append(f"row {rid}: violates " + "; ".join(why))
    if violations:
        return "fail", exp, f"{len(violations)} of {len(rows)} row(s) violate the filters: " + " | ".join(violations[:6])
    if undecided:
        raise OracleError(f"could not check {len(undecided)} row(s): " + ", ".join(undecided[:5]))
    return "pass", exp, f"all {len(rows)} displayed row(s) satisfy the filters"


def _active_filters(filters: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in (filters or {}).items()
            if not (v is None or (isinstance(v, str) and v.strip().lower() in _ALL_VALUES))}


_AMOUNT_LABEL = re.compile(r"amount|value|sum|total", re.I)
_MONEY_TOKEN = re.compile(r"[-−(]?\s?[$€£¥]?\s?[-−]?\d[\d,]*\.\d{2}(?!\d)")


_ROW_CONTENT = re.compile(r"amount|column|field|row (data|content|detail)|each row|rows? shows?", re.I)


def _about_row_content(scenario: Scenario) -> bool:
    """The scenario checks what rows display (not only which rows): row values are in scope."""
    return bool(_ROW_CONTENT.search(f"{scenario.title}\n{scenario.rationale}"))


def row_amount_mismatches(rows: Any, all_tx: list[dict[str, Any]] | None) -> list[str]:
    """Rows whose amount cell does not show the API transaction amount (sign ignored).

    Only rows with an id found in the API list and a cell labelled like "amount" holding at
    least one money value are judged; anything else is skipped (never guessed).
    """
    if not isinstance(rows, list) or not all_tx:
        return []
    by_id = {tx_id(t): t for t in all_tx if isinstance(t, dict) and tx_id(t)}
    out = []
    for r in rows:
        if not isinstance(r, dict) or r.get("id") is None:
            continue
        tx = by_id.get(str(r["id"]))
        if tx is None:
            continue
        api_amt = parse_money(_first(tx, "amount", "value", "amount_value"))
        if api_amt is None:
            continue
        cells = r.get("cells") or {}
        amount_cells = [str(v) for k, v in cells.items() if _AMOUNT_LABEL.search(str(k))]
        if not amount_cells:
            continue
        shown = [parse_money(m.group(0)) for cell in amount_cells for m in _MONEY_TOKEN.finditer(cell)]
        shown = [x for x in shown if x is not None]
        if not shown:
            continue
        if abs(api_amt) not in {abs(x) for x in shown}:
            text = " / ".join(_clip(a, 40) for a in amount_cells)
            out.append(f"row {r['id']} ({tx_type(tx)}, {tx_status(tx)}): shows {text!r}, but the record's own amount field is {fmt_money(abs(api_amt))}")
    return out


def _rows_equal_api_set(c: Ctx) -> Result:
    filters = _filters(c)
    p = c.probe()
    displayed = p.get("displayed_ids")
    all_tx = _tx_list(p.get("all_tx"))
    if not isinstance(displayed, list) or all_tx is None:
        raise OracleError("probe lacks displayed_ids or all_tx")
    if any(d is None for d in displayed):
        raise OracleError("some displayed rows have no id")
    expected: set[str] = set()
    for t in all_tx:
        tid = tx_id(t)
        if tid is None:
            raise OracleError("API transaction without id")
        ok, _ = tx_matches_filters(t, filters, c.tz)
        if ok:
            expected.add(tid)
    shown = {str(d) for d in displayed}
    missing = sorted(expected - shown)
    extra = sorted(shown - expected)
    exp = f"{len(expected)} transaction(s) matching {_describe_filters(filters)} (from the API, dates inclusive in {c.tz})"
    if not missing and not extra:
        bad_amounts = row_amount_mismatches(p.get("rows"), all_tx)
        if bad_amounts and not _active_filters(filters) and (
                bool(c.oracle.args.get("check_amounts")) or _about_row_content(c.scenario)):
            # Unfiltered: the list is meant to mirror the API records, including the amount shown.
            return ("fail", exp + ", each row showing its API amount",
                    f"UI shows the right {len(shown)} transaction(s) but {len(bad_amounts)} row(s) do not show "
                    "the transaction amount: " + " | ".join(bad_amounts[:6]))
        note = f" (note: {len(bad_amounts)} row(s) show an amount different from the API)" if bad_amounts else ""
        return "pass", exp, f"UI shows exactly those {len(shown)} transaction(s)" + note
    truncated = any(p.get(k) for k in ("truncated", "has_more", "paginated"))
    if truncated and not extra:
        raise OracleError("UI list is paginated/truncated; cannot compare full sets")
    tx_by = {tx_id(t): t for t in all_tx}
    def desc(i: str) -> str:
        t = tx_by.get(i)
        if not t:
            return i
        d = local_date(tx_datetime_raw(t), c.tz)
        return f"{i} ({tx_type(t)}, {tx_status(t)}, {d.isoformat() if d else '?'})"
    parts = [f"UI shows {len(shown)} row(s)"]
    if missing:
        parts.append(f"missing {len(missing)} expected: " + ", ".join(desc(i) for i in missing[:6]))
    if extra:
        parts.append(f"{len(extra)} unexpected: " + ", ".join(desc(i) for i in extra[:6]))
    return "fail", exp, "; ".join(parts)


def _ui_matches_api(c: Ctx) -> Result:
    p = c.probe()
    ui = p.get("ui_balances")
    api = p.get("api_accounts")
    if isinstance(api, dict):
        api = api.get("accounts")
    if not isinstance(ui, dict) or not isinstance(api, list):
        raise OracleError("probe lacks ui_balances or api_accounts")
    mismatches, compared, unparsed = [], 0, []
    for acc in api:
        if not isinstance(acc, dict):
            continue
        aid = str(_first(acc, "id", "account_id"))
        if aid not in ui:
            continue
        api_bal = parse_money(_first(acc, "balance", "available_balance", "current_balance", "ledger_balance"))
        ui_bal = parse_money(ui[aid])
        if api_bal is None or ui_bal is None:
            unparsed.append(aid)
            continue
        compared += 1
        if api_bal != ui_bal:
            name = acc.get("name") or aid
            mismatches.append(f"{name}: UI shows {ui[aid]!r}, API balance {fmt_money(api_bal)}")
    exp = "UI balances equal the API balances for every account"
    if mismatches:
        return "fail", exp, "; ".join(mismatches)
    if compared == 0:
        raise OracleError("no account could be compared" + (f" (unparseable: {', '.join(unparsed)})" if unparsed else ""))
    return "pass", exp, f"{compared} account balance(s) match"


# ---------------------------------------------------------------------------
# Network oracles
# ---------------------------------------------------------------------------


def _matching_events(c: Ctx, method: str | None, glob: str) -> list:
    out = []
    for ev in c.run.network:
        if method and ev.method.upper() != method.upper():
            continue
        parts = urlsplit(ev.url)
        path_q = parts.path + (f"?{parts.query}" if parts.query else "")
        if fnmatch.fnmatchcase(parts.path, glob) or fnmatch.fnmatchcase(path_q, glob) or fnmatch.fnmatchcase(ev.url, glob):
            out.append(ev)
    return out


def _api_status(c: Ctx) -> Result:
    a = c.args()
    glob = a.get("path_glob")
    if not glob:
        raise OracleError("api_status requires path_glob")
    method = a.get("method")
    wanted: list[int] = [int(x) for x in a.get("status_in") or ([a["status"]] if a.get("status") is not None else [])]
    if not wanted:
        raise OracleError("api_status requires status or status_in")
    events = [e for e in _matching_events(c, method, glob) if e.status > 0]
    exp = f"{method or 'any'} {glob} responds with status {' or '.join(map(str, wanted))}"
    if not events:
        raise OracleError(f"no completed {method or ''} request to {glob} was observed")
    statuses = [e.status for e in events]
    ok = any(s in wanted for s in statuses)
    return ("pass" if ok else "fail", exp, f"observed status(es): {', '.join(map(str, statuses))}")


def has_error_code(body: Any) -> str | None:
    """Return the error code if the response body carries a structured error object."""
    if not isinstance(body, dict):
        return None
    err = body.get("error")
    if isinstance(err, dict):
        code = _first(err, "code", "type", "error_code")
        if code not in (None, ""):
            return str(code)
    if body.get("error_code") not in (None, ""):
        return str(body["error_code"])
    errs = body.get("errors")
    if isinstance(errs, list):
        for e in errs:
            if isinstance(e, dict) and e.get("code") not in (None, ""):
                return str(e["code"])
    return None


def _api_error_code_present(c: Ctx) -> Result:
    a = c.args()
    glob = a.get("path_glob")
    if not glob:
        raise OracleError("api_error_code_present requires path_glob")
    events = [e for e in _matching_events(c, a.get("method"), glob) if e.status > 0]
    exp = f"error response from {a.get('method') or 'any'} {glob} contains a structured error code (error.code)"
    if not events:
        raise OracleError(f"no completed request to {glob} was observed")
    for e in events:
        code = has_error_code(e.resp_body)
        if code:
            return "pass", exp, f"status {e.status} with error code {code!r}"
    bodies = "; ".join(f"{e.status}: {_clip(repr(e.resp_body), 160)}" for e in events[:3])
    return "fail", exp, f"no error code in response(s): {bodies}"


def _llm_judge(c: Ctx) -> Result:
    q = c.oracle.args.get("question", "")
    return "skipped", f"LLM judge: {q}", "pending LLM judge"


# ---------------------------------------------------------------------------
# Registry and entry point
# ---------------------------------------------------------------------------

LAYERS: dict[str, str] = {
    "url_matches": "ui", "element_visible": "ui", "element_absent": "ui", "text_matches": "ui",
    "text_not_matches": "ui", "money_format": "ui", "masked_identifier": "both",
    "balance_delta": "api", "no_state_change": "api", "new_tx_count": "api",
    "tx_present_in_histories": "both", "rows_satisfy_filters": "both", "rows_equal_api_set": "both",
    "row_count_equals": "ui", "empty_state_shown": "ui", "ui_matches_api": "both",
    "api_status": "api", "api_error_code_present": "api", "no_success_ui": "ui",
    "button_disabled_during": "both", "no_horizontal_overflow": "ui", "llm_judge": "ui",
}

ORACLES: dict[str, Callable[[Ctx], Result]] = {
    "url_matches": _url_matches,
    "element_visible": _element_visible,
    "element_absent": _element_absent,
    "text_matches": _text_matches,
    "text_not_matches": _text_not_matches,
    "money_format": _money_format,
    "masked_identifier": _masked_identifier,
    "balance_delta": _balance_delta,
    "no_state_change": _no_state_change,
    "new_tx_count": _new_tx_count,
    "tx_present_in_histories": _tx_present_in_histories,
    "rows_satisfy_filters": _rows_satisfy_filters,
    "rows_equal_api_set": _rows_equal_api_set,
    "row_count_equals": _row_count_equals,
    "empty_state_shown": _empty_state_shown,
    "ui_matches_api": _ui_matches_api,
    "api_status": _api_status,
    "api_error_code_present": _api_error_code_present,
    "no_success_ui": _no_success_ui,
    "button_disabled_during": _button_disabled_during,
    "no_horizontal_overflow": _no_horizontal_overflow,
    "llm_judge": _llm_judge,
}


def _clip(s: str, n: int = 240) -> str:
    s = re.sub(r"\s+", " ", s).strip()
    return s if len(s) <= n else s[: n - 1] + "…"


def _find_index(oracle: Oracle, scenario: Scenario) -> int | None:
    for i, o in enumerate(scenario.oracles):
        if o is oracle:
            return i
    for i, o in enumerate(scenario.oracles):
        if o == oracle:
            return i
    return None


def check_oracle(oracle: Oracle, run: ScenarioRun, scenario: Scenario, sitemap: SiteMap | None,
                 index: int | None = None) -> OracleResult:
    """Judge one oracle. `index` is its position in scenario.oracles (probe key)."""
    if index is None:
        index = _find_index(oracle, scenario)
    c = Ctx(oracle, run, scenario, sitemap, index)
    layer = LAYERS.get(oracle.type, "ui")
    fn = ORACLES.get(oracle.type)
    probe: dict[str, Any] | None = None
    try:
        probe = c.probe()
    except OracleError:
        probe = None
    evidence = _probe_evidence(probe)
    if fn is None:
        return OracleResult(oracle=oracle, status="error", expected=oracle.type,
                            observed=f"unknown oracle type {oracle.type}", evidence=evidence, layer=layer)
    try:
        status, expected, observed = fn(c)
    except OracleError as e:
        return OracleResult(oracle=oracle, status="error", expected=_expected_hint(oracle),
                            observed=f"could not verify: {e}", evidence=evidence, layer=layer)
    except Exception as e:  # noqa: BLE001 - a bad probe must never crash the verifier
        return OracleResult(oracle=oracle, status="error", expected=_expected_hint(oracle),
                            observed=f"could not verify: {type(e).__name__}: {e}", evidence=evidence, layer=layer)
    if status == "fail" and layer != "api":
        for sr in reversed(run.steps):
            if sr.evidence:
                evidence = evidence + [e for e in sr.evidence if e not in evidence]
                break
    return OracleResult(oracle=oracle, status=status, expected=expected, observed=observed,
                        evidence=evidence, layer=layer)


def _expected_hint(oracle: Oracle) -> str:
    args = ", ".join(f"{k}={v}" for k, v in oracle.args.items())
    return f"{oracle.type}({args})"


def check_all(scenario: Scenario, run: ScenarioRun, sitemap: SiteMap | None) -> list[OracleResult]:
    return [check_oracle(o, run, scenario, sitemap, i) for i, o in enumerate(scenario.oracles)]


__all__: Iterable[str] = [
    "OracleError", "parse_money", "check_oracle", "check_all", "ORACLES", "LAYERS",
    "tx_matches_filters", "tx_accounts", "local_date", "has_error_code", "substitute",
]
