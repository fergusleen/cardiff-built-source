(() => {
  "use strict";

  const AGE_COLOURS = [
    "#f2d16b", "#ffae55", "#f27b55", "#db5b82", "#b45eaa", "#8767c7", "#5c77d1",
    "#398fc5", "#28a5af", "#32bb88", "#6bc96e", "#a8d65d", "#e4e76d"
  ];
  const HEIGHT_COLOUR = [
    "interpolate", ["linear"], ["get", "h"],
    3, "#455e74", 7, "#2fd2c8", 12, "#7de17d", 22, "#ffd166", 40, "#ff735d", 72, "#eaa7ff"
  ];
  const PLACES = {
    centre: { center: [-3.1782, 51.4817], zoom: 15.1, pitch: 60, bearing: -19 },
    bay: { center: [-3.1665, 51.4632], zoom: 14.4, pitch: 58, bearing: 14 },
    cathays: { center: [-3.1785, 51.4952], zoom: 14.5, pitch: 55, bearing: -24 },
    roath: { center: [-3.1513, 51.4915], zoom: 14.4, pitch: 54, bearing: 22 },
    llanishen: { center: [-3.188, 51.5301], zoom: 14.1, pitch: 52, bearing: -18 },
    radyr: { center: [-3.2586, 51.5169], zoom: 14.0, pitch: 54, bearing: 16 }
  };

  const baseURL = new URL("./", window.location.href).href;
  const $ = (selector) => document.querySelector(selector);
  const $$ = (selector) => [...document.querySelectorAll(selector)];
  const formatNumber = new Intl.NumberFormat("en-GB");
  const state = { metadata: null, map: null, rank: 0, mode: "age", playing: false, timer: null, bright: false, showAll: false, hasAutoPlayed: false };

  const elements = {
    loading: $("#loading"), introCard: $("#introCard"), coverageText: $("#coverageText"), watchButton: $("#watchButton"),
    playButton: $("#playButton"), slider: $("#timelineSlider"), periodLabel: $("#periodLabel"), buildingCount: $("#buildingCount"),
    ticks: $("#timelineTicks"), legend: $("#legendRow"), ageMode: $("#ageMode"), heightMode: $("#heightMode"),
    showAll: $("#showAllBuildings"), countLabel: $("#buildingCountLabel"), readoutPrefix: $("#readoutPrefix"), lightButton: $("#lightButton"), infoButton: $("#infoButton"), aboutDialog: $("#aboutDialog"), coverageDetail: $("#coverageDetail"),
    searchForm: $("#searchForm"), searchInput: $("#searchInput"), searchStatus: $("#searchStatus"), buildingCard: $("#buildingCard")
  };

  function parseHash() {
    const match = location.hash.match(/map=([\d.]+)\/([\d.-]+)\/([\d.-]+)\/([\d.-]+)\/([\d.-]+)/);
    const band = location.hash.match(/[?&]b=(\d+)/);
    const mode = location.hash.match(/[?&]m=(age|height)/);
    if (band) state.rank = Math.max(0, Math.min(12, Number(band[1])));
    if (mode) state.mode = mode[1];
    state.showAll = /[?&]all=1(?:&|$)/.test(location.hash);
    if (state.showAll) state.rank = state.metadata.ageLabels.length - 1;
    if (!match) return null;
    return { zoom: Number(match[1]), center: [Number(match[3]), Number(match[2])], bearing: Number(match[4]), pitch: Number(match[5]) };
  }

  function updateHash() {
    if (!state.map) return;
    const center = state.map.getCenter();
    const value = `#map=${state.map.getZoom().toFixed(2)}/${center.lat.toFixed(5)}/${center.lng.toFixed(5)}/${state.map.getBearing().toFixed(0)}/${state.map.getPitch().toFixed(0)}&b=${state.rank}&m=${state.mode}${state.showAll ? "&all=1" : ""}`;
    history.replaceState(null, "", value);
  }

  function mapStyle() {
    return {
      version: 8,
      sources: {
        base: {
          type: "raster",
          tiles: ["https://basemaps.cartocdn.com/rastertiles/voyager/{z}/{x}/{y}.png?key=cb1_32as_1_fe95f34f70f024ec7c14308b"],
          tileSize: 256,
          attribution: "© OpenStreetMap contributors · © CARTO"
        },
        boundary: { type: "geojson", data: `${baseURL}data/cardiff-boundary.geojson` }
      },
      layers: [
        { id: "background", type: "background", paint: { "background-color": "#07131f" } },
        { id: "base", type: "raster", source: "base", paint: { "raster-opacity": 0.78, "raster-brightness-max": 0.48, "raster-saturation": -0.25 } },
        { id: "city-wash", type: "fill", source: "boundary", paint: { "fill-color": "#173247", "fill-opacity": 0.12 } }
      ]
    };
  }

  function heightExpression() {
    return [
      "interpolate", ["linear"], ["zoom"],
      11, ["*", ["get", "h"], 7.5],
      13, ["*", ["get", "h"], 3.0],
      16, ["get", "h"]
    ];
  }

  function setupBuildingLayers() {
    const map = state.map;
    map.addSource("buildings", {
      type: "vector",
      tiles: [`${baseURL}tiles/{z}/{x}/{y}.pbf`],
      bounds: [-3.35, 51.37, -3.06, 51.57],
      minzoom: state.metadata.tileMinZoom,
      maxzoom: state.metadata.tileMaxZoom
    });

    map.addLayer({
      id: "undated",
      type: "fill-extrusion",
      source: "buildings",
      "source-layer": "buildings",
      filter: ["==", ["get", "r"], 99],
      paint: {
        "fill-extrusion-color": "#5d7181",
        "fill-extrusion-height": heightExpression(),
        "fill-extrusion-opacity": 0.055,
        "fill-extrusion-vertical-gradient": true
      }
    });

    AGE_COLOURS.forEach((colour, rank) => {
      map.addLayer({
        id: `age-${rank}`,
        type: "fill-extrusion",
        source: "buildings",
        "source-layer": "buildings",
        filter: ["==", ["get", "r"], rank],
        paint: {
          "fill-extrusion-color": state.mode === "height" ? HEIGHT_COLOUR : colour,
          "fill-extrusion-height": heightExpression(),
          "fill-extrusion-opacity": rank <= state.rank ? 0.9 : 0,
          "fill-extrusion-vertical-gradient": true
        }
      });
    });

    map.addLayer({ id: "cardiff-edge", type: "line", source: "boundary", paint: { "line-color": "rgba(126,231,224,.55)", "line-width": ["interpolate", ["linear"], ["zoom"], 11, 1, 15, 2], "line-dasharray": [2, 2] } });
  }

  function setRank(rank, userInitiated = false) {
    if (userInitiated) state.showAll = false;
    state.rank = Math.max(0, Math.min(state.metadata.ageLabels.length - 1, Number(rank)));
    elements.slider.value = String(state.rank);
    elements.slider.style.setProperty("--progress", `${(state.rank / (state.metadata.ageLabels.length - 1)) * 100}%`);
    elements.showAll.checked = state.showAll;
    elements.readoutPrefix.textContent = state.showAll ? "SHOWING" : "BUILT BY";
    elements.periodLabel.textContent = state.showAll ? "ALL BUILDINGS" : state.metadata.ageLabels[state.rank].toUpperCase();
    elements.buildingCount.textContent = formatNumber.format(state.showAll ? state.metadata.footprints : state.metadata.cumulative[state.rank]);
    elements.countLabel.textContent = state.showAll ? "total footprints" : "dated buildings visible";
    if (state.map.getLayer("undated")) state.map.setPaintProperty("undated", "fill-extrusion-opacity", state.showAll ? 0.9 : (state.bright ? 0.11 : 0.055));
    AGE_COLOURS.forEach((_, index) => {
      if (state.map.getLayer(`age-${index}`)) {
        state.map.setPaintProperty(`age-${index}`, "fill-extrusion-opacity", index <= state.rank ? (state.bright ? 0.98 : 0.9) : 0);
      }
    });
    if (userInitiated) {
      pause();
      elements.introCard.classList.add("dismissed");
    }
    updateHash();
  }

  function setMode(mode) {
    state.mode = mode;
    const isAge = mode === "age";
    elements.ageMode.classList.toggle("active", isAge);
    elements.heightMode.classList.toggle("active", !isAge);
    elements.ageMode.setAttribute("aria-pressed", String(isAge));
    elements.heightMode.setAttribute("aria-pressed", String(!isAge));
    AGE_COLOURS.forEach((colour, index) => {
      if (state.map.getLayer(`age-${index}`)) state.map.setPaintProperty(`age-${index}`, "fill-extrusion-color", isAge ? colour : HEIGHT_COLOUR);
    });
    renderLegend();
    updateHash();
  }

  function play(reset = false) {
    if (state.playing) return pause();
    state.showAll = false;
    if (reset || state.rank >= state.metadata.ageLabels.length - 1) setRank(0);
    state.playing = true;
    elements.playButton.classList.add("playing");
    elements.playButton.setAttribute("aria-label", "Pause timeline");
    elements.introCard.classList.add("dismissed");
    const advance = () => {
      if (!state.playing) return;
      if (state.rank >= state.metadata.ageLabels.length - 1) {
        pause();
        return;
      }
      setRank(state.rank + 1);
      state.timer = window.setTimeout(advance, 980);
    };
    state.timer = window.setTimeout(advance, 420);
  }

  function pause() {
    state.playing = false;
    window.clearTimeout(state.timer);
    elements.playButton.classList.remove("playing");
    elements.playButton.setAttribute("aria-label", "Play timeline");
  }

  function renderLegend() {
    if (!state.metadata) return;
    if (state.mode === "height") {
      const stops = [["3 m", "#455e74"], ["7 m", "#2fd2c8"], ["12 m", "#7de17d"], ["22 m", "#ffd166"], ["40 m", "#ff735d"], ["72 m", "#eaa7ff"]];
      elements.legend.innerHTML = stops.map(([label, colour]) => `<span><i style="background:${colour}"></i>${label}</span>`).join("");
      return;
    }
    const shown = [0, 2, 4, 6, 8, 10, 12];
    elements.legend.innerHTML = shown.map((index) => `<span><i style="background:${AGE_COLOURS[index]}"></i>${state.metadata.ageLabels[index].replace("Before ", "<")}</span>`).join("");
  }

  function propertyForm(typeId) {
    if (typeId > 0 && typeId < 10) return "Bungalow";
    if (typeId >= 10 && typeId < 20) return "Flat";
    if (typeId === 21) return "Detached house";
    if (typeId === 22) return "Back-to-back house";
    if (typeId >= 23 && typeId <= 25) return "Terraced house";
    if (typeId >= 26 && typeId <= 29) return "Semi-detached house";
    if (typeId >= 30 && typeId < 40) return "Maisonette";
    if (typeId >= 40) return "Park home / other";
    return "No residential EPC match";
  }

  function showBuilding(properties) {
    const rank = Number(properties.r);
    $("#buildingPeriod").textContent = rank === 99 ? "Construction period unknown" : state.metadata.ageLabels[rank];
    $("#buildingHeight").textContent = `${formatNumber.format(Number(properties.h))} m (${Number(properties.s) === 1 ? "LiDAR" : "modelled"})`;
    $("#buildingArea").textContent = `${formatNumber.format(Number(properties.a))} m²`;
    $("#buildingUnits").textContent = formatNumber.format(Number(properties.u));
    $("#buildingType").textContent = propertyForm(Number(properties.t));
    elements.buildingCard.hidden = false;
    elements.introCard.classList.add("dismissed");
  }

  async function geocode(query) {
    const normal = query.toLowerCase().trim();
    for (const [key, value] of Object.entries(PLACES)) {
      if (normal === key || normal.includes(key) || (key === "centre" && normal.includes("center"))) {
        state.map.flyTo({ ...value, duration: 1800, essential: true });
        return true;
      }
    }
    const url = new URL("https://nominatim.openstreetmap.org/search");
    url.searchParams.set("format", "jsonv2");
    url.searchParams.set("limit", "1");
    url.searchParams.set("countrycodes", "gb");
    url.searchParams.set("bounded", "1");
    url.searchParams.set("viewbox", "-3.35,51.57,-3.06,51.43");
    url.searchParams.set("q", `${query}, Cardiff, Wales`);
    const response = await fetch(url, { headers: { "Accept-Language": "en-GB,en" } });
    if (!response.ok) throw new Error("Search unavailable");
    const results = await response.json();
    if (!results.length) return false;
    state.map.flyTo({ center: [Number(results[0].lon), Number(results[0].lat)], zoom: 16.2, pitch: 58, duration: 1900, essential: true });
    return true;
  }

  function bindEvents() {
    elements.showAll.addEventListener("change", () => {
      pause();
      state.showAll = elements.showAll.checked;
      elements.introCard.classList.add("dismissed");
      setRank(state.showAll ? state.metadata.ageLabels.length - 1 : state.rank);
    });
    elements.slider.addEventListener("input", (event) => setRank(event.target.value, true));
    elements.playButton.addEventListener("click", () => play());
    elements.watchButton.addEventListener("click", () => play(true));
    elements.ageMode.addEventListener("click", () => setMode("age"));
    elements.heightMode.addEventListener("click", () => setMode("height"));
    elements.infoButton.addEventListener("click", () => elements.aboutDialog.showModal());
    $("#closeBuilding").addEventListener("click", () => { elements.buildingCard.hidden = true; });
    $(".brand").addEventListener("click", (event) => {
      event.preventDefault();
      state.map.flyTo({ center: [-3.1791, 51.4934], zoom: 11.35, pitch: 52, bearing: -17, duration: 1800, essential: true });
    });
    elements.lightButton.addEventListener("click", () => {
      state.bright = !state.bright;
      state.map.setPaintProperty("base", "raster-opacity", state.bright ? 0.98 : 0.78);
      state.map.setPaintProperty("base", "raster-brightness-max", state.bright ? 0.72 : 0.48);
      setRank(state.rank);
    });
    $$(".places button").forEach((button) => button.addEventListener("click", () => {
      elements.introCard.classList.add("dismissed");
      state.map.flyTo({ ...PLACES[button.dataset.place], duration: 1700, essential: true });
    }));
    elements.searchForm.addEventListener("submit", async (event) => {
      event.preventDefault();
      const query = elements.searchInput.value.trim();
      if (!query) return;
      elements.searchStatus.textContent = "Searching Cardiff…";
      elements.searchStatus.classList.add("visible");
      try {
        const found = await geocode(query);
        elements.searchStatus.textContent = found ? "Location found" : "No Cardiff result found";
      } catch {
        elements.searchStatus.textContent = "Search is temporarily unavailable";
      }
      window.setTimeout(() => elements.searchStatus.classList.remove("visible"), 2200);
    });
  }

  async function initialise() {
    try {
      state.metadata = await fetch(`${baseURL}data/metadata.json`).then((response) => {
        if (!response.ok) throw new Error("Metadata unavailable");
        return response.json();
      });
      const hashView = parseHash();
      elements.slider.max = String(state.metadata.ageLabels.length - 1);
      elements.ticks.innerHTML = state.metadata.ageLabels.map(() => "<i></i>").join("");
      elements.coverageText.innerHTML = `<strong>${formatNumber.format(state.metadata.datedBuildings)}</strong> of <strong>${formatNumber.format(state.metadata.footprints)}</strong> present footprints have a residential construction period (${state.metadata.coveragePct}%).`;
      elements.coverageDetail.textContent = `${formatNumber.format(state.metadata.datedBuildings)} of ${formatNumber.format(state.metadata.footprints)} present footprints are dated (${state.metadata.coveragePct}%). LiDAR supplies ${state.metadata.heightCoveragePct}% of footprint heights; the remainder use a modelled fallback. Residential EPCs do not cover every footprint, particularly non-residential buildings.`;
      renderLegend();
      bindEvents();

      state.map = new maplibregl.Map({
        container: "map",
        style: mapStyle(),
        center: hashView?.center || [-3.1791, 51.4934],
        zoom: hashView?.zoom || 11.35,
        pitch: hashView?.pitch ?? 52,
        bearing: hashView?.bearing ?? -17,
        minZoom: 10.75,
        maxZoom: 18,
        maxPitch: 70,
        antialias: true,
        attributionControl: false,
        hash: false
      });
      state.map.addControl(new maplibregl.NavigationControl({ visualizePitch: true }), "top-right");
      state.map.addControl(new maplibregl.AttributionControl({ compact: true, customAttribution: "Contains OS data © Crown copyright and database right 2026 · EPC: GeoDS/CDRC · LiDAR: Welsh Government" }), "bottom-right");

      state.map.on("load", () => {
        setupBuildingLayers();
        setMode(state.mode);
        setRank(state.rank);
        elements.loading.classList.add("hidden");
        window.setTimeout(() => elements.loading.remove(), 600);

        const interactiveLayers = ["undated", ...AGE_COLOURS.map((_, index) => `age-${index}`)];
        state.map.on("mousemove", interactiveLayers, (event) => {
          const hit = event.features?.some((item) => Number(item.properties.r) === 99 || Number(item.properties.r) <= state.rank);
          state.map.getCanvas().style.cursor = hit ? "pointer" : "";
        });
        state.map.on("mouseleave", interactiveLayers, () => { state.map.getCanvas().style.cursor = ""; });
        state.map.on("click", interactiveLayers, (event) => {
          const feature = event.features?.find((item) => Number(item.properties.r) === 99 || Number(item.properties.r) <= state.rank);
          if (feature) showBuilding(feature.properties);
        });
        state.map.on("moveend", updateHash);

        if (!state.showAll && !location.hash.includes("b=") && !window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
          state.hasAutoPlayed = true;
          window.setTimeout(() => play(true), 1300);
        }
      });

      state.map.on("error", (event) => {
        if (event?.error?.message) console.warn("Map resource:", event.error.message);
      });
    } catch (error) {
      console.error(error);
      elements.loading.querySelector("p").textContent = "Cardiff’s building data could not be loaded";
    }
  }

  initialise();
})();
