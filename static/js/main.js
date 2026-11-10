/* =========================================================================
   HIM DUB FESTIVAL — interações e animações (vanilla JS, sem dependências)
   ========================================================================= */

let pageEvents = new AbortController();
let pageCleanups = [];
function canNavigateWithMusic(href) {
  const url=new URL(href,window.location.href);
  const path=url.pathname.replace(/^\/(en|fr)(?=\/|$)/,'')||'/';
  return url.origin===window.location.origin && (/^\/(?:gallery(?:\/\d+)?|infos\/(?:faq|sustainability|partners|contact)|line-up(?:\/[a-z0-9-]+)?)$/.test(path) || ['/', '/tickets','/the-experience','/glamping-village','/participate','/the-festival','/the-map','/ambassadors'].includes(path));
}
if(window.Turbo) window.Turbo.session.drive=false;
function initFestivalPage() {
  document.documentElement.lang=document.body.dataset.siteLang||'pt';
  pageEvents.abort(); pageEvents=new AbortController();
  pageCleanups.forEach(cleanup=>cleanup()); pageCleanups=[];
  document.body.style.overflow='';
  document.querySelectorAll('a[href]').forEach(a=>{if(canNavigateWithMusic(a.href)&&!a.target&&!a.hasAttribute('download'))a.dataset.turbo='true';});
  document.querySelectorAll('form').forEach(form=>{form.dataset.turbo='false';});
  initScrollProgress();
  initHeaderScrollState();
  initMobileNav();
  initScrollReveal();
  initHeroVideo();
  initImmersiveVideos();
  initBackgroundMusic();
  initTestimonialCarousel();
  initCart();
  initLangPills();
  initHeroArrowWhoosh();
}
document.addEventListener('turbo:load',initFestivalPage);
document.addEventListener('DOMContentLoaded',()=>{if(!window.Turbo)initFestivalPage();});
document.addEventListener('turbo:before-cache',()=>{pageEvents.abort();pageCleanups.forEach(cleanup=>cleanup());pageCleanups=[];});

/* --------------------------------------------------------------------- */
/* Seletor de idioma (decorativo — sem tradução real ainda)              */
/* --------------------------------------------------------------------- */
function initLangPills() {
  document.querySelectorAll(".lang-pills").forEach((group) => {
    group.querySelectorAll("button").forEach((btn) => {
      btn.addEventListener("click", () => {
        group.querySelectorAll("button").forEach((b) => b.classList.remove("is-active"));
        btn.classList.add("is-active");
      });
    });
  });
}

/* --------------------------------------------------------------------- */
/* Barra de progresso de scroll                                          */
/* --------------------------------------------------------------------- */
function initScrollProgress() {
  const bar = document.getElementById("scrollProgress");
  if (!bar) return;
  const update = () => {
    const scrollTop = window.scrollY;
    const docHeight = document.documentElement.scrollHeight - window.innerHeight;
    const pct = docHeight > 0 ? (scrollTop / docHeight) * 100 : 0;
    bar.style.width = pct + "%";
  };
  window.addEventListener("scroll", update, { passive: true, signal: pageEvents.signal });
  update();
}

/* --------------------------------------------------------------------- */
/* Header muda de aparência ao fazer scroll                              */
/* --------------------------------------------------------------------- */
function initHeaderScrollState() {
  const header = document.getElementById("siteHeader");
  if (!header) return;
  const update = () => {
    header.classList.toggle("is-scrolled", window.scrollY > 40);
  };
  window.addEventListener("scroll", update, { passive: true, signal: pageEvents.signal });
  update();
}

/* --------------------------------------------------------------------- */
/* Menu mobile (hamburger)                                               */
/* --------------------------------------------------------------------- */
function initMobileNav() {
  const btn = document.getElementById("hamburgerBtn");
  const nav = document.getElementById("mobileNav");
  if (!btn || !nav) return;

  const closeNav = () => {
    btn.setAttribute("aria-expanded", "false");
    nav.classList.remove("is-open");
    document.body.style.overflow = "";
  };

  btn.addEventListener("click", () => {
    const isOpen = nav.classList.toggle("is-open");
    btn.setAttribute("aria-expanded", String(isOpen));
    document.body.style.overflow = isOpen ? "hidden" : "";
  });

  nav.querySelectorAll("a").forEach((link) => link.addEventListener("click", closeNav));
}

