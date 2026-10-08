(function () {
  'use strict';
  function boxesForPage(snippets, width, height) {
    var boxes = [], seen = new Set();
    (snippets || []).forEach(function (snippet) {
      (snippet.highlights || []).forEach(function (group) {
        group.forEach(function (hit) {
          var region = (snippet.regions || [])[hit.parentRegionIdx];
          var page = region && (snippet.pages || [])[region.pageIdx];
          if (!page || page.width !== width || page.height !== height) return;
          var coordinates = [hit.ulx, hit.uly, hit.lrx, hit.lry];
          if (!coordinates.every(Number.isFinite) || hit.ulx < 0 || hit.uly < 0 ||
              hit.lrx <= hit.ulx || hit.lry <= hit.uly || hit.lrx > width || hit.lry > height) return;
          var key = coordinates.join(',');
          if (seen.has(key)) return;
          seen.add(key);
          boxes.push({x: hit.ulx, y: hit.uly, width: hit.lrx - hit.ulx, height: hit.lry - hit.uly});
        });
      });
    });
    return boxes;
  }
  if (typeof module !== 'undefined') module.exports = {boxesForPage: boxesForPage};
  if (typeof document === 'undefined') return;
  // A separate stylesheet avoids stale cached pilot styles after this upgrade.
  var stylesheet = document.createElement('link');
  stylesheet.rel = 'stylesheet';
  stylesheet.href = document.currentScript.src.replace(/ocr_highlights\.js.*$/, 'ocr_highlights.css?v=1');
  document.head.appendChild(stylesheet);

  document.addEventListener('digital-viewer:open', function (event) {
    var root = event.target.closest('.ocr-search[data-ocr-highlights]');
    if (!root) return;
    var viewer = event.detail.viewer;
    var image = viewer.world.getItemAt(0);
    if (!image) return;
    var size = image.getContentSize();
    var payload = JSON.parse(root.dataset.ocrHighlights);
    var boxes = boxesForPage(payload.snippets, size.x, size.y);
    var controls = root.querySelector('.ocr-match-controls');
    var status = controls.querySelector('[role="status"]');
    controls.hidden = false;
    if (!boxes.length) {
      status.textContent = payload.error || 'No highlight boxes available on this page for this search.';
      return;
    }
    var overlays = boxes.map(function (box) {
      var element = document.createElement('div');
      element.className = 'ocr-word-highlight';
      element.setAttribute('aria-hidden', 'true');
      viewer.addOverlay({element: element, location: image.imageToViewportRectangle(box.x, box.y, box.width, box.height), checkResize: false});
      return element;
    });
    var selected = -1;
    function focusMatch() {
      overlays.forEach(function (element, index) { element.classList.toggle('ocr-word-current', index === selected); });
      var box = boxes[selected];
      var paddingX = Math.max(box.width, size.x * 0.07);
      var paddingY = Math.max(box.height, size.y * 0.025);
      viewer.viewport.fitBounds(image.imageToViewportRectangle(box.x - paddingX, box.y - paddingY,
        box.width + paddingX * 2, box.height + paddingY * 2));
      status.textContent = 'Match ' + (selected + 1) + ' of ' + boxes.length + ' shown' +
        (payload.partial ? ' (partial search results)' : '') + '.';
    }
    function describeAll() {
      selected = -1;
      overlays.forEach(function (element) { element.classList.toggle('ocr-word-current', false); });
      status.textContent = boxes.length + ' highlighted word' + (boxes.length === 1 ? '' : 's') + ' shown' +
        (payload.partial ? ' (partial search results)' : '') + '.';
    }
    controls.querySelectorAll('button').forEach(function (button) { button.hidden = false; });
    controls.querySelector('[data-ocr-next]').onclick = function () { selected = (selected + 1) % boxes.length; focusMatch(); };
    controls.querySelector('[data-ocr-previous]').onclick = function () { selected = selected < 0 ? boxes.length - 1 : (selected + boxes.length - 1) % boxes.length; focusMatch(); };
    controls.querySelector('[data-ocr-fit]').onclick = function () { viewer.viewport.goHome(); describeAll(); };
    describeAll();
  });
})();
