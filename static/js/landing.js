/* Landing-page interactive frame selector — progressive enhancement.
 *
 * The hero authors a row of frames (one per audience) as a plain list. One
 * frame carries `option--is-open` so a frame is always open, even before this
 * script runs. CSS already opens a frame on hover or keyboard focus, so the
 * selector works with no JavaScript at all.
 *
 * This script adds the click-to-choose behaviour people expect on touch and
 * mouse: click (or press) a frame and it stays open while the others close,
 * until another is chosen. It only moves a class and keeps `aria-expanded`
 * honest; all the motion lives in landing.css.
 */
(function () {
  "use strict";

  var OPEN_CLASS = "option--is-open";

  document.addEventListener("DOMContentLoaded", function () {
    var selector = document.querySelector("[data-selector]");
    if (!selector) {
      return;
    }

    var options = Array.prototype.slice.call(
      selector.querySelectorAll("[data-option]")
    );
    if (options.length < 2) {
      return; // Nothing to switch between.
    }

    function open(option) {
      if (option.classList.contains(OPEN_CLASS)) {
        return;
      }
      options.forEach(function (other) {
        var isTarget = other === option;
        other.classList.toggle(OPEN_CLASS, isTarget);
        var trigger = other.querySelector(".option__trigger");
        if (trigger) {
          trigger.setAttribute("aria-expanded", isTarget ? "true" : "false");
        }
      });
    }

    // One listener on the row: a click anywhere in a frame opens that frame.
    selector.addEventListener("click", function (event) {
      // Let the "way in" link inside an open frame navigate normally.
      if (event.target.closest(".option__cta")) {
        return;
      }
      var option = event.target.closest("[data-option]");
      if (option && selector.contains(option)) {
        open(option);
      }
    });
  });
})();