/* --------------------------------------------------------------------- */
/* Reveal-on-scroll com IntersectionObserver                             */
/* --------------------------------------------------------------------- */
function initScrollReveal() {
  const items = document.querySelectorAll("[data-reveal]");
  if (!items.length) return;

  items.forEach((el) => {
    const delay = el.getAttribute("data-reveal-delay");
    if (delay) el.style.setProperty("--reveal-delay", `${delay}ms`);
  });

  if (!("IntersectionObserver" in window)) {
    items.forEach((el) => el.classList.add("is-visible"));
    return;
  }

  const observer = new IntersectionObserver(
    (entries) => {
      entries.forEach((entry) => {
        if (entry.isIntersecting) {
          entry.target.classList.add("is-visible");

          // Contadores animados (stat-card__num) quando entram em vista
          // (usa querySelectorAll porque um único contentor revelado, como
          // a stat-grid, pode ter vários números para animar)
          entry.target.querySelectorAll("[data-count]").forEach((counter) => {
            if (!counter.dataset.done) animateCounter(counter);
          });

          observer.unobserve(entry.target);
        }
      });
    },
    { threshold: 0.15, rootMargin: "0px 0px -8% 0px" }
  );

  items.forEach((el) => observer.observe(el));
  pageCleanups.push(()=>observer.disconnect());
}

function animateCounter(el) {
  const target = parseInt(el.getAttribute("data-count"), 10) || 0;
  const duration = 1100;
  const start = performance.now();

  function tick(now) {
    const progress = Math.min((now - start) / duration, 1);
    const eased = 1 - Math.pow(1 - progress, 3);
    el.textContent = Math.round(eased * target);
    if (progress < 1) requestAnimationFrame(tick);
    else el.textContent = target;
  }
  el.dataset.done = "1";
  requestAnimationFrame(tick);
}

/* --------------------------------------------------------------------- */
/* Vídeo de fundo do hero: reproduz em ecrã inteiro (100vh) e pausa fora  */
/* do ecrã / com o separador em segundo plano. Sem efeito de encolher ao  */
/* fazer scroll -- essa versão deixava uma barra preta visível entre o    */
/* vídeo e o rodapé (o "wrap" encolhia e revelava o fundo escuro do hero  */
/* por baixo), por isso foi removida: o vídeo mantém-se sempre a 100% e   */
/* o scroll normal da página é que o faz sair de ecrã, revelando o        */
/* rodapé logo a seguir, tal como nas outras páginas imersivas.           */
/* --------------------------------------------------------------------- */
function initHeroVideo() {
  const hero = document.getElementById("hero");
  const wrap = document.getElementById("heroVideoWrap");
  const video = document.getElementById("heroVideo");
  if (!hero || !wrap || !video) return;

  const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  // Só reproduz o vídeo se o utilizador não pedir movimento reduzido.
  if (!reduceMotion) {
    const playPromise = video.play();
    if (playPromise && playPromise.catch) playPromise.catch(() => {});
  }

  // Pausa o vídeo quando sai do ecrã ou o separador fica em segundo plano,
  // para poupar recursos.
  if ("IntersectionObserver" in window) {
    const visObserver = new IntersectionObserver((entries) => {
      entries.forEach((entry) => {
        if (reduceMotion) return;
        if (entry.isIntersecting) video.play().catch(() => {});
        else video.pause();
      });
    }, { threshold: 0.05 });
    visObserver.observe(hero);
    pageCleanups.push(()=>visObserver.disconnect());
  }
  document.addEventListener("visibilitychange", () => {
    if (reduceMotion) return;
    if (document.hidden) video.pause();
    else if (hero.getBoundingClientRect().bottom > 0) video.play().catch(() => {});
  }, {signal:pageEvents.signal});
}

