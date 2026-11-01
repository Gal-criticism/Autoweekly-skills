// Created by Miyin
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
for (const [file, entry, collection] of [
  ['render_covers.jsx', 'renderCovers', 'covers'],
  ['refine_cover_background_batch.jsx', 'refineCoverBackgroundBatch', 'items'],
]) {
  for (const reason of ['open document', 'existing output']) {
    test(`${file}: preserve ${reason} before opening a batch document`, () => {
      let opened = 0, closed = 0, report = '';
      const userDocument = { fullName: { fsName: '/batch/template.psd' }, close() { closed++; } };
      const app = { version: 'mock', documents: reason === 'open document' ? [userDocument] : [],
        activeDocument: userDocument, displayDialogs: 'original', preferences: { rulerUnits: 'original' },
        fonts: { getByName() {} }, open() { opened++; throw Error('Unexpected open'); } };
      function File(name) { return { fsName: name, exists: reason === 'existing output' && name === '/batch/T1_CG1.png',
        open() { return true; }, write(text) { report = text; }, close() {} }; }
      const job = { run_id: 'test', output_dir: '/batch', [collection]: [
        { template: '/batch/template.psd', filename: 'T1_CG1' },
      ] };
      const code = fs.readFileSync(path.join(__dirname, '../weekly-cover/scripts/runtime/photoshop', file), 'utf8');
      vm.runInNewContext(code + `\n${entry}(job);`, { app, File, job, DialogModes: { NO: 'off' }, Units: { PIXELS: 'px' } });
      assert.equal(opened, 0); assert.equal(closed, 0);
      assert.match(report, /already open|Output exists/);
      assert.equal(app.displayDialogs, 'original');
      assert.equal(app.preferences.rulerUnits, 'original');
    });
  }
}
