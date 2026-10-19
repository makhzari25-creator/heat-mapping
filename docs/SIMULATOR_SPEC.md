# Multi-Store Shopper Simulator Spec — VisionRetail IQ

This document details the design and operational logic of the Real-time Shopper CV Event Simulator (`app/simulator.py`) and Database Seeder (`app/startup_seed.py`).


## Purpose of the Simulator

In physical retail analytics deployments, camera feeds may not always be active (e.g. outside store operating hours, during edge hardware downtime, or in local development environments). 

To ensure the platform dashboard remains functional and provides developers and system evaluators with live, dynamic datasets, VisionRetail IQ includes a background **Shopper CV Event Simulator**:

* It runs automatically in the FastAPI server lifecycle loop.
* It simulates realistic customer behaviors, walk-paths, dwell metrics, checkout queues, and transaction correlations.
* It supports multi-store operations, simulating **Store 1 (Bangalore)** and **Store 2 (Mumbai)** independently.


## Core Simulation Logic

```
   [Store Hours Start]
            |
            v
    [Spawn Shoppers] --> Spawns buyers and non-buyers dynamically
            |
            v
    [Simulate Walkpath] --> Visitor moves Entry -> Floor Zones -> Billing (optional) -> Exit
            |
            v
   [Emit Event Logs] --> Writes Entry, Zone Enters, Dwells (30s intervals), Exits
            |
            v
    [Ingest to Database] --> Automatically commits to SQLite
```


## 1. Multi-Store Parametrization

The simulator partitions state and execution variables using dictionaries keyed by `store_id` (e.g. `STORE_BLR_002` and `STORE_MUM_001`), running isolated loops for each:

* **Salesperson Mapping**:
  * **Store 1 (Bangalore)**: salesperson IDs `1178`, `971`, `523`, `737`, `1190`.
  * **Store 2 (Mumbai)**: salesperson IDs `2001`, `2002`, `2003`, `2004`, `2005`.
* **Revenue Multiplier**: For Store 2, transaction values are automatically scaled by **1.15x** to simulate different regional store ticket values.
* **Camera Suffixes**: Events are emitted with correct camera tags (`_01` for Bangalore, `_02` for Mumbai).


## 2. Behavioral Shopper Rules

* **Browsing Paths**: Spawns shoppers and selects browsing zones based on relative popularity weights (Makeup: 45%, Skincare: 40%, Bath & Body: 20%, Hair: 25%, Fragrance: 18%, Personal Care: 15%).
* **Real-time Dwells**: Emits `ZONE_DWELL` events every 30 continuous seconds of dwell.
* **Checkout Queue Entry**: Buyers join checkout lines. The simulator increments the queue depth dynamically and emits a `BILLING_QUEUE_JOIN` event.
* **Transactional Correlation**: At exit from the billing zone, the simulator links the visitor to a transaction, attaching transaction `amount` and `salesperson_id` in the event metadata.
* **Queue Abandonment**: Simulates a 15% abandonment rate for non-purchasing queue entrants. They join the queue, wait for 30–180 seconds, and emit a `BILLING_QUEUE_ABANDON` event.
* **Re-entries**: Approximately 10% of customers are simulated to return within 30 minutes, triggering a `REENTRY` event.
* **Staff Patrolling**: Simulates 5 salespeople patrolling floor zones for longer intervals (5–30 minutes) without making transactions.

## 3. Database Seeding (`startup_seed.py`)

On database initialization (first server launch):
* The seeder checks if the `events` table is empty.
* If empty, it reads the POS CSV (`pos_transactions.csv`), parses dates and transaction times, and maps them to:
  * Historical Operating Date (`2026-04-10`)
  * Current Date (`today`)
* It creates simulated customer paths anchored to these transactions.
* It performs a bulk insert in chunks of 200 events, creating thousands of data points for instant analytical graphs.

## 4. Presenter Override Endpoints

The simulator exposes control hooks under `/simulator/control` allowing external presenters to inject alerts:
* **`trigger_queue_spike`**: Sets queue depth to 5+, forcing a critical alert banner in the UI.
* **`trigger_conversion_drop`**: Drastically drops checkout events, triggering a low conversion alert.
* **`frozen_dead_zone`**: Freezes Skincare or Makeup visits to 0, triggering layout dead-zone alerts.
