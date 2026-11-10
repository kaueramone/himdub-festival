(function () {
  const notice = document.getElementById("checkinNetwork");
  function update() {
    if (notice) notice.hidden = navigator.onLine;
    document.querySelectorAll('form[method="post"] button[type="submit"]').forEach(function (button) { button.disabled = !navigator.onLine; });
  }
  window.addEventListener("online", update);
  window.addEventListener("offline", update);
  update();
})();
