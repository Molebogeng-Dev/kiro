/* Small progressive enhancements. Every page works with this file blocked.
 *
 * There is no framework here on purpose. The app has to load quickly on a phone
 * with little data, and none of these behaviours are worth a dependency.
 */
(function () {
  "use strict";

  /* ------------------------------------------------------------------------
     Filter a long dropdown by typing.
     Used for the learner picker: a native select is familiar and works without
     JavaScript, and this makes it bearable once a school has 300 learners.
     ------------------------------------------------------------------------ */
  function makeFilterable(select) {
    var options = Array.prototype.slice
      .call(select.options)
      .map(function (option) {
        return { value: option.value, text: option.text };
      });

    if (options.length < 8) {
      return; // Short list: a filter box would be more clutter than help.
    }

    var wrapper = document.createElement("div");
    wrapper.className = "filter-box";

    var label = document.createElement("label");
    label.className = "form__help";
    label.setAttribute("for", select.id + "_filter");
    label.textContent = select.dataset.filterLabel || "Type to narrow the list";

    var input = document.createElement("input");
    input.type = "search";
    input.id = select.id + "_filter";
    input.className = "filter-box__input";
    input.autocomplete = "off";
    input.setAttribute("aria-controls", select.id);

    var status = document.createElement("p");
    status.className = "form__help";
    status.setAttribute("role", "status");
    status.setAttribute("aria-live", "polite");

    wrapper.appendChild(label);
    wrapper.appendChild(input);
    wrapper.appendChild(status);
    select.parentNode.insertBefore(wrapper, select);

    input.addEventListener("input", function () {
      var needle = input.value.trim().toLowerCase();
      var kept = 0;
      var previous = select.value;

      select.innerHTML = "";
      options.forEach(function (option) {
        var isPlaceholder = option.value === "";
        if (!isPlaceholder && needle && option.text.toLowerCase().indexOf(needle) === -1) {
          return;
        }
        if (!isPlaceholder) {
          kept += 1;
        }
        var element = document.createElement("option");
        element.value = option.value;
        element.text = option.text;
        select.appendChild(element);
      });

      // Keep the teacher's choice if it survived the filter.
      select.value = previous;

      if (!needle) {
        status.textContent = "";
      } else if (kept === 0) {
        status.textContent = "No matches. Clear the box to see everyone.";
      } else {
        status.textContent = kept === 1 ? "1 match" : kept + " matches";
      }
    });
  }

  /* ------------------------------------------------------------------------
     Confirm to the teacher which photo they picked.
     A file input shows a filename in a small grey font that is easy to miss.
     ------------------------------------------------------------------------ */
  function announceFile(input) {
    var output = document.createElement("p");
    output.className = "form__help";
    output.setAttribute("role", "status");
    output.setAttribute("aria-live", "polite");
    input.parentNode.appendChild(output);

    input.addEventListener("change", function () {
      if (!input.files || !input.files.length) {
        output.textContent = "";
        return;
      }
      var file = input.files[0];
      var megabytes = file.size / 1048576;
      output.textContent =
        "Chosen: " + file.name + " (" + megabytes.toFixed(1) + " MB). " +
        "It will be shrunk before sending.";
    });
  }

  /* ------------------------------------------------------------------------
     Say something while marking runs.
     Marking is synchronous and can take up to a minute. Without feedback a
     teacher reasonably assumes nothing happened and clicks again, which would
     upload and mark the same paper twice.
     ------------------------------------------------------------------------ */
  function showProgressOnSubmit(form) {
    var note = form.querySelector("[data-busy-note]");

    form.addEventListener("submit", function (event) {
      // A form may have more than one submit button (for example "Read the
      // photo" and "Save"). Only the button actually pressed should go busy,
      // and only if it opted in with data-busy-button. event.submitter is the
      // pressed control; fall back to the first opted-in button for older
      // browsers.
      var button = event.submitter;
      if (!button || !button.hasAttribute("data-busy-button")) {
        button = form.querySelector("[data-busy-button]");
        if (event.submitter && button !== event.submitter) {
          // A different, non-busy button was pressed (e.g. Save). Leave it be.
          return;
        }
      }
      if (!button) {
        return;
      }

      var busyLabel = button.dataset.busyLabel || "Working…";
      button.disabled = true;
      button.setAttribute("aria-disabled", "true");
      button.textContent = busyLabel;
      form.setAttribute("aria-busy", "true");
      if (note) {
        note.hidden = false;
      }
    });
  }

  document.addEventListener("DOMContentLoaded", function () {
    document.querySelectorAll("select[data-filterable]").forEach(makeFilterable);
    document.querySelectorAll('input[type="file"]').forEach(announceFile);
    document.querySelectorAll("form[data-busy]").forEach(showProgressOnSubmit);
  });
})();
