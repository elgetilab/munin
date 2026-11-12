// The guide and architecture pages: phone-friendly tables and a contents
// list that marks the section in view. Served through Caddy templates, so no
// double braces.
// Tables: each cell learns its column's name, for the stacked phone layout.
document.querySelectorAll('.prose table').forEach(function (t) {
    var names = [].map.call(t.querySelectorAll('thead th'), function (th) { return th.textContent.trim(); });
    t.querySelectorAll('tbody tr').forEach(function (tr) {
        [].forEach.call(tr.children, function (td, i) { if (names[i]) td.setAttribute('data-label', names[i]); });
    });
});
// Contents: folded on a phone, and the section in view is marked as current.
(function () {
    var toc = document.querySelector('.toc');
    if (!toc) return;
    if (matchMedia('(max-width: 900px)').matches) toc.removeAttribute('open');
    toc.addEventListener('click', function (e) {
        if (e.target.closest('a') && matchMedia('(max-width: 900px)').matches) toc.removeAttribute('open');
    });
    var links = toc.querySelectorAll('a[href^="#"]');
    var byId = {};
    links.forEach(function (a) { byId[a.getAttribute('href').slice(1)] = a; });
    var heads = Object.keys(byId).map(function (id) { return document.getElementById(id); }).filter(Boolean);
    if (!heads.length || !('IntersectionObserver' in window)) return;
    function mark() {
        var cur = heads[0];
        heads.forEach(function (h) { if (h.getBoundingClientRect().top < 120) cur = h; });
        links.forEach(function (a) { a.removeAttribute('aria-current'); });
        byId[cur.id].setAttribute('aria-current', 'true');
    }
    var io = new IntersectionObserver(mark, { rootMargin: '-100px 0px -60% 0px' });
    heads.forEach(function (h) { io.observe(h); });
    mark();
})();
