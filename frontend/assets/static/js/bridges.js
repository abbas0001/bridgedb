// Takes one argument, `element`, which should be a string specifying the id
// of the element whose text should be selected.
function selectText(element) {
  try {
    var range;
    var selection;
    text = document.getElementById(element);

    if (document.body.createTextRange) {
      range = document.body.createTextRange();
      range.moveToElementText(text);
      range.select();
    } else if (window.getSelection) {
      selection = window.getSelection();
      range = document.createRange();
      range.selectNodeContents(text);
      selection.removeAllRanges();
      selection.addRange(range);
    }
  } catch (e) {
    console.log(e);
  }
}

function copyText(element) {
  'use strict';
  try {
    let text = document.getElementById(element).innerText;
    navigator.clipboard.writeText(text);
  } catch (e) {
    console.log(e);
  }
}

function displayOrHide(element) {
  try {
    e = document.getElementById(element);
    if (e.classList.contains('hidden')) {
      // Don't use classList.toggle() because vendors seem to handle the
      // secondary, optional "force" parameter in different ways.
      e.classList.remove('hidden');
      e.classList.add('visible');
      e.setAttribute('aria-hidden', 'false');
    } else if (e.classList.contains('visible')) {
      e.classList.remove('visible');
      e.classList.add('hidden');
      e.setAttribute('aria-hidden', 'true');
    }
  } catch (err) {
    console.log(err);
  }
}

window.onload = function() {
  var selectBtn = document.getElementById('bridgedb-selectbtn');
  if (selectBtn && navigator.clipboard) {
    selectBtn.addEventListener('click',
      function() {
        copyText('bridgelines');
      }, false);
    // Make the 'Select All' button clickable:
    selectBtn.classList.remove('disabled');
    selectBtn.setAttribute('aria-disabled', 'false');
  }

  var bridgesContainer = document.getElementById('container-bridges');
  if (bridgesContainer) {
    var bridgeLines = document.getElementById('bridgelines');
    bridgeLines.classList.add('cursor-copy');
    bridgeLines.addEventListener('click',
      function() {
        selectText('bridgelines');
      }, false);
  }

  var qrcodeBtn = document.getElementById('bridgedb-qrcodebtn');
  if (qrcodeBtn) {
    qrcodeBtn.addEventListener('click',
      function() {
        displayOrHide('qrcode');
      }, false);
    // Remove the href attribute that opens the QRCode image as a data: URL if
    // JS is disabled:
    qrcodeBtn.removeAttribute('href');
  }

  var qrModalBtn = document.getElementById('qrcode-modal-btn');
  if (qrModalBtn) {
    qrModalBtn.addEventListener('click',
      function() {
        displayOrHide('qrcode');
      }, false);
  }
};