/* --------------------------------------------------------------------- */
/* Vídeos de fundo das páginas "imersivas" (Tickets/Glamping/Merchandise/ */
/* Participate) -- mesma ideia do vídeo da Home (autoplay, pausa fora do  */
/* ecrã), mas sem o efeito de encolher no scroll, porque estas páginas    */
/* não têm scroll de página. Genérico: apanha qualquer .immersive-hero    */
/* que tenha um <video class="immersive-hero__video">, sem precisar de    */
/* um id por página. Se não houver ficheiro de vídeo, o <video> falha em  */
/* silêncio e o gradiente de fundo da secção fica visível na mesma.       */
/* --------------------------------------------------------------------- */
function initImmersiveVideos() {
  const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  document.querySelectorAll(".immersive-hero__video").forEach((video) => {
    const hero = video.closest(".immersive-hero");
    if (!hero) return;

    // Set the properties as well as the HTML attributes: pages inserted by
    // Turbo must also start muted and inline on mobile browsers.
    video.muted = true;
    video.defaultMuted = true;
    video.playsInline = true;
    video.autoplay = !reduceMotion;
    if (reduceMotion) {
      video.pause();
      return;
    }
    const playVisibleVideo = () => {
      const bounds = hero.getBoundingClientRect();
      if (document.hidden || bounds.bottom <= 0 || bounds.top >= window.innerHeight || !video.paused || video.error) return;
      video.play().catch(() => {});
    };
    playVisibleVideo();
    // If autoplay was blocked, the next genuine user interaction can start
    // the background without affecting the separate music player.
    document.addEventListener('pointerdown', playVisibleVideo, {passive:true, signal:pageEvents.signal});
    document.addEventListener('keydown', playVisibleVideo, {signal:pageEvents.signal});

    if ("IntersectionObserver" in window) {
      const observer = new IntersectionObserver((entries) => {
        entries.forEach((entry) => {
          if (reduceMotion) return;
          if (entry.isIntersecting) playVisibleVideo();
          else video.pause();
        });
      }, { threshold: 0.05 });
      observer.observe(hero);
      pageCleanups.push(()=>observer.disconnect());
    }

    document.addEventListener("visibilitychange", () => {
      if (reduceMotion) return;
      if (document.hidden) video.pause();
      else playVisibleVideo();
    }, {signal:pageEvents.signal});
  });
}

/* --------------------------------------------------------------------- */
/* Música de fundo: continua "a tocar" ao navegar entre páginas, mesmo    */
/* sendo um site tradicional (cada página é um load novo, o que por si só */
/* pararia sempre o áudio). O truque: guardamos em localStorage se estava */
/* a tocar (e sessionStorage com o instante exato), e em cada carregamento*/
/* de página retomamos a partir daí -- dá a sensação de continuidade,     */
/* com um corte muito breve durante a própria navegação.                 */
/* Por política dos browsers, o primeiro "play" tem sempre de ser um      */
/* clique direto do utilizador (autoplay com som é bloqueado); a partir   */
/* daí o browser já permite retomar sozinho nas páginas seguintes deste   */
/* mesmo site.                                                            */
/* --------------------------------------------------------------------- */
function initBackgroundMusic() {
  const audio=document.getElementById('bgMusic');
  const button=document.getElementById('musicToggle');
  if(!audio||!button)return;
  const read=(key,fallback)=>{try{return sessionStorage.getItem(key)??fallback;}catch{return fallback;}};
  const write=(key,value)=>{try{sessionStorage.setItem(key,value);}catch{/* Storage may be unavailable. */}};
  function update(){
    const active=!audio.paused;
    const current=document.getElementById('musicToggle');
    if(current){current.classList.toggle('is-playing',active);current.setAttribute('aria-pressed',String(active));current.setAttribute('aria-label',active?current.dataset.labelPause:current.dataset.labelPlay);}
    if('mediaSession' in navigator)navigator.mediaSession.playbackState=active?'playing':'paused';
  }
  async function play(){try{await audio.play();write('himdub_music_playing','1');}catch{update();}}
  function pause(){audio.pause();write('himdub_music_playing','0');}
  if(!audio.himdubInitialized){
    audio.himdubInitialized=true;
    audio.addEventListener('play',update);audio.addEventListener('pause',update);
    audio.addEventListener('timeupdate',()=>write('himdub_music_time',String(audio.currentTime)));
    const time=parseFloat(read('himdub_music_time','0'));
    if(Number.isFinite(time)&&time>0)audio.addEventListener('loadedmetadata',()=>{if(time<audio.duration)audio.currentTime=time;},{once:true});
    if('mediaSession' in navigator){
      if('MediaMetadata' in window)navigator.mediaSession.metadata=new MediaMetadata({title:'Fally Ranking (Remix)',artist:'V.I.V.E.K',album:'HIM Dub Festival'});
      for(const [action,handler] of [['play',play],['pause',pause],['stop',pause]]){try{navigator.mediaSession.setActionHandler(action,handler);}catch{/* Optional device support. */}}
    }
    if(read('himdub_music_playing','')!=='0')play();
    // If autoplay is blocked, the explicit play button remains available.
  }
  button.addEventListener('click',()=>audio.paused?play():pause());
  update();
}

