(function () {
  'use strict';

  window.__Joy_HCAPTCHA_LOADED = true;

  const isHcaptchaFrame = window.location.href.includes('hcaptcha.com') ||
    window.location.href.includes('hcaptcha') ||
    window.location.href.includes('newassets.hcaptcha.com');

  if (!isHcaptchaFrame) {
    return;
  }

  const checkboxSelector = '#checkbox';
  const checkedAttribute = 'data-checked';
  let clicked = false;
  let checkInterval = null;

  function tryClickCheckbox() {
    const checkbox = document.querySelector(checkboxSelector);
    if (!checkbox) {
      return false;
    }

    const isChecked = checkbox.getAttribute(checkedAttribute);
    if (isChecked === 'true') {
      if (checkInterval) {
        clearInterval(checkInterval);
        checkInterval = null;
      }
      return true;
    }

    if (isChecked !== 'true' && !clicked) {
      try {
        checkbox.click();
        clicked = true;
        setTimeout(() => {
          const newIsChecked = checkbox.getAttribute(checkedAttribute);
          if (newIsChecked === 'true') {
            if (checkInterval) {
              clearInterval(checkInterval);
              checkInterval = null;
            }
          }
        }, 500);
        return true;
      } catch (e) {
        return false;
      }
    }
    return false;
  }

  setTimeout(tryClickCheckbox, 100);
  setTimeout(tryClickCheckbox, 500);
  setTimeout(tryClickCheckbox, 1000);

  checkInterval = setInterval(function () {
    if (!clicked) {
      tryClickCheckbox();
    }
  }, 500);

  setTimeout(() => {
    if (checkInterval) {
      clearInterval(checkInterval);
      checkInterval = null;
    }
  }, 30000);

})();
