const STORAGE_KEY = "camsleuth.plannedCameras";
const GEOJSON_PATH = "/trailcam_coverage/trailcam_locations.geojson";

const map = L.map("map", { zoomControl: false }).setView([40.3876, -75.7894], 8);
L.control.zoom({ position: "topright" }).addTo(map);

L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
  maxZoom: 19,
  attribution: "&copy; OpenStreetMap contributors",
}).addTo(map);

const deploymentLayer = L.layerGroup().addTo(map);
const signalLayer = L.layerGroup().addTo(map);
const plannedLayer = L.layerGroup().addTo(map);

const state = {
  deployments: [],
  signals: [],
  planned: loadPlannedCameras(),
};

const els = {
  deploymentCount: document.getElementById("deployment-count"),
  signalCount: document.getElementById("signal-count"),
  plannedCount: document.getElementById("planned-count"),
  listSummary: document.getElementById("list-summary"),
  plannedList: document.getElementById("planned-list"),
  showDeployments: document.getElementById("show-deployments"),
  showSignals: document.getElementById("show-signals"),
  showPlanned: document.getElementById("show-planned"),
  fitAll: document.getElementById("fit-all"),
  exportPlanned: document.getElementById("export-planned"),
  clearPlanned: document.getElementById("clear-planned"),
};

function loadPlannedCameras() {
  try {
    return JSON.parse(localStorage.getItem(STORAGE_KEY) || "[]");
  } catch (error) {
    return [];
  }
}

function savePlannedCameras() {
  localStorage.setItem(STORAGE_KEY, JSON.stringify(state.planned));
}

function makeCircleMarker(latlng, color) {
  return L.circleMarker(latlng, {
    radius: 7,
    weight: 1,
    color: "#102315",
    fillColor: color,
    fillOpacity: 0.88,
  });
}

function popupHtml(title, props, includeAdd = false) {
  const bits = [];
  if (props.coordinate_precision) {
    bits.push(`<li>Precision: ${props.coordinate_precision}</li>`);
  }
  if (props.source_type) {
    bits.push(`<li>Source: ${props.source_type}</li>`);
  }
  if (props.location_label) {
    bits.push(`<li>Label: ${props.location_label}</li>`);
  }
  if (props.species_terms && props.species_terms.length) {
    bits.push(`<li>Species: ${props.species_terms.join(", ")}</li>`);
  }
  const addButton = includeAdd
    ? `<p><button type="button" data-add-lat="${props.lat}" data-add-lng="${props.lng}" data-add-name="${title.replace(/"/g, "&quot;")}">Plot Camera Here</button></p>`
    : "";
  return `
    <strong>${title}</strong>
    <ul class="popup-meta">${bits.join("")}</ul>
    ${addButton}
  `;
}

function renderIndexedCameras() {
  deploymentLayer.clearLayers();
  signalLayer.clearLayers();

  state.deployments.forEach((feature) => {
    const [lng, lat] = feature.geometry.coordinates;
    const props = { ...feature.properties, lat, lng };
    makeCircleMarker([lat, lng], "#2d6a4f")
      .bindPopup(popupHtml(props.display_name || "Deployment", props, true))
      .addTo(deploymentLayer);
  });

  state.signals.forEach((feature) => {
    const [lng, lat] = feature.geometry.coordinates;
    const props = { ...feature.properties, lat, lng };
    makeCircleMarker([lat, lng], "#bc6c25")
      .bindPopup(popupHtml(props.display_name || "Signal", props, true))
      .addTo(signalLayer);
  });
}

function renderPlannedCameras() {
  plannedLayer.clearLayers();
  state.planned.forEach((camera) => {
    const marker = L.marker([camera.lat, camera.lng])
      .bindPopup(`
        <strong>${camera.name}</strong>
        <p>${camera.notes || "No notes"}</p>
        <p>${camera.lat.toFixed(5)}, ${camera.lng.toFixed(5)}</p>
      `);
    marker.addTo(plannedLayer);
  });

  els.plannedList.innerHTML = "";
  if (!state.planned.length) {
    els.plannedList.innerHTML = "<p>No planned cameras yet.</p>";
  } else {
    state.planned.forEach((camera) => {
      const item = document.createElement("article");
      item.className = "planned-item";
      item.innerHTML = `
        <h3>${camera.name}</h3>
        <p>${camera.notes || "No notes"}</p>
        <p>${camera.lat.toFixed(5)}, ${camera.lng.toFixed(5)}</p>
        <div class="row">
          <button type="button" data-focus="${camera.id}">Focus</button>
          <button type="button" data-delete="${camera.id}" class="danger">Delete</button>
        </div>
      `;
      els.plannedList.appendChild(item);
    });
  }

  els.deploymentCount.textContent = String(state.deployments.length);
  els.signalCount.textContent = String(state.signals.length);
  els.plannedCount.textContent = String(state.planned.length);
  els.listSummary.textContent = `${state.planned.length} saved`;
}