/* --------------------------------------------------------------------- */
/* Efeito "Whoosh": ao clicar nas setas do hero, dá sensação de           */
/* movimento horizontal (esquerda/direita) entre páginas — NÃO é scroll.  */
/* A classe de entrada (aplicada na página seguinte) já é adicionada mais */
/* cedo por um pequeno script inline no <body>, para evitar um "flash"    */
/* da página já na posição final antes da animação começar; aqui só a     */
/* limpamos no fim e tratamos o clique/saída.                            */
/* --------------------------------------------------------------------- */
function initHeroArrowWhoosh() {
  // A limpeza da classe de entrada corre em TODAS as páginas (o destino de
  // uma seta pode ser qualquer página, não só a homepage, que é a única com
  // setas) — por isso fica antes do "return" que só se aplica ao resto.
  ["whoosh-enter-next", "whoosh-enter-prev"].forEach((cls) => {
    if (document.body.classList.contains(cls)) {
      document.body.addEventListener(
        "animationend",
        () => document.body.classList.remove(cls),
        { once: true }
      );
      // Rede de segurança: se por algum motivo o animationend não disparar
      // (ex: página sem a keyframe, animação interrompida), remove na mesma.
      setTimeout(() => document.body.classList.remove(cls), 700);
    }
  });

  const arrows = document.querySelectorAll(".hero-arrow");
  if (!arrows.length) return;

  const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  arrows.forEach((arrow) => {
    arrow.addEventListener("click", (event) => {
      const href = arrow.getAttribute("href");
      if (!href) return;
      const direction = arrow.classList.contains("hero-arrow--next") ? "next" : "prev";
      event.preventDefault();
      navigateWithWhoosh(href, direction, reduceMotion);
    });
  });

  initSwipeNav(arrows, reduceMotion);
}

/* Navega para outra página imersiva com a mesma animação "whoosh" das
   setas -- usado tanto pelo clique nas setas (desktop) como pelo gesto de
   arrastar (telemóvel, ver initSwipeNav). */
function navigateWithWhoosh(href, direction, reduceMotion) {
  if (reduceMotion) {
    if(window.Turbo&&canNavigateWithMusic(href))window.Turbo.visit(href);else window.location.href=href;
    return;
  }

  try {
    sessionStorage.setItem("himdub_whoosh_enter", direction);
  } catch (e) {
    /* sessionStorage indisponível (modo privado, etc.) — navega sem o efeito de entrada */
  }

  document.body.classList.add(direction === "next" ? "whoosh-exit-next" : "whoosh-exit-prev");

  let navigated = false;
  const go = () => {
    if (navigated) return;
    navigated = true;
    if(window.Turbo&&canNavigateWithMusic(href))window.Turbo.visit(href);else window.location.href=href;
  };
  document.body.addEventListener("animationend", go, { once: true });
  setTimeout(go, 480); // rede de segurança caso o evento animationend não dispare
}

