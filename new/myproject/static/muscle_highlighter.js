(function () {
  const SVG_NS = "http://www.w3.org/2000/svg";
  const VIEWBOXES = {
    male: {
      front: "0 0 724 1448",
      back: "724 0 724 1448",
    },
    female: {
      front: "-50 -40 734 1538",
      back: "756 0 774 1448",
    },
  };

  class MuscleHighlighter {
    constructor(root) {
      this.root = root;
      this.svg = root.querySelector(".muscle-highlighter__svg");
      this.gender = root.dataset.defaultGender || "male";
      this.side = root.dataset.defaultSide || "front";
      this.lockGender = root.dataset.lockGender === "true";
      this.lockSide = root.dataset.lockSide === "true";
      this.layers = [];
      this.pathElements = [];

      this.bindToggleEvents();
      this.updateToggleState("gender", this.gender);
      this.updateToggleState("side", this.side);
      this.render();
    }

    bindToggleEvents() {
      if (!this.lockGender) {
        this.root.querySelectorAll("[data-gender]").forEach((btn) => {
          btn.addEventListener("click", () => {
            const value = btn.dataset.gender;
            if (value === this.gender) return;
            this.gender = value;
            this.updateToggleState("gender", value);
            this.render();
          });
        });
      }

      if (!this.lockSide) {
        this.root.querySelectorAll("[data-side]").forEach((btn) => {
          btn.addEventListener("click", () => {
            const value = btn.dataset.side;
            if (value === this.side) return;
            this.side = value;
            this.updateToggleState("side", value);
            this.render();
          });
        });
      }
    }

    updateToggleState(type, activeValue) {
      const selector = type === "gender" ? "[data-gender]" : "[data-side]";
      this.root.querySelectorAll(selector).forEach((btn) => {
        const isActive =
          type === "gender"
            ? btn.dataset.gender === activeValue
            : btn.dataset.side === activeValue;
        btn.classList.toggle("is-active", isActive);
      });
    }

    render() {
      const dataKey = `${this.gender}_${this.side}`;
      const dataset = (resolveSvgData() || {})[dataKey] || [];
      const viewBox = VIEWBOXES[this.gender]?.[this.side];

      if (!dataset.length || !viewBox) {
        this.svg.innerHTML = "";
        this.pathElements = [];
        return;
      }

      this.svg.setAttribute("viewBox", viewBox);
      this.svg.innerHTML = "";

      dataset.forEach((entry) => {
        Object.entries(entry.paths || {}).forEach(([region, paths]) => {
          if (!Array.isArray(paths)) return;
          const group = document.createElementNS(SVG_NS, "g");
          group.classList.add("muscle-region");
          group.dataset.muscleSlug = entry.slug;
          group.dataset.regionSide = region;
          group.setAttribute("id", `muscle-${entry.slug}-${region}`);

          paths.forEach((d) => {
            const path = document.createElementNS(SVG_NS, "path");
            path.setAttribute("d", d);
            group.appendChild(path);
          });

          this.svg.appendChild(group);
        });
      });

      this.pathElements = Array.from(
        this.svg.querySelectorAll("[data-muscle-slug]")
      );

      this.applyHighlight();
    }

    applyHighlight() {
      if (!this.layers.length) {
        this.pathElements.forEach((node) => {
          node.classList.remove("is-active");
          node.style.fill = "";
          node.style.opacity = "0.9";
        });
        return;
      }

      this.pathElements.forEach((node) => {
        const slug = node.dataset.muscleSlug;
        let matched = null;
        this.layers.forEach((layer) => {
          if (layer.slugSet.has(slug)) {
            if (!matched || layer.priority >= matched.priority) {
              matched = layer;
            }
          }
        });

        if (matched) {
          node.classList.add("is-active");
          node.style.fill = matched.color;
          node.style.opacity = matched.opacity || "1";
        } else {
          node.classList.remove("is-active");
          node.style.fill = "";
          node.style.opacity = "0.9";
        }
      });
    }

    highlight(slugs, color) {
      if (!Array.isArray(slugs) || !slugs.length) {
        this.setLayers([]);
        return;
      }
      this.setLayers([
        {
          slugs,
          color: color || "#6f42c1",
          priority: 1,
        },
      ]);
    }

    setLayers(layers) {
      if (!Array.isArray(layers)) {
        this.layers = [];
        this.applyHighlight();
        return;
      }

      this.layers = layers
        .filter((layer) => Array.isArray(layer.slugs) && layer.slugs.length)
        .map((layer, index) => ({
          slugs: layer.slugs,
          slugSet: new Set(layer.slugs),
          color: layer.color || "#6f42c1",
          priority:
            typeof layer.priority === "number" ? layer.priority : index,
          opacity: layer.opacity,
        }));

      this.applyHighlight();
    }

    clearLayers() {
      this.layers = [];
      this.applyHighlight();
    }
  }

  function resolveSvgData() {
    if (window.BODY_SVG_DATA) {
      return window.BODY_SVG_DATA;
    }

    const holder = document.getElementById("body-svg-data");
    if (holder) {
      try {
        window.BODY_SVG_DATA = JSON.parse(holder.textContent || "{}");
      } catch (error) {
        console.error("解析 BODY_SVG_DATA 失败", error);
      }
    }

    return window.BODY_SVG_DATA;
  }

  function initHighlighters() {
    if (!resolveSvgData()) {
      console.warn("BODY_SVG_DATA 未注入，无法渲染肌肉图");
      return;
    }

    const instances = [];
    document
      .querySelectorAll(".muscle-highlighter")
      .forEach((root) => instances.push(new MuscleHighlighter(root)));

    window.MuscleHighlighters = instances;
    document.dispatchEvent(
      new CustomEvent("muscleHighlighter:ready", {
        detail: { instances },
      })
    );
  }

  document.addEventListener("DOMContentLoaded", initHighlighters);
})();
