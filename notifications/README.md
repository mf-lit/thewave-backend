# Notifications System Backend

Backend service for managing session availability notifications for the waveform mobile app.

## Overview

This system consists of two main components:

1. **Flask API** - RESTful endpoints for clients to manage their notifications
2. **Daemon Service** - Periodically checks session availability and sends notifications

## Architecture

- **Storage**: SQLite (lightweight, embedded database)
- **API Framework**: Flask
- **Language**: Python 3.11+

## Setup

### Prerequisites

- Python 3.11 or higher
- UV package manager

### Installation

```bash
# Install dependencies using UV
uv sync
```

### Environment Variables

- `CALENDAR_API_URL` - Upstream calendar API URL (default: `http://localhost:5000/calendar`)
- `SQLITE_DB_PATH` - Path to SQLite database file (default: `notifications.db`)

## Usage

### Running the API Server

```bash
python -m src.api.app
```

The API will be available at `http://localhost:5001`

### Running the Daemon

```bash
python -m src.daemon.scheduler
```

The daemon will:
- Check availability every 10 minutes
- Automatically delete notifications for past sessions
- Send notifications when conditions are met (currently logs to console)

## API Endpoints

All endpoints are scoped to a client UUID.

### Create Notification

```http
POST /clients/{client_id}/notifications
Content-Type: application/json

{
  "date": "2025-12-30",
  "time": "10:00",
  "side": "left",
  "notification_type": "below_threshold",
  "thresholds": [5, 2]
}
```

**Notification Types:**
- `below_threshold`: Notify when availability falls below specified thresholds
- `above_zero`: Notify when availability increases above zero

### List Notifications

```http
GET /clients/{client_id}/notifications
```

### Delete Notification

```http
DELETE /clients/{client_id}/notifications/{notification_id}
```

## Database Schema

### Notifications Table

```sql
CREATE TABLE notifications (
    client_id TEXT NOT NULL,
    notification_id TEXT NOT NULL,
    performance_ak TEXT NOT NULL,
    date TEXT NOT NULL,
    time TEXT NOT NULL,
    side TEXT NOT NULL,
    title TEXT NOT NULL,
    notification_type TEXT NOT NULL,
    thresholds TEXT,  -- JSON array for below_threshold type
    notified_thresholds TEXT,  -- JSON array of triggered thresholds
    last_checked_availability INTEGER,
    created_at TEXT NOT NULL,
    PRIMARY KEY (client_id, notification_id)
);
```

**Indexes:**
- `idx_performance_ak` on `performance_ak` for efficient queries by performance
- `idx_date` on `date` for date-based queries

### Clients Table

```sql
CREATE TABLE clients (
    client_id TEXT PRIMARY KEY,
    fcm_token TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
```

## Migrating from DynamoDB

If you have existing data in DynamoDB and want to migrate to SQLite, run the migration script:

```bash
python migrate_to_sqlite.py
```

This will:
1. Connect to your existing DynamoDB instance (LocalStack)
2. Fetch all notifications and client tokens
3. Create SQLite database tables
4. Migrate all data to SQLite
5. Save the database to `notifications.db`

**Note**: The migration script does not delete data from DynamoDB. You can safely run it multiple times.

## Development

The project structure:

```
notifications/
├── pyproject.toml
├── src/
│   ├── api/          # Flask API endpoints
│   ├── daemon/       # Background daemon service
│   ├── storage/      # DynamoDB operations
│   └── utils/        # Calendar API client
```

