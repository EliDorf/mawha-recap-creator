export function chapterURL(value) {
  const url = new URL(value);
  const match = url.pathname.match(/^\/title\/([a-z0-9-]+)\/(\d+)-chapter-(\d+)\/?$/);
  if (url.protocol !== "https:" || !["comix.to", "www.comix.to"].includes(url.hostname) ||
      url.port || url.username || url.password || !match) {
    throw new Error("Paste a Comix chapter URL, such as the chapter 1 reader link.");
  }
  const number = Number(match[3]);
  if (!Number.isSafeInteger(number) || number < 1) throw new Error("Use a whole-number chapter starting at 1 or later.");
  return {url: `${url.origin}${url.pathname.replace(/\/$/, "")}`, series: match[1], number};
}

export function range(value, endValue) {
  const first = chapterURL(value);
  const end = endValue === "" || endValue == null ? null : Number(endValue);
  if (end !== null && (!Number.isSafeInteger(end) || end < first.number || end - first.number >= 500)) {
    throw new Error("Last chapter must be at or after the starting chapter, with at most 500 chapters per run.");
  }
  return {...first, end};
}

export function nextChapter(job, nextURL) {
  const current = chapterURL(job.currentURL);
  if (job.end !== null && current.number >= job.end) return null;
  if (!nextURL) {
    if (job.end !== null) throw new Error(`The reader has no next chapter after ${current.number}; requested through ${job.end}.`);
    return null;
  }
  const next = chapterURL(nextURL);
  if (next.series !== job.series || next.number !== current.number + 1) {
    throw new Error("Next chapter is missing, out of order, or belongs to another series. Stopped without skipping it.");
  }
  if (next.number - job.start >= 500) throw new Error("Reached the 500-chapter limit. Start another run to continue.");
  return next.url;
}

export function pagePath(series, chapter, page, source) {
  if (!/^[a-z0-9-]+$/.test(series) || !Number.isSafeInteger(chapter) || chapter < 1 ||
      !Number.isSafeInteger(page) || page < 1) throw new Error("Invalid chapter or page number.");
  const url = new URL(source);
  if (!['https:', 'data:'].includes(url.protocol)) throw new Error("Unsupported image URL.");
  let extension = url.pathname.match(/\.(png|webp|jpe?g)$/i)?.[1]?.toLowerCase();
  if (url.protocol === 'data:') {
    if (!source.startsWith('data:image/png;base64,')) throw new Error('Expected a rendered PNG page.');
    extension = 'png';
  }
  // Some image CDNs use opaque URLs. Chrome determines the actual image MIME
  // type before saving; the background filename hook replaces this placeholder.
  extension ||= 'download';
  return `Manhwa Downloads/${series}/input/ch${String(chapter).padStart(3, '0')}/${String(page).padStart(4, '0')}.${extension}`;
}

export function imageExtension(mime) {
  const extension = {'image/png':'png', 'image/jpeg':'jpg', 'image/webp':'webp'}[mime?.split(';')[0]?.toLowerCase()];
  if (!extension) throw new Error(`The server did not return a supported page image (${mime || 'unknown type'}).`);
  return extension;
}

export function publicManifest(job) {
  return {
    version: 1, series: job.series, starting_chapter: job.start, last_chapter: job.end,
    status: job.status, updated_at: new Date().toISOString(),
    chapters: Object.entries(job.chapters).map(([number, ch]) => ({
      chapter: Number(number), source: ch.url, expected_pages: ch.expected,
      complete: Object.keys(ch.pages).length === ch.expected,
      pages: Object.entries(ch.pages).map(([page, p]) => ({page: Number(page), file: p.file, bytes: p.bytes}))
    }))
  };
}