/* --------------------------------------------------------------------- */
/* Navegação por arrastar (swipe) no telemóvel -- as setas ficam          */
/* escondidas abaixo dos 760px (ver CSS), por isso a mesma navegação      */
/* entre páginas passa a fazer-se arrastando o dedo para a esquerda/      */
/* direita. Os links das setas continuam no DOM (só visualmente           */
/* escondidos) e servem de fonte para os URLs de destino.                 */
/* --------------------------------------------------------------------- */
function initSwipeNav(arrows, reduceMotion) {
  if (!("ontouchstart" in window)) return; // só faz sentido em ecrãs táteis

  const prevArrow = document.querySelector(".hero-arrow--prev");
  const nextArrow = document.querySelector(".hero-arrow--next");
  if (!prevArrow && !nextArrow) return;

  // A secção herói tem classes diferentes consoante a página (".hero" na
  // home, com o efeito de encolher no scroll; ".immersive-hero" nas
  // outras) -- indo buscar o filho direto do <main>, cobrem-se as duas.
  const hero = document.querySelector("body.immersive-page main > *");
  if (!hero) return;

  const SWIPE_MIN_DISTANCE = 60; // px -- abaixo disto é só um toque/scroll, não um swipe
  const SWIPE_MAX_ANGLE_RATIO = 0.6; // vertical não pode ser mais que 60% do horizontal

  let startX = 0;
  let startY = 0;
  let tracking = false;

  hero.addEventListener("touchstart", (event) => {
    // Ignora se o dedo começou num botão/link/formulário -- não queremos
    // roubar o toque a um "adicionar ao carrinho" ou a um link do menu.
    if (event.target.closest("a, button, form, input, select, textarea")) {
      tracking = false;
      return;
    }
    const touch = event.touches[0];
    startX = touch.clientX;
    startY = touch.clientY;
    tracking = true;
  }, { passive: true });

  hero.addEventListener("touchend", (event) => {
    if (!tracking) return;
    tracking = false;

    const touch = event.changedTouches[0];
    const deltaX = touch.clientX - startX;
    const deltaY = touch.clientY - startY;

    if (Math.abs(deltaX) < SWIPE_MIN_DISTANCE) return; // gesto curto demais
    if (Math.abs(deltaY) > Math.abs(deltaX) * SWIPE_MAX_ANGLE_RATIO) return; // mais vertical que horizontal (scroll)

    // Arrastar para a esquerda = avançar (próxima página), tal como
    // folhear -- arrastar para a direita = voltar (mesma direção das setas).
    const arrow = deltaX < 0 ? nextArrow : prevArrow;
    const direction = deltaX < 0 ? "next" : "prev";
    if (!arrow) return;

    const href = arrow.getAttribute("href");
    if (!href) return;

    navigateWithWhoosh(href, direction, reduceMotion);
  }, { passive: true });

  initSwipeHint(hero);
}

