# Troubleshooting Guide — VisionRetail IQ

This document lists common system exceptions, errors, installation issues, and their resolutions.

## 1. Pipeline Execution Errors

### Timezone Aware vs Naive Math Error
* **Error message**: `TypeError: can't subtract offset-naive and offset-aware datetimes`
* **Cause**: Occurs inside the `VisitorTracker.get_dwell_ms` method when subtracting the current local system time (offset-naive) from `zone_enter` (which contains timezone metadata parsed from the UTC layout config).
* **Fix**: Ensure `now` is offset-aware by matching its timezone offset dynamically:
  ```python
  now = datetime.now(zone_enter.tzinfo) if zone_enter.tzinfo else datetime.utcnow()
  ```
  *(Note: This is already implemented in the current codebase).*


### YOLO Weight Download Timeout
* **Error message**: `ConnectionError: Failed to download yolov8n.pt from GitHub assets.`
* **Cause**: The computer vision pipeline tries to fetch the YOLO model weights on its first run, which can fail behind proxies or firewalls.
* **Fix**: Manually download `yolov8n.pt` from the Ultralytics assets release page and place it directly into the root directory `f:\_Code\VisionRetail-IQ\`.


### CCTV Clip Missing Warnings
* **Error message**: `[WARN] Clip not found: resources/clips/Store 2/CAM 1_MUM.mp4 — skipping`
* **Cause**: The physical MP4 files under the `resources/clips/` directory do not match the exact filenames declared in the `store_layout.json` schema.
* **Fix**: The pipeline has a built-in fallback mapper. Ensure the filenames contain target keywords:
  * Entry clip contains `"entry"` (e.g. `entry 1.mp4`).
  * Floor clip contains `"zone"` or `"floor"` (e.g. `zone.mp4`).
  * Billing clip contains `"billing"` or `"cash"` (e.g. `billing_area.mp4`).


## 2. Backend API Server Errors

### Database Lock Error
* **Error message**: `sqlalchemy.exc.OperationalError: (sqlite3.OperationalError) database is locked`
* **Cause**: SQLite only supports one concurrent writer. If the background simulator loop attempts to write events while a batch event ingestion is ongoing, the table may lock.
* **Fix**: The backend uses an asynchronous engine. Restart the FastAPI server. For multi-store production environments with high write volumes, change the `DATABASE_URL` in `.env` to a PostgreSQL server:
  ```env
  DATABASE_URL=postgresql+asyncpg://user:pass@host:5432/dbname
  ```


### Redis Connectivity Failure
* **Error message**: `redis.exceptions.ConnectionError: Error connecting to localhost:6379.`
* **Cause**: The FastAPI server attempts to connect to Redis on startup to track real-time queue occupancies.
* **Fix**:
  * **Local Setup**: Ensure Redis service is running locally on port `6379`.
    * Windows: Start Redis via WSL or Memurai.
    * Linux/macOS: `sudo service redis-server start` or `brew services start redis`.
  * **Docker Setup**: Verify that port `6379` is open in the `redis` container.


### Port Conflict Errors
* **Error message**: `OSError: [Errno 98] Address already in use (port 8000 / 3000)`
* **Cause**: Another service or process is already running on ports `8000` (FastAPI) or `3000` (Next.js).
* **Fix**: Identify and kill the blocking process:
  * Windows:
    ```cmd
    netstat -ano | findstr :8000
    taskkill /PID <PID_NUMBER> /F
    ```
  * Linux/macOS:
    ```bash
    lsof -i :8000
    kill -9 <PID_NUMBER>
    ```


## 3. Frontend UI Dashboard Errors

### Heatmap/KPI Values showing Zero or NaN
* **Cause**: The frontend Next.js server cannot communicate with the backend FastAPI port, resulting in failed API proxy fetches.
* **Fix**:
  * Verify that the FastAPI backend is running at `http://localhost:8000`.
  * Ensure the Next.js `.env.local` contains:
    ```env
    NEXT_PUBLIC_API_URL=http://localhost:8000
    ```
  * Inspect the browser developer console for network errors.
