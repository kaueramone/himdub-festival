/* Optional first-party statistics. No form values, full URLs or identifiers. */
(() => {
  if (window.himdubAudienceInstalled) return;
  window.himdubAudienceInstalled = true;
  let current = null;
  let previousTick = performance.now();
  const csrf = () => document.querySelector('meta[name="csrf-token"]')?.content || '';
  const blocked = () => navigator.globalPrivacyControl === true || navigator.doNotTrack === '1';
  function payload() {
    if (!current) return null;
    const params = new URLSearchParams(window.location.search);
    let referrer = '';
    try { referrer = document.referrer ? new URL(document.referrer).hostname : ''; } catch { /* Unknown source. */ }
    return {token: current.token, seconds: Math.floor(current.seconds), scroll: current.scroll,
      referrer, source: params.get('utm_source') || '', medium: params.get('utm_medium') || '',
      campaign: params.get('utm_campaign') || ''};
  }
  function form(data) {
    const body = new FormData();
    body.append('csrf_token', csrf()); body.append('data', JSON.stringify(data));
    return body;
  }
  function send(beacon = false) {
    const data = payload();
    if (!data || blocked() || document.body.dataset.audienceChoice !== 'yes') return;
    current.lastSent = current.seconds;
    const endpoint = document.body.dataset.audienceCollect;
    if (beacon && navigator.sendBeacon) { navigator.sendBeacon(endpoint, form(data)); return; }
    fetch(endpoint, {method:'POST', credentials:'same-origin', body:form(data), keepalive:true}).catch(() => {});
  }
  function scrollDepth() {
    if (!current) return;
    const maximum = document.documentElement.scrollHeight - window.innerHeight;
    current.scroll = Math.max(current.scroll, maximum > 0 ? Math.round(100 * window.scrollY / maximum) : 100);
  }
  function start() {
    const panel = document.getElementById('audienceConsent');
    const state = document.body.dataset.audienceChoice;
    if (panel) panel.hidden = !!state || !document.body.dataset.audienceToken || blocked();
    const token = document.body.dataset.audienceToken;
    if (!token || state !== 'yes' || blocked() || document.visibilityState !== 'visible') return;
    if (current?.token === token) return;
    current = {token, seconds:0, scroll:0, lastSent:0, input:performance.now()};
    previousTick = performance.now();
    scrollDepth(); send();
  }
  function stop(flush = true) { if (flush) send(true); current = null; }
  const interaction = () => { if (current) current.input = performance.now(); };
  for (const name of ['pointerdown','keydown','touchstart']) document.addEventListener(name, interaction, {passive:true});
  window.addEventListener('scroll', () => { interaction(); scrollDepth(); }, {passive:true});
  setInterval(() => {
    const now = performance.now();
    if (current && document.visibilityState === 'visible' && now-current.input < 60000) {
      current.seconds += Math.min(5, (now-previousTick)/1000);
      if (current.seconds-current.lastSent >= 30) send();
    }
    previousTick = now;
  }, 5000);
  document.addEventListener('visibilitychange', () => {
    if (document.visibilityState === 'hidden') send(true);
    else if (!current) start();
    else { current.input=performance.now(); previousTick=performance.now(); }
  });
  window.addEventListener('pagehide', () => send(true));
  document.addEventListener('turbo:before-render', () => stop());
  document.addEventListener('turbo:load', start);
  document.addEventListener('DOMContentLoaded', start);
  if (document.readyState !== 'loading') start();
  document.addEventListener('click', async event => {
    const settings = event.target.closest('[data-audience-settings]');
    const panel = document.getElementById('audienceConsent');
    if (settings && panel) { panel.hidden=false; panel.querySelector('h2')?.focus(); return; }
    const button = event.target.closest('[data-audience-choice]');
    if (!button || !panel) return;
    const value = button.dataset.audienceChoice;
    const buttons = [...panel.querySelectorAll('button')];
    buttons.forEach(b => { b.disabled=true; });
    if (value==='no') stop(false);
    const body = new FormData(); body.append('csrf_token',csrf()); body.append('choice',value);
    try {
      const response = await fetch(document.body.dataset.audienceConsent, {method:'POST',credentials:'same-origin',body});
      if (!response.ok) throw new Error('Unavailable');
      const result = await response.json();
      document.body.dataset.audienceChoice=result.choice;
      panel.hidden=true;
      if (result.choice==='yes') start();
    } catch {
      panel.querySelector('[role="status"]').textContent=panel.dataset.error;
    } finally { buttons.forEach(b => { b.disabled=false; }); }
  });
})();