/* --------------------------------------------------------------------- */
/* Dica visual de swipe -- no telemóvel, nada indica que dá para arrastar */
/* entre páginas (as setas só aparecem a partir dos 760px). Mostra uma    */
/* animação pequena e discreta (ícone + texto) na primeira vez que a      */
/* pessoa entra numa página com swipe disponível, e nunca mais aparece    */
/* depois disso -- fica guardado no telemóvel via localStorage, tanto se  */
/* a pessoa chegar a arrastar como se a dica simplesmente desaparecer     */
/* sozinha ao fim de uns segundos (ambos contam como "já viu"). */
/* --------------------------------------------------------------------- */
function initSwipeHint(hero) {
  const STORAGE_KEY = "himdub_swipe_hint_seen";
  let alreadySeen = true;
  try {
    alreadySeen = localStorage.getItem(STORAGE_KEY) === "1";
  } catch (e) {
    // localStorage indisponível (modo privado, etc.) -- não arrisca mostrar
    // a dica sempre (seria pior que não mostrar de todo), assume "já viu".
    alreadySeen = true;
  }
  if (alreadySeen) return;

  const lang = document.documentElement.lang || "pt";
  const LABELS = {
    pt: "Desliza para navegar",
    en: "Swipe to navigate",
    fr: "Glissez pour naviguer",
    es: "Desliza para navegar",
  };
  const label = LABELS[lang] || LABELS.pt;

  const hint = document.createElement("div");
  hint.className = "swipe-hint";
  hint.setAttribute("aria-hidden", "true"); // puramente decorativo, não rouba foco a leitores de ecrã
  hint.innerHTML =
    '<span class="swipe-hint__icon">' +
    '<svg viewBox="0 0 64 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round">' +
    '<path d="M2 12h60M2 12l9-9M2 12l9 9M62 12l-9-9M62 12l-9 9"/>' +
    "</svg>" +
    "</span>" +
    '<span class="swipe-hint__label">' + label + "</span>";
  hero.appendChild(hint);

  let dismissed = false;
  const dismiss = () => {
    if (dismissed) return;
    dismissed = true;
    hint.classList.remove("is-visible");
    try {
      localStorage.setItem(STORAGE_KEY, "1");
    } catch (e) {
      /* ignora -- pior caso é a dica voltar a aparecer, não é grave */
    }
    setTimeout(() => hint.remove(), 400);
  };

  // Pequeno atraso antes de mostrar, para não competir com a animação de
  // entrada da própria página.
  setTimeout(() => hint.classList.add("is-visible"), 500);

  // Desaparece definitivamente assim que a pessoa começa a arrastar o dedo
  // NA HORIZONTAL -- o mesmo gesto que muda de página (ver initSwipeNav).
  // Um scroll vertical normal (ler a página, fechar o teclado, etc.) não a
  // deve fazer desaparecer. Usa a mesma proporção vertical/horizontal do
  // initSwipeNav (SWIPE_MAX_ANGLE_RATIO = 0.6) para não confundir os dois:
  // só conta se o movimento horizontal for claramente dominante, não basta
  // ser "um bocadinho maior" que o vertical (que acontecia com "dx > dy" e
  // disparava em falso logo nos primeiros pixels de um swipe vertical).
  let startX = null;
  let startY = null;
  hero.addEventListener("touchstart", (event) => {
    const touch = event.touches[0];
    startX = touch.clientX;
    startY = touch.clientY;
  }, { passive: true });
  hero.addEventListener("touchmove", (event) => {
    if (dismissed || startX === null) return;
    const touch = event.touches[0];
    const dx = Math.abs(touch.clientX - startX);
    const dy = Math.abs(touch.clientY - startY);
    if (dx > 24 && dy <= dx * 0.6) dismiss();
  }, { passive: true });
}

/* --------------------------------------------------------------------- */
/* Carrossel de testemunhos                                              */
/* --------------------------------------------------------------------- */
function initTestimonialCarousel() {
  const carousel = document.getElementById("testimonialCarousel");
  if (!carousel) return;

  const slides = Array.from(carousel.querySelectorAll(".carousel__slide"));
  const dotsWrap = carousel.querySelector(".carousel__dots");
  if (slides.length <= 1) return;

  let current = 0;
  let timer = null;

  slides.forEach((_, i) => {
    const dot = document.createElement("button");
    dot.type = "button";
    dot.setAttribute("aria-label", `Testemunho ${i + 1}`);
    if (i === 0) dot.classList.add("is-active");
    dot.addEventListener("click", () => goTo(i));
    dotsWrap.appendChild(dot);
  });
  const dots = Array.from(dotsWrap.children);

  function goTo(index) {
    slides[current].classList.remove("is-active");
    dots[current].classList.remove("is-active");
    current = (index + slides.length) % slides.length;
    slides[current].classList.add("is-active");
    dots[current].classList.add("is-active");
  }

  function next() { goTo(current + 1); }

  function startAutoplay() {
    stopAutoplay();
    timer = setInterval(next, 5500);
  }
  function stopAutoplay() { if (timer) clearInterval(timer); }

  carousel.addEventListener("mouseenter", stopAutoplay);
  carousel.addEventListener("mouseleave", startAutoplay);
  startAutoplay();
  pageCleanups.push(stopAutoplay);
}

