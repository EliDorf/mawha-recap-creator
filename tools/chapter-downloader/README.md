# Manhwa Chapter Downloader

A local Chrome extension for saving complete chapters of your own manhwa from the Comix reader. It uses the normal reader tab, scrolls through every numbered page, waits for each image or canvas to load, and saves the pages in order. It does not use private API tokens or defeat sign-in or browser verification.

## Install and use

1. Open `chrome://extensions` in Chrome and enable **Developer mode**.
2. Click **Load unpacked** and select this `tools/chapter-downloader` folder. If using the ZIP, extract it first and select the folder containing `manifest.json`.
3. Open the first chapter in Chrome. Click the extension's **Manhwa Chapter Downloader** icon (pin it from Chrome's extensions menu if useful).
4. The starting chapter URL fills from the active tab. Set **Last chapter**, such as `10`, or leave it empty to follow the reader's next links to the end. Click **Start download**.

The extension opens a reader tab and shows live page counts. Keep that tab open; the popup can be closed. **Stop** cancels the current transfer. **Resume** reopens the current chapter and skips downloads that Chrome still confirms are present and complete. Missing files are downloaded again. There is a 500-chapter limit per run, and chapter gaps or unexpected series changes stop with a visible error.

Each new run creates its own folder, so it cannot mix old pages with a changed translation or shorter chapter. Resume keeps using the same folder.

```text
Downloads/Manhwa Downloads/<series>-<run>/
  download-manifest.json
  input/
    ch001/0001.webp
    ch001/0002.png
    ch001/0003.webp
    ch002/0001.webp
    ...
```

The manifest records expected page counts, completed downloads, source chapter URLs, and file sizes. It never contains API credentials. Credit pages and ads embedded among the numbered chapter pages are retained in their original positions; review and exclude those during the video pipeline's panel/script review.

To prepare a video, create a sleep project using `recap init`, then copy the downloaded `input` folder into it. Run stages 0, 1 and 2, review the script, approve, and continue the pipeline. Do not mix these complete chapters with the earlier single-image files.

## Limits and troubleshooting

- The starting URL must be a **chapter** URL, not the series overview. Whole-number chapters are supported; fractional/special chapters stop navigation for manual review.
- The reader must work normally in Chrome. If sign-in, verification, or a failed page appears, complete the normal site interaction yourself and resume. This extension does not bypass those screens.
- The extension supports the reader's `.rpage-page[data-page]` images and canvases and its `syncData` next-chapter link. If the site changes these, the downloader stops instead of guessing a page list.
- Saved image files must be PNG, JPEG or WebP. Browser canvas pages are exported as PNG at their canvas resolution. Image pages use the image URL shown by the reader; opaque CDN URLs get their file extension from the server's image MIME type. HTML error responses are rejected.
- The extension has download, local storage, and temporary active-tab permissions. Its content script runs only on Comix title pages. No service, API key, subscription or third-party upload is involved.

## Tests

```bash
node --test tools/chapter-downloader/core.test.mjs
python -m pip install playwright pillow
python -m playwright install chromium
python tools/chapter-downloader/verify_browser.py
```

The browser test loads the actual extension in an isolated profile and serves synthetic chapters without fetching a real comic. It tests lazy images, canvas pages, chapter order, actual browser downloads, resume, missing-file retry, and missing-next detection.
