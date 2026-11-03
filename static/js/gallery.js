(() => {
  const dialog=document.querySelector('.gallery-lightbox');
  if(!dialog)return;
  const photos=Array.from(document.querySelectorAll('[data-gallery-image]'));let index=0;
  function show(i){index=(i+photos.length)%photos.length;const p=photos[index];dialog.querySelector('img').src=p.dataset.galleryImage;dialog.querySelector('img').alt=p.dataset.caption||p.querySelector('img').alt;dialog.querySelector('figcaption').textContent=p.dataset.caption||'';}
  photos.forEach((p,i)=>p.addEventListener('click',()=>{show(i);dialog.showModal();}));
  dialog.querySelector('.gallery-close').addEventListener('click',()=>dialog.close());
  dialog.querySelector('.gallery-prev').addEventListener('click',()=>show(index-1));
  dialog.querySelector('.gallery-next').addEventListener('click',()=>show(index+1));
  dialog.addEventListener('keydown',e=>{if(e.key==='ArrowLeft')show(index-1);if(e.key==='ArrowRight')show(index+1);});
})();
