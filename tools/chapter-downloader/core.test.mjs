import test from 'node:test';
import assert from 'node:assert/strict';
import {chapterURL, range, nextChapter, pagePath, publicManifest, imageExtension} from './core.mjs';

const url = 'https://comix.to/title/test-story/123-chapter-1';
test('accepts chapter URLs and removes tracking parameters', () => {
  assert.equal(chapterURL(url + '?ref=x').url, url);
  assert.equal(range(url, '10').end, 10);
  assert.equal(range(url, '').end, null);
});
test('rejects unrelated, malformed, fractional and oversized ranges', () => {
  for (const bad of ['https://evil.com/title/test-story/123-chapter-1', 'file:///tmp/a',
    'https://comix.to/title/test-story', url + '.5', url.replace('https:', 'http:'), url.replace('comix.to', 'comix.to.evil.com')]) {
    assert.throws(() => chapterURL(bad));
  }
  for (const end of ['0', '501', '1.5', 'NaN']) assert.throws(() => range(url, end));
});
test('follows consecutive chapters and honors end, rejecting gaps and series changes', () => {
  const job = {series:'test-story', start:1, end:10, currentURL:url};
  assert.equal(nextChapter(job, url.replace('chapter-1', 'chapter-2')), url.replace('chapter-1', 'chapter-2'));
  assert.equal(nextChapter({...job, end:1}, null), null);
  assert.equal(nextChapter({...job, end:null}, null), null);
  for (const next of [null, url, url.replace('chapter-1', 'chapter-3'), url.replace('test-story','another-story')]) {
    assert.throws(() => nextChapter(job, next));
  }
});
test('saves page order, preserves image extension, and blocks unsafe output paths', () => {
  assert.equal(pagePath('test-story', 1, 12, 'https://cdn.test/page.webp?token=x'), 'Manhwa Downloads/test-story/input/ch001/0012.webp');
  assert.equal(pagePath('test-story', 10, 1, 'data:image/png;base64,abc'), 'Manhwa Downloads/test-story/input/ch010/0001.png');
  assert.throws(() => pagePath('../escape', 1, 1, 'https://cdn.test/page.png'));
  assert.throws(() => pagePath('test', 1, 1, 'data:text/html;base64,abc'));
  assert.throws(() => pagePath('test', 1, 1, 'javascript:alert(1)'));
});
test('manifest marks incomplete chapters and excludes session details and image URLs', () => {
  const manifest = publicManifest({series:'test', start:1, end:2, status:'error', runId:'private-run', chapters:{
    1:{url, expected:2, pages:{1:{id:3,file:'input/ch001/0001.png',bytes:100,source:'secret'}}}
  }});
  assert.equal(manifest.chapters[0].complete, false);
  assert.equal(manifest.chapters[0].pages[0].page, 1);
  assert.ok(!JSON.stringify(manifest).includes('private-run'));
  assert.ok(!JSON.stringify(manifest).includes('secret'));
});
test('opaque image URLs defer extension to MIME detection, rejecting HTML error responses', () => {
  assert.ok(pagePath('test', 1, 1, 'https://cdn.test/opaque-token').endsWith('0001.download'));
  assert.equal(imageExtension('image/webp'), 'webp');
  assert.equal(imageExtension('image/jpeg'), 'jpg');
  assert.throws(() => imageExtension('text/html'));
});
test('Chrome filename hook uses MIME for opaque URLs and leaves unrelated downloads alone', async () => {
  let filenameHook;
  globalThis.chrome = {
    runtime:{id:'unit-extension', onMessage:{addListener(){}}},
    storage:{session:{get:async key=>({[key]:{file:'Manhwa Downloads/test/input/ch001/0001.download',image:true}})}},
    downloads:{onDeterminingFilename:{addListener(listener){filenameHook=listener;}}}
  };
  try {
    await import('./background.js');
    let suggestion;
    suggestion = await new Promise(resolve => filenameHook({byExtensionId:'unit-extension', url:'https://cdn.test/opaque', filename:'download', mime:'image/webp'}, resolve));
    assert.equal(suggestion.filename, 'Manhwa Downloads/test/input/ch001/0001.webp');
    filenameHook({byExtensionId:'another-extension', filename:'/tmp/personal.pdf', mime:'application/pdf'}, value=>{suggestion=value;});
    assert.equal(suggestion, undefined);
  } finally {delete globalThis.chrome;}
});