function fitAllData() {
  const bounds = [];
  [...state.deployments, ...state.signals].forEach((feature) => {
    const [lng, lat] = feature.geometry.coordinates;
    bounds.push([lat, lng]);
  });
  state.planned.forEach((camera) => bounds.push([camera.lat, camera.lng]));
  if (bounds.length) {
    map.fitBounds(bounds, { padding: [30, 30] });
  }
}

function addPlannedCamera(lat, lng, seedName = "") {
  const name = window.prompt("Camera name", seedName || `Camera ${state.planned.length + 1}`);
  if (!name) {
    return;
  }
  const notes = window.prompt("Notes", "") || "";
  state.planned.push({
    id: crypto.randomUUID(),
    name,
    notes,
    lat,
    lng,
    createdAt: new Date().toISOString(),
  });
  savePlannedCameras();
  renderPlannedCameras();
}

async function loadGeoJson() {
  try {
    const response = await fetch(GEOJSON_PATH, { cache: "no-store" });
    if (!response.ok) {
      throw new Error(`HTTP ${response.status}`);
    }
    const geojson = await response.json();
    state.deployments = geojson.features.filter((feature) => feature.properties.record_type === "deployment");
    state.signals = geojson.features.filter((feature) => feature.properties.record_type === "signal");
    renderIndexedCameras();
    renderPlannedCameras();
    fitAllData();
  } catch (error) {
    els.plannedList.innerHTML = `<p>Could not load ${GEOJSON_PATH}. Build or export the location index first.</p>`;
    renderPlannedCameras();
  }
}

map.on("click", (event) => addPlannedCamera(event.latlng.lat, event.latlng.lng));

document.addEventListener("click", (event) => {
  const addButton = event.target.closest("[data-add-lat]");
  if (addButton) {
    addPlannedCamera(Number(addButton.dataset.addLat), Number(addButton.dataset.addLng), addButton.dataset.addName || "");
    return;
  }

  const focusButton = event.target.closest("[data-focus]");
  if (focusButton) {
    const camera = state.planned.find((item) => item.id === focusButton.dataset.focus);
    if (camera) {
      map.setView([camera.lat, camera.lng], 14);
    }
    return;
  }

  const deleteButton = event.target.closest("[data-delete]");
  if (deleteButton) {
    state.planned = state.planned.filter((item) => item.id !== deleteButton.dataset.delete);
    savePlannedCameras();
    renderPlannedCameras();
  }
});

els.showDeployments.addEventListener("change", () => {
  if (els.showDeployments.checked) {
    deploymentLayer.addTo(map);
  } else {
    deploymentLayer.remove();
  }
});

els.showSignals.addEventListener("change", () => {
  if (els.showSignals.checked) {
    signalLayer.addTo(map);
  } else {
    signalLayer.remove();
  }
});

els.showPlanned.addEventListener("change", () => {
  if (els.showPlanned.checked) {
    plannedLayer.addTo(map);
  } else {
    plannedLayer.remove();
  }
});

els.fitAll.addEventListener("click", fitAllData);

els.exportPlanned.addEventListener("click", () => {
  const blob = new Blob([JSON.stringify(state.planned, null, 2)], { type: "application/json" });
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = "camsleuth_planned_cameras.json";
  anchor.click();
  URL.revokeObjectURL(url);
});

els.clearPlanned.addEventListener("click", () => {
  if (!window.confirm("Delete all planned cameras from this browser?")) {
    return;
  }
  state.planned = [];
  savePlannedCameras();
  renderPlannedCameras();
});

loadGeoJson();
