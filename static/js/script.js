// Custom scripts for Local Product Catalog

// --- Dark mode -------------------------------------------------------------
(function () {
    var KEY = 'catalog-theme';
    var button = document.getElementById('themeToggle');
    if (!button) return;

    function icon() {
        var dark = document.documentElement.getAttribute('data-bs-theme') === 'dark';
        button.innerHTML = dark
            ? '<i class="bi bi-sun"></i>'
            : '<i class="bi bi-moon-stars"></i>';
    }

    button.addEventListener('click', function () {
        var next = document.documentElement.getAttribute('data-bs-theme') === 'dark'
            ? 'light' : 'dark';
        document.documentElement.setAttribute('data-bs-theme', next);
        try { localStorage.setItem(KEY, next); } catch (e) {}
        icon();
    });

    icon();
})();

// --- Auto-submit filter selects -------------------------------------------
document.querySelectorAll('[data-autosubmit]').forEach(function (el) {
    el.addEventListener('change', function () { el.form.submit(); });
});

console.log('Local Product Catalog loaded');
