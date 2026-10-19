# Setup & Installation Guide — VisionRetail IQ

This guide details instructions for setting up, installing dependencies, configuring, and launching the VisionRetail IQ application.


## System Prerequisites

Ensure you have the following software installed:
* **Python**: Version `3.10` or higher
* **Node.js**: Version `18` or higher
* **Docker & Docker Compose**: (Required for containerized setup)
* **Git**


## 1. Local Development Setup

### Backend API Server Setup

1. **Clone the Repository**:
   ```bash
   git clone https://github.com/UjjwalSaini07/VisionRetail-IQ.git
   ```
   ```bash
   cd VisionRetail-IQ
   ```

2. **Create and Activate a Virtual Environment**:
   ```bash
   # On Windows
   python -m venv .venv
   .venv\Scripts\activate
   ```
   ```bash
   # On macOS/Linux
   python3 -m venv .venv
   source .venv/bin/activate
   ```

3. **Install Python Packages**:
   ```bash
   pip install -r requirements.txt
   # Ensure OpenCV and Ultralytics (YOLO) are installed
   pip install opencv-python ultralytics
   ```

4. **Environment Configuration**:
   Create a `.env` file in the root directory (or copy `.env.example`):
   ```env
   DATABASE_URL=sqlite+aiosqlite:///data/store_intelligence.db
   CORS_ORIGINS=http://localhost:3000
   APP_VERSION=1.0.0
   LOG_LEVEL=INFO
   GROQ_API_KEY=your_groq_api_key_here
   GROQ_MODEL=llama3-70b-8192
   REDIS_URL=redis://localhost:6379
   ```

5. **Start the FastAPI Server**:
   ```bash
   # Run from the root directory
   python app/main.py
   ```
   The backend API will start at `http://localhost:8000`. You can inspect the Swagger UI documentation at `http://localhost:8000/docs`.


### Frontend Client Setup

1. **Navigate to the Frontend Folder**:
   ```bash
   cd frontend
   ```

2. **Install Node Modules**:
   ```bash
   npm install
   ```

3. **Configure Environment Variables**:
   Create a `frontend/.env.local` file:
   ```env
   NEXT_PUBLIC_API_URL=http://localhost:8000
   ```

4. **Launch Next.js Dev Client**:
   ```bash
   npm run dev
   ```
   Open `http://localhost:3000` in your browser. The dashboard will populate automatically.


## 2. Running the Video Processing Pipeline

You can run the YOLO tracking pipeline against the actual video files:

1. **Ensure Clips Exist**:
   Verify that your video files are located in `resources/clips/Store 1` and `resources/clips/Store 2`.

2. **Execute Detection Script**:
   ```bash
   # Run for Store 2 (Mumbai)
   python pipeline/detect.py --store STORE_MUM_001 --output resources/events_seed.jsonl

   # Run for Store 1 (Bangalore)
   python pipeline/detect.py --store STORE_BLR_002 --output resources/events_seed.jsonl
   ```

3. **Ingest Events into the API**:
   With the FastAPI server running, run:
   ```bash
   python pipeline/ingest_jsonl.py resources/events_seed.jsonl
   ```
   This will post all processed events in batches of 500 to the SQLite database.


## 3. Containerized Deployment (Docker)

You can run the entire stack (FastAPI Backend, Redis, SQLite, Next.js Frontend) inside Docker:

1. **Verify `docker-compose.yml` Config**:
   Ensure ports `8000` and `3000` are free on your host.

2. **Launch Containers**:
   ```bash
   docker compose up --build
   ```

3. **Containers Status**:
   - Backend API: `http://localhost:8000`
   - Frontend Dashboard: `http://localhost:3000`
   - Redis: Runs internally on port `6379`
