(() => {
  const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
  let busy = false;
  let lastRun = null;
  async function send(message) {
    const result = await chrome.runtime.sendMessage(message);
    if (result.error) throw new Error(result.error);
    return result.value;
  }
  async function until(fn, message, timeout = 60000) {
    const deadline = Date.now() + timeout;
    while (Date.now() < deadline) {
      const value = fn(); if (value) return value;
      await sleep(300);
    }
    throw new Error(message);
  }
  async function capture(job) {
    const request = (type, extra = {}) => send({type, runId: job.runId, ...extra});
    const stalled = 'The reader did not load. Open the chapter normally, finish any sign-in or verification yourself, then Resume.';
    await until(() => document.querySelector('.rpage-page[data-page]'), stalled);
    await sleep(1200);
    const pages = [...document.querySelectorAll('.rpage-page[data-page]')]
      .sort((a, b) => Number(a.dataset.page) - Number(b.dataset.page));
    if (pages.some((p, i) => Number(p.dataset.page) !== i + 1)) throw new Error('Reader page numbering is incomplete or duplicated.');
    await request('begin', {url: location.href, count: pages.length});
    for (let i = 0; i < pages.length; i++) {
      const status = await send({type: 'status'});
      if (status.runId !== job.runId || status.status !== 'running') return;
      const holder = pages[i]; holder.scrollIntoView({block: 'center', behavior: 'instant'});
      const media = await until(() => {
        if (holder.classList.contains('is-errored')) throw new Error(`Page ${i + 1} failed to load in the reader.`);
        if (holder.classList.contains('is-loading')) return null;
        const canvas = holder.querySelector('canvas.rpage-page__img');
        const img = holder.querySelector('img.rpage-page__img');
        return canvas?.width > 0 && canvas?.height > 0 ? canvas : img?.complete && img.naturalWidth > 0 ? img : null;
      }, `Page ${i + 1} did not load. Resume to retry.`);
      const source = media instanceof HTMLCanvasElement ? media.toDataURL('image/png') : media.currentSrc || media.src;
      await request('page', {page: i + 1, source});
      await sleep(350);
    }
    if (document.querySelectorAll('.rpage-page[data-page]').length !== pages.length) throw new Error('Reader page count changed while downloading. Resume to inspect the complete chapter.');
    let sync;
    try {sync = JSON.parse(document.querySelector('#syncData')?.textContent || '{}');} catch {sync = {};}
    await request('chapterDone', {nextURL: sync.next_chapter_url || null});
  }
  setInterval(async () => {
    if (busy) return;
    try {
      const job = await send({type: 'status'});
      if (!job || job.status !== 'running' || job.runId === lastRun || new URL(job.currentURL).pathname !== location.pathname.replace(/\/$/, '')) return;
      busy = true; lastRun = job.runId;
      try {await capture(job);}
      catch (error) {await send({type: 'error', runId: job.runId, error: error.message}).catch(() => {});}
      finally {busy = false;}
    } catch { /* Extension reloaded or this tab is no longer part of a job. */ }
  }, 1000);
})();
