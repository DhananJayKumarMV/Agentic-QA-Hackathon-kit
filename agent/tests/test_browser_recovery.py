"""Generic recovery handlers against inline pages."""
from qa_agent.browser.session import launch
from qa_agent.contracts import SiteMap

LANDING = """
<main><h1>Welcome</h1><button onclick="document.body.innerHTML='<main><h1>Home</h1></main>'">Start now</button></main>
"""
ERROR = """
<main><div role="alert"><h2>Something went wrong</h2>
<button onclick="this.closest('[role=alert]').remove(); document.querySelector('main').insertAdjacentHTML('beforeend', '<p id=ok>Recovered</p>')">Try again</button></div></main>
"""
NOT_FOUND = "<main><h1>Page not found</h1></main>"


def _sm() -> SiteMap:
    return SiteMap(base_url="http://localhost", auth={
        "entry": {"key": "landing.entry", "role": "button", "name": "Start now"}, "landing_route": "/"})


async def test_recovery_handlers(tmp_path):
    b = await launch("http://localhost", headed=False, evidence_dir=tmp_path, llm=None, sitemap=_sm())
    try:
        await b.page.set_content(LANDING)
        obs = await b.recover(expect_logged_in=True)
        assert any("re-entered" in o and "ok" in o for o in obs)
        assert await b.evaluate("() => document.querySelector('h1').innerText") == "Home"

        await b.page.set_content(ERROR)
        obs = await b.recover(expect_logged_in=True)
        assert any("clicked retry" in o for o in obs)
        assert await b.evaluate("() => !!document.getElementById('ok')")

        await b.page.set_content(NOT_FOUND)
        obs = await b.recover(expect_logged_in=False)
        assert any("not-found" in o for o in obs)

        await b.page.set_content(LANDING)
        assert await b.recover(expect_logged_in=False) == []
    finally:
        await b.close()