/* --------------------------------------------------------------------- */
/* Carrinho: abrir/fechar drawer + adicionar via AJAX                    */
/* --------------------------------------------------------------------- */
function initCart() {
  const drawer = document.getElementById("cartDrawer");
  const backdrop = document.getElementById("cartBackdrop");
  const toggleBtn = document.getElementById("cartToggle");
  const closeBtn = document.getElementById("cartClose");
  const countEl = document.getElementById("cartCount");
  const totalEl = document.getElementById("cartDrawerTotal");
  const bodyEl = document.getElementById("cartDrawerBody");

  function openDrawer() {
    drawer.classList.add("is-open");
    backdrop.classList.add("is-open");
  }
  function closeDrawer() {
    drawer.classList.remove("is-open");
    backdrop.classList.remove("is-open");
  }

  if (toggleBtn) toggleBtn.addEventListener("click", openDrawer);
  if (closeBtn) closeBtn.addEventListener("click", closeDrawer);
  if (backdrop) backdrop.addEventListener("click", closeDrawer);

  // Intercepta os formulários "adicionar ao carrinho" para os submeter via
  // AJAX (assim o utilizador nao perde a pagina onde esta) e abre o drawer.
  document.querySelectorAll(".quick-add-form, .product-form").forEach((form) => {
    form.addEventListener("submit", (event) => {
      event.preventDefault();
      const formData = new FormData(form);
      const submitBtn = form.querySelector("button[type=submit]");
      if (submitBtn) submitBtn.disabled = true;

      fetch(form.action, {
        method: "POST",
        headers: { "X-Requested-With": "XMLHttpRequest" },
        body: formData,
      })
        .then((res) => res.json())
        .then((data) => {
          if (!data.ok) { alert(data.error || "Não foi possível atualizar o carrinho."); return; }
          if (countEl) {
            countEl.textContent = data.cart_count;
            countEl.classList.remove("bump");
            void countEl.offsetWidth; // reinicia a animacao
            countEl.classList.add("bump");
          }
          if (totalEl) totalEl.textContent = `€${data.cart_total.toFixed(2)}`;
          refreshDrawerLines();
          openDrawer();
        })
        .catch(() => { alert("Não foi possível confirmar a atualização. Atualiza a página antes de tentar novamente."); })
        .finally(() => { if (submitBtn) submitBtn.disabled = false; });
    });
  });

  function refreshDrawerLines() {
    if (!bodyEl) return;
    fetch(window.location.origin + (["en", "fr", "es"].includes(document.documentElement.lang) ? "/" + document.documentElement.lang : "") + "/cart/mini-fragment", { headers: { "X-Requested-With": "XMLHttpRequest" } })
      .then((res) => (res.ok ? res.text() : null))
      .then((html) => { if (html) bodyEl.innerHTML = html; })
      .catch(() => {});
  }

  // Remover uma linha do drawer (botão "×" em cada cart-line, ver
  // _cart_lines.html) -- delegado no bodyEl (em vez de um listener por
  // formulário) porque refreshDrawerLines() substitui o innerHTML inteiro
  // sempre que o carrinho muda, o que destruiria listeners presos
  // diretamente aos elementos antigos.
  if (bodyEl) {
    bodyEl.addEventListener("submit", (event) => {
      const form = event.target.closest(".cart-line__remove-form");
      if (!form) return;
      event.preventDefault();
      const btn = form.querySelector("button");
      if (btn) btn.disabled = true;

      fetch(form.action, {
        method: "POST",
        headers: { "X-Requested-With": "XMLHttpRequest" },
        body: new FormData(form),
      })
        .then((res) => res.json())
        .then((data) => {
          if (!data.ok) { alert(data.error || "Não foi possível atualizar o carrinho."); return; }
          if (countEl) countEl.textContent = data.cart_count;
          if (totalEl) totalEl.textContent = `€${data.cart_total.toFixed(2)}`;
          refreshDrawerLines();
        })
        .catch(() => { form.submit(); }); // fallback sem JS/erro de rede
    });
  }
}
