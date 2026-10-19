# Frontend Architecture & UI Guide — VisionRetail IQ

This document details the layout, design architecture, proxy routes, and UI components of the Next.js web application dashboard (`frontend/`).


## 1. Directory Structure

The frontend is built using Next.js 14 App Router, styled with vanilla CSS/Tailwind CSS, and visualization components from Recharts:

```
frontend/
├── app/
│   ├── api/                 # Next.js Server API Proxy Routes
│   │   ├── simulator/       # Simulator active / override forwarding
│   │   └── stores/          # Forwarding to metrics, heatmaps, funnels
│   ├── dashboard/           # Store dashboard page
│   │   └── page.jsx         # Executive Analytics UI & SVG layout twin
│   ├── favicon.ico
│   ├── layout.js
│   └── page.js              # Redirects to /dashboard
├── public/                  # Static assets and logo files
├── styles/                  # Global and layout styles
└── package.json
```

## 2. Next.js API Proxy Routes

To prevent CORS warnings in containerized environments and simplify API URLs, the Next.js node server acts as a proxy:
* All requests are sent to the frontend's server segments (e.g. `/api/stores/STORE_MUM_001/metrics`).
* The Next.js API route reads the backend base url from `process.env.BACKEND_URL` (local or docker service name) and forwards the request.
* It appends the query arguments (e.g. `store_id`, `visitor_id`, `date`) and returns the clean JSON payload back to the client.


## 3. Real Store Digital Twin (SVG Overlays)

The central element of the dashboard is the interactive **Retail Digital Twin**:
* **SVG rendering**: Coordinates for product zones (Makeup, Skincare, Bath & Body, Hair, Fragrance, Personal Care, Billing) are drawn as scaled SVG `<polygon>` paths based on layout coordinates.
* **Dynamic Heatmap Fills**: Polygons are colored dynamically:
  * **Hotspots**: Render glowing gold outlines or overlays for zones with high conversions.
  * **Dead Zones**: Rendered with faded gray fills if visits are zero.
  * **Congestion/Anomaly Alert**: Flashes a thick red border and overlay if occupancy limits are breached or a queue spike occurs.


## 4. Interactive Click Popovers

When a operator clicks any zone on the SVG layout map:
* An event handler opens a glassmorphic Details Sidebar.
* It fetches live metrics for that specific zone, rendering:
  1. **Zone Dwell Time**: Average customer browse duration in minutes.
  2. **Zone Conversion Rate**: Ratio of zone visits resulting in purchase.
  3. **Abandonment Count**: Visitors who entered the checkout queue but left without a transaction.
  4. **Revenue Influence**: Monetary sum of transactions associated with this zone.
  5. **Associate Assignment**: Displays staff currently stationed at the zone.
  6. **Active Promo Banner**: Active promotion mapped to that department.


## 5. CCTV Camera Sidebar & Telemetry

* **Dynamic Feed Toggles**: Displays camera list for the chosen store (e.g. `CAM_ENTRY_02`, `CAM_FLOOR_02`, `CAM_BILLING_02` for Store 2).
* **Live Camera Health HUD**: Renders diagnostic metrics for edge hardware:
  * **Status indicator**: Green dot for online/active, amber for warnings, red for offline.
  * **FPS Telemetry**: Displays current frame rates (fluctuating realistically around 24.8 FPS).
  * **Event Rate (EPM)**: Events per minute sent to the API.


## 6. Executive Presentation Mode

The header features a **"Trigger Executive Demo Mode"** button:
* When clicked, it sets a local override state in the UI.
* It renders live, interactive shopping trails (colored walking lines overlaying the digital twin showing visitors moving across Makeup -> Skincare -> Billing).
* Triggers simulated anomalies (e.g. queue spikes, low conversion warnings) to let system evaluators preview AI notifications and system recommendations without waiting for real clips to play.
