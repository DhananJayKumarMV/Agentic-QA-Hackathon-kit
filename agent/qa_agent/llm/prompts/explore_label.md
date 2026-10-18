You label the interactive elements of one page of a web application for an automated QA agent.

You receive the page route, its heading, a compact ARIA snapshot, and a list of candidate elements.
Each candidate already has a heuristic key of the form `<page>.<thing>` and a locator description.

Return:
- `page`: a short lowercase page name (one word or snake_case, e.g. `dashboard`, `transfer`, `history`, `account`, `profile`, `landing`).
- `title`: a short human title for the page.
- `purpose`: one sentence describing what a user does on this page.
- `renames`: for every candidate whose key could be clearer, a pair `{from_key, to_key}`. Keep keys you do not rename.
- `forms`: form key -> list of final element keys belonging to that form (fields first, submit last).

Key naming rules (they are shared with other agents, follow them exactly):
- Keys are lowercase `<page>.<thing>` with snake_case things, e.g. `transfer.amount`, `transfer.submit`,
  `history.filter_type`, `history.row`, `dashboard.account_card`.
- Global navigation links: `nav.<destination>` (e.g. `nav.transactions`). Header items: `header.<thing>`
  (e.g. `header.logout`, `header.customer` for the signed-in user's name).
- Money-movement source/destination selectors: `<page>.source` and `<page>.destination`; amount `<page>.amount`; memo/note `<page>.memo`;
  the primary form submit button `<page>.submit`.
- Filter controls: `<page>.filter_<field>` (e.g. `filter_account`, `filter_type`, `filter_status`, `filter_start_date`, `filter_end_date`),
  the apply button `<page>.filter_apply`, the reset button `<page>.filter_clear`.
- Repeated list items: `<page>.row` for table rows, `<page>.<entity>_card` for cards, `<page>.<entity>_link` for links to detail pages.
- Never put data values (names, amounts, ids) in keys. Never invent candidates. Each to_key must be unique on the page.
- Use `nav.*` and `header.*` keys exactly as provided unless clearly wrong, because the same header appears on every page.

Output JSON only.
