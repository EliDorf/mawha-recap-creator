import {chapterURL, range, nextChapter, pagePath, publicManifest, imageExtension} from './core.mjs';

const get = async () => (await chrome.storage.local.get('job')).job;
const put = job => chrome.storage.local.set({job});
const delay = ms => new Promise(resolve => setTimeout(resolve, ms));
const pendingKey = async url => 'pending-' + [...new Uint8Array(await crypto.subtle.digest('SHA-256', new TextEncoder().encode(url)))].map(x=>x.toString(16).padStart(2,'0')).join('');

chrome.downloads.onDeterminingFilename.addListener((item, suggest) => {
  if (item.byExtensionId !== chrome.runtime.id) {suggest(); return false;}
  (async () => {
    const key = await pendingKey(item.url);
    const pending = (await chrome.storage.session.get(key))[key];
    if (!pending) {suggest(); return;}
    const filename = pending.image ? pending.file.replace(/\.[a-z]+$/, '.' + imageExtension(item.mime)) : pending.file;
    suggest({filename, conflictAction: 'overwrite'});
  })().catch(() => {chrome.downloads.cancel(item.id).catch(() => {}); suggest();});
  return true;
});

async function startDownload(url, file, image = false) {
  const key = await pendingKey(url);
  await chrome.storage.session.set({[key]: {file, image}});
  try {return await chrome.downloads.download({url, filename: file, conflictAction: 'overwrite', saveAs: false});}
  catch (error) {await chrome.storage.session.remove(key); throw error;}
}

async function checked(runId, sender) {
  const job = await get();
  if (!job || job.runId !== runId || job.status !== 'running' || job.tabId !== sender.tab?.id) {
    throw new Error('Download is stopped or belongs to another tab.');
  }
  return job;
}

async function waitDownload(id) {
  for (let attempt = 0; attempt < 180; attempt++) {
    const [item] = await chrome.downloads.search({id});
    if (!item || item.state === 'interrupted') {
      if (item) await chrome.storage.session.remove(await pendingKey(item.url));
      throw new Error(`Page download failed: ${item?.error || 'download not found'}`);
    }
    if (item.state === 'complete') {
      await chrome.storage.session.remove(await pendingKey(item.url));
      if (item.fileSize <= 0) throw new Error('Downloaded page is empty.');
      return item;
    }
    await delay(500);
  }
  await chrome.downloads.cancel(id).catch(() => {});
  throw new Error('Page download timed out. Resume to retry.');
}

async function exportManifest(job) {
  const url = 'data:application/json;charset=utf-8,' + encodeURIComponent(JSON.stringify(publicManifest(job), null, 2));
  const id = await startDownload(url, `Manhwa Downloads/${job.folder}/download-manifest.json`);
  await waitDownload(id);
}

