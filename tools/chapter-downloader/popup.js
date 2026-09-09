const $ = id => document.getElementById(id);
let shownRun = null;
async function request(type, data = {}) {
  const result = await chrome.runtime.sendMessage({type, ...data});
  if (result.error) throw new Error(result.error);
  return result.value;
}
async function refresh() {
  const job = await request('status');
  $('stop').disabled = job?.status !== 'running';
  $('resume').disabled = !job || ['running', 'complete'].includes(job.status);
  $('start').disabled = job?.status === 'running';
  if (!job) return;
  if (shownRun !== job.runId) {
    shownRun = job.runId; $('url').value = job.currentURL; $('end').value = job.end ?? '';
  }
  const chapters = Object.values(job.chapters);
  const total = chapters.reduce((n, ch) => n + ch.expected, 0);
  const saved = chapters.reduce((n, ch) => n + Object.keys(ch.pages).length, 0);
  $('status').textContent = {running:'Downloading', error:'Needs attention', stopped:'Stopped', complete:'Complete'}[job.status];
  $('count').textContent = `${saved} / ${total} pages`;
  $('progress').max = total || 1; $('progress').value = saved;
  $('detail').textContent = job.detail;
}
async function act(type, data) {
  try {await request(type, data); await refresh();}
  catch (error) {$('detail').textContent = error.message;}
}
$('form').addEventListener('submit', event => {event.preventDefault(); act('start', {url:$('url').value, end:$('end').value});});
for (const action of ['stop', 'resume', 'show']) $(action).addEventListener('click', () => act(action));
refresh().catch(error => {$('detail').textContent = error.message;});
chrome.tabs.query({active:true, currentWindow:true}).then(([tab]) => {
  if (!$('url').value && tab?.url) {
    try {$('url').value = chapterURL(tab.url).url;} catch { /* Open a chapter or paste its URL. */ }
  }
});
setInterval(() => refresh().catch(() => {}), 1000);
import {chapterURL} from './core.mjs';
