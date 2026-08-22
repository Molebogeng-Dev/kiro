/* "Ask iSgela" — landing-page assistant.
 *
 * A small ChatGPT-style bar. Type a question, press Enter (or the send button),
 * and the answer is fetched from core:assistant_query and rendered just below
 * the bar. While the model is being reached a "Thinking…" indicator shows, so
 * the wait is legible. Each question and its answer stack as an exchange.
 *
 * Progressive: with JavaScript off the form still posts to its action URL, and
 * everything the model returns is written with textContent (never innerHTML),
 * so a reply can never inject markup into the page.
 */
(function () {
  "use strict";

  document.addEventListener("DOMContentLoaded", function () {
    var form = document.querySelector("[data-assistant-form]");
    if (!form) {
      return;
    }

    var input = form.querySelector("[data-assistant-input]");
    var sendButton = form.querySelector("[data-assistant-send]");
    var results = document.querySelector("[data-assistant-results]");
    var tokenField = form.querySelector("[name=csrfmiddlewaretoken]");
    var endpoint = form.getAttribute("action");
    var busy = false;

    // ---- Textarea: grow to fit, and send on Enter (Shift+Enter = newline) ----

    function autoGrow() {
      input.style.height = "auto";
      input.style.height = Math.min(input.scrollHeight, 200) + "px";
    }
    input.addEventListener("input", autoGrow);

    input.addEventListener("keydown", function (event) {
      if (event.key === "Enter" && !event.shiftKey) {
        event.preventDefault();
        form.requestSubmit ? form.requestSubmit() : submit();
      }
    });

    // ---- Rendering helpers -------------------------------------------------

    function el(tag, className, text) {
      var node = document.createElement(tag);
      if (className) {
        node.className = className;
      }
      if (text != null) {
        node.textContent = text;
      }
      return node;
    }

    // Build an exchange (question + a slot for the answer) and return the slot.
    function addExchange(question) {
      var exchange = el("div", "assistant__exchange");
      exchange.appendChild(el("p", "assistant__question", question));

      var answer = el("div", "assistant__answer");
      var thinking = el("div", "assistant__thinking");
      thinking.setAttribute("role", "status");
      thinking.appendChild(el("span", "assistant__spinner"));
      thinking.appendChild(el("span", "assistant__thinking-text", "Thinking\u2026"));
      answer.appendChild(thinking);

      exchange.appendChild(answer);
      results.appendChild(exchange);
      exchange.scrollIntoView({ behavior: "smooth", block: "nearest" });
      return answer;
    }

    // Replace a slot's contents with the model's reply, split into paragraphs.
    function renderAnswer(slot, text) {
      slot.textContent = "";
      var paragraphs = String(text).split(/\n{2,}/);
      paragraphs.forEach(function (para) {
        var trimmed = para.trim();
        if (!trimmed) {
          return;
        }
        var p = el("p", "assistant__answer-p");
        // Keep single line breaks inside a paragraph.
        trimmed.split(/\n/).forEach(function (line, index) {
          if (index > 0) {
            p.appendChild(document.createElement("br"));
          }
          p.appendChild(document.createTextNode(line));
        });
        slot.appendChild(p);
      });
    }

    function renderError(slot, message) {
      slot.textContent = "";
      slot.appendChild(el("p", "assistant__error", message));
    }

    // ---- Submit ------------------------------------------------------------

    function setBusy(state) {
      busy = state;
      input.disabled = state;
      sendButton.disabled = state;
    }

    function submit() {
      if (busy) {
        return;
      }
      var question = (input.value || "").trim();
      if (!question) {
        return;
      }

      var slot = addExchange(question);
      setBusy(true);
      input.value = "";
      autoGrow();

      fetch(endpoint, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "X-CSRFToken": tokenField ? tokenField.value : "",
          "X-Requested-With": "XMLHttpRequest",
        },
        body: JSON.stringify({ query: question }),
      })
        .then(function (response) {
          return response
            .json()
            .catch(function () {
              return {};
            })
            .then(function (data) {
              return { ok: response.ok, data: data };
            });
        })
        .then(function (result) {
          if (result.ok && result.data.answer) {
            renderAnswer(slot, result.data.answer);
          } else {
            renderError(
              slot,
              result.data.detail ||
                "Something went wrong reaching the assistant. Please try again."
            );
          }
        })
        .catch(function () {
          renderError(
            slot,
            "Couldn't reach the assistant. Check your connection and try again."
          );
        })
        .then(function () {
          setBusy(false);
          input.focus();
        });
    }

    form.addEventListener("submit", function (event) {
      event.preventDefault();
      submit();
    });
  });
})();
