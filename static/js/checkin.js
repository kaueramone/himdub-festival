/* =========================================================================
   Scanner de QR code para o check-in dos bilhetes (usa a câmara do
   telemóvel do porteiro + a biblioteca jsQR, carregada localmente para
   funcionar mesmo com má ligação no recinto).
   ========================================================================= */
(function () {
  const video = document.getElementById("scannerVideo");
  const canvas = document.getElementById("scannerCanvas");
  const statusEl = document.getElementById("scannerStatus");
  if (!video || !canvas || typeof jsQR === "undefined") return;

  const ctx = canvas.getContext("2d", { willReadFrequently: true });
  let scanning = true;
  let cameraStream = null;
  const retry = document.getElementById("retryCamera");

  function setStatus(text) {
    if (statusEl) statusEl.textContent = text;
  }

  if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
    setStatus("Este browser não suporta acesso à câmara. Usa um browser mais recente (Chrome/Safari atualizados).");
    return;
  }

  function startCamera() {
    if (retry) retry.hidden = true;
    setStatus("A iniciar câmara…");
    navigator.mediaDevices
    .getUserMedia({ video: { facingMode: "environment" } })
    .then((stream) => {
      cameraStream = stream;
      video.srcObject = stream;
      video.play().catch(() => setStatus("Toca no vídeo para iniciar a imagem ou introduz o código manualmente."));
      setStatus("A ler…");
      requestAnimationFrame(tick);
    })
    .catch((err) => {
      setStatus("Não foi possível aceder à câmara (" + err.message + "). Confirma que deste permissão ao browser para usar a câmara.");
      if (retry) retry.hidden = false;
    });
  }
  if (retry) retry.addEventListener("click", startCamera);
  video.addEventListener("click", () => video.play().catch(() => {}));
  window.addEventListener("pagehide", () => {
    scanning = false;
    if (cameraStream) cameraStream.getTracks().forEach((track) => track.stop());
  });
  window.addEventListener("pageshow", (event) => {
    if (event.persisted) window.location.reload();
  });
  startCamera();

  function tick() {
    if (!scanning) return;

    if (video.readyState === video.HAVE_ENOUGH_DATA) {
      canvas.width = video.videoWidth;
      canvas.height = video.videoHeight;
      ctx.drawImage(video, 0, 0, canvas.width, canvas.height);
      const imageData = ctx.getImageData(0, 0, canvas.width, canvas.height);
      const result = jsQR(imageData.data, imageData.width, imageData.height, {
        inversionAttempts: "dontInvert",
      });

      if (result && result.data) {
        scanning = false;
        setStatus("Bilhete encontrado — a abrir…");
        // O QR contém o URL completo da página do bilhete (/checkin/<code>).
        try {
          const target = new URL(result.data, window.location.origin);
          if ([window.location.hostname, "himdubfestival.com", "www.himdubfestival.com", "himdub-festival.onrender.com"].includes(target.hostname) && /^\/checkin\/[A-Z0-9]+$/i.test(target.pathname)) {
            window.location.assign(target.pathname);
            return;
          } else {
            alert("Este QR não é um bilhete deste festival.");
          }
        } catch (_) {
          alert("Código QR inválido.");
        }
        scanning = true;
        setStatus("A procurar bilhetes…");
      }
    }
    requestAnimationFrame(tick);
  }
})();
