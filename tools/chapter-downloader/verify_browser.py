"""Isolated extension integration test. Uses synthetic pages; never fetches real chapters.

Run: python -m pip install playwright pillow; python -m playwright install chromium
     python tools/chapter-downloader/verify_browser.py
"""

from __future__ import annotations

import base64
import io
import json
import tempfile
import time
from pathlib import Path

from PIL import Image
from playwright.sync_api import sync_playwright

EXTENSION = Path(__file__).resolve().parent
FIRST = "https://comix.to/title/test-story/101-chapter-1"
SECOND = "https://comix.to/title/test-story/102-chapter-2"


def fixture(chapter: int) -> str:
    output = io.BytesIO()
    Image.new("RGB", (600, 900), "#687aa0" if chapter == 1 else "#a08068").save(output, format="PNG")
    image = "data:image/png;base64," + base64.b64encode(output.getvalue()).decode()
    pages = []
    for number in range(1, 4 if chapter == 1 else 3):
        media = ('<canvas class="rpage-page__img" width="600" height="900"></canvas>' if number == 2 else
                 f'<img class="rpage-page__img" width="600" height="900" data-src="{image}">')
        pages.append(f'<div class="rpage-page is-loading" data-page="{number}" style="height:900px">{media}</div>')
    sync = json.dumps({"next_chapter_url": SECOND if chapter == 1 else ""})
    return ('<!doctype html><html><body>' + ''.join(pages) +
            f'<script id="syncData" type="application/json">{sync}</script>' + '''<script>
      const observer = new IntersectionObserver(entries => entries.forEach(({target, isIntersecting}) => {
        if (!isIntersecting) return;
        const img = target.querySelector('img');
        if (img) {img.onload = () => target.classList.remove('is-loading'); img.src = img.dataset.src;}
        else {const canvas = target.querySelector('canvas'); const c = canvas.getContext('2d');
          c.fillStyle='#876ab0'; c.fillRect(0,0,600,900); target.classList.remove('is-loading');}
      }));
      document.querySelectorAll('.rpage-page').forEach(e=>observer.observe(e));
    </script></body></html>''')


def wait_job(page, statuses: set[str], timeout: int = 90) -> dict:
    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        job = page.evaluate("async () => (await chrome.storage.local.get('job')).job")
        if job and job.get('detail') != last:
            last = job.get('detail')
            print(last, flush=True)
        if job and job['status'] in statuses:
            return job
        page.wait_for_timeout(300)
    raise AssertionError(f"Job did not reach {statuses}: {job}")


def main() -> None:
    with tempfile.TemporaryDirectory(prefix='manhwa-extension-test-') as directory, sync_playwright() as p:
        root = Path(directory)
        profile = root / 'profile'
        (profile / 'Default').mkdir(parents=True)
        downloads = root / 'downloads'
        downloads.mkdir()
        (profile / 'Default/Preferences').write_text(json.dumps({'download': {
            'default_directory': str(downloads), 'prompt_for_download': False,
        }}))
        context = p.chromium.launch_persistent_context(
            str(profile), channel='chromium', headless=True, accept_downloads=True,
            args=[f'--disable-extensions-except={EXTENSION}', f'--load-extension={EXTENSION}',
                  '--host-resolver-rules=MAP comix.to 127.0.0.1'],
        )
        def serve(route):
            print('Fixture request:', route.request.url, flush=True)
            route.fulfill(status=200, content_type='text/html', body=fixture(2 if 'chapter-2' in route.request.url else 1))
        context.route('https://comix.to/**', serve)
        worker = context.service_workers[0] if context.service_workers else context.wait_for_event('serviceworker')
        extension_id = worker.url.split('/')[2]
        popup = context.new_page()
        # Playwright normally renames downloads to GUIDs. In this isolated test
        # profile, use Chrome's normal filenames so extension manifests can be checked.
        session = context.new_cdp_session(popup)
        session.send('Browser.setDownloadBehavior', {'behavior': 'default'})
        popup.goto(f'chrome-extension://{extension_id}/popup.html')
        popup.locator('#url').fill(FIRST)
        popup.locator('#end').fill('2')
        with context.expect_page() as opened:
            popup.locator('#start').click()
        # Chrome extension-created tabs start navigating before Playwright attaches.
        # Reload once attached so the route supplies the synthetic reader document.
        opened.value.goto(FIRST)
        job = wait_job(popup, {'complete', 'error'})
        if job['status'] == 'error':
            print('Downloads:', popup.evaluate('async () => (await chrome.downloads.search({})).map(({filename,mime,state})=>({filename,mime,state}))'), flush=True)
            for tab in context.pages:
                print('Tab:', tab.url, tab.locator('body').inner_text()[:500], flush=True)
        assert job['status'] == 'complete', job['detail']
        assert [len(job['chapters'][str(n)]['pages']) for n in [1, 2]] == [3, 2]
        for chapter in job['chapters'].values():
            for item in chapter['pages'].values():
                saved = popup.evaluate('async id => (await chrome.downloads.search({id}))[0]', item['id'])
                assert saved['state'] == 'complete' and saved['fileSize'] > 0
                with Image.open(saved['filename']) as image:
                    assert image.size == (600, 900)
        popup.screenshot(path='/tmp/manhwa-downloader-ui.png')
        # Resume skips completed files and refetches a missing download.
        victim = job['chapters']['1']['pages']['2']['id']
        popup.evaluate('id => chrome.downloads.removeFile(id)', victim)
        popup.evaluate('async url => {const {job} = await chrome.storage.local.get("job"); job.status="stopped"; job.currentURL=url; await chrome.storage.local.set({job});}', FIRST)
        popup.reload()
        with context.expect_page() as opened:
            popup.locator('#resume').click()
        opened.value.goto(FIRST)
        resumed = wait_job(popup, {'complete', 'error'})
        assert resumed['status'] == 'complete', resumed['detail']
        assert resumed['chapters']['1']['pages']['1']['id'] == job['chapters']['1']['pages']['1']['id']
        assert resumed['chapters']['1']['pages']['2']['id'] != victim
        # Explicit range must not silently complete if the site's next link ends early.
        popup.locator('#url').fill(SECOND)
        popup.locator('#end').fill('3')
        with context.expect_page() as opened:
            popup.locator('#start').click()
        opened.value.goto(SECOND)
        incomplete = wait_job(popup, {'error', 'complete'})
        assert incomplete['status'] == 'error' and 'no next chapter' in incomplete['detail']
        context.close()
        print('PASS: loaded extension, lazy image/canvas pages, ordered chapters, verified downloads, resume, missing-file retry, missing-next error.')


if __name__ == '__main__':
    main()
