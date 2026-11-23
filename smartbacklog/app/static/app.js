// Smart Product Backlog — interacciones ligeras (tema, toasts, tablero kanban).
(function () {
  const root = document.documentElement;

  // Tema claro/oscuro: se recuerda por navegador; si falla storage, sigue funcionando.
  try {
    const saved = localStorage.getItem("spb-theme");
    if (saved) root.dataset.theme = saved;
  } catch (_) { /* storage no disponible */ }

  document.querySelectorAll("[data-theme-toggle]").forEach((btn) => {
    btn.addEventListener("click", () => {
      const dark = window.matchMedia("(prefers-color-scheme: dark)").matches;
      const current = root.dataset.theme || (dark ? "dark" : "light");
      const next = current === "dark" ? "light" : "dark";
      root.dataset.theme = next;
      try { localStorage.setItem("spb-theme", next); } catch (_) { /* ignorar */ }
    });
  });

  // Toasts: se cierran solos y al hacer clic.
  document.querySelectorAll(".toast").forEach((el) => {
    const close = () => el.remove();
    el.addEventListener("click", close);
    setTimeout(close, 4500);
  });

  // Revisión con agentes en curso: recarga la página hasta que termine.
  const auto = document.querySelector("[data-autorefresh]");
  if (auto) {
    const secs = Math.max(3, Number(auto.dataset.autorefresh) || 6);
    setTimeout(() => {
      // no recargar si la persona está escribiendo un comentario
      const typing = [...document.querySelectorAll("textarea")].some((t) => t.value.trim());
      if (!typing) window.location.reload();
    }, secs * 1000);
  }

  // Tablero kanban: arrastrar tarjeta cambia el estado vía API.
  if (window.Sortable) {
    document.querySelectorAll("[data-kanban]").forEach((board) => {
      const project = board.dataset.project;
      document.querySelectorAll(".column-body").forEach((col) => {
        window.Sortable.create(col, {
          group: "hu",
          animation: 150,
          ghostClass: "sortable-ghost",
          disabled: board.dataset.readonly === "true",
          onAdd: async (evt) => {
            const card = evt.item;
            const target = evt.to.dataset.status;
            const from = evt.from;
            card.classList.add("is-saving");
            try {
              const res = await fetch(`/api/v1/projects/${project}/stories/${card.dataset.code}/status`, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                credentials: "same-origin",
                body: JSON.stringify({ status: target, expected_version: Number(card.dataset.version) }),
              });
              if (!res.ok) {
                const body = await res.json().catch(() => ({}));
                throw new Error(body.detail || `Error ${res.status}`);
              }
              const data = await res.json();
              card.dataset.version = data.version;
              updateCounts();
              flashMessage(`${card.dataset.code} → ${target.replace(/_/g, " ")}`, "ok");
            } catch (err) {
              from.insertBefore(card, from.children[evt.oldIndex] || null);
              flashMessage(err.message, "error");
            }
          },
        });
      });
    });
  }

  function updateCounts() {
    document.querySelectorAll(".column").forEach((col) => {
      const n = col.querySelectorAll(".k-card").length;
      const badge = col.querySelector(".count");
      if (badge) badge.textContent = n;
    });
  }

  function flashMessage(text, kind) {
    const stack = document.querySelector(".toast-stack") || (() => {
      const s = document.createElement("div");
      s.className = "toast-stack";
      document.body.appendChild(s);
      return s;
    })();
    const el = document.createElement("div");
    el.className = `alert toast ${kind}`;
    el.textContent = text;
    el.addEventListener("click", () => el.remove());
    stack.appendChild(el);
    setTimeout(() => el.remove(), 4500);
  }
})();
