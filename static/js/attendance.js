/* Camera capture + face-descriptor extraction for the attendance pages.
 *
 * This is the only part of attendance that must run in the browser: the camera
 * and face-api.js live here, but the *decisions* (who matched, arrival vs
 * departure, consent, routing) are all made on the server. This script does one
 * job — turn a camera frame into a 128-number descriptor and put it in a hidden
 * field — then lets the normal form submit carry it to the server.
 *
 * The camera is only ever requested after an explicit button click, so the
 * browser's permission prompt is tied to a deliberate action. Nothing here
 * works without JavaScript or a camera; the pages that use it always offer the
 * manual fallback, which does.
 */
(function () {
  "use strict";

  var root = document.querySelector("[data-attendance]");
  if (!root) {
    return;
  }

  var modelUrl = root.getAttribute("data-model-url");
  var video = root.querySelector("[data-camera]");
  var startButton = root.querySelector("[data-start]");
  var captureButton = root.querySelector("[data-capture]");
  var descriptorField = root.querySelector("[data-descriptor]");
  var submitButton = root.querySelector("[data-submit]");
  var status = root.querySelector("[data-status]");
  var autoSubmit = root.hasAttribute("data-auto-submit");

  var modelsReady = false;
  var stream = null;

  function say(message) {
    if (status) {
      status.textContent = message;
    }
  }

  if (typeof faceapi === "undefined") {
    say(
      "The face library could not load. Use “mark present by hand” below, " +
      "which works without the camera."
    );
    if (startButton) {
      startButton.disabled = true;
    }
    return;
  }

  async function loadModels() {
    if (modelsReady) {
      return;
    }
    say("Loading the face models…");
    await Promise.all([
      faceapi.nets.tinyFaceDetector.loadFromUri(modelUrl),
      faceapi.nets.faceLandmark68Net.loadFromUri(modelUrl),
      faceapi.nets.faceRecognitionNet.loadFromUri(modelUrl),
    ]);
    modelsReady = true;
  }

  async function startCamera() {
    try {
      await loadModels();
      say("Starting the camera…");
      // getUserMedia triggers the browser's permission prompt here, on click.
      stream = await navigator.mediaDevices.getUserMedia({
        video: { facingMode: "user" },
        audio: false,
      });
      video.srcObject = stream;
      await video.play();
      video.hidden = false;
      if (captureButton) {
        captureButton.disabled = false;
      }
      say("Camera ready. Line up the face, then capture.");
    } catch (error) {
      say(
        "Could not start the camera (" +
        (error && error.name ? error.name : "unknown") +
        "). Check the browser's camera permission, or mark present by hand below."
      );
    }
  }

  async function capture() {
    if (!modelsReady) {
      say("Still loading — try again in a moment.");
      return;
    }
    say("Reading the face…");
    if (captureButton) {
      captureButton.disabled = true;
    }

    try {
      var detection = await faceapi
        .detectSingleFace(video, new faceapi.TinyFaceDetectorOptions())
        .withFaceLandmarks()
        .withFaceDescriptor();

      if (!detection) {
        say("No face was found. Face the camera in good light and try again.");
        if (captureButton) {
          captureButton.disabled = false;
        }
        return;
      }

      descriptorField.value = JSON.stringify(Array.from(detection.descriptor));
      say("Face captured.");

      if (submitButton) {
        submitButton.disabled = false;
      }
      stopCamera();

      if (autoSubmit && descriptorField.form) {
        descriptorField.form.submit();
      }
    } catch (error) {
      say("Something went wrong reading the face. Try again, or mark by hand.");
      if (captureButton) {
        captureButton.disabled = false;
      }
    }
  }

  function stopCamera() {
    if (stream) {
      stream.getTracks().forEach(function (track) {
        track.stop();
      });
      stream = null;
    }
    if (video) {
      video.hidden = true;
    }
  }

  if (startButton) {
    startButton.addEventListener("click", startCamera);
  }
  if (captureButton) {
    captureButton.addEventListener("click", capture);
    captureButton.disabled = true;
  }
  window.addEventListener("pagehide", stopCamera);
})();