async function handle(message, sender) {
  if (message.type === 'status') return await get() || null;
  if (message.type === 'start') {
    const previous = await get();
    if (previous?.status === 'running') throw new Error('Stop the current download before starting another.');
    const first = range(message.url, message.end);
    const job = {runId: crypto.randomUUID(), series: first.series, folder: `${first.series}-${Date.now().toString(36)}`, start: first.number, end: first.end,
      currentURL: first.url, status: 'running', chapters: {}, detail: 'Opening reader…', tabId: null};
    await put(job);
    const tab = await chrome.tabs.create({url: first.url, active: true});
    job.tabId = tab.id; await put(job); return job;
  }
  if (message.type === 'stop') {
    const job = await get();
    if (!job) return null;
    job.status = 'stopped'; job.detail = 'Stopped. Resume continues from verified downloaded pages.';
    await put(job);
    if (job.activeDownload) await chrome.downloads.cancel(job.activeDownload).catch(() => {});
    return job;
  }
  if (message.type === 'resume') {
    const job = await get();
    if (!job || job.status === 'complete') throw new Error('Start a new download first.');
    if (job.status === 'running') throw new Error('The download is already running.');
    job.runId = crypto.randomUUID(); job.status = 'running'; job.detail = 'Resuming…'; delete job.activeDownload;
    await put(job);
    const tab = await chrome.tabs.create({url: job.currentURL, active: true});
    job.tabId = tab.id; await put(job); return job;
  }
  if (message.type === 'show') {await chrome.downloads.showDefaultFolder(); return null;}
  let job = await checked(message.runId, sender);
  const current = chapterURL(job.currentURL);
  if (message.type === 'begin') {
    const actual = chapterURL(message.url);
    if (actual.url !== current.url || !Number.isInteger(message.count) || message.count < 1 || message.count > 2000) {
      throw new Error('Reader page or page count does not match the requested chapter.');
    }
    const old = job.chapters[current.number];
    if (old && (old.expected !== message.count || old.url !== current.url)) {
      throw new Error('Chapter page count changed since the previous download. Start a new run to replace it.');
    }
    job.chapters[current.number] ||= {url: current.url, expected: message.count, pages: {}};
    job.detail = `Chapter ${current.number}: found ${message.count} pages`; await put(job); return null;
  }
  if (message.type === 'page') {
    const ch = job.chapters[current.number];
    if (!ch || !Number.isInteger(message.page) || message.page < 1 || message.page > ch.expected) throw new Error('Invalid page index.');
    const file = pagePath(job.folder, current.number, message.page, message.source);
    const saved = ch.pages[message.page];
    if (saved) {
      const [item] = await chrome.downloads.search({id: saved.id});
      if (item?.state === 'complete' && item.exists && item.fileSize > 0 &&
          saved.file.replace(/\.[a-z]+$/, '') === file.replace(/\.[a-z]+$/, '')) return {cached: true};
    }
    const id = await startDownload(message.source, file, true);
    try {job = await checked(message.runId, sender);}
    catch (error) {await chrome.downloads.cancel(id).catch(() => {}); throw error;}
    job.activeDownload = id; job.detail = `Chapter ${current.number}: saving page ${message.page} / ${ch.expected}`;
    await put(job);
    const item = await waitDownload(id);
    imageExtension(item.mime);
    const actualFile = item.filename.replaceAll('\\', '/').match(/(?:^|\/)(Manhwa Downloads\/.*)$/)?.[1];
    if (!actualFile || !/\.(png|jpe?g|webp)$/.test(actualFile)) throw new Error('Chrome did not save the page with a supported image filename.');
    job = await checked(message.runId, sender);
    job.chapters[current.number].pages[message.page] = {id, file: actualFile, bytes: item.fileSize};
    delete job.activeDownload; await put(job); return {cached: false};
  }
  if (message.type === 'chapterDone') {
    const ch = job.chapters[current.number];
    if (!ch || Object.keys(ch.pages).length !== ch.expected) throw new Error('Chapter has missing pages; it was not marked complete.');
    const next = nextChapter(job, message.nextURL);
    if (next) {job.currentURL = next; job.detail = 'Opening next chapter…';}
    else {job.status = 'complete'; job.detail = `Finished through chapter ${current.number}.`;}
    await put(job); await exportManifest(job);
    if (next) await chrome.tabs.update(job.tabId, {url: next});
    return null;
  }
  if (message.type === 'error') {
    job.status = 'error'; job.detail = message.error; delete job.activeDownload;
    await put(job); await exportManifest(job); return null;
  }
  throw new Error('Unknown downloader action.');
}

chrome.runtime.onMessage.addListener((message, sender, respond) => {
  // Control actions originate in our extension popup; reader tabs only get job-scoped actions.
  if (['start', 'stop', 'resume', 'show'].includes(message.type) && sender.url !== chrome.runtime.getURL('popup.html')) {
    respond({error: 'Use the extension popup for this action.'}); return false;
  }
  handle(message, sender).then(value => respond({value}), error => respond({error: error.message}));
  return true;
});
